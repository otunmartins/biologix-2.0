"""The simulation follows the request: conditions.py, offline.

    python test_conditions.py
"""

import conditions as C
from design import DesignGoal


def main():
    # Nothing stated: every condition is the default, and says so.
    c = C.for_goal(DesignGoal().model_dump())
    assert c["values"] == C.DEFAULTS, c
    assert set(c["sources"].values()) == {"default"}
    print("ok  an unstated condition runs at its default and is marked as one")

    # Today 40 C, tomorrow pH 5.5 in PBS at 2% polymer: each reaches the run.
    c = C.for_goal(DesignGoal(target_temp_c=40).model_dump())
    assert c["values"]["temperature_c"] == 40 and c["sources"]["temperature_c"] == "request"
    assert c["values"]["ph"] == 7.0 and c["sources"]["ph"] == "default"
    c = C.for_goal(DesignGoal(ph=5.5, salt_mm=150, polymer_wv_percent=2).model_dump())
    assert (c["values"]["ph"], c["values"]["salt_mm"], c["values"]["polymer_wv_percent"]) == (5.5, 150, 2)
    assert c["sources"]["temperature_c"] == "default"
    print("ok  whatever the request states - temperature, pH, salt, polymer - is what is simulated")

    # Outside what a simulation can take: held to the limit, and said.
    c = C.for_goal(DesignGoal(target_temp_c=-20, format="lyophilised", duration_months=24).model_dump())
    assert c["values"]["temperature_c"] == 0 and c["sources"]["temperature_c"] == "request, limited"
    text = " ".join(c["notes"])
    assert "-20 °C asked for, simulated at 0 °C" in text, text
    assert "24-month shelf life is not simulated" in text and "freeze-dried" in text, text
    print("ok  a condition beyond the simulation is limited and noted; shelf life and a dried form are noted, not faked")
    print("\nall checks passed")


if __name__ == "__main__":
    main()
