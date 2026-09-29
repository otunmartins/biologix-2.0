"""What a run measures beyond Gamma23. Numpy and scipy only, no OpenMM in here,
so every number is tested on synthetic frames (test_metrics.py).

TIER 1 -- the restrained production run, the same frames Gamma23 uses:

  Interactions   polymer-protein interaction energy (electrostatic and van der
                 Waals, whole protein and per residue), hydrogen bonds between
                 polymer and protein and between protein and water, how long a
                 contact lasts once made (residence time), how much of each
                 class of liability residue the polymer covers, and whether the
                 chains stick to each other instead (self-association).
  QC             temperature, density and potential energy: was the run what it
                 says it was.

TIER 2 -- a short UNRESTRAINED stage at a stress temperature, after production:

  Stability      C-alpha RMSD and RMSF, radius of gyration, native contacts Q,
                 solvent-accessible surface (total and hydrophobic) and
                 secondary structure, against the structure the stage started
                 from.

INTERACTION ENERGY, NOT BINDING FREE ENERGY. The energy is the direct
polymer-protein nonbonded energy from the force field's own charges and
Lennard-Jones parameters: Coulomb with a reaction field (water's dielectric
beyond the cutoff) plus LJ, both cut at 1.2 nm, minimum image. It says how
strongly the polymer is held where it sits. It leaves out desolvation and
entropy, so it is not a binding free energy and is never labelled one; a
strongly negative number with a negative Gamma23 means few chains held tightly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

COULOMB = 138.935458          # kJ mol^-1 nm e^-2
CUTOFF_NM = 1.2
EPS_RF = 78.5                 # water, for the reaction field beyond the cutoff
CONTACT_NM = 0.45             # heavy atom to heavy atom: the same contact analysis.py counts
HB_DA_NM = 0.35               # donor to acceptor, heavy atoms
HB_COS = np.cos(np.radians(150.0))  # donor-H...acceptor at least 150 degrees
SERIES_POINTS = 150           # time series are thinned to about this many points

# Maximum solvent-accessible surface per residue type, nm^2 (Tien et al. 2013,
# theoretical), for relative accessibility. Amber's protonation variants mapped.
MAX_SASA = {"ALA": 1.29, "ARG": 2.74, "ASN": 1.95, "ASP": 1.93, "CYS": 1.67, "GLN": 2.25,
            "GLU": 2.23, "GLY": 1.04, "HIS": 2.24, "ILE": 1.97, "LEU": 2.01, "LYS": 2.36,
            "MET": 2.24, "PHE": 2.40, "PRO": 1.59, "SER": 1.55, "THR": 1.72, "TRP": 2.85,
            "TYR": 2.63, "VAL": 1.74}
ALIASES = {"HID": "HIS", "HIE": "HIS", "HIP": "HIS", "HSD": "HIS", "HSE": "HIS", "HSP": "HIS",
           "CYX": "CYS", "CYM": "CYS", "LYN": "LYS", "ASH": "ASP", "GLH": "GLU"}
HYDROPHOBIC = {"ALA", "VAL", "LEU", "ILE", "MET", "PHE", "TRP", "TYR", "PRO"}
# Heavy-atom radii (nm) for the surface, Bondi; the probe is water's.
RADII = {"C": 0.17, "N": 0.155, "O": 0.152, "S": 0.18, "SE": 0.19}
PROBE_NM = 0.14


def canonical(resname: str) -> str:
    return ALIASES.get(resname, resname)


def _wrap(x: np.ndarray, box: np.ndarray) -> np.ndarray:
    w = np.mod(x, box)
    return np.where(w >= box, 0.0, w)  # np.mod can round up to exactly box


def min_image(d: np.ndarray, box: np.ndarray) -> np.ndarray:
    return d - box * np.round(d / box)


def _thin(values: list[float], digits: int = 3) -> list[float]:
    step = max(1, int(np.ceil(len(values) / SERIES_POINTS)))
    return [round(float(v), digits) for v in values[::step]]


def _block_se(x: np.ndarray, n_blocks: int = 5) -> float | None:
    blocks = [b.mean() for b in np.array_split(np.asarray(x, float), min(n_blocks, len(x))) if len(b)]
    return float(np.std(blocks, ddof=1) / np.sqrt(len(blocks))) if len(blocks) > 1 else None


def pair_energy(q1, q2, s1, s2, e1, e2, r, cutoff=CUTOFF_NM, eps_rf=EPS_RF):
    """(electrostatic, LJ) in kJ/mol for atom pairs at distance r (nm).
    Lorentz-Berthelot mixing, as both Amber and OpenFF use; reaction-field
    Coulomb, zero at the cutoff."""
    k_rf = (eps_rf - 1) / ((2 * eps_rf + 1) * cutoff ** 3)
    c_rf = 1 / cutoff + k_rf * cutoff ** 2
    elec = COULOMB * q1 * q2 * (1 / r + k_rf * r ** 2 - c_rf)
    sig = 0.5 * (s1 + s2)
    eps = np.sqrt(e1 * e2)
    sr6 = (sig / r) ** 6
    vdw = 4 * eps * (sr6 * sr6 - sr6)
    return elec, vdw


def hbonds(xyz: np.ndarray, box: np.ndarray, donors: np.ndarray, acceptors: np.ndarray):
    """Hydrogen bonds from donors (k, 2: heavy atom, its H) to acceptors (m,),
    indices into xyz. Returns (donor row, acceptor position in `acceptors`) per
    bond. Geometric: donor-acceptor within 0.35 nm, angle at H >= 150 degrees."""
    if len(donors) == 0 or len(acceptors) == 0:
        return np.zeros(0, int), np.zeros(0, int)
    ta = cKDTree(_wrap(xyz[acceptors], box), boxsize=box)
    td = cKDTree(_wrap(xyz[donors[:, 0]], box), boxsize=box)
    m = td.sparse_distance_matrix(ta, HB_DA_NM, output_type="ndarray")
    di, ai = m["i"].astype(int), m["j"].astype(int)
    keep = donors[di, 0] != acceptors[ai]
    di, ai = di[keep], ai[keep]
    if len(di) == 0:
        return di, ai
    h = xyz[donors[di, 1]]
    to_d = min_image(xyz[donors[di, 0]] - h, box)
    to_a = min_image(xyz[acceptors[ai]] - h, box)
    cos = np.einsum("ij,ij->i", to_d, to_a) / (np.linalg.norm(to_d, axis=1) * np.linalg.norm(to_a, axis=1))
    ok = cos <= HB_COS
    return di[ok], ai[ok]


class _Runs:
    """Contact episodes for n things (residues, chains): how long each lasted."""

    def __init__(self, n: int):
        self.run = np.zeros(n, int)
        self.lengths: list[list[int]] = [[] for _ in range(n)]

    def add(self, on: np.ndarray) -> None:
        ended = (self.run > 0) & ~on
        for k in np.flatnonzero(ended):
            self.lengths[k].append(int(self.run[k]))
        self.run = np.where(on, self.run + 1, 0)

    def close(self) -> None:
        # An episode still going at the end is counted as it stands (right-censored).
        self.add(np.zeros(len(self.run), bool))


@dataclass
class Interactions:
    """Tier 1, frame by frame. Every index is into the full system's atoms."""

    protein: np.ndarray               # protein atoms, hydrogens included
    protein_res: np.ndarray           # residue index per protein atom
    residue_labels: list[str]
    polymer: np.ndarray               # polymer atoms, hydrogens included
    polymer_chain: np.ndarray         # chain index per polymer atom
    n_chains: int
    heavy: np.ndarray                 # bool per system atom
    charge: np.ndarray                # e, per system atom
    sigma: np.ndarray                 # nm
    epsilon: np.ndarray               # kJ/mol
    donors: dict[str, np.ndarray]     # "protein" / "polymer" / "water" -> (k, 2)
    acceptors: dict[str, np.ndarray]  # same keys -> (m,)
    res_of_atom: np.ndarray           # system atom -> protein residue index, -1 elsewhere
    liabilities: list[dict] = field(default_factory=list)  # {"class", "why", "residues": [idx]}
    report_ps: float = 10.0

    def __post_init__(self):
        n_res = len(self.residue_labels)
        self.energy_elec: list[float] = []
        self.energy_vdw: list[float] = []
        self.res_elec = np.zeros(n_res)
        self.res_vdw = np.zeros(n_res)
        self.hb_pp: list[int] = []
        self.hb_pw: list[int] = []
        self.hb_res = np.zeros(n_res)
        self.touched = np.zeros(n_res, int)
        self.res_runs = _Runs(n_res)
        self.chain_runs = _Runs(self.n_chains)
        self.bound: list[float] = []
        self.largest: list[int] = []
        self.clusters: list[int] = []
        self.free: list[float] = []
        self.n = 0
        self._p_heavy = self.heavy[self.protein]
        self._q_heavy = self.heavy[self.polymer]
        self._poly_heavy = self.polymer[self._q_heavy]
        self._poly_heavy_chain = self.polymer_chain[self._q_heavy]

    def add(self, xyz: np.ndarray, box: np.ndarray) -> None:
        box = np.asarray(box, float)
        n_res = len(self.residue_labels)
        # --- energy, and contacts from the same pair list --------------------
        tp = cKDTree(_wrap(xyz[self.protein], box), boxsize=box)
        tq = cKDTree(_wrap(xyz[self.polymer], box), boxsize=box)
        m = tp.sparse_distance_matrix(tq, CUTOFF_NM, output_type="ndarray")
        i, j, r = m["i"].astype(int), m["j"].astype(int), m["v"]
        r = np.maximum(r, 0.05)  # never divide by an overlap
        a, b = self.protein[i], self.polymer[j]
        elec, vdw = pair_energy(self.charge[a], self.charge[b], self.sigma[a], self.sigma[b],
                                self.epsilon[a], self.epsilon[b], r)
        self.energy_elec.append(float(elec.sum()))
        self.energy_vdw.append(float(vdw.sum()))
        res = self.protein_res[i]
        self.res_elec += np.bincount(res, weights=elec, minlength=n_res)
        self.res_vdw += np.bincount(res, weights=vdw, minlength=n_res)

        c = (r < CONTACT_NM) & self._p_heavy[i] & self._q_heavy[j]
        on_res = np.zeros(n_res, bool)
        on_res[res[c]] = True
        on_chain = np.zeros(self.n_chains, bool)
        on_chain[self.polymer_chain[j[c]]] = True
        self.touched += on_res
        self.res_runs.add(on_res)
        self.chain_runs.add(on_chain)
        self.bound.append(float(on_chain.mean()) if self.n_chains else 0.0)

        # --- hydrogen bonds --------------------------------------------------
        D, A = self.donors, self.acceptors
        pp = 0
        for dk, ak in (("protein", "polymer"), ("polymer", "protein")):
            di, ai = hbonds(xyz, box, D[dk], A[ak])
            pp += len(di)
            prot_atoms = D[dk][di, 0] if dk == "protein" else A[ak][ai]
            self.hb_res += np.bincount(self.res_of_atom[prot_atoms], minlength=n_res)
        self.hb_pp.append(pp)
        pw = len(hbonds(xyz, box, D["protein"], A["water"])[0]) + \
            len(hbonds(xyz, box, D["water"], A["protein"])[0])
        self.hb_pw.append(pw)

        # --- self-association: chains in contact with each other ------------
        parent = list(range(self.n_chains))

        def find(k):
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        if self.n_chains > 1 and len(self._poly_heavy):
            th = cKDTree(_wrap(xyz[self._poly_heavy], box), boxsize=box)
            pairs = th.query_pairs(CONTACT_NM, output_type="ndarray")
            if len(pairs):
                ca, cb = self._poly_heavy_chain[pairs[:, 0]], self._poly_heavy_chain[pairs[:, 1]]
                for x, y in set(zip(ca[ca != cb].tolist(), cb[ca != cb].tolist())):
                    parent[find(x)] = find(y)
        roots = [find(k) for k in range(self.n_chains)]
        sizes = np.bincount(roots, minlength=self.n_chains)
        sizes = sizes[sizes > 0]
        self.largest.append(int(sizes.max()) if len(sizes) else 0)
        self.clusters.append(int(len(sizes)))
        self.free.append(float((sizes == 1).sum() / max(1, self.n_chains)))
        self.n += 1

    def result(self) -> dict:
        if self.n == 0:
            raise ValueError("no frames analysed")
        self.res_runs.close()
        self.chain_runs.close()
        n = self.n
        labels = self.residue_labels
        total = np.asarray(self.energy_elec) + np.asarray(self.energy_vdw)
        se = _block_se(total)
        res_total = (self.res_elec + self.res_vdw) / n
        order = np.argsort(res_total)
        per_res = [{"residue": labels[k], "kj": round(float(res_total[k]), 2),
                    "elec_kj": round(float(self.res_elec[k] / n), 2),
                    "vdw_kj": round(float(self.res_vdw[k] / n), 2)}
                   for k in order[:15] if res_total[k] < -0.05]
        repulsive = [{"residue": labels[k], "kj": round(float(res_total[k]), 2)}
                     for k in order[::-1][:5] if res_total[k] > 0.5]

        ps = self.report_ps
        chain_eps = [x for runs in self.chain_runs.lengths for x in runs]
        res_stats = []
        for k, runs in enumerate(self.res_runs.lengths):
            if runs:
                res_stats.append({"residue": labels[k], "mean_ps": round(float(np.mean(runs)) * ps, 1),
                                  "max_ps": round(float(max(runs)) * ps, 1), "events": len(runs)})
        res_stats.sort(key=lambda d: -d["mean_ps"])

        hb_order = np.argsort(-self.hb_res)
        hb_res = [{"residue": labels[k], "per_frame": round(float(self.hb_res[k]) / n, 3)}
                  for k in hb_order[:10] if self.hb_res[k] > 0]

        frac = self.touched / n
        liab = []
        for cls in self.liabilities:
            idx = list(cls["residues"])
            if not idx:
                continue
            liab.append({
                "class": cls["class"], "why": cls["why"], "n_sites": len(idx),
                "coverage": round(float(np.mean(frac[idx])), 3),
                "residues": sorted(({"residue": labels[k], "fraction": round(float(frac[k]), 3)}
                                    for k in idx), key=lambda d: -d["fraction"])[:12],
            })
        return {
            "interaction_energy": {
                "total_kj": round(float(total.mean()), 1),
                "total_se": round(se, 1) if se is not None else None,
                "elec_kj": round(float(np.mean(self.energy_elec)), 1),
                "vdw_kj": round(float(np.mean(self.energy_vdw)), 1),
                "per_chain_kj": round(float(total.mean()) / max(1, self.n_chains), 1),
                "per_residue": per_res,
                "repulsive": repulsive,
                "series": _thin(list(total), 1),
                "method": (f"direct polymer-protein nonbonded energy from the force field: "
                           f"reaction-field Coulomb (eps {EPS_RF:g}) + Lennard-Jones, "
                           f"{CUTOFF_NM:g} nm cutoff, averaged over {n} frames; not a binding "
                           "free energy (no desolvation, no entropy)"),
            },
            "hbonds": {
                "polymer_protein": round(float(np.mean(self.hb_pp)), 2),
                "protein_water": round(float(np.mean(self.hb_pw)), 1),
                "per_residue": hb_res,
                "series_polymer_protein": _thin([float(x) for x in self.hb_pp], 2),
                "criterion": f"donor-acceptor <= {HB_DA_NM:g} nm, angle >= 150 degrees",
            },
            "residence": {
                "bound_fraction": round(float(np.mean(self.bound)), 3),
                "chain_mean_ps": round(float(np.mean(chain_eps)) * ps, 1) if chain_eps else 0.0,
                "chain_max_ps": round(float(max(chain_eps)) * ps, 1) if chain_eps else 0.0,
                "binding_events": len(chain_eps),
                "residues": res_stats[:12],
                "frame_ps": ps,
            },
            "liability_coverage": liab,
            "self_association": {
                "n_chains": self.n_chains,
                "mean_largest_cluster": round(float(np.mean(self.largest)), 2),
                "max_cluster": int(max(self.largest)),
                "mean_clusters": round(float(np.mean(self.clusters)), 2),
                "free_fraction": round(float(np.mean(self.free)), 3),
            },
        }


@dataclass
class QC:
    """Was the run what it says: temperature, density, potential energy."""

    target_k: float
    mass_g_per_mol: float
    report_ps: float = 10.0
    t: list[float] = field(default_factory=list)
    density: list[float] = field(default_factory=list)
    pe: list[float] = field(default_factory=list)

    def add(self, temperature_k: float, volume_nm3: float, potential_kj: float) -> None:
        self.t.append(temperature_k)
        self.density.append(self.mass_g_per_mol / 6.02214076e23 / (volume_nm3 * 1e-21))
        self.pe.append(potential_kj)

    def result(self) -> dict:
        t, rho, pe = map(np.asarray, (self.t, self.density, self.pe))
        ns = np.arange(len(pe)) * self.report_ps / 1000
        drift = float(np.polyfit(ns, pe, 1)[0]) if len(pe) > 2 and ns[-1] > 0 else 0.0
        return {
            "temperature_k": round(float(t.mean()), 2), "temperature_sd": round(float(t.std()), 2),
            "target_k": round(self.target_k, 2),
            "density_g_ml": round(float(rho.mean()), 4), "density_sd": round(float(rho.std()), 4),
            "potential_drift_kj_per_ns": round(drift, 1),
            "ok": bool(abs(t.mean() - self.target_k) < 3.0),
        }


# ---------------------------------------------------------------------------
# Tier 2: structure
# ---------------------------------------------------------------------------

def kabsch(mobile: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """mobile superposed onto ref (both (n, 3)), returned."""
    mc, rc = mobile.mean(axis=0), ref.mean(axis=0)
    h = (mobile - mc).T @ (ref - rc)
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    rot = vt.T @ np.diag([1, 1, d]) @ u.T
    return (mobile - mc) @ rot.T + rc


def _sphere(n: int) -> np.ndarray:
    k = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * k / n)
    theta = np.pi * (1 + 5 ** 0.5) * k
    return np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1)


def sasa(xyz: np.ndarray, radii: np.ndarray, n_points: int = 64) -> np.ndarray:
    """Shrake-Rupley solvent-accessible area per atom, nm^2 (non-periodic)."""
    R = radii + PROBE_NM
    pts = (xyz[:, None, :] + R[:, None, None] * _sphere(n_points)[None]).reshape(-1, 3)
    owner = np.repeat(np.arange(len(xyz)), n_points)
    tree = cKDTree(xyz)
    k = min(12, len(xyz))
    d, nb = tree.query(pts, k=k)
    d, nb = d.reshape(len(pts), k), nb.reshape(len(pts), k)
    buried = ((d < R[nb] - 1e-9) & (nb != owner[:, None])).any(axis=1)
    free = (~buried).reshape(len(xyz), n_points).sum(axis=1)
    return 4 * np.pi * R ** 2 * free / n_points


def secondary_structure(bb: dict[str, np.ndarray], xyz: np.ndarray, chain: np.ndarray) -> np.ndarray:
    """A DSSP-style call per residue: 'H' (alpha helix), 'E' (beta bridge or
    strand) or '-'. Kabsch-Sander backbone hydrogen-bond energy (< -0.5
    kcal/mol); helix from two consecutive i->i+4 turns, strand from parallel or
    antiparallel bridges. No 3-10/pi helices or turns: the fractions of H and E
    are what is compared. bb maps "N", "CA", "C", "O", "H" to a per-residue atom
    index into xyz, -1 where missing (no H on proline or the N-terminus)."""
    n = len(chain)
    N, C, O, H = bb["N"], bb["C"], bb["O"], bb["H"]
    acc = np.flatnonzero((O >= 0) & (C >= 0))
    don = np.flatnonzero((N >= 0) & (H >= 0))
    ss = np.array(["-"] * n)
    if len(acc) == 0 or len(don) == 0:
        return ss
    tree = cKDTree(xyz[N[don]])
    hb = set()
    for a_i, near in zip(acc, tree.query_ball_point(xyz[O[acc]], r=0.52)):
        if not near:
            continue
        d = don[np.asarray(near)]
        d = d[np.abs(d - a_i) > 1]
        if len(d) == 0:
            continue
        rON = np.linalg.norm(xyz[N[d]] - xyz[O[a_i]], axis=1) * 10
        rCH = np.linalg.norm(xyz[H[d]] - xyz[C[a_i]], axis=1) * 10
        rOH = np.linalg.norm(xyz[H[d]] - xyz[O[a_i]], axis=1) * 10
        rCN = np.linalg.norm(xyz[N[d]] - xyz[C[a_i]], axis=1) * 10
        e = 0.084 * 332 * (1 / rON + 1 / rCH - 1 / rOH - 1 / rCN)
        hb.update((int(a_i), int(x)) for x in d[e < -0.5])

    def same(*idx):
        return all(0 <= k < n for k in idx) and len({int(chain[k]) for k in idx}) == 1

    turn4 = {i for (i, j) in hb if j == i + 4 and same(i, j)}
    for i in turn4:
        if i - 1 in turn4:
            ss[i:i + 4] = "H"
    cand = set()
    for a, d in hb:
        cand.update({(a + 1, d), (d - 1, a), (d, a + 1), (a, d - 1), (a, d), (d, a), (a + 1, d - 1),
                     (d - 1, a + 1)})
    for i, j in cand:
        if not (0 <= i < n and 0 <= j < n) or abs(i - j) <= 2 and chain[i] == chain[j]:
            continue
        par = ((i - 1, j) in hb and (j, i + 1) in hb and same(i - 1, i, i + 1)) or \
              ((j - 1, i) in hb and (i, j + 1) in hb and same(j - 1, j, j + 1))
        anti = ((i, j) in hb and (j, i) in hb) or \
               ((i - 1, j + 1) in hb and (j - 1, i + 1) in hb and same(i - 1, i, i + 1) and same(j - 1, j, j + 1))
        if par or anti:
            for k in (i, j):
                if ss[k] != "H":
                    ss[k] = "E"
    return ss


@dataclass
class Stability:
    """Tier 2. Positions are the protein's atoms only (hydrogens included),
    UNWRAPPED, so a chain never jumps across the box mid-analysis."""

    ref: np.ndarray                   # (n_atoms, 3) at the start of the stage
    ca: np.ndarray                    # per residue, index into the protein atoms
    heavy: np.ndarray                 # protein heavy atom indices
    heavy_res: np.ndarray             # residue per heavy atom
    elements: list[str]               # per heavy atom
    resnames: list[str]               # per residue
    residue_labels: list[str]
    chain: np.ndarray                 # chain index per residue
    bb: dict[str, np.ndarray]         # backbone atoms per residue (secondary_structure)
    report_ps: float = 10.0
    slow_every: int = 10              # SASA and secondary structure every this many frames

    def __post_init__(self):
        n_res = len(self.residue_labels)
        self._radii = np.array([RADII.get(e.upper(), 0.17) for e in self.elements])
        self._hydro_atom = np.array([canonical(self.resnames[r]) in HYDROPHOBIC for r in self.heavy_res])
        # Native heavy-atom contacts: residues more than 3 apart, within 0.45 nm.
        h = self.ref[self.heavy]
        pairs = cKDTree(h).query_pairs(0.45, output_type="ndarray")
        if len(pairs):
            ri, rj = self.heavy_res[pairs[:, 0]], self.heavy_res[pairs[:, 1]]
            keep = (np.abs(ri - rj) > 3) | (self.chain[ri] != self.chain[rj])
            pairs = pairs[keep]
        self._pairs = pairs
        self._r0 = np.linalg.norm(h[pairs[:, 0]] - h[pairs[:, 1]], axis=1) if len(pairs) else np.zeros(0)
        self._ref_ca = self.ref[self.ca]
        self.ref_rg = self._rg(h)
        s = sasa(h, self._radii)
        self.ref_sasa, self.ref_hydro = float(s.sum()), float(s[self._hydro_atom].sum())
        self.ref_ss = secondary_structure(self.bb, self.ref, self.chain)
        self.t, self.rmsd, self.q, self.rg = [], [], [], []
        self.t_slow, self.sasa, self.hydro, self.helix, self.strand, self.kept = [], [], [], [], [], []
        self._fit_ca: list[np.ndarray] = []
        self.n = 0
        self.n_res = n_res

    @staticmethod
    def _rg(x: np.ndarray) -> float:
        return float(np.sqrt(((x - x.mean(axis=0)) ** 2).sum(axis=1).mean()))

    def add(self, xyz: np.ndarray) -> None:
        ca = kabsch(xyz[self.ca], self._ref_ca)
        self._fit_ca.append(ca)
        self.t.append(self.n * self.report_ps / 1000)
        self.rmsd.append(float(np.sqrt(((ca - self._ref_ca) ** 2).sum(axis=1).mean())))
        h = xyz[self.heavy]
        if len(self._pairs):
            r = np.linalg.norm(h[self._pairs[:, 0]] - h[self._pairs[:, 1]], axis=1)
            self.q.append(float(np.mean(1 / (1 + np.exp(50.0 * (r - 1.8 * self._r0))))))
        else:
            self.q.append(1.0)
        self.rg.append(self._rg(h))
        if self.n % self.slow_every == 0:
            s = sasa(h, self._radii)
            ss = secondary_structure(self.bb, xyz, self.chain)
            self.t_slow.append(self.t[-1])
            self.sasa.append(float(s.sum()))
            self.hydro.append(float(s[self._hydro_atom].sum()))
            self.helix.append(float((ss == "H").mean()))
            self.strand.append(float((ss == "E").mean()))
            native = self.ref_ss != "-"
            self.kept.append(float((ss[native] == self.ref_ss[native]).mean()) if native.any() else 1.0)
        self.n += 1

    def result(self, temperature_c: float, ns: float) -> dict:
        if self.n == 0:
            raise ValueError("no frames analysed")
        half = slice(self.n // 2, None)
        half_slow = slice(len(self.kept) // 2, None)
        fit = np.asarray(self._fit_ca)
        rmsf = np.sqrt(((fit - fit.mean(axis=0)) ** 2).sum(axis=2).mean(axis=0))
        order = np.argsort(-rmsf)
        return {
            "temperature_c": round(temperature_c, 1),
            "ns": ns,
            "n_frames": self.n,
            "rmsd_nm": {"final": round(self.rmsd[-1], 3), "mean_last_half": round(float(np.mean(self.rmsd[half])), 3),
                        "max": round(max(self.rmsd), 3)},
            "q": {"final": round(self.q[-1], 3), "mean_last_half": round(float(np.mean(self.q[half])), 3),
                  "min": round(min(self.q), 3), "n_contacts": int(len(self._pairs))},
            "rg_nm": {"start": round(self.ref_rg, 3), "final": round(self.rg[-1], 3)},
            "sasa_nm2": {"start": round(self.ref_sasa, 1), "final": round(self.sasa[-1], 1),
                         "hydrophobic_start": round(self.ref_hydro, 1),
                         "hydrophobic_final": round(self.hydro[-1], 1)},
            "secondary": {"helix_start": round(float((self.ref_ss == "H").mean()), 3),
                          "strand_start": round(float((self.ref_ss == "E").mean()), 3),
                          "helix_final": round(self.helix[-1], 3), "strand_final": round(self.strand[-1], 3),
                          "retained": round(float(np.mean(self.kept[half_slow])), 3)},
            "rmsf_top": [{"residue": self.residue_labels[k], "nm": round(float(rmsf[k]), 3)} for k in order[:12]],
            "rmsf": [round(float(x), 3) for x in rmsf[:2000]],
            "rmsf_residues": self.residue_labels[:2000],
            "series": {"t_ns": _thin(self.t, 4), "rmsd": _thin(self.rmsd), "q": _thin(self.q),
                       "rg": _thin(self.rg), "t_slow_ns": _thin(self.t_slow, 4),
                       "sasa": _thin(self.sasa, 1), "hydrophobic_sasa": _thin(self.hydro, 1),
                       "helix": _thin(self.helix), "strand": _thin(self.strand)},
        }


# ---------------------------------------------------------------------------
# Liability residues, from the structure the run starts from
# ---------------------------------------------------------------------------

def liability_classes(resnames: list[str], chain: np.ndarray, rel_sasa: np.ndarray,
                      ca_xyz: np.ndarray, free_cys: np.ndarray) -> list[dict]:
    """The residues the app's liability scan cares about, exposed ones only
    (relative accessibility >= 0.1 at the start): oxidation (Met, Trp, His, free
    Cys), deamidation (Asn before Gly/Ser/Thr/His), isomerisation (Asp before
    Gly/Ser/Thr/Asp), amine-reactive Lys, and hydrophobic surface patches (three
    or more exposed hydrophobic side chains within 1 nm of each other)."""
    n = len(resnames)
    names = [canonical(r) for r in resnames]
    exposed = rel_sasa >= 0.1

    def nxt(k):
        return names[k + 1] if k + 1 < n and chain[k + 1] == chain[k] else ""

    ox = [k for k in range(n) if exposed[k] and (names[k] in ("MET", "TRP", "HIS") or
                                                 (names[k] == "CYS" and free_cys[k]))]
    deam = [k for k in range(n) if exposed[k] and names[k] == "ASN" and nxt(k) in ("GLY", "SER", "THR", "HIS")]
    iso = [k for k in range(n) if exposed[k] and names[k] == "ASP" and nxt(k) in ("GLY", "SER", "THR", "ASP")]
    lys = [k for k in range(n) if exposed[k] and names[k] == "LYS"]
    hyd = np.array([k for k in range(n) if names[k] in HYDROPHOBIC - {"ALA", "PRO"} and rel_sasa[k] >= 0.3])
    patch: list[int] = []
    if len(hyd) >= 3:
        tree = cKDTree(ca_xyz[hyd])
        counts = tree.query_ball_point(ca_xyz[hyd], r=1.0, return_length=True)
        patch = sorted(int(k) for k, c in zip(hyd, counts) if c >= 3)
    return [
        {"class": "Oxidation", "why": "exposed Met, Trp, His and free Cys", "residues": ox},
        {"class": "Deamidation", "why": "exposed Asn before Gly, Ser, Thr or His", "residues": deam},
        {"class": "Isomerisation", "why": "exposed Asp before Gly, Ser, Thr or Asp", "residues": iso},
        {"class": "Lysine", "why": "exposed Lys, the target of amine-reactive groups", "residues": lys},
        {"class": "Hydrophobic patch", "why": "clusters of exposed hydrophobic side chains, where aggregation starts",
         "residues": patch},
    ]


def relative_sasa(xyz_heavy: np.ndarray, elements: list[str], heavy_res: np.ndarray,
                  resnames: list[str]) -> np.ndarray:
    radii = np.array([RADII.get(e.upper(), 0.17) for e in elements])
    per_atom = sasa(xyz_heavy, radii)
    per_res = np.bincount(heavy_res, weights=per_atom, minlength=len(resnames))
    mx = np.array([MAX_SASA.get(canonical(r), 2.0) for r in resnames])
    return per_res / mx
