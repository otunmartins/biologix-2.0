"""One compatibility simulation: the candidate polymer around the target protein.

    run(job, settings, progress, should_stop) -> result dict (api/main.py SimulationResult)

THE SYSTEM
- Protein: the target biologic's structure as the API fetched it (RCSB mmCIF, or
  an AlphaFold model). Waters, ligands and ions are removed; an AlphaFold
  model's low-confidence termini (pLDDT < 50) are trimmed, because a floppy
  disordered tail both dominates the box size and is not a structure. PDBFixer
  fills missing atoms and INTERNAL missing residues and protonates at the
  goal's pH. Missing termini are left off rather than invented.
- Polymer: the candidate's screened oligomer -- the exact chain the structural
  alerts ran on -- as several copies. OpenFF Sage (openff-2.2.0) parameters with
  NAGL graph-network AM1-BCC charges: real AM1-BCC on a 300-atom oligomer takes
  hours on its own, the NAGL model reproduces it in seconds. The number of chains
  gives the requested % w/v in the box, at least MIN_CHAINS for statistics.
- Solvent: TIP3P water, neutralised, 0.15 M NaCl. Protein: Amber ff14SB.

THE RUN. At the temperature the biologic must survive (the design goal's), 1 bar,
Langevin middle integrator, PME, 4 fs with hydrogen mass repartitioning. The
protein's C-alpha atoms are held with a harmonic restraint: the question is how
the polymer distributes around the NATIVE state, and an unrestrained protein on
a nanosecond scale mostly adds noise to that. Minimise, equilibrate, then
production with Gamma23 accumulated on the fly (analysis.py) so no trajectory
has to be written or shipped. The same frames give the Tier 1 metrics
(metrics.py): polymer-protein interaction energy, hydrogen bonds, residence
times, liability-residue coverage, self-association, and QC.

THE STRESS STAGE (Tier 2). The restraint holds the fold, so production alone
cannot show a polymer destabilising the protein. After it, the restraint is
released and the protein runs unrestrained for stress_ns at a stress
temperature (77 C, or the goal's if higher): RMSD, RMSF, native contacts,
surface and secondary structure, against where the stage started. Nanoseconds
at 77 C are a comparison between polymers on the same protein, not a melting
temperature.

The same code runs on CPU and GPU; OpenMM picks the fastest platform present
unless OPENMM_PLATFORM says otherwise. A CPU run of a real system takes days:
CPU is for the smoke test.
"""

from __future__ import annotations

import io
import os
import random
import time
from dataclasses import dataclass

import numpy as np

import analysis
import metrics
import snapshot

AVOGADRO = 6.02214076e23


@dataclass
class Settings:
    production_ns: float = 20.0
    equilibration_ns: float = 1.0
    report_ps: float = 10.0          # one analysed frame every this often
    timestep_fs: float = 4.0
    padding_nm: float = 2.5          # protein to box edge; the bulk domain lives here
    polymer_wv_percent: float = 5.0  # polymer concentration in the box, % w/v
    salt_mm: float = 150.0           # NaCl added to the water, mM
    min_chains: int = 4
    max_chains: int = 40
    r_local_nm: float = 1.0
    r_bulk_nm: float = 1.2
    max_residues: int = 1500
    platform: str = ""               # "", "CUDA", "OpenCL", "CPU"
    stress_ns: float = 10.0          # the unrestrained stage (Tier 2); 0 skips it
    stress_temperature_c: float = 0.0  # 0: 77 C, or the goal's temperature if higher
    slow_every: int = 10             # surface and secondary structure every this many frames
    smoke: bool = False
    preview: bool = False            # a short CPU run: not converged, never a measurement

    @classmethod
    def from_env(cls) -> "Settings":
        if os.environ.get("SIM_SMOKE") == "1":
            return cls.smoke_test()
        s = cls()
        for f, cast in (("production_ns", float), ("equilibration_ns", float),
                        ("report_ps", float), ("timestep_fs", float), ("padding_nm", float),
                        ("polymer_wv_percent", float), ("min_chains", int),
                        ("max_chains", int), ("max_residues", int), ("stress_ns", float),
                        ("stress_temperature_c", float), ("slow_every", int)):
            v = os.environ.get(f"SIM_{f.upper()}")
            if v:
                setattr(s, f, cast(v))
        s.platform = os.environ.get("OPENMM_PLATFORM", "")
        return s

    @classmethod
    def smoke_test(cls) -> "Settings":
        """Seconds on a CPU. Proves every stage runs; the number means nothing."""
        return cls(production_ns=0.004, equilibration_ns=0.002, report_ps=0.4, timestep_fs=2.0,
                   padding_nm=1.4, polymer_wv_percent=5.0, min_chains=2, max_chains=2,
                   r_local_nm=0.6, r_bulk_nm=0.8, max_residues=200, stress_ns=0.004, slow_every=2,
                   platform=os.environ.get("OPENMM_PLATFORM", ""), smoke=True)


class Abandoned(Exception):
    """The API says this job is no longer ours; stop without reporting."""


# ---------------------------------------------------------------------------
# Protein
# ---------------------------------------------------------------------------

def prepare_protein(mmcif: str, predicted: bool, ph: float, max_residues: int):
    """(topology, positions, notes) for the protein alone, hydrogens added."""
    import gemmi
    from openmm.app import PDBFile
    from pdbfixer import PDBFixer

    notes = []
    st = gemmi.read_structure_string(mmcif) if hasattr(gemmi, "read_structure_string") \
        else gemmi.make_structure_from_block(gemmi.cif.read_string(mmcif).sole_block())
    st.setup_entities()
    st.remove_ligands_and_waters()
    st.remove_hydrogens()
    st.remove_empty_chains()
    while len(st) > 1:  # NMR ensembles: first model only
        del st[len(st) - 1]
    model = st[0]

    if predicted:
        # pLDDT lives in the B-factor column of AlphaFold models.
        trimmed = 0
        for chain in model:
            res = list(chain)
            plddt = [np.mean([a.b_iso for a in r]) for r in res]
            lo, hi = 0, len(res)
            while lo < hi and plddt[lo] < 50:
                lo += 1
            while hi > lo and plddt[hi - 1] < 50:
                hi -= 1
            for i in sorted(list(range(hi, len(res))) + list(range(0, lo)), reverse=True):
                del chain[i]
                trimmed += 1
        if trimmed:
            notes.append(f"trimmed {trimmed} low-confidence terminal residues (pLDDT < 50) "
                         "from the AlphaFold model")

    n_res = sum(len(c) for c in model)
    if n_res == 0:
        raise ValueError("the structure has no protein residues")
    if n_res > max_residues:
        raise ValueError(
            f"the structure has {n_res} residues, over this worker's limit of {max_residues}. "
            "For an antibody, queue with a Fab or Fc structure instead of the whole IgG.")

    fixer = PDBFixer(pdbfile=io.StringIO(st.make_pdb_string()))
    fixer.removeHeterogens(keepWater=False)
    fixer.findMissingResidues()
    # Keep only internal gaps: a missing terminus is left off, not invented.
    chains = list(fixer.topology.chains())
    for (ci, ri) in list(fixer.missingResidues):
        if ri == 0 or ri == len(list(chains[ci].residues())):
            del fixer.missingResidues[(ci, ri)]
    if fixer.missingResidues:
        notes.append(f"modelled {sum(len(v) for v in fixer.missingResidues.values())} "
                     "missing internal residues with PDBFixer")
    fixer.findNonstandardResidues()
    fixer.replaceNonstandardResidues()
    fixer.findMissingAtoms()
    fixer.addMissingAtoms()
    fixer.addMissingHydrogens(ph)
    return fixer.topology, fixer.positions, notes, n_res


# ---------------------------------------------------------------------------
# Polymer
# ---------------------------------------------------------------------------

def _nagl_model() -> str:
    from openff.nagl_models import list_available_nagl_models
    models = [str(m) for m in list_available_nagl_models() if "am1bcc" in str(m)]
    if not models:
        raise RuntimeError("no NAGL AM1-BCC model installed (openff-nagl-models)")
    return sorted(models)[-1]


def prepare_polymer(smiles: str):
    """An OpenFF Molecule with one conformer and NAGL AM1-BCC charges."""
    from openff.toolkit import Molecule
    mol = Molecule.from_smiles(smiles, allow_undefined_stereo=True)
    mol.generate_conformers(n_conformers=1)
    model = _nagl_model()
    mol.assign_partial_charges(model)
    return mol, os.path.basename(model)


def _rotation(rng: random.Random) -> np.ndarray:
    q = np.array([rng.gauss(0, 1) for _ in range(4)])
    a, b, c, d = q / np.linalg.norm(q)
    return np.array([[a*a+b*b-c*c-d*d, 2*(b*c-a*d), 2*(b*d+a*c)],
                     [2*(b*c+a*d), a*a-b*b+c*c-d*d, 2*(c*d-a*b)],
                     [2*(b*d-a*c), 2*(c*d+a*b), a*a-b*b-c*c+d*d]])


def place_chains(protein_xyz: np.ndarray, chain_xyz: np.ndarray, n: int, box: np.ndarray,
                 min_gap: float = 0.35, seed: int = 0, tries: int = 4000) -> list[np.ndarray]:
    """n randomly rotated copies of the chain, anywhere in the periodic box, no
    atom within min_gap of the protein or of another copy."""
    from scipy.spatial import cKDTree
    rng = random.Random(seed)
    centred = chain_xyz - chain_xyz.mean(axis=0)
    placed_atoms = np.mod(protein_xyz, box)
    placed: list[np.ndarray] = []
    for _ in range(tries):
        if len(placed) == n:
            break
        xyz = centred @ _rotation(rng).T + np.array([rng.random() * L for L in box])
        tree = cKDTree(placed_atoms, boxsize=box)
        d, _ = tree.query(np.mod(xyz, box), k=1)
        if d.min() > min_gap:
            placed.append(xyz)
            placed_atoms = np.vstack([placed_atoms, np.mod(xyz, box)])
    if len(placed) < n:
        raise RuntimeError(f"could only fit {len(placed)} of {n} polymer chains in the box; "
                           "lower SIM_POLYMER_WV_PERCENT or raise SIM_PADDING_NM")
    return placed


# ---------------------------------------------------------------------------
# Metric inputs
# ---------------------------------------------------------------------------

class _Thermo:
    """Instantaneous temperature, volume and potential energy from a State."""

    R = 0.00831446261815324  # kJ/mol/K

    def __init__(self, system, unit):
        import openmm
        n = system.getNumParticles()
        masses = [system.getParticleMass(i).value_in_unit(unit.dalton) for i in range(n)]
        self.mass = float(sum(masses))
        dof = 3 * sum(1 for m in masses if m > 0) - system.getNumConstraints()
        if any(isinstance(f, openmm.CMMotionRemover) for f in system.getForces()):
            dof -= 3
        self.dof, self.unit = dof, unit

    def read(self, st, box: np.ndarray) -> tuple[float, float, float]:
        u = self.unit
        ke = st.getKineticEnergy().value_in_unit(u.kilojoule_per_mole)
        pe = st.getPotentialEnergy().value_in_unit(u.kilojoule_per_mole)
        return 2 * ke / (self.dof * self.R), float(np.prod(box)), pe


def _metric_inputs(topology, system, xyz: np.ndarray, protein_res: set, res_index: dict,
                   labels: list[str]):
    """Everything metrics.py needs, as index arrays into the system's atoms:
    (Interactions, keyword inputs for Stability, notes). xyz is the starting
    structure, in nm, for which residues are exposed."""
    import openmm
    from openmm import unit

    atoms = list(topology.atoms())
    n = len(atoms)
    nb = next(f for f in system.getForces() if isinstance(f, openmm.NonbondedForce))
    q, sig, eps = np.zeros(n), np.zeros(n), np.zeros(n)
    for i in range(n):
        c, sg, ep = nb.getParticleParameters(i)
        q[i] = c.value_in_unit(unit.elementary_charge)
        sig[i] = sg.value_in_unit(unit.nanometer)
        eps[i] = ep.value_in_unit(unit.kilojoule_per_mole)
    heavy = np.array([a.element is not None and a.element.symbol != "H" for a in atoms])
    hs: dict[int, list[int]] = {a.index: [] for a in atoms}
    nbonds = np.zeros(n, int)
    for b in topology.bonds():
        i, j = b[0].index, b[1].index
        nbonds[i] += 1
        nbonds[j] += 1
        if heavy[i] and not heavy[j]:
            hs[i].append(j)
        elif heavy[j] and not heavy[i]:
            hs[j].append(i)

    # Protein, polymer (every chain added after the protein that is not water or
    # an ion) and water.
    protein_chains = {r.chain for r in protein_res}
    poly_chain_ids: dict = {}
    groups: dict[str, list[int]] = {"protein": [], "polymer": [], "water": []}
    prot, prot_res, poly, poly_chain = [], [], [], []
    res_of_atom = np.full(n, -1)
    for a in atoms:
        if a.residue in protein_res:
            g = "protein"
            prot.append(a.index)
            prot_res.append(res_index[a.residue])
            res_of_atom[a.index] = res_index[a.residue]
        elif a.residue.name == "HOH":
            g = "water"
        elif a.residue.chain not in protein_chains and a.residue.name not in ("NA", "CL", "Na+", "Cl-"):
            g = "polymer"
            poly.append(a.index)
            poly_chain.append(poly_chain_ids.setdefault(a.residue.chain, len(poly_chain_ids)))
        else:
            continue
        groups[g].append(a.index)
    donors, acceptors = {}, {}
    for g, idx in groups.items():
        d, acc = [], []
        for i in idx:
            el = atoms[i].element.symbol if atoms[i].element is not None else ""
            if el not in ("N", "O"):
                continue
            d += [(i, h) for h in hs[i]]
            if el == "O" or (not hs[i] and nbonds[i] <= 2):
                acc.append(i)
        donors[g] = np.array(d, int).reshape(-1, 2)
        acceptors[g] = np.array(acc, int)

    # Per residue: backbone atoms, chain, name -- for Stability and liabilities.
    prot = np.array(prot)
    local = {int(i): k for k, i in enumerate(prot)}
    n_res = len(labels)
    residues: list = [None] * n_res
    for r, k in res_index.items():
        residues[k] = r
    bb = {name: np.full(n_res, -1) for name in ("N", "CA", "C", "O", "H")}
    chain_ids: dict = {}
    chain_of = np.zeros(n_res, int)
    resnames, sg = [], {}
    for k, r in enumerate(residues):
        resnames.append(r.name)
        chain_of[k] = chain_ids.setdefault(r.chain.index, len(chain_ids))
        for a in r.atoms():
            if a.name in bb:
                bb[a.name][k] = local[a.index]
            if a.name == "SG":
                sg[k] = a.index
    heavy_local = np.array([k for k, i in enumerate(prot) if heavy[i]])
    heavy_res = np.array([res_index[atoms[prot[k]].residue] for k in heavy_local])
    elements = [atoms[prot[k]].element.symbol for k in heavy_local]

    # Which liability residues are exposed at the start; a Cys whose sulfur has
    # another within 0.25 nm is in a disulfide, not free.
    rel = metrics.relative_sasa(xyz[prot[heavy_local]], elements, heavy_res, resnames)
    free_cys = np.zeros(n_res, bool)
    for k, i in sg.items():
        others = [j for kk, j in sg.items() if kk != k]
        free_cys[k] = not others or np.min(np.linalg.norm(xyz[others] - xyz[i], axis=1)) > 0.25
    ca_xyz = np.array([xyz[prot[bb["CA"][k]]] if bb["CA"][k] >= 0 else xyz[prot[0]] for k in range(n_res)])
    liab = metrics.liability_classes(resnames, chain_of, rel, ca_xyz, free_cys)
    counts = ", ".join(f"{c['class'].lower()} {len(c['residues'])}" for c in liab if c["residues"])
    notes = [f"exposed liability sites at the start: {counts}"] if counts else []

    inter = metrics.Interactions(
        protein=prot, protein_res=np.array(prot_res), residue_labels=labels, polymer=np.array(poly),
        polymer_chain=np.array(poly_chain), n_chains=len(poly_chain_ids), heavy=heavy, charge=q,
        sigma=sig, epsilon=eps, donors=donors, acceptors=acceptors, res_of_atom=res_of_atom,
        liabilities=liab)
    stab = {"atoms": prot, "ca": bb["CA"], "heavy": heavy_local, "heavy_res": heavy_res,
            "elements": elements, "resnames": resnames, "residue_labels": labels, "chain": chain_of,
            "bb": bb}
    return inter, stab, notes


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run(job: dict, s: Settings, progress=lambda msg: None, should_stop=lambda: False) -> dict:
    import openmm
    from openmm import unit
    from openmm.app import (HBonds, Modeller, PME, ForceField, Simulation)
    from openmmforcefields.generators import SMIRNOFFTemplateGenerator

    t0 = time.time()
    notes: list[str] = []
    # The conditions the user asked for (api/conditions.py); a job from an API
    # before "conditions" existed carries temperature and pH at the top level.
    cond = job.get("conditions") or {}
    asked = cond.get("values") or {}
    temperature_k = float(asked.get("temperature_c", job.get("temperature_c", 25.0))) + 273.15
    ph = float(asked.get("ph", job.get("ph") or 7.0))
    notes += list(cond.get("notes") or [])
    if not cond and job.get("format") == "lyophilised":
        notes.append("the biologic is lyophilised, but this is a solution simulation: it says how "
                     "the polymer distributes around the protein in water, not in the dried cake")
    if abs(ph - 7.0) > 0.01:
        notes.append(f"the protein is protonated for pH {ph:g}; the polymer keeps the charge "
                     "states it was drawn with")

    progress("preparing the protein")
    top, pos, pnotes, n_res = prepare_protein(job["structure"]["mmcif"], job["structure"]["predicted"],
                                              ph, s.max_residues)
    notes += pnotes

    progress("parameterising the polymer")
    smiles = job["candidate"]["screened_oligomer_smiles"]
    if not smiles:
        raise ValueError("candidate has no screened oligomer to simulate")
    mol, charge_model = prepare_polymer(smiles)
    ff = ForceField("amber14-all.xml", "amber14/tip3p.xml")
    gen = SMIRNOFFTemplateGenerator(molecules=[mol], forcefield="openff-2.2.0")
    ff.registerTemplateGenerator(gen.generator)

    # Box: the protein plus padding on every side, cubic so it can tumble-free
    # sit in the middle; the padding is where the bulk domain is measured.
    pxyz = np.array(pos.value_in_unit(unit.nanometer))
    extent = pxyz.max(axis=0) - pxyz.min(axis=0)
    L = float(extent.max() + 2 * s.padding_nm)
    box = np.array([L, L, L])
    pxyz = pxyz - pxyz.mean(axis=0) + box / 2

    # Chains for the requested % w/v in the box volume, within [min, max].
    mw = sum(a.mass.m for a in mol.atoms)  # g/mol
    volume_ml = L ** 3 * 1e-21
    want = s.polymer_wv_percent / 100 * volume_ml * AVOGADRO / mw
    n_chains = int(min(s.max_chains, max(s.min_chains, round(want))))
    if round(want) < s.min_chains:
        notes.append(f"{s.polymer_wv_percent:g}% w/v would be {want:.1f} chains in this box; "
                     f"{n_chains} were used so there is something to count")

    progress(f"building the box: {n_chains} chains, {L:.1f} nm")
    chain_xyz = np.array(mol.conformers[0].m_as("nanometer"))
    span = float(np.max(np.linalg.norm(chain_xyz[:, None] - chain_xyz[None], axis=-1)))
    if span > L - 2 * s.r_bulk_nm:
        # A chain longer than the box would touch its own periodic image.
        raise ValueError(f"the {span:.1f} nm polymer chain does not fit a {L:.1f} nm box; "
                         "raise SIM_PADDING_NM")
    copies = place_chains(pxyz, chain_xyz, n_chains, box)

    modeller = Modeller(top, pxyz * unit.nanometer)
    ptop = mol.to_topology().to_openmm()
    for xyz in copies:
        modeller.add(ptop, xyz * unit.nanometer)
    modeller.addSolvent(ff, model="tip3p", boxSize=openmm.Vec3(L, L, L) * unit.nanometer,
                        ionicStrength=s.salt_mm / 1000 * unit.molar, neutralize=True)

    progress("creating the system")
    system = ff.createSystem(modeller.topology, nonbondedMethod=PME,
                             nonbondedCutoff=0.9 * unit.nanometer, constraints=HBonds,
                             hydrogenMass=1.5 * unit.amu if s.timestep_fs > 2 else None)
    system.addForce(openmm.MonteCarloBarostat(1 * unit.bar, temperature_k * unit.kelvin))

    # Index sets, from the final topology.
    atoms = list(modeller.topology.atoms())
    n_protein_res = sum(1 for _ in top.residues())
    protein_res = set(list(modeller.topology.residues())[:n_protein_res])
    polymer_resname = next(iter(ptop.residues())).name
    prot_heavy, ca, poly_heavy, water_o, res_of_atom, labels = [], [], [], [], [], []
    res_index = {}
    for a in atoms:
        heavy = a.element is not None and a.element.symbol != "H"
        if a.residue in protein_res:
            if heavy:
                if a.residue not in res_index:
                    res_index[a.residue] = len(labels)
                    labels.append(f"{a.residue.name} {a.residue.id} {a.residue.chain.id}")
                prot_heavy.append(a.index)
                res_of_atom.append(res_index[a.residue])
            if a.name == "CA":
                ca.append(a.index)
        elif a.residue.name == polymer_resname and heavy:
            poly_heavy.append(a.index)
        elif a.residue.name == "HOH" and a.name == "O":
            water_o.append(a.index)
    heavy_per_chain = sum(1 for a in mol.atoms if a.atomic_number > 1)
    assert len(poly_heavy) == heavy_per_chain * n_chains, (len(poly_heavy), heavy_per_chain, n_chains)
    start_xyz = np.array(modeller.positions.value_in_unit(unit.nanometer))
    inter, stab_inputs, liab_notes = _metric_inputs(modeller.topology, system, start_xyz, protein_res,
                                                    res_index, labels)

    # Hold the native fold: C-alpha atoms restrained to their starting positions.
    restraint = openmm.CustomExternalForce("0.5*k*periodicdistance(x, y, z, x0, y0, z0)^2")
    restraint.addGlobalParameter("k", 1000.0 * unit.kilojoule_per_mole / unit.nanometer**2)
    for p in ("x0", "y0", "z0"):
        restraint.addPerParticleParameter(p)
    for i in ca:
        restraint.addParticle(i, modeller.positions[i].value_in_unit(unit.nanometer))
    system.addForce(restraint)
    barostat = next(f for f in system.getForces() if isinstance(f, openmm.MonteCarloBarostat))

    dt = s.timestep_fs * unit.femtosecond
    integrator = openmm.LangevinMiddleIntegrator(temperature_k * unit.kelvin, 1 / unit.picosecond, dt)
    platform = openmm.Platform.getPlatformByName(s.platform) if s.platform else None
    sim = Simulation(modeller.topology, system, integrator, platform) if platform \
        else Simulation(modeller.topology, system, integrator)
    sim.context.setPositions(modeller.positions)
    platform_name = sim.context.getPlatform().getName()
    progress(f"minimising ({len(atoms)} atoms on {platform_name})")
    sim.minimizeEnergy(maxIterations=2000 if not s.smoke else 200)
    sim.context.setVelocitiesToTemperature(temperature_k * unit.kelvin)

    steps_per_frame = max(1, int(round(s.report_ps * 1000 / s.timestep_fs)))
    equil_steps = int(s.equilibration_ns * 1e6 / s.timestep_fs)
    prod_frames = max(1, int(round(s.production_ns * 1000 / s.report_ps)))

    done = 0
    while done < equil_steps:
        if should_stop():
            raise Abandoned()
        n = min(steps_per_frame * 50, equil_steps - done)
        sim.step(n)
        done += n
        progress(f"equilibrating {done * s.timestep_fs / 1e6:.2f}/{s.equilibration_ns:g} ns")

    acc = analysis.Accumulator(heavy_per_chain=heavy_per_chain, residue_labels=labels,
                               residue_of_atom=np.array(res_of_atom),
                               r_local=s.r_local_nm, r_bulk=s.r_bulk_nm)
    inter.report_ps = s.report_ps
    thermo = _Thermo(system, unit)
    qc = metrics.QC(target_k=temperature_k, mass_g_per_mol=thermo.mass, report_ps=s.report_ps)
    prot_heavy, poly_heavy, water_o = map(np.array, (prot_heavy, poly_heavy, water_o))
    for f in range(prod_frames):
        if should_stop():
            raise Abandoned()
        sim.step(steps_per_frame)
        st = sim.context.getState(getPositions=True, getEnergy=True, enforcePeriodicBox=True)
        xyz = np.asarray(st.getPositions(asNumpy=True).value_in_unit(unit.nanometer))
        vecs = st.getPeriodicBoxVectors(asNumpy=True).value_in_unit(unit.nanometer)
        box_now = np.diag(vecs)
        acc.add(xyz[prot_heavy], xyz[poly_heavy], xyz[water_o], box_now)
        inter.add(xyz, box_now)
        qc.add(*thermo.read(st, box_now))
        if f % 10 == 0 or f == prod_frames - 1:
            ns = (f + 1) * s.report_ps / 1000
            progress(f"production {ns:.2f}/{s.production_ns:g} ns, Gamma23 so far "
                     f"{np.mean(acc.gamma):+.2f}, interaction "
                     f"{inter.energy_elec[-1] + inter.energy_vdw[-1]:.0f} kJ/mol")

    out = acc.result() | inter.result() | {"qc": qc.result()}
    if not out["qc"]["ok"] and not s.smoke:  # a few picoseconds never settle
        notes.append(f"QC: the production run averaged {out['qc']['temperature_k']:.1f} K against "
                     f"{temperature_k:.1f} K asked; treat its numbers with care")
    notes += liab_notes
    last_xyz, last_vecs = xyz, vecs  # the last production frame, for the 3D view

    # --- Tier 2: the restraint off, at a stress temperature ------------------
    stability = None
    if s.stress_ns > 0:
        stress_c = s.stress_temperature_c or max(77.0, temperature_k - 273.15)
        stress_k = stress_c + 273.15
        sim.context.setParameter("k", 0.0)
        integrator.setTemperature(stress_k * unit.kelvin)
        try:
            sim.context.setParameter(openmm.MonteCarloBarostat.Temperature(), stress_k)
        except Exception:  # noqa: BLE001 -- an OpenMM without the parameter: reinitialise
            barostat.setDefaultTemperature(stress_k * unit.kelvin)
            sim.context.reinitialize(preserveState=True)
        sim.context.setVelocitiesToTemperature(stress_k * unit.kelvin)
        prot_all = stab_inputs["atoms"]
        # Unwrapped positions throughout: a chain must not jump across the box.
        st = sim.context.getState(getPositions=True)
        ref = np.asarray(st.getPositions(asNumpy=True).value_in_unit(unit.nanometer))[prot_all]
        stab = metrics.Stability(ref=ref, report_ps=s.report_ps, slow_every=s.slow_every,
                                 **{k: v for k, v in stab_inputs.items() if k != "atoms"})
        stress_frames = max(1, int(round(s.stress_ns * 1000 / s.report_ps)))
        progress(f"stress stage: restraint off, {stress_c:g} C for {s.stress_ns:g} ns")
        for f in range(stress_frames):
            if should_stop():
                raise Abandoned()
            sim.step(steps_per_frame)
            st = sim.context.getState(getPositions=True)
            stab.add(np.asarray(st.getPositions(asNumpy=True).value_in_unit(unit.nanometer))[prot_all])
            if f % 10 == 0 or f == stress_frames - 1:
                progress(f"stress stage {(f + 1) * s.report_ps / 1000:.2f}/{s.stress_ns:g} ns at "
                         f"{stress_c:g} C, C-alpha RMSD {stab.rmsd[-1]:.2f} nm, Q {stab.q[-1]:.2f}")
        stability = stab.result(stress_c, s.stress_ns)
        notes.append(f"stress stage: after production the C-alpha restraint was released and the "
                     f"protein ran unrestrained for {s.stress_ns:g} ns at {stress_c:g} C; compare it "
                     "between polymers on the same protein (there is no polymer-free control yet)")
    xyz, vecs = last_xyz, last_vecs

    # The last frame, protein and polymer only, for the 3D view. A picture must
    # never cost a result, so a failure here is a note, not a failed run.
    snap = None
    try:
        def resseq(r, k):
            digits = "".join(ch for ch in str(r.id) if ch.isdigit())
            return int(digits) if digits else k + 1
        rnum = {r: resseq(r, k) for k, r in enumerate(protein_res)}
        protein_atoms = [(i, atoms[i].name, atoms[i].residue.name, atoms[i].residue.chain.id,
                          rnum[atoms[i].residue], atoms[i].element.symbol) for i in prot_heavy]
        chains = [[(i, atoms[i].name, atoms[i].element.symbol)
                   for i in poly_heavy[k * heavy_per_chain:(k + 1) * heavy_per_chain]]
                  for k in range(n_chains)]
        snap = snapshot.pdb(xyz=np.asarray(xyz), box=np.diag(vecs), protein=protein_atoms,
                            polymer_chains=chains)
    except Exception as e:  # noqa: BLE001
        notes.append(f"no 3D snapshot saved ({type(e).__name__}: {e})"[:300])
    return {
        **out,
        "production_ns": s.production_ns,
        "temperature_k": round(temperature_k, 2),
        "n_chains": n_chains,
        "engine": f"OpenMM {openmm.__version__}, {platform_name}",
        "forcefields": f"Amber ff14SB / OpenFF 2.2.0 with {charge_model} charges / TIP3P, "
                       f"{s.salt_mm:g} mM NaCl",
        # What was applied, whatever was asked: the record of what ran.
        "conditions": {"temperature_c": round(temperature_k - 273.15, 2), "ph": ph,
                       "salt_mm": s.salt_mm, "polymer_wv_percent": s.polymer_wv_percent},
        "structure_source": job["structure"].get("source", ""),
        "n_atoms": len(atoms),
        "wall_seconds": round(time.time() - t0, 1),
        "smoke": s.smoke,
        "preview": s.preview,
        "snapshot_pdb": snap,
        "stability": stability,
        "notes": notes[:17] + ([f"CPU preview: {s.production_ns:g} ns, not converged; a full GPU "
                                "run is needed before this number means much"]
                               if s.preview else []) + [f"{n_res} protein residues; C-alpha atoms restrained to the "
                               "starting structure (native state)"],
    }
