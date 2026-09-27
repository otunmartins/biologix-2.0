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
import tg_model

# Representative chain length for screening. Alerts are substructure-presence
# tests, so a longer chain fires exactly the same ones (see polymer.py).
SCREEN_UNITS = 4


# ---------------------------------------------------------------------------
# Motif library
# ---------------------------------------------------------------------------
#
# Tg is the homopolymer glass-transition temperature in C. It matters because
# vitrification is the mechanism that protects a DRIED product above fridge
# temperature — a matrix has to be a glass at the storage temperature to
# immobilise the protein, which is why PEG (Tg around -60 C) is a poor
# lyophilisation matrix and PVP (around 175 C) is a good one.
#
# The value below is the BACKBONE's handbook value, and it is only a fallback.
# It cannot see the pendant, so it assigns one number to every candidate on a
# given backbone — which is wrong in the direction that matters: hanging a
# sulfobetaine or an oligo(ethylene glycol) off a chain plasticises it heavily,
# and the handbook value for the bare backbone would score that candidate as if
# it had the glassiness of the unsubstituted polymer. tg_model.py predicts the
# assembled repeat unit instead, and is preferred wherever it can answer. See
# _tg_estimate for which one is used and what happens when neither is trusted.

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
    # The biologic itself, as a PDB ID (1IGT) or UniProt accession (P01857, whose
    # AlphaFold model is used). What the OpenMM worker simulates the polymer
    # against, so a candidate's simulation is about THIS protein, not a stand-in.
    structure_id: str = Field(default="", max_length=40)


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


# A composition is a backbone plus a weighted mixture of pendants whose mole
# fractions sum to 1. A homopolymer is the degenerate single-pendant case, and
# every function below reduces to the homopolymer result for it — see the
# active-learning loop (active.py), which searches this larger space.
Composition = list[tuple[Pendant, float]]


def build_copolymer_chain(backbone: Backbone, components: Composition,
                          units: int = SCREEN_UNITS) -> str | None:
    """A representative chain carrying every pendant in the mixture.

    Chain length per pendant is proportional to its fraction, floored at one, so
    each motif appears at least once. Order does not matter: structural alerts are
    substructure-presence tests and the descriptors are per-heavy-atom ratios, so
    which motifs are present — not their sequence along the chain — is what the
    screens see. Sequence (blocky vs random) is therefore NOT modelled, and every
    result says so.
    """
    if len(components) == 1:
        return build_chain(backbone, components[0][0], units)
    counts = [max(1, round(frac * units)) for _, frac in components]
    pools = [[repeat_unit(backbone, p)] * c for (p, _), c in zip(components, counts)]
    seq: list[str] = []
    while any(pools):                      # round-robin interleave
        for pool in pools:
            if pool:
                seq.append(pool.pop())
    try:
        return Chem.MolToSmiles(polymer.build_sequence(seq))
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


# ---------------------------------------------------------------------------
# Glass transition: predicted for the assembled repeat unit where possible
# ---------------------------------------------------------------------------
#
# WHY THE SCREEN JUDGES A LOWER BOUND AND NOT THE PREDICTION. The two ways to be
# wrong here do not cost the same. Understate a candidate's Tg and it ranks below
# something else that the laboratory then measures anyway. Overstate it and the
# app recommends a matrix that is not a glass at storage temperature, which is
# the failure the screen exists to catch. So the margin is taken from the
# prediction MINUS the model's own uncertainty, and a candidate has to clear the
# storage temperature with that bound - not with its point estimate - to earn
# vitrification credit.
#
# The uncertainty used is the LARGER of the forest's spread on this query and
# its measured scaffold-split error, because either can be the binding one: the
# spread catches chemistry the ensemble disagrees about, the scaffold-split MAE
# catches the error it makes even where the trees agree.
#
# EVERY Tg HERE IS UNCERTAIN, INCLUDING THE HANDBOOK ONE. The first version of
# this charged uncertainty only to candidates the model could speak to, which
# rewarded falling outside its domain: an out-of-domain candidate kept the bare
# backbone's flattering handbook value at full confidence and outranked
# everything the model had actually looked at. A backbone value standing in for a
# substituted repeat unit is not a safer number, it is an unmeasured one - the
# in-domain predictions here sit 60-100 C below their backbone's handbook value,
# because a flexible side chain plasticises a stiff backbone. So the handbook
# fallback carries at least the model's own measured error, and where an
# out-of-domain prediction is LOWER than the handbook value, the lower of the two
# is what gets judged. An extrapolation is allowed to argue a candidate down,
# never up.

# Floor on any Tg uncertainty: the model's measured error on unfamiliar
# chemistry, which is the least a number that was never measured for this
# repeat unit could be wrong by.
TG_UNCERTAINTY_FLOOR_C = tg_model.PERFORMANCE["scaffold_split"]["mae_c"]

_TG_CACHE: dict[str, dict] = {}


def _tg_estimate(backbone: Backbone, repeat_unit_smiles: str) -> dict:
    """Best available Tg for this candidate, and how much to trust it.

    Cached per repeat unit: the forest costs about 0.15 s a query, the same motif
    pairs recur on every request, and the answer cannot change between them.
    """
    if repeat_unit_smiles in _TG_CACHE:
        return _TG_CACHE[repeat_unit_smiles]

    lit = backbone.tg_c
    out = {
        "tg_c": lit,
        "source": "backbone literature value" if lit is not None else None,
        "literature_backbone_tg_c": lit,
        "spread_sd": None,
        "uncertainty_c": TG_UNCERTAINTY_FLOOR_C if lit is not None else None,
        # What the screen actually judges: the estimate less its uncertainty.
        "judged_tg_c": (round(lit - TG_UNCERTAINTY_FLOOR_C, 1) if lit is not None else None),
        "in_domain": None,
        "note": "",
    }

    p = tg_model.predict(repeat_unit_smiles)
    if p.get("available"):
        sd = p["model_spread_sd"]
        mae = p["expected_error_mae_c"]
        unc = max(sd, mae)
        if p["in_domain"]:
            judged = round(p["tg_c"] - unc, 1)
            out.update({
                "tg_c": p["tg_c"],
                "source": "predicted from the assembled repeat unit",
                "spread_sd": sd,
                "uncertainty_c": unc,
                "judged_tg_c": judged,
                "in_domain": True,
                "note": (
                    f"Tg {p['tg_c']:g} C predicted for this repeat unit by a forest trained on "
                    f"{p['performance']['n_training_polymers']} experimental polymers "
                    f"(nearest training polymer {p['nearest_training_similarity']:.2f} similar). "
                    f"Judged on {judged:g} C, the prediction less its {unc:g} C uncertainty, "
                    "because overstating the glass is the expensive mistake. Confirm by "
                    "modulated DSC on the cake."),
            })
        else:
            # An extrapolation cannot carry a stated error, so it does not get to
            # raise a candidate. It can lower one: it is the only number here that
            # saw the pendant, and if it says the chain is softer than its bare
            # backbone, that is the direction worth believing.
            anchor = p["tg_c"] if lit is None else min(lit, p["tg_c"])
            out.update({
                "tg_c": lit if lit is not None else p["tg_c"],
                "spread_sd": sd,
                "uncertainty_c": TG_UNCERTAINTY_FLOOR_C,
                "judged_tg_c": round(anchor - TG_UNCERTAINTY_FLOOR_C, 1),
                "in_domain": False,
                "note": (
                    f"Tg for this repeat unit is outside the model's domain (nearest training "
                    f"polymer only {p['nearest_training_similarity']:.2f} similar), so its "
                    f"{p['tg_c']:g} C estimate is an extrapolation. "
                    + (f"Judged against the lower of it and the {lit:g} C backbone handbook value, "
                       f"less {TG_UNCERTAINTY_FLOOR_C:g} C: the handbook value cannot see the "
                       "pendant, so neither number is trusted to raise this candidate."
                       if lit is not None else
                       "There is no backbone handbook value either, so the extrapolation is all "
                       f"there is, less {TG_UNCERTAINTY_FLOOR_C:g} C.")),
            })
    elif p.get("reason"):
        out["note"] = (f"Tg not predicted ({p['reason']}). "
                       + (f"Judged on the {lit:g} C backbone handbook value less "
                          f"{TG_UNCERTAINTY_FLOOR_C:g} C, because it cannot see the pendant."
                          if lit is not None else "No Tg available."))

    _TG_CACHE[repeat_unit_smiles] = out
    return out


def _weight_fractions(backbone: Backbone, components: Composition) -> list[float]:
    """Mole fractions -> weight fractions, using each repeat unit's molar mass.

    The Fox equation is written in weight fractions, so a heavy sugar pendant
    pulls the blend toward its own Tg more than its mole fraction alone would."""
    masses = []
    for pendant, frac in components:
        m = Chem.MolFromSmiles(repeat_unit(backbone, pendant))
        masses.append(frac * (Descriptors.MolWt(m) if m else 100.0))
    total = sum(masses) or 1.0
    return [x / total for x in masses]


def _blended_tg(backbone: Backbone, components: Composition) -> dict:
    """Copolymer Tg by the Fox equation, blending the per-component estimates.

    1/Tg = Σ wᵢ/Tgᵢ in Kelvin, applied to both the point estimate and the judged
    lower bound so the conservative number stays conservative. A single component
    returns _tg_estimate unchanged. The Fox rule assumes an ideal random copolymer:
    real sequence, reactivity ratios and composition drift are not modelled.
    """
    if len(components) == 1:
        return _tg_estimate(backbone, repeat_unit(backbone, components[0][0]))

    ests = [_tg_estimate(backbone, repeat_unit(backbone, p)) for p, _ in components]
    ws = _weight_fractions(backbone, components)

    def fox(key: str) -> float | None:
        terms = []
        for w, e in zip(ws, ests):
            t = e.get(key)
            if t is None:
                return None
            terms.append(w / (t + 273.15))
        s = sum(terms)
        return round(1.0 / s - 273.15, 1) if s > 0 else None

    tg, judged = fox("tg_c"), fox("judged_tg_c")
    domains = [e.get("in_domain") for e in ests]
    in_domain = True if all(d is True for d in domains) else (
        False if any(d is False for d in domains) else None)

    if tg is not None:
        blend = ", ".join(f"{p.name.split(' (')[0]} {w:.2f}"
                          for (p, _), w in zip(components, ws))
        note = (
            f"Tg about {tg:g} C is a Fox-equation blend of the component repeat-unit Tg "
            f"estimates (weight fractions {blend}), judged on {judged:g} C once each "
            "component's uncertainty is taken off. The Fox rule assumes an ideal random "
            "copolymer; the real sequence (blocky vs random), reactivity ratios and "
            "composition drift are NOT modelled, and the cake Tg must be measured by "
            "modulated DSC.")
    else:
        note = "Tg could not be blended: a component has no Tg estimate."

    return {
        "tg_c": tg,
        "source": "Fox blend of component repeat-unit Tg estimates" if tg is not None else None,
        "literature_backbone_tg_c": backbone.tg_c,
        "spread_sd": None,
        "uncertainty_c": (round(tg - judged, 1) if tg is not None and judged is not None else None),
        "judged_tg_c": judged,
        "in_domain": in_domain,
        "note": note,
    }


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
    # (pendant, mole_fraction) pairs summing to 1. A homopolymer is one pair at 1.0.
    components: Composition
    smiles: str
    descriptors: dict
    alerts: list[str]
    tg: dict = field(default_factory=dict)
    units: int = SCREEN_UNITS
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    @property
    def pendant(self) -> Pendant:
        """The (dominant) pendant — convenience for the homopolymer case."""
        return max(self.components, key=lambda cp: cp[1])[0]


def _frac_prefix(frac: float) -> str:
    """'' for a homopolymer, else a short 'at fraction 0.5, ' lead-in for a message."""
    return "" if frac >= 0.999 else f"at fraction {frac:g}, "


def score(cand: Candidate, goal: DesignGoal) -> Candidate:
    """Transparent additive scoring: every term states its own reason.

    This ranks candidates against each other for laboratory triage. It is not a
    probability, not a predicted Tm shift, and not comparable across runs. For a
    copolymer, composition-dependent terms (charge, motif cautions) are weighted by
    fraction; alert terms stay presence-based on the built chain. A single-pendant
    composition reduces this to the homopolymer score exactly.
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
    # Weighted across the mixture: a copolymer half anionic carries half the
    # complexation penalty. A single pendant at 1.0 gives the homopolymer term.
    for pendant, frac in cand.components:
        pre = _frac_prefix(frac)
        if pendant.charge == "zwitterion":
            total += 2.0 * frac
            reasons.append(f"{pre}zwitterionic: binds water strongly while carrying no net "
                           "charge, so it suppresses aggregation without electrostatic binding "
                           "to the protein")
        elif pendant.charge in ("anionic", "cationic"):
            q = goal.net_charge
            opposite = (q is not None and
                        ((q > 1 and pendant.charge == "anionic") or
                         (q < -1 and pendant.charge == "cationic")))
            if opposite:
                # Known to be wrong for THIS protein, not merely unhelpful.
                total -= 4.0 * frac
                risks.append(
                    f"{pre}{pendant.charge} against a protein carrying {q:+.1f} net charge at "
                    f"pH {goal.ph:g}: it would bind electrostatically, which is complexation "
                    "rather than stabilisation")
            elif q is None:
                total -= 1.5 * frac
                risks.append(f"{pre}net {pendant.charge} — may bind the protein electrostatically; "
                             "the protein's charge was not supplied, so this could not be checked")
            else:
                total -= 0.5 * frac
                risks.append(f"{pre}net {pendant.charge}, same sign as the protein ({q:+.1f}), so "
                             "electrostatic binding is unlikely, but the charge still perturbs the "
                             "local ionic environment")

    # --- vitrification, only for a dried product ---------------------------
    est = cand.tg
    tg, judged = est["tg_c"], est["judged_tg_c"]
    what = est["source"] or "Tg"
    if goal.format == "lyophilised":
        target = goal.target_temp_c if goal.target_temp_c is not None else 25.0
        if judged is None:
            risks.append("no Tg available for this repeat unit, from the model or the backbone "
                         "table; the cake's glass transition must be measured by modulated DSC "
                         "before this can be judged")
        else:
            # Continuous in the margin, not stepped: water plasticises a real cake
            # by tens of degrees, so headroom above the storage temperature keeps
            # paying past the point where the dry polymer is merely glassy. The
            # margin is measured from the lower bound, not the point estimate --
            # see _tg_estimate for why.
            margin = judged - target
            total += max(-2.5, min(3.0, margin / 45.0))
            if margin >= 100:
                reasons.append(f"Tg about {tg:g} C ({what}) clears the {target:g} C target by "
                               f"{margin:g} C even at the low end of its uncertainty — ample "
                               "headroom for the drop once residual moisture plasticises the cake")
            elif margin >= 50:
                reasons.append(f"Tg about {tg:g} C ({what}) clears the {target:g} C target by "
                               f"{margin:g} C at the low end of its uncertainty — the matrix "
                               "should stay glassy, though moisture will erode that margin")
            elif margin > 0:
                risks.append(f"Tg about {tg:g} C ({what}) is only {margin:g} C above the "
                             f"{target:g} C target once its uncertainty is taken off; residual "
                             "moisture could plasticise it below storage temperature")
            else:
                risks.append(f"Tg about {tg:g} C ({what}) is not reliably above the {target:g} C "
                             "target; the matrix may not be a glass at storage temperature")
        if est["in_domain"] is False:
            risks.append(est["note"])
    elif judged is not None and tg is not None and tg < 0:
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
    # The backbone is the whole chain, so its caution is charged in full; a
    # pendant's caution is charged in proportion to how much of it is present.
    if cand.backbone.caution:
        total += cand.backbone.penalty
        risks.append(cand.backbone.caution)
    for pendant, frac in cand.components:
        if pendant.caution:
            total += pendant.penalty * frac
            risks.append(pendant.caution)

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


def evaluate(backbone: Backbone, components: Composition, goal: DesignGoal,
             units: int = SCREEN_UNITS) -> Candidate | None:
    """Build, screen and score one composition. Returns None if it will not assemble.

    The single entry point the exhaustive enumerator and the active-learning loop
    both go through, so a copolymer is judged by exactly the same screens as a
    homopolymer.
    """
    smiles = build_copolymer_chain(backbone, components, units)
    if not smiles:
        return None
    built_units = units if len(components) == 1 else sum(
        max(1, round(frac * units)) for _, frac in components)
    cand = Candidate(backbone, components, smiles, descriptors(smiles), _alerts(smiles),
                     _blended_tg(backbone, components), built_units)
    return score(cand, goal)


def _mechanism(cand: Candidate) -> str:
    pend = " ".join(p.mechanism for p, _ in cand.components)
    return f"{cand.backbone.mechanism} {pend}"


def candidate_dict(cand: Candidate, goal: DesignGoal, rank: int) -> dict:
    """The wire shape of a candidate. Homopolymer output is unchanged; a copolymer
    fills the same keys plus a `composition` breakdown."""
    comps = cand.components
    is_homo = len(comps) == 1
    charges = {p.charge for p, _ in comps}
    charge = comps[0][0].charge if is_homo else ("mixed" if len(charges) > 1 else charges.pop())
    dominant = cand.pendant

    if is_homo:
        pendant_label = comps[0][0].name
        ru = repeat_unit(cand.backbone, comps[0][0])
    else:
        pendant_label = " + ".join(f"{p.name.split(' (')[0]} {frac:g}" for p, frac in comps)
        ru = " ; ".join(repeat_unit(cand.backbone, p) for p, _ in comps)

    return {
        "rank": rank,
        "name": f"{cand.backbone.name} bearing {pendant_label}",
        "backbone": cand.backbone.name,
        "pendant": pendant_label,
        "composition": [
            {"pendant": p.name, "fraction": round(frac, 3),
             "repeat_unit_smiles": repeat_unit(cand.backbone, p), "charge": p.charge}
            for p, frac in comps
        ],
        "repeat_unit_smiles": ru,
        "screened_oligomer_smiles": cand.smiles,
        "screened_units": cand.units,
        # backbone_tg_c is the handbook value for the bare backbone, kept for
        # reference. tg_c is what the screen actually used.
        "backbone_tg_c": cand.backbone.tg_c,
        "tg_c": cand.tg["tg_c"],
        "tg_source": cand.tg["source"],
        "tg_judged_c": cand.tg["judged_tg_c"],
        "tg_model_spread_sd": cand.tg["spread_sd"],
        "tg_in_model_domain": cand.tg["in_domain"],
        "tg_note": cand.tg["note"],
        "charge": charge,
        "score": cand.score,
        "mechanism": _mechanism(cand),
        "supports": cand.reasons,
        "risks": cand.risks,
        "alerts_fired": cand.alerts,
        "descriptors": cand.descriptors,
        "suggested_experiments": experiments(cand, goal),
        # Handed to the existing screen, which is what actually judges it. A
        # copolymer has no single repeat unit, so the dominant motif is offered.
        "screen_as": {
            "repeat_unit": repeat_unit(cand.backbone, dominant),
            "end_group_a": "[*][H]",
            "end_group_b": "[*][H]",
        },
    }


def enumerate_candidates(goal: DesignGoal, limit: int = 12) -> list[dict]:
    """Every motif pair that assembles, screened, scored and ranked."""
    ranked: list[Candidate] = []
    for backbone in BACKBONES:
        for pendant in PENDANTS:
            cand = evaluate(backbone, [(pendant, 1.0)], goal)
            if cand is not None:
                ranked.append(cand)

    ranked.sort(key=lambda c: (-c.score, -c.descriptors.get("oh_density", 0.0),
                               c.backbone.key, c.pendant.key))
    return [candidate_dict(c, goal, rank) for rank, c in enumerate(ranked[:limit], 1)]


LIMITS = (
    "Candidates are enumerated from a curated motif table, not invented, and each is built "
    "and screened with the same structural alerts and rule table as any other excipient here. "
    "The ranking is a triage ordering for laboratory work — it is NOT a prediction that a "
    "candidate will stabilise this protein. No molecular dynamics, no free-energy or "
    "preferential-interaction calculation and no Tm prediction was performed for this ranking. "
    "A candidate queued for simulation gets an OpenMM preferential-interaction run against the "
    "biologic's structure, reported on its card; that is still not a Tm, and still grade D. Tg is a HOMOPOLYMER value either predicted "
    "for the repeat unit from experimental data (scaffold-split error about 36 C, and each "
    "candidate reports whether it fell inside the model's domain) or taken from the backbone's "
    "handbook value; neither is the Tg of a real copolymer or cake, which depends on composition, "
    "moisture and processing and has to be measured by modulated DSC. Every candidate is grade D "
    "and 'Data gap: test' until laboratory data "
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
