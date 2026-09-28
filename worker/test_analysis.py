"""The Gamma23 arithmetic, on synthetic frames where the answer is known.

    python test_analysis.py

Needs only numpy and scipy, so it runs anywhere -- in CI's api image too.
"""

import numpy as np

from analysis import Accumulator

rng = np.random.default_rng(0)
BOX = np.array([6.0, 6.0, 6.0])
CENTRE = BOX / 2
PROTEIN = CENTRE + rng.normal(scale=0.3, size=(40, 3))       # a small blob
LABELS = [f"ALA {i} A" for i in range(40)]
RES_OF_ATOM = np.arange(40)


def shell(n, r_min, r_max):
    """n points uniformly in the spherical shell r_min..r_max around the centre."""
    u = rng.random(n)
    r = (r_min**3 + u * (r_max**3 - r_min**3)) ** (1 / 3)
    v = rng.normal(size=(n, 3))
    return CENTRE + v / np.linalg.norm(v, axis=1, keepdims=True) * r[:, None]


def uniform(n):
    """n points uniformly in the box, outside the protein core."""
    pts = rng.random((n * 2, 3)) * BOX
    keep = np.linalg.norm(pts - CENTRE, axis=1) > 1.0
    return pts[keep][:n]


def run(polymer_fn, frames=20, heavy=10):
    acc = Accumulator(heavy_per_chain=heavy, residue_labels=LABELS, residue_of_atom=RES_OF_ATOM,
                      r_local=1.6, r_bulk=2.0)
    for _ in range(frames):
        acc.add(PROTEIN, polymer_fn(), uniform(6000), BOX)
    return acc.result()


# Polymer spread exactly like the water: no preference either way, Gamma23 ~ 0.
r = run(lambda: uniform(400))
assert abs(r["gamma23"]) < 3 * max(r["gamma23_se"], 0.5), r
print(f"ok  uniform polymer: Gamma23 = {r['gamma23']} +/- {r['gamma23_se']} (expected ~0)")

# Polymer kept out of the local domain entirely: excluded, Gamma23 clearly negative.
r = run(lambda: shell(400, 2.2, 2.9))
assert r["gamma23"] < -1, r
assert not r["contacts"], "an excluded polymer cannot touch the protein"
print(f"ok  excluded polymer: Gamma23 = {r['gamma23']} (negative: preferential hydration)")

# Polymer piled onto the surface: accumulated, Gamma23 clearly positive, and contacts found.
r = run(lambda: np.vstack([shell(300, 0.9, 1.3), uniform(100)]))
assert r["gamma23"] > 1, r
assert r["contacts"] and r["contacts"][0]["fraction"] > 0.5, r["contacts"][:3]
print(f"ok  accumulated polymer: Gamma23 = {r['gamma23']}, top contact {r['contacts'][0]}")

# Chain equivalents: the same atoms counted as bigger chains give a proportionally smaller Gamma23.
a = run(lambda: shell(300, 0.9, 1.3), heavy=10)["gamma23"]
b = run(lambda: shell(300, 0.9, 1.3), heavy=20)["gamma23"]
assert abs(a / b - 2) < 0.2, (a, b)
print("ok  polymer counted in chain equivalents (heavy atoms / heavy atoms per chain)")

# Periodic boundaries: a polymer atom just across the box edge from the protein is near it.
acc = Accumulator(heavy_per_chain=1, residue_labels=["X 1 A"], residue_of_atom=np.array([0]),
                  r_local=0.5, r_bulk=1.0)
acc.add(np.array([[0.1, 3.0, 3.0]]), np.array([[5.9, 3.0, 3.0]]), uniform(3000), BOX)
assert acc.contact_frames[0] == 1, "minimum image not applied"
print("ok  distances use the minimum image across the periodic box")

# The 3D snapshot: a chain wrapped across the box is drawn beside the protein, not a box away.
import snapshot  # noqa: E402

xyz = np.array([[0.2, 3.0, 3.0], [0.4, 3.0, 3.0],     # protein, against the -x face
                [5.7, 3.0, 3.0], [5.9, 3.0, 3.0]])    # a chain across the boundary from it
text = snapshot.pdb(xyz=xyz, box=BOX,
                    protein=[(0, "CA", "LYS", "A", 45, "C"), (1, "CB", "LYS", "A", 45, "C")],
                    polymer_chains=[[(2, "C1", "C"), (3, "O2", "O")]])
rows = [l for l in text.splitlines() if l.startswith(("ATOM", "HETATM"))]
coords = np.array([[float(l[30:38]), float(l[38:46]), float(l[46:54])] for l in rows])
assert len(rows) == 4 and rows[0][17:20] == "LYS" and rows[2][17:20] == snapshot.POLYMER_RESNAME
assert abs(coords[:2, 0].mean()) < 1e-6, "centred on the protein"
assert np.linalg.norm(coords[2:, 0].mean() - coords[:2, 0].mean()) < 10, \
    "the chain is moved to its image beside the protein (within 1 nm, not 5)"
assert rows[0][76:78].strip() == "C" and rows[3][76:78].strip() == "O", "element columns"
print("ok  snapshot: heavy atoms only, centred on the protein, chains at their nearest image")

print("\nall analysis checks passed")
