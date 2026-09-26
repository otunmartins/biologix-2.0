"""Candidate polymer excipients for stabilising a biologic above fridge temperature.

WHAT THIS IS. A hypothesis generator. It enumerates polymer candidates by
combining backbones and pendant groups whose stabilisation mechanisms are
described in the formulation literature, builds each as a real structure, and
puts every one through the SAME screens the rest of this app uses: RDKit
structural alerts, the excipient-residue rule table, and FDA precedent. What
comes out is a ranked shortlist to take into the laboratory.

WHAT THIS IS NOT. It does not predict that a candidate will stabilise anything.
There is no molecular dynamics here, no free-energy calculation, no preferential
-interaction coefficient, and no Tm prediction — those need the Stage 3 OpenMM
work that does not exist yet. Nothing it returns can exceed grade D, and the
verdict on every candidate is "Data gap: test".

WHY COMBINATORIAL AND NOT GENERATIVE. A model asked to invent stabilising
polymers will produce fluent, plausible, unfalsifiable suggestions — precisely
what the evidence gate in main.py exists to stop. So the chemistry comes from a
curated motif table with citations to the mechanism, and the model's only job is
reading the user's goal out of their own words.

THE SCREENS DO REAL WORK HERE. The alert catalogue is not decoration:
  - a glucose pendant fires the reducing-sugar alert, because it would glycate
    the protein's lysines — which is why the literature uses trehalose and
    sucrose, both non-reducing, and why this ranks them above glucose;
  - any polyether fires the autoxidation alert, because peroxides formed on
    storage oxidise Met and Trp — the failure mode that matters most for a
    product held at room temperature for months;
  - an acrylamide backbone fires the Michael-acceptor alert on residual monomer;
  - a methacrylate backbone fires the hydrolysable-ester alert, which shifts pH
    locally and promotes deamidation.
So the ranking is driven by chemistry the app can actually see, not by assertion.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors

import polymer

# Representative chain length for screening. Alerts are substructure-presence
# tests, so a longer chain fires exactly the same ones (see polymer.py).
SCREEN_UNITS = 4


# ---------------------------------------------------------------------------
# Motif library
# ---------------------------------------------------------------------------
#
# Tg is the homopolymer glass-transition temperature in C, from the polymer
# handbook literature. It is reported, never computed: a copolymer's real Tg
# depends on composition, water content and processing, none of which are
# modelled. It matters because vitrification is the mechanism that protects a
# DRIED product above fridge temperature — a matrix has to be a glass at the
# storage temperature to immobilise the protein, which is why PEG (Tg around
# -60 C) is a poor lyophilisation matrix and PVP (around 175 C) is a good one.

@dataclass(frozen=True)
class Backbone:
    key: str
    name: str
    # {p} is where the pendant attaches. Two [*] = the polymer attachment points.
    template: str
    tg_c: float | None
    mechanism: str
    caution: str = ""
    # Cost of that caution. Alerts are scored separately from the built
    # structure; this covers what the structure cannot show - residual monomer
    # toxicity, hydrolysis to something worse - and is kept small where an
    # alert already charges for the same defect.
    penalty: float = 0.0


BACKBONES = [
    Backbone("methacrylamide", "Poly(methacrylamide)", "[*]CC(C)([*])C(=O)N{p}", 165.0,
             "Amide backbone, hydrolytically stable and strongly hydrated; the workhorse "
             "backbone for trehalose glycopolymers reported to protect proteins through "
             "heating and lyophilisation."),
    Backbone("acrylamide", "Poly(acrylamide)", "[*]CC([*])C(=O)N{p}", 165.0,
             "As methacrylamide but more flexible, with a lower chain-transfer constant.",
             "Residual acrylamide monomer is a Michael acceptor and a neurotoxin; a "
             "residual-monomer specification is mandatory, not optional.", -1.5),
    Backbone("methacrylate", "Poly(methacrylate)", "[*]CC(C)([*])C(=O)O{p}", 105.0,
             "Ester link to the pendant, straightforward to synthesise by RAFT.",
             "The ester hydrolyses in aqueous formulation, releasing acid that shifts "
             "local pH and promotes deamidation. Poor choice for a liquid product.", -0.5),
    Backbone("vinyl", "Poly(vinyl)", "[*]CC([*]){p}", 85.0,
             "Simple carbon backbone with no hydrolysable link; polyvinyl alcohol is the "
             "familiar member and is used as a cryoprotectant."),
    Backbone("oxazoline", "Poly(2-oxazoline)", "[*]N(CC[*])C(=O){p}", 55.0,
             "Tertiary-amide backbone, highly water-soluble and low-fouling; studied as a "
             "PEG replacement without PEG's autoxidation liability."),
]


# Sugars attach through the 6-hydroxyl with a short spacer, which is how real
# glycopolymer chemistry does it. The regiochemistry is the whole point: attach
# a sugar through its anomeric carbon instead and you cap the very centre that
# decides whether it is reducing, so glucose would read as safe and sucrose
# would not. Linked at 6-O, trehalose and sucrose keep both anomeric carbons
# glycosidic and stay clean, while glucose keeps its free anomeric OH and trips
# the reducing-sugar alert — which is exactly why the literature builds these
# polymers from trehalose.
@dataclass(frozen=True)
class Pendant:
    key: str
    name: str
    smiles: str          # fragment; its first atom bonds to the backbone
    mechanism: str
    charge: str          # "neutral" | "zwitterion" | "anionic" | "cationic"
    caution: str = ""
    penalty: float = 0.0


PENDANTS = [
    Pendant("trehalose", "Trehalose (non-reducing disaccharide)",
            "CCOC[C@H]1O[C@H](O[C@H]2O[C@H](CO)[C@@H](O)[C@H](O)[C@H]2O)[C@H](O)[C@@H](O)[C@@H]1O",
            "Preferential exclusion in solution and water replacement in the dried state. "
            "Trehalose is the reference stabiliser for drying biologics, and tethering it to "
            "a polymer concentrates that effect per chain.",
            "neutral"),
    Pendant("sucrose", "Sucrose (non-reducing disaccharide)",
            "CCOC[C@H]1O[C@H](O[C@]2(CO)O[C@H](CO)[C@@H](O)[C@@H]2O)[C@H](O)[C@@H](O)[C@@H]1O",
            "Same exclusion and water-replacement mechanism as trehalose, with a lower "
            "glass transition and a known hydrolysis route to reducing sugars at low pH.",
            "neutral",
            "Hydrolyses to glucose and fructose below about pH 5; both are reducing sugars "
            "and would glycate lysine.", -1.0),
    Pendant("glucose", "Glucose (reducing sugar)",
            "CCOC[C@H]1O[C@@H](O)[C@H](O)[C@@H](O)[C@@H]1O",
            "Included deliberately as a negative control: it hydrates like the "
            "disaccharides but carries a free anomeric centre.",
            "neutral",
            "Reducing sugar. Glycates lysine by the Maillard route; the screen should and "
            "does reject it.", -1.0),
    Pendant("sulfobetaine", "Sulfobetaine (zwitterion)",
            "CCC[N+](C)(C)CCCS(=O)(=O)[O-]",
            "Zwitterionic hydration binds water far more strongly than a neutral polyol, "
            "suppressing protein-protein contact and aggregation while carrying no net "
            "charge to bind the protein electrostatically.",
            "zwitterion"),
    Pendant("carboxybetaine", "Carboxybetaine (zwitterion)",
            "CCC[N+](C)(C)CC(=O)[O-]",
            "As sulfobetaine, with a pH-sensitive carboxylate that is the weaker "
            "zwitterion below about pH 4.",
            "zwitterion"),
    Pendant("hydroxyethyl", "Hydroxyethyl",
            "CCO",
            "Simple polyol hydration; the smallest motif that still contributes hydrogen "
            "bonding for water replacement.",
            "neutral"),
    Pendant("oligoethylene_glycol", "Oligo(ethylene glycol)",
            "CCOCCOCCO",
            "Steric crowding and excluded volume; the familiar PEG mechanism.",
            "neutral",
            "Polyether chain autoxidises to peroxides on storage, oxidising Met and Trp. "
            "The liability that matters most for extended storage above 2-8 C.", -0.5),
    Pendant("carboxylate", "Carboxylate (anionic)",
            "CCC(=O)[O-]",
            "Polyanions are used deliberately with net-positive proteins to form complexes, and "
            "they hydrate well. Whether that is stabilisation or complexation depends entirely "
            "on the protein's charge, which is why this motif is only judged once the charge "
            "is known.",
            "anionic",
            "Binds a net-positive protein electrostatically; that is complexation, and it "
            "changes the conformational equilibrium rather than merely protecting it."),
    Pendant("quaternary_ammonium", "Quaternary ammonium (cationic)",
            "CCC[N+](C)(C)C",
            "Polycations bind net-negative proteins and are common in delivery formulations.",
            "cationic",
            "Binds a net-negative protein electrostatically, and quaternary ammonium surfactant "
            "motifs carry their own membrane-lytic and tolerability concerns at a parenteral "
            "route."),
    Pendant("proline", "Proline-like tertiary amide",
            "CC(=O)N1CCCC1",
            "Proline suppresses aggregation and is a known solution stabiliser; the "
            "pyrrolidone motif is also what makes PVP an effective cryoprotectant.",
            "neutral"),
]


# ---------------------------------------------------------------------------
# What the user is asking for
# ---------------------------------------------------------------------------

class DesignGoal(BaseModel):
    """The formulation problem, as parsed from the user's own words."""

    protein: str = Field(default="", max_length=300)
    route: str = Field(default="", max_length=60)
    # The whole point: what temperature must it survive, and for how long.
    target_temp_c: float | None = Field(default=None, ge=-80, le=60)
    duration_months: float | None = Field(default=None, gt=0, le=120)
    # "liquid" or "lyophilised"; decides whether vitrification is even relevant.
    format: str = Field(default="liquid", pattern="^(liquid|lyophilised)$")
    # Residues the protein actually exposes, from the liability scan if it ran.
    exposed_residues: list[str] = Field(default_factory=list)
    # Net charge of the biologic at the formulation pH, from profile.py. Decides
    # which ionic pendants are chemically wrong rather than merely unhelpful:
    # a polyanion binds a net-positive protein, which is complexation, not
    # stabilisation. None means unknown, and no charge rule is applied.
    net_charge: float | None = None
    ph: float | None = None
    notes: str = Field(default="", max_length=1000)


# ---------------------------------------------------------------------------
# Construction and measurement
# ---------------------------------------------------------------------------

def repeat_unit(backbone: Backbone, pendant: Pendant) -> str:
    return backbone.template.format(p=pendant.smiles)


def build_chain(backbone: Backbone, pendant: Pendant, units: int = SCREEN_UNITS) -> str | None:
    """The representative oligomer, or None if the motif pair will not assemble."""
    try:
        spec = polymer.PolymerSpec(repeat_unit=repeat_unit(backbone, pendant))
        return Chem.MolToSmiles(polymer.build(spec, units))
    except Exception:
        return None


def descriptors(smiles: str) -> dict:
    """Computed physicochemical properties. Every one is measured off the built
    structure by RDKit — none is estimated, and none predicts stabilisation."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {}
    heavy = mol.GetNumHeavyAtoms()
    oh = len(mol.GetSubstructMatches(Chem.MolFromSmarts("[OX2H1]")))
    return {
        "mw": round(Descriptors.MolWt(mol), 1),
        "clogp": round(Descriptors.MolLogP(mol), 2),
        "tpsa": round(Descriptors.TPSA(mol), 1),
        "hbd": rdMolDescriptors.CalcNumHBD(mol),
        "hba": rdMolDescriptors.CalcNumHBA(mol),
        "rotatable": rdMolDescriptors.CalcNumRotatableBonds(mol),
        "hydroxyls": oh,
        # Hydrogen-bonding capacity per unit size. The physical basis of both
        # preferential exclusion and water replacement is the excipient's ability
        # to hydrogen-bond water and, when dried, the protein surface itself.
        "oh_density": round(oh / heavy, 3) if heavy else 0.0,
        "polar_fraction": round(Descriptors.TPSA(mol) / heavy, 2) if heavy else 0.0,
    }


def _alerts(smiles: str) -> list[str]:
    # Imported lazily: main imports this module, so a top-level import would
    # be circular.
    from main import _fired
    return _fired(smiles)


# Alerts that matter for a molecule meant to sit beside a protein for months,
# with the residue each one attacks and how hard to penalise it.
ALERT_PENALTY = {
    "Reducing sugar (cyclic hemiacetal, opens to an aldehyde)": (
        -4.0, "Lys", "glycation on storage — the faster the warmer"),
    "Polyether chain (autoxidises to peroxides on storage)": (
        -2.5, "Met", "peroxide-mediated oxidation of Met and Trp on storage"),
    "Michael acceptor (acrylate/acrylamide)": (
        -2.5, "Cys", "residual monomer alkylates free thiols"),
    "Hydrolysable ester": (
        -1.5, "Asn", "hydrolysis shifts local pH and promotes deamidation"),
    "Aldehyde": (-4.0, "Lys", "Schiff-base formation with lysine"),
    "Epoxide": (-3.0, "His", "alkylation"),
}


@dataclass
class Candidate:
    backbone: Backbone
    pendant: Pendant
    smiles: str
    descriptors: dict
    alerts: list[str]
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


def score(cand: Candidate, goal: DesignGoal) -> Candidate:
    """Transparent additive scoring: every term states its own reason.

    This ranks candidates against each other for laboratory triage. It is not a
    probability, not a predicted Tm shift, and not comparable across runs.
    """
    reasons, risks, total = [], [], 0.0

    # --- hydration: the shared basis of exclusion and water replacement -----
    d = cand.descriptors
    if d.get("oh_density", 0) >= 0.20:
        total += 2.0
        reasons.append(f"high hydroxyl density ({d['oh_density']}/heavy atom) — strong "
                       "hydrogen-bonding capacity for preferential exclusion and, when dried, "
                       "water replacement")
    elif d.get("oh_density", 0) >= 0.10:
        total += 1.0
        reasons.append(f"moderate hydroxyl density ({d['oh_density']}/heavy atom)")
    if d.get("clogp", 0) > 2:
        total -= 1.0
        risks.append(f"lipophilic for a stabiliser (cLogP {d['clogp']}); may adsorb to the "
                     "protein rather than being excluded from it")

    # --- charge: zwitterions hydrate, opposite charges complex --------------
    if cand.pendant.charge == "zwitterion":
        total += 2.0
        reasons.append("zwitterionic: binds water strongly while carrying no net charge, so "
                       "it suppresses aggregation without electrostatic binding to the protein")
    elif cand.pendant.charge in ("anionic", "cationic"):
        q = goal.net_charge
        opposite = (q is not None and
                    ((q > 1 and cand.pendant.charge == "anionic") or
                     (q < -1 and cand.pendant.charge == "cationic")))
        if opposite:
            # Known to be wrong for THIS protein, not merely unhelpful.
            total -= 4.0
            risks.append(
                f"{cand.pendant.charge} against a protein carrying {q:+.1f} net charge at "
                f"pH {goal.ph:g}: it would bind electrostatically, which is complexation "
                "rather than stabilisation")
        elif q is None:
            total -= 1.5
            risks.append(f"net {cand.pendant.charge} — may bind the protein electrostatically; "
                         "the protein's charge was not supplied, so this could not be checked")
        else:
            total -= 0.5
            risks.append(f"net {cand.pendant.charge}, same sign as the protein ({q:+.1f}), so "
                         "electrostatic binding is unlikely, but the charge still perturbs the "
                         "local ionic environment")

    # --- vitrification, only for a dried product ---------------------------
    tg = cand.backbone.tg_c
    if goal.format == "lyophilised":
        target = goal.target_temp_c if goal.target_temp_c is not None else 25.0
        if tg is None:
            risks.append("homopolymer Tg not on record; the cake's glass transition must be "
                         "measured by modulated DSC before this can be judged")
        else:
            # Continuous in the margin, not stepped: water plasticises a real cake
            # by tens of degrees, so headroom above the storage temperature keeps
            # paying past the point where the dry polymer is merely glassy.
            margin = tg - target
            total += max(-2.5, min(3.0, margin / 45.0))
            if margin >= 100:
                reasons.append(f"backbone Tg about {tg:g} C, {margin:g} C above the {target:g} C "
                               "target — ample headroom for the drop once residual moisture "
                               "plasticises the cake")
            elif margin >= 50:
                reasons.append(f"backbone Tg about {tg:g} C, {margin:g} C above the {target:g} C "
                               "target — the matrix should stay glassy, though moisture will "
                               "erode that margin")
            elif margin > 0:
                risks.append(f"backbone Tg about {tg:g} C is only {margin:g} C above the "
                             f"{target:g} C target; residual moisture could plasticise it below "
                             "storage temperature")
            else:
                risks.append(f"backbone Tg about {tg:g} C is at or below the {target:g} C target; "
                             "the matrix would not be a glass at storage temperature")
    elif tg is not None and tg < 0:
        reasons.append(f"low Tg ({tg:g} C) is irrelevant for a liquid product, but would rule "
                       "this out for a dried one")

    # --- chemistry the screens actually found ------------------------------
    for alert in cand.alerts:
        if alert in ALERT_PENALTY:
            penalty, residue, why = ALERT_PENALTY[alert]
            # Worse when the protein is known to expose the residue at risk.
            if residue in goal.exposed_residues:
                penalty *= 1.5
                why += f" — and this protein exposes {residue}"
            total += penalty
            risks.append(f"{alert}: {why}")

    # --- the stated caution on each motif ----------------------------------
    for motif in (cand.backbone, cand.pendant):
        if motif.caution:
            total += motif.penalty
            risks.append(motif.caution)

    # --- warmer and longer is harder ---------------------------------------
    if goal.target_temp_c is not None and goal.target_temp_c >= 30:
        if any(a in ALERT_PENALTY for a in cand.alerts):
            total -= 1.0
            risks.append(f"every degradation route above runs faster at {goal.target_temp_c:g} C")

    cand.score = round(total, 2)
    cand.reasons = reasons
    cand.risks = risks
    return cand


# Standard assays, chosen by what the candidate actually risks. These are the
# experiments that would confirm or kill the hypothesis.
def experiments(cand: Candidate, goal: DesignGoal) -> list[str]:
    plan = [
        "nanoDSF or DSC for the Tm shift against excipient-free control — the direct "
        "read-out of whether the candidate thermally stabilises this protein",
        f"accelerated stability at {max(40, (goal.target_temp_c or 25) + 15):g} C with "
        "SEC-HPLC for soluble aggregate and fragment",
    ]
    if goal.format == "lyophilised":
        plan.append("modulated DSC on the cake for its actual Tg, with Karl Fischer for "
                    "residual moisture — a wet cake plasticises below the storage temperature")
    else:
        plan.append("agitation and freeze-thaw stress with sub-visible particle counting "
                    "(micro-flow imaging or light obscuration)")
    for alert in cand.alerts:
        if "Reducing sugar" in alert:
            plan.append("boronate-affinity or intact-mass for glycation of lysine after "
                        "accelerated storage")
        if "Polyether" in alert:
            plan.append("peroxide value on the incoming polymer, and peptide mapping for "
                        "Met and Trp oxidation after storage")
        if "Michael acceptor" in alert:
            plan.append("residual-monomer assay by GC-MS, and free-thiol titration")
        if "ester" in alert.lower():
            plan.append("pH drift and free-acid release across the stability study")
    return plan


def enumerate_candidates(goal: DesignGoal, limit: int = 12) -> list[dict]:
    """Every motif pair that assembles, screened, scored and ranked."""
    ranked: list[Candidate] = []
    for backbone in BACKBONES:
        for pendant in PENDANTS:
            smiles = build_chain(backbone, pendant)
            if not smiles:
                continue
            cand = Candidate(backbone, pendant, smiles, descriptors(smiles), _alerts(smiles))
            ranked.append(score(cand, goal))

    ranked.sort(key=lambda c: (-c.score, -c.descriptors.get("oh_density", 0.0),
                               c.backbone.key, c.pendant.key))
    out = []
    for rank, c in enumerate(ranked[:limit], 1):
        out.append({
            "rank": rank,
            "name": f"{c.backbone.name} bearing {c.pendant.name}",
            "backbone": c.backbone.name,
            "pendant": c.pendant.name,
            "repeat_unit_smiles": repeat_unit(c.backbone, c.pendant),
            "screened_oligomer_smiles": c.smiles,
            "screened_units": SCREEN_UNITS,
            "backbone_tg_c": c.backbone.tg_c,
            "charge": c.pendant.charge,
            "score": c.score,
            "mechanism": f"{c.backbone.mechanism} {c.pendant.mechanism}",
            "supports": c.reasons,
            "risks": c.risks,
            "alerts_fired": c.alerts,
            "descriptors": c.descriptors,
            "suggested_experiments": experiments(c, goal),
            # Handed to the existing screen, which is what actually judges it.
            "screen_as": {
                "repeat_unit": repeat_unit(c.backbone, c.pendant),
                "end_group_a": "[*][H]",
                "end_group_b": "[*][H]",
            },
        })
    return out


LIMITS = (
    "Candidates are enumerated from a curated motif table, not invented, and each is built "
    "and screened with the same structural alerts and rule table as any other excipient here. "
    "The ranking is a triage ordering for laboratory work — it is NOT a prediction that a "
    "candidate will stabilise this protein. No molecular dynamics, no free-energy or "
    "preferential-interaction calculation and no Tm prediction was performed; those require "
    "the compatibility simulation that is not built. Tg values are homopolymer literature "
    "values, not the Tg of a real copolymer or cake, which depends on composition, moisture "
    "and processing. Every candidate is grade D and 'Data gap: test' until laboratory data "
    "exists. Nothing here addresses synthesis feasibility, polydispersity, endotoxin, "
    "immunogenicity or clearance, any of which can rule out a candidate on its own."
)


def design(goal: DesignGoal, limit: int = 12) -> dict:
    return {
        "goal": goal.model_dump(),
        "candidates": enumerate_candidates(goal, limit),
        "n_motif_pairs_considered": len(BACKBONES) * len(PENDANTS),
        "limits": LIMITS,
    }
