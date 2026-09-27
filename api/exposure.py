"""Impurity exposure margins against the ICH M7 threshold of toxicological concern.

A residual impurity at "<= 1 ppm" is a specification, not an exposure. This
turns it into one: excipient mass per dose (from the concentration and the dose
volume), times the impurity level, gives micrograms per administration, which
is compared with the ICH M7 acceptable intake for the treatment duration.

Computed from the request alone, before the agent runs, so the numbers never
pass through the model. The model reports them; it cannot change them.

What this is and is not:
  - The limits are ICH M7 Table 2's acceptable intakes for an individual
    mutagenic impurity. They are the most conservative generic limits there
    are, and they are only meaningful for a DNA-reactive impurity.
  - Compound-specific acceptable intakes (the M7 addendum, published PDEs)
    supersede these tiers. None are applied here.
  - ICH M7 formally excludes biotechnological products. For a biologic's
    excipient this is a conservative benchmark, not a regulatory limit.
  - Only one impurity at a time. Totals across impurities are not summed.
"""

import math
import re

from pydantic import BaseModel, Field

import precedent

# (upper bound on dosing days, acceptable intake µg/day, ICH M7 duration category)
TTC_TIERS = [
    (30, 120.0, "<= 1 month"),
    (365, 20.0, "> 1-12 months"),
    (3650, 10.0, "> 1-10 years"),
    (math.inf, 1.5, "> 10 years to lifetime"),
]

# Everything relative to the excipient's mass, in µg per g of excipient.
_LEVEL_UNITS = {
    "ppm": 1.0, "µg/g": 1.0, "ug/g": 1.0, "mcg/g": 1.0, "mg/kg": 1.0,
    "ppb": 1e-3, "ng/g": 1e-3, "µg/kg": 1e-3, "ug/kg": 1e-3,
    "mg/g": 1e3, "%": 1e4, "%w/w": 1e4,
}
_BOUND = re.compile(r"^(?:<=|≤|<|nmt|not\s+more\s+than|max\.?|maximum|up\s+to)\s*", re.I)


class ExposureInputs(BaseModel):
    # The excipient concentration exactly as the user wrote it, as for the
    # precedent check: "0.02% w/v", "10 mg/mL", "5 mg per dose".
    excipient_concentration: str = Field(default="", max_length=100)
    dose_volume_ml: float | None = Field(default=None, gt=0, le=1000)
    dosing_interval_days: float = Field(default=1.0, gt=0, le=3650)
    # None means not given: assume lifetime, the most conservative tier.
    treatment_duration_days: float | None = Field(default=None, gt=0, le=36500)


def parse_level(text: str) -> float | None:
    """'<= 1 ppm', 'NMT 10 µg/g', '0.001%' -> µg per g of excipient, or None."""
    t = _BOUND.sub("", (text or "").strip())
    m = re.match(r"^(\d+(?:\.\d+)?|\.\d+)\s*(.*?)\s*$", t)
    if not m:
        return None
    unit = re.sub(r"\s", "", m.group(2).lower()).replace("μ", "µ")
    factor = _LEVEL_UNITS.get(unit)
    return float(m.group(1)) * factor if factor is not None else None


def excipient_mg_per_dose(concentration: str, volume_ml: float | None) -> tuple[float | None, str]:
    parsed = precedent.parse_concentration(concentration)
    if not parsed:
        return None, (
            "excipient concentration not given" if not concentration.strip()
            else f"excipient concentration {concentration!r} is not in a mass unit"
        )
    if parsed["unit"] == "mg":
        return parsed["value"], f"{parsed['value']:g} mg per dose, as given"
    if volume_ml is None:
        return None, "a concentration needs a dose volume to become a mass per dose"
    # 1 %w/v is 10 mg/mL by definition.
    mg = parsed["value"] * 10 * volume_ml
    return mg, f"{parsed['interpreted_as']} x {volume_ml:g} mL = {mg:g} mg per dose"


def tier(dosing_days: float) -> tuple[float, str]:
    for upper, ai, label in TTC_TIERS:
        if dosing_days <= upper:
            return ai, label
    raise AssertionError("unreachable")


def assess(impurities, inputs: ExposureInputs | None) -> dict:
    inputs = inputs or ExposureInputs()
    mg, mass_basis = excipient_mg_per_dose(inputs.excipient_concentration, inputs.dose_volume_ml)

    interval = inputs.dosing_interval_days
    # ICH M7 places intermittent dosing in a duration category by the number of
    # dosing days, not the calendar span. More than one dose a day adds up.
    doses_per_dosing_day = max(1.0, 1.0 / interval)
    if inputs.treatment_duration_days is None:
        dosing_days, duration_basis = math.inf, "treatment duration not given: lifetime assumed"
    else:
        d = inputs.treatment_duration_days
        dosing_days = d if interval <= 1 else math.floor(d / interval) + 1
        duration_basis = f"{dosing_days:g} dosing day(s) over {d:g} day(s)"
    ai, category = tier(dosing_days)

    rows = []
    for imp in impurities:
        level = parse_level(imp.level)
        row = {"name": imp.name, "level": imp.level, "status": "not_computed"}
        if level is None:
            row["reason"] = (
                "no level given" if not imp.level.strip()
                else f"level {imp.level!r} is not a mass fraction (ppm, µg/g, mg/kg, %)"
            )
        elif mg is None:
            row["reason"] = mass_basis
        else:
            per_dose = mg / 1000 * level  # g of excipient x µg per g
            per_day = per_dose * doses_per_dosing_day
            row.update(
                level_ug_per_g=level,
                ug_per_dose=round(per_dose, 6),
                ug_per_dosing_day=round(per_day, 6),
                margin=round(ai / per_day, 3) if per_day else None,
                status="within" if per_day <= ai else "above",
            )
        rows.append(row)

    return {
        "acceptable_intake_ug_per_day": ai,
        "duration_category": category,
        "duration_basis": duration_basis,
        "excipient_mg_per_dose": mg,
        "excipient_mass_basis": mass_basis,
        "impurities": rows,
        "basis": (
            "ICH M7 Table 2 acceptable intake for an individual mutagenic impurity, with the "
            "duration category set by the number of dosing days. A conservative benchmark only: "
            "ICH M7 formally excludes biotechnological products, applies only to DNA-reactive "
            "impurities, and compound-specific acceptable intakes supersede these tiers — none "
            "are applied here. A level is a specification maximum, so 'above' means the "
            "specification allows more than the benchmark, not that a lot contains it."
        ),
    }
