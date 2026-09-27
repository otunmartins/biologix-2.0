"""Run the whole simulation pipeline once, tiny, with no API and no GPU.

    python smoke.py

Trp-cage (PDB 1L2Y, 20 residues) with two short poly(ethylene glycol) chains,
a few picoseconds on whatever platform is present. Takes about 15 minutes on two cores of a
CPU. It proves every stage runs -- structure prep, OpenFF parameters and NAGL
charges, packing, solvation, the restraint, the integrator and the Gamma23
analysis -- and that the result has the shape the API accepts. The number it
prints means nothing: a few picoseconds is not a measurement.
"""

import json
import os
import sys

import httpx

os.environ["SIM_SMOKE"] = "1"
import simulate  # noqa: E402

cif = httpx.get("https://files.rcsb.org/download/1L2Y.cif", timeout=60)
cif.raise_for_status()

job = {
    "job_id": "smoke",
    "candidate": {"id": "smoke", "name": "PEG 6-mer (smoke test)",
                  "screened_oligomer_smiles": "COCCOCCOCCOCCOCCOC"},
    "temperature_c": 25.0, "ph": 7.0, "format": "liquid", "protein": "Trp-cage",
    "structure": {"id": "1L2Y", "source": "RCSB PDB 1L2Y (experimental)",
                  "predicted": False, "mmcif": cif.text},
}

result = simulate.run(job, simulate.Settings.from_env(), progress=lambda m: print("  " + m, flush=True))
print(json.dumps({k: v for k, v in result.items() if k != "contacts"}, indent=1))

required = {"gamma23", "gamma23_se", "production_ns", "temperature_k", "n_chains", "n_frames",
            "r_local_nm", "r_bulk_nm", "engine", "forcefields", "n_atoms", "wall_seconds"}
missing = required - set(result)
assert not missing, f"result is missing {missing}"
assert result["smoke"] is True and result["n_chains"] == 2 and result["n_frames"] >= 5, result
assert abs(result["temperature_k"] - 298.15) < 0.01
print("\nsmoke test passed: every stage of the simulation ran")
sys.exit(0)
