"""The Tier 1 and Tier 2 arithmetic (metrics.py), on synthetic frames where the
answer is known.

    python test_metrics.py

Needs only numpy and scipy, like test_analysis.py.
"""

import numpy as np

import metrics
from metrics import Interactions, QC, Stability, hbonds, kabsch, pair_energy, sasa, secondary_structure

rng = np.random.default_rng(1)
BOX = np.array([6.0, 6.0, 6.0])

# --- pair energy ---------------------------------------------------------------
e, v = pair_energy(1.0, -1.0, 0.3, 0.3, 0.5, 0.5, np.array([0.5]))
assert e[0] < 0, "opposite charges attract"
e, _ = pair_energy(1.0, 1.0, 0.3, 0.3, 0.5, 0.5, np.array([metrics.CUTOFF_NM]))
assert abs(e[0]) < 1e-9, "reaction-field Coulomb is zero at the cutoff"
_, v = pair_energy(0.0, 0.0, 0.3, 0.3, 0.5, 0.5, np.array([0.3 * 2 ** (1 / 6)]))
assert abs(v[0] + 0.5) < 1e-9, "LJ minimum is -epsilon at 2^(1/6) sigma"
print("ok  pair energy: attraction, zero at the cutoff, LJ minimum")

# --- hydrogen bonds --------------------------------------------------------------
xyz = np.array([[1.0, 1, 1], [1.1, 1, 1], [1.29, 1, 1]])      # D, H, A in a line
assert len(hbonds(xyz, BOX, np.array([[0, 1]]), np.array([2]))[0]) == 1
bent = xyz.copy()
bent[1] = [1.0, 1.1, 1]                                         # H pointing away: 90 degrees
assert len(hbonds(bent, BOX, np.array([[0, 1]]), np.array([2]))[0]) == 0
wrapped = np.array([[5.95, 1, 1], [0.05, 1, 1], [0.24, 1, 1]])  # across the box edge
assert len(hbonds(wrapped, BOX, np.array([[0, 1]]), np.array([2]))[0]) == 1
print("ok  hydrogen bonds: distance, angle, and across the periodic boundary")


# --- Interactions: residence, energy sign, self-association, liabilities ------------
def system(n_chains=2):
    """Protein: 2 residues of 2 atoms each (heavy + H). Polymer: n_chains chains of
    2 atoms (heavy O + H). No water beyond two molecules."""
    prot = np.array([0, 1, 2, 3])
    poly = np.arange(4, 4 + 2 * n_chains)
    water = np.arange(4 + 2 * n_chains, 4 + 2 * n_chains + 6)
    n = water[-1] + 1
    heavy = np.ones(n, bool)
    heavy[[1, 3]] = False
    heavy[poly[1::2]] = False
    heavy[water[[1, 2, 4, 5]]] = False
    charge = np.zeros(n)
    charge[0], charge[4] = 0.5, -0.5          # residue 0 attracts chain 0's heavy atom
    res_of = np.full(n, -1)
    res_of[prot] = [0, 0, 1, 1]
    return dict(
        protein=prot, protein_res=np.array([0, 0, 1, 1]), residue_labels=["LYS 1 A", "MET 2 A"],
        polymer=poly, polymer_chain=np.repeat(np.arange(n_chains), 2), n_chains=n_chains, heavy=heavy,
        # Polar hydrogens carry no LJ, as in Amber and OpenFF.
        charge=charge, sigma=np.full(n, 0.25), epsilon=np.where(heavy, 0.2, 0.0),
        donors={"protein": np.array([[0, 1]]), "polymer": np.zeros((0, 2), int),
                "water": np.array([[water[0], water[1]], [water[0], water[2]], [water[3], water[4]],
                                   [water[3], water[5]]])},
        acceptors={"protein": np.array([2]), "polymer": poly[0::2], "water": water[[0, 3]]},
        res_of_atom=res_of,
        liabilities=[{"class": "Lysine", "why": "", "residues": [0]}, {"class": "Oxidation", "why": "", "residues": [1]}],
        report_ps=10.0,
    )


def frame(chain0_near: bool, together: bool):
    xyz = np.zeros((4 + 4 + 6, 3)) + 3.0
    xyz[0], xyz[1] = [3.0, 3.0, 3.0], [3.1, 3.0, 3.0]          # residue 0 (N-H pointing +x)
    xyz[2], xyz[3] = [3.0, 3.4, 3.0], [3.0, 3.5, 3.0]          # residue 1
    xyz[4] = [3.29, 3.0, 3.0] if chain0_near else [0.5, 0.5, 0.5]
    xyz[5] = xyz[4] + [0.1, 0, 0]
    xyz[6] = xyz[4] + [0.35, 0, 0] if together else [5.0, 5.0, 1.0]
    xyz[7] = xyz[6] + [0.1, 0, 0]
    xyz[8:14] = [[1.0, 5, 5], [1.1, 5, 5], [1.0, 5.1, 5], [4, 1, 5], [4.1, 1, 5], [4, 1.1, 5]]
    return xyz


it = Interactions(**system())
pattern = [True, True, True, False, False, True, False, False, False, False]
for k, near in enumerate(pattern):
    it.add(frame(near, together=k < 2), BOX)
r = it.result()
res = r["residence"]
assert res["binding_events"] == 2, res
assert abs(res["chain_mean_ps"] - 20.0) < 1e-6 and res["chain_max_ps"] == 30.0, res  # episodes of 3 and 1 frames
assert abs(res["bound_fraction"] - 0.2) < 1e-6, res   # 4 of 10 frames, one of two chains
assert r["interaction_energy"]["elec_kj"] < 0, r["interaction_energy"]
assert r["interaction_energy"]["per_residue"][0]["residue"] == "LYS 1 A"
assert r["hbonds"]["polymer_protein"] == 0.4, r["hbonds"]  # the N-H...O bond, in 4 of 10 frames
assert r["hbonds"]["per_residue"][0]["residue"] == "LYS 1 A"
sa = r["self_association"]
assert sa["max_cluster"] == 2 and abs(sa["free_fraction"] - 0.8) < 1e-6, sa
lc = {c["class"]: c for c in r["liability_coverage"]}
assert lc["Lysine"]["coverage"] == 0.4 and lc["Oxidation"]["coverage"] == 0.0, lc
print(f"ok  interactions: residence {res['chain_mean_ps']} ps over {res['binding_events']} events, "
      f"energy {r['interaction_energy']['total_kj']} kJ/mol, H-bonds, clusters, liability coverage")

# --- QC --------------------------------------------------------------------------------
qc = QC(target_k=300.0, mass_g_per_mol=18.015 * 1000, report_ps=10.0)
for _ in range(5):
    qc.add(300.5, 1000 * 18.015 / 6.02214076e23 / 1e-21, -100.0)   # a box at exactly 1 g/mL
q = qc.result()
assert abs(q["density_g_ml"] - 1.0) < 1e-3 and q["ok"] and q["potential_drift_kj_per_ns"] == 0, q
print("ok  QC: density and temperature")

# --- surface ------------------------------------------------------------------------------
one = sasa(np.array([[0.0, 0, 0]]), np.array([0.17]), n_points=200)[0]
assert abs(one - 4 * np.pi * 0.31 ** 2) < 1e-9
two = sasa(np.array([[0.0, 0, 0], [0.2, 0, 0]]), np.array([0.17, 0.17]), n_points=200)
assert two.sum() < 2 * one * 0.8, "overlapping atoms hide each other's surface"
print("ok  surface: an isolated atom, and two that bury each other")


# --- secondary structure: an ideal helix, and an extended chain ---------------------------------
def place(a, b, c, bond, angle, torsion):
    bc = (c - b) / np.linalg.norm(c - b)
    nrm = np.cross(b - a, bc)
    nrm /= np.linalg.norm(nrm)
    m = np.array([bc, np.cross(nrm, bc), nrm]).T
    ang, tor = np.radians(angle), np.radians(torsion)
    return c + m @ np.array([-bond * np.cos(ang), bond * np.sin(ang) * np.cos(tor), bond * np.sin(ang) * np.sin(tor)])


def backbone(n, phi, psi):
    """(xyz in nm, bb dict) for n residues with N, CA, C, O, H, from internal coordinates."""
    N, CA, C = [np.array([0.0, 0, 0])], [np.array([1.458, 0, 0])], []
    C.append(place(np.array([0.0, 1, 0]), N[0], CA[0], 1.525, 111.2, -60))
    for i in range(1, n):
        N.append(place(N[i - 1], CA[i - 1], C[i - 1], 1.329, 116.2, psi))
        CA.append(place(CA[i - 1], C[i - 1], N[i], 1.458, 121.7, 180))
        C.append(place(C[i - 1], N[i], CA[i], 1.525, 111.2, phi))
    O = [place(N[i], CA[i], C[i], 1.23, 120.5, psi + 180) for i in range(n)]
    H = [None] + [N[i] + 1.01 * ((N[i] - C[i - 1]) / np.linalg.norm(N[i] - C[i - 1]) + (N[i] - CA[i]) / np.linalg.norm(N[i] - CA[i]))
                  / np.linalg.norm((N[i] - C[i - 1]) / np.linalg.norm(N[i] - C[i - 1]) + (N[i] - CA[i]) / np.linalg.norm(N[i] - CA[i]))
                  for i in range(1, n)]
    xyz, bb = [], {k: np.full(n, -1) for k in ("N", "CA", "C", "O", "H")}
    for i in range(n):
        for k, arr in (("N", N), ("CA", CA), ("C", C), ("O", O), ("H", H)):
            if arr[i] is not None:
                bb[k][i] = len(xyz)
                xyz.append(arr[i] / 10)
    return np.array(xyz), bb


hx, hbb = backbone(20, -57, -47)
ss = secondary_structure(hbb, hx, np.zeros(20, int))
assert (ss == "H").mean() > 0.6, "".join(ss)
ex, ebb = backbone(20, -120, 130)
assert (secondary_structure(ebb, ex, np.zeros(20, int)) == "H").sum() == 0
print(f"ok  secondary structure: ideal helix {''.join(ss)}, extended chain has none")

# --- kabsch ---------------------------------------------------------------------------------
pts = rng.normal(size=(30, 3))
th = 0.7
rot = np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1]])
assert np.allclose(kabsch(pts @ rot.T + 5.0, pts), pts, atol=1e-9)
print("ok  superposition removes rotation and translation")

# --- Stability on the helix: still is native, unfolded is not --------------------------------------
heavy = np.array([hbb[k][i] for i in range(20) for k in ("N", "CA", "C", "O")])
heavy_res = np.repeat(np.arange(20), 4)
kw = dict(ca=hbb["CA"], heavy=heavy, heavy_res=heavy_res, elements=["N", "C", "C", "O"] * 20,
          resnames=["ALA"] * 20, residue_labels=[f"ALA {i + 1} A" for i in range(20)],
          chain=np.zeros(20, int), bb=hbb, report_ps=10.0, slow_every=2)
st = Stability(ref=hx, **kw)
for _ in range(6):
    st.add(hx @ rot.T + 1.0)   # the same helix, moved: nothing changed
s1 = st.result(77.0, 0.06)
assert s1["rmsd_nm"]["final"] < 1e-6 and s1["q"]["final"] > 0.99 and s1["secondary"]["retained"] == 1.0, s1
st = Stability(ref=hx, **kw)
for _ in range(6):
    st.add(ex)                 # unfolded into the extended chain
s2 = st.result(77.0, 0.06)
assert s2["rmsd_nm"]["final"] > 0.5 and s2["q"]["final"] < 0.5 and s2["secondary"]["helix_final"] == 0, s2
assert s2["rg_nm"]["final"] > s2["rg_nm"]["start"] and s2["sasa_nm2"]["final"] > s2["sasa_nm2"]["start"]
print(f"ok  stability: native RMSD {s1['rmsd_nm']['final']} Q {s1['q']['final']}; "
      f"unfolded RMSD {s2['rmsd_nm']['final']} Q {s2['q']['final']}")

# --- liability classes -------------------------------------------------------------------------
names = ["MET", "ASN", "GLY", "ASP", "GLY", "LYS", "CYS", "LEU", "ILE", "PHE", "LYS"]
rel = np.array([0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.0])
ca = np.array([[i * 0.38, 0, 0] for i in range(len(names))])
cls = {c["class"]: c["residues"] for c in metrics.liability_classes(
    names, np.zeros(len(names), int), rel, ca, free_cys=np.array([False] * 6 + [True] + [False] * 4))}
assert cls["Oxidation"] == [0, 6] and cls["Deamidation"] == [1] and cls["Isomerisation"] == [3], cls
assert cls["Lysine"] == [5], "the buried Lys is left out"
assert cls["Hydrophobic patch"] == [7, 8, 9], cls  # Met 1 is 2.7 nm from them: not in the patch
print("ok  liability classes: oxidation, deamidation, isomerisation, lysine, hydrophobic patch")

print("\nall metrics checks passed")
