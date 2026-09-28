"""The conditions a simulation runs at, from the user's own request.

    for_goal(goal) -> {"values": {...}, "sources": {...}, "notes": [...]}

Whatever the user asked for -- 40 C today, pH 5.5 tomorrow, PBS the day after
-- is what the worker simulates, within what a simulation can physically do.
Each value says where it came from: the request, or the default because the
request did not say. The worker reports back what it actually applied
(SimulationResult.conditions), so the record is what ran, not what was hoped.

A condition the simulation cannot represent (shelf life, route, a freeze-dried
cake) is not silently dropped: it comes back as a note.

The web mirrors DEFAULTS in web/lib/conditions.ts to show them before a run.
"""

from __future__ import annotations

# What each condition is when the request does not say, and the range a
# simulation of a protein in water can take.
DEFAULTS = {
    "temperature_c": 25.0,       # room temperature
    "ph": 7.0,
    "salt_mm": 150.0,            # NaCl, physiological ionic strength
    "polymer_wv_percent": 5.0,   # the polymer in the box, % w/v
}
LIMITS = {
    # Water below 0 C is supercooled in a simulation, not frozen; above ~95 C it
    # is no longer the liquid the force field was fitted to.
    "temperature_c": (0.0, 95.0),
    "ph": (2.0, 12.0),
    "salt_mm": (0.0, 1000.0),
    "polymer_wv_percent": (0.5, 20.0),
}
LABELS = {
    "temperature_c": ("temperature", "{:g} °C"),
    "ph": ("pH", "{:g}"),
    "salt_mm": ("salt", "{:g} mM NaCl"),
    "polymer_wv_percent": ("polymer concentration", "{:g}% w/v"),
}


def for_goal(goal: dict) -> dict:
    values, sources, notes = {}, {}, []
    for key, default in DEFAULTS.items():
        asked = goal.get("target_temp_c" if key == "temperature_c" else key)
        if asked is None:
            values[key], sources[key] = default, "default"
            continue
        lo, hi = LIMITS[key]
        v = min(max(float(asked), lo), hi)
        values[key], sources[key] = v, "request"
        if v != float(asked):
            name, fmt = LABELS[key]
            notes.append(f"{name} {fmt.format(float(asked))} asked for, simulated at {fmt.format(v)}: "
                         f"the simulation covers {fmt.format(lo)} to {fmt.format(hi)}")
            sources[key] = "request, limited"
    if goal.get("duration_months"):
        notes.append(f"the {goal['duration_months']:g}-month shelf life is not simulated: a run is "
                     "nanoseconds, so it shows where the polymer sits, not how long the protein lasts")
    if goal.get("format") == "lyophilised":
        notes.append("the freeze-dried form is not simulated: the run is in solution")
    return {"values": values, "sources": sources, "notes": notes}
