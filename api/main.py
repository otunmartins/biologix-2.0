"""
Excipient Screen API — wraps the PydanticAI agent behind a single endpoint
that the Next.js frontend calls.

What's real: PubChem identity resolution, RDKit structural alerts, a small
protein-liability rule table.

What's also real: regulatory precedent, from the FDA Inactive Ingredient
Database and openFDA's substance registry, and the protein scan is weighted by
solvent accessibility when a structure identifier is supplied.

What's simplified: no mutagenicity model, exposure margins for residual impurities
only (against the generic ICH M7 benchmark), and no compatibility simulation. See root README.

Run standalone:
  pip install -r requirements.txt
  export ANTHROPIC_API_KEY=sk-ant-...
  uvicorn main:app --reload --port 8000
"""

import functools
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from itertools import product
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent, ModelRetry, RunContext, Tool, capture_run_messages
from pydantic_ai.exceptions import UserError
from rdkit import Chem

import accessibility
import active
import candidates
import db
import depict
import orchestrator
import design
import exposure
import history
import interactions
import calibration
import conditions
import measurements
import tg_model
from design import DesignGoal
from exposure import ExposureInputs
import polymer
from polymer import PolymerSpec
import precedent
import profile
import results
import runpod
import stats
import structures
import users

# Uvicorn's logger, so these land in `docker compose logs api`. The detail of a
# server-side failure goes here; the user is told what happened in plain words.
log = logging.getLogger("uvicorn.error")

UNAVAILABLE = "the assistant is not available right now; try again later"
NO_GOAL = ("could not read a design goal from that description; try rephrasing it, "
           "or fill in the form instead")

# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class EndpointResult(BaseModel):
    endpoint: str
    verdict: Literal[
        "Precedented", "Supported without precedent", "Data gap: test", "Alert: avoid"
    ]
    evidence_grade: Literal["A", "B", "C", "D", "E"]
    rationale: str
    sources: list[str] = Field(default_factory=list)


class LiabilityFlag(BaseModel):
    residue: str
    reaction_class: str
    severity: Literal["low", "moderate", "high"]
    mitigation: str
    # Filled verbatim from the tool. "not modelled" when no structure was given.
    accessibility: str = "not modelled"
    # "excipient", or the residual impurity whose alert produced this flag.
    source: str = "excipient"


# Stage 1 landed: precedent.py reads the FDA Inactive Ingredient Database and
# openFDA's substance registry. Set this to False to put the system back into
# the pre-Stage-1 posture — a blanket ban on grade A and "Precedented" — which
# is what you want if the lookup is ever found to be misreporting.
PRECEDENT_LOOKUP_AVAILABLE = True


@dataclass
class ScreenDeps:
    """What the precedent tool actually returned during this one run.

    The evidence gate has to be answerable per request, not per process: "may
    this dossier claim precedent?" depends entirely on whether the tool was
    called for THIS excipient and route and came back with a route match. The
    tool writes here, the output validator reads here, and nothing the model
    says can reach it.
    """

    precedent_calls: list[dict] = field(default_factory=list)
    # Same idea for structure: what resolve_identity actually screened this run
    # sets the ceiling on every structure-derived grade.
    identity_calls: list[dict] = field(default_factory=list)
    # A user-described polymer, from the request. When present, resolve_identity
    # screens this instead of the surrogate table or PubChem.
    polymer: PolymerSpec | None = None
    # Impurity exposure margins, computed from the request before the run.
    exposure: dict | None = None
    # Every time the evidence gate sent a dossier back, and why. A run that runs
    # out of retries fails with only "exceeded maximum retries"; this is the why.
    gate_rejections: list[str] = field(default_factory=list)

    def structure_ceiling(self) -> tuple[str | None, str]:
        """(best grade any structure-derived endpoint may carry, basis).

        The strictest ceiling across every identity call wins, so resolving a
        second name cannot buy back a grade the first one ruled out."""
        worst, basis = None, ""
        for call in self.identity_calls:
            grade = call.get("max_structure_grade")
            if grade and (worst is None or grade > worst):
                worst, basis = grade, call.get("structure_basis", "")
            elif not basis:
                basis = call.get("structure_basis", "")
        return worst, basis

    def impurity_sources(self) -> list[tuple[str, str]]:
        """(label, SMILES) for every residual impurity that resolved."""
        out = []
        for call in self.identity_calls:
            for imp in call.get("impurities", []):
                if imp.get("resolved"):
                    level = f" ({imp['level']})" if imp.get("level") else ""
                    out.append((f"residual {imp['name']}{level}", imp["smiles"]))
        return out

    def route_matched(self) -> bool:
        levels = {call.get("precedent_level") for call in self.precedent_calls}
        # Any call that found the concentration above the record vetoes the
        # rest, so re-asking without a concentration can't buy the A back.
        return "route_match" in levels and "route_match_above_record" not in levels

    def summary(self) -> str:
        if not self.precedent_calls:
            return "the precedent tool was never called"
        return "; ".join(
            f"{c.get('excipient_as_asked', '?')} at "
            f"{c.get('route_matched', '?')}: {c.get('precedent_level', '?')}"
            for c in self.precedent_calls
        )


class Dossier(BaseModel):
    excipient: str
    protein: str
    route: str
    # Bounded deliberately. Left open, the model writes the entire analysis into
    # the summary, runs out of output tokens part-way through the JSON, and the
    # endpoints array never arrives — which surfaces as a schema error about a
    # missing field rather than as the truncation it actually is. The detail
    # belongs in each endpoint's rationale, where the reader can act on it.
    summary: str = Field(max_length=2000)
    endpoints: list[EndpointResult]
    liabilities: list[LiabilityFlag]
    needs_testing: bool
    # Set by the system from what resolve_identity returned, never by the model:
    # "pubchem", "polymer_description", "surrogate" or "unresolved".
    structure_basis: str = Field(default="", description="Leave empty; the system fills this in.")
    # System-filled too: the SMILES resolve_identity actually screened, so the
    # structure drawn next to the dossier is the one the alerts ran on.
    structure_smiles: str = Field(default="", description="Leave empty; the system fills this in.")
    # Likewise system-filled: the impurity exposure margins exactly as computed.
    exposure: dict | None = Field(default=None, description="Leave null; the system fills this in.")
    # The id of this run in the user's history, or null if it could not be saved.
    history_id: str | None = Field(default=None, description="Leave null; the system fills this in.")

    def overclaims_precedent(self) -> list[str]:
        """Endpoints asserting precedent, which only a tool result can support."""
        return [
            e.endpoint
            for e in self.endpoints
            if e.evidence_grade == "A" or e.verdict == "Precedented"
        ]

    @model_validator(mode="after")
    def enforce_evidence_floor(self):
        """Hard guard, not a prompt instruction. A ValueError here is handed back
        to the model by PydanticAI as a retry, so it corrects itself.

        Since Stage 1 the per-run check lives in the agent's output validator,
        which can see what the precedent tool returned; this stays as the kill
        switch for when the lookup is turned off entirely."""
        if not PRECEDENT_LOOKUP_AVAILABLE:
            overclaimed = self.overclaims_precedent()
            if overclaimed:
                raise ValueError(
                    f"Endpoints {overclaimed} claim regulatory precedent, but the precedent "
                    "lookup is disabled on this deployment. Precedent cannot come from your "
                    "own recollection: regrade these as 'Data gap: test' at grade C, D, or E."
                )

        # Derived, never taken from the model: below grade B, or any high-severity liability.
        self.needs_testing = any(
            e.evidence_grade not in ("A", "B") for e in self.endpoints
        ) or any(l.severity == "high" for l in self.liabilities)
        return self


class DesignRequest(BaseModel):
    """Either describe the problem in your own words, or hand over a parsed goal.

    goal wins when both are given, so a caller that already knows the numbers
    never pays for a model round trip to restate them.
    """

    prompt: str = Field(default="", max_length=4000)
    goal: DesignGoal | None = None
    limit: int = Field(default=12, ge=1, le=40)


class IterateRequest(BaseModel):
    """Run one active-learning iteration. Start a campaign by giving a goal or a
    prompt; continue one by giving its campaign_id (the goal is read from the store).
    """

    campaign_id: str | None = None
    prompt: str = Field(default="", max_length=4000)
    goal: DesignGoal | None = None
    batch_size: int = Field(default=8, ge=1, le=20)


# A PDB ID (digit first, 4 characters) or a UniProt accession (6 or 10
# characters, letter first). Checked at the door, so a typo is a 422 now rather
# than a failed simulation an hour later.
STRUCTURE_ID_RE = re.compile(
    r"^(?:[0-9][A-Za-z0-9]{3}|[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})$")


def _clean_structure_id(v: str) -> str:
    v = (v or "").strip().upper()
    if v and not STRUCTURE_ID_RE.match(v):
        raise ValueError("not a PDB ID (e.g. 1IGT) or UniProt accession (e.g. P01857)")
    return v


class QueueRequest(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=200)
    # The target biologic to simulate against. Optional when the campaign's goal
    # already names one; required (422 otherwise) when it does not.
    structure_id: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def _structure(self):
        self.structure_id = _clean_structure_id(self.structure_id)
        return self


class ScreenRequest(BaseModel):
    prompt: str
    # Optional structured description of a polymeric excipient. Validated here,
    # so a bad SMILES is a 422 at the door rather than a failed agent run.
    polymer: PolymerSpec | None = None
    # Dose volume, interval and duration, for impurity exposure margins.
    exposure: ExposureInputs | None = None
    # The form exactly as the user filled it, kept only for their history so
    # "open in workspace" can refill it. Never read by the screen itself.
    form: dict | None = None
    # Set when a designed candidate was handed over with "Screen this candidate":
    # the screen is then reported with the designer's work, not as an excipient
    # screen. A label only; the screen itself never reads it.
    candidate_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _form_is_small(self):
        if self.form is not None and len(json.dumps(self.form)) > 20_000:
            raise ValueError("form snapshot is too large")
        return self


# ---------------------------------------------------------------------------
# Tools — deterministic. The agent orchestrates and narrates only.
# ---------------------------------------------------------------------------

PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"

# PubChem renamed its SMILES properties in 2025 (CanonicalSMILES -> ConnectivitySMILES,
# IsomericSMILES -> SMILES). Older mirrors still answer to the old names, so try both.
PROPERTY_SETS = ("SMILES,InChIKey,IUPACName", "CanonicalSMILES,InChIKey,IUPACName")


# Polymeric and mixture excipients have no single PubChem CID — polysorbates,
# poloxamers and PEGs all 404 on a name lookup, and they're half of what anyone
# will actually type in. The design doc's answer is to predict on a short
# repeat-unit surrogate and tag it as surrogate-based, so that's what these are:
# 3-mer stand-ins that carry the reactive features (polyether chain, ester) but
# not the real chain length or the polydispersity. Grade D at best, never higher.
SURROGATES = {
    "polysorbate 80": (
        "CCCCCCCCC=CCCCCCCCC(=O)OCCOCCOCCO",
        "oleate ester on a triethylene glycol (PEG 3-mer) chain, standing in for the sorbitan polyethoxylate",
    ),
    "polysorbate 20": (
        "CCCCCCCCCCCC(=O)OCCOCCOCCO",
        "laurate ester on a triethylene glycol (PEG 3-mer) chain",
    ),
    "poloxamer 188": (
        "OCCOCCOC(C)COCCOCCO",
        "PEO-PPO-PEO fragment, three repeat units per block",
    ),
    "polyethylene glycol": ("OCCOCCOCCO", "triethylene glycol (PEG 3-mer)"),
    "povidone": ("CCN1CCCC1=O", "N-ethylpyrrolidone, the PVP repeat unit capped"),
    "polyvinyl alcohol": ("CC(O)CC(O)CC(O)C", "vinyl alcohol 3-mer"),
    # Cellulosics and dextran are glucose polymers. Their anomeric carbons are
    # glycosidically linked, so the surrogate is capped as a methyl glycoside:
    # drawing a free anomeric OH would fire the reducing-sugar alert for the
    # whole polymer when in reality only the single chain end is reducing.
    "hypromellose": (
        "COC1OC(CO)C(OC)C(OCC(C)O)C1O",
        "methyl/hydroxypropyl-substituted glucose unit, anomeric position capped",
    ),
    "carboxymethylcellulose": (
        "COC1OC(CO)C(OCC(=O)O)C(O)C1O",
        "carboxymethyl-substituted glucose unit, anomeric position capped",
    ),
    "dextran": (
        "COC1OC(CO)C(O)C(O)C1O",
        "glucose unit, anomeric position capped; note the single reducing end per "
        "chain is NOT represented, so glycation potential is understated",
    ),
}

# Grade numbers (PEG 3350, Poloxamer 407, Macrogol 4000) are molecular-weight
# labels, not different chemistry. Normalising them means one table entry covers
# a whole family instead of needing a row per grade.
POLYMER_ALIASES = [
    (r"^(?:polysorbate|tween)\s*80\b", "polysorbate 80"),
    (r"^(?:polysorbate|tween)\s*(?:20|21|40|60|65|85)\b", "polysorbate 20"),
    (r"^poloxamer\b", "poloxamer 188"),
    (r"^(?:peg|macrogol|polyethylene\s*glycol|polyoxyethylene)\b", "polyethylene glycol"),
    (r"^(?:pvp|povidone|polyvinylpyrrolidone|kollidon)\b", "povidone"),
    (r"^(?:pva|polyvinyl\s*alcohol)\b", "polyvinyl alcohol"),
    (r"^(?:hpmc|hypromellose|hydroxypropyl\s*methylcellulose)\b", "hypromellose"),
    (r"^(?:cmc|carboxymethylcellulose|carmellose)\b", "carboxymethylcellulose"),
    (r"^(?:sodium\s+)?carboxymethylcellulose\b", "carboxymethylcellulose"),
    (r"^dextran\b", "dextran"),
    (r"^9005-65-6$", "polysorbate 80"),
    (r"^9005-64-5$", "polysorbate 20"),
]


def _surrogate_for(raw: str):
    """Match an excipient name to a repeat-unit surrogate, ignoring grade numbers."""
    name = re.sub(r"\s+", " ", raw.strip().lower())
    if name in SURROGATES:
        return SURROGATES[name]
    for pattern, key in POLYMER_ALIASES:
        if re.match(pattern, name):
            return SURROGATES[key]
    return None


def resolve_identity(name_or_smiles: str) -> dict:
    """Resolve an excipient name, CAS number, or SMILES to canonical SMILES
    and InChIKey via PubChem's free REST API. Polymeric excipients fall back to
    a repeat-unit surrogate, flagged as such."""
    raw = name_or_smiles.strip()

    # Check the surrogate table first — these names 404 on PubChem anyway.
    hit = _surrogate_for(raw)
    if hit:
        smiles, note = hit
        return {
            "resolved": True,
            "surrogate": True,
            "structure_basis": "surrogate",
            "max_structure_grade": "D",
            "smiles": smiles,
            "inchikey": None,
            "source": "local surrogate table (polymeric excipient — no single PubChem CID)",
            "note": (
                f"SURROGATE, not the real molecule: {note}. Every structure-derived "
                "finding is a surrogate prediction — grade D, and say so in the rationale."
            ),
        }

    ident = quote(raw, safe="")
    for namespace in ("name", "smiles"):
        for props in PROPERTY_SETS:
            try:
                r = httpx.get(
                    f"{PUBCHEM}/{namespace}/{ident}/property/{props}/JSON",
                    timeout=15,
                )
                if r.status_code == 404:
                    break  # definitive miss in this namespace; a retry won't help
                if r.status_code != 200:
                    continue
                row = r.json()["PropertyTable"]["Properties"][0]
                smiles = (
                    row.get("SMILES")
                    or row.get("ConnectivitySMILES")
                    or row.get("CanonicalSMILES")
                )
                if not smiles:
                    continue
                return {
                    "resolved": True,
                    "surrogate": False,
                    "structure_basis": "pubchem",
                    "max_structure_grade": None,
                    "smiles": smiles,
                    "inchikey": row.get("InChIKey"),
                    "iupac_name": row.get("IUPACName"),
                    "cid": row.get("CID"),
                    "source": f"PubChem PUG REST ({namespace} lookup)",
                }
            except Exception:
                continue
    return {
        "resolved": False,
        "structure_basis": "unresolved",
        "max_structure_grade": "E",
        "note": (
            "Could not resolve via PubChem — treat as unidentified and grade E on every "
            "endpoint. Do not guess a structure. Polymers and mixtures often have no single "
            "PubChem record; tell the user they can re-run with the repeat-unit SMILES "
            "instead of a trade name, which this tool accepts directly."
        ),
    }


def _fired(smiles: str) -> list[str]:
    return [a["alert"] for a in structural_alerts(smiles) if a["found"]]


def describe_polymer(name: str, spec: PolymerSpec, margins: dict | None = None) -> dict:
    """Screen a user-described polymer instead of the surrogate table.

    A description may refine the surrogate, never silently drop an alert it
    fired: if this name has a surrogate and the described chain loses one of its
    alerts, the description is reported as conflicting and the grade ceiling
    stays at D. Otherwise the structure is the user's own chemistry, an
    in-domain prediction, and the ceiling is C.
    """
    chain = polymer.describe(spec)
    described = _fired(chain["smiles"])

    check = None
    ceiling = "C"
    hit = _surrogate_for(name)
    if hit:
        expected = _fired(hit[0])
        dropped = [a for a in expected if a not in described]
        check = {"surrogate_alerts": expected, "dropped_by_description": dropped}
        if dropped:
            ceiling = "D"

    impurities = []
    for imp in spec.impurities:
        r = resolve_identity(imp.name)
        entry = {"name": imp.name, "level": imp.level, "resolved": r.get("resolved", False)}
        if entry["resolved"]:
            entry["smiles"] = r["smiles"]
            entry["alerts"] = _fired(r["smiles"])
            entry["source"] = r.get("source")
        impurities.append(entry)

    note = (
        f"USER-DESCRIBED POLYMER. {chain['limits']} Structure-derived endpoints may be graded C at "
        "best (in-domain prediction on the described chemistry) — never A or B — and the rationale "
        "must say the structure is the user's description."
    )
    if check and check["dropped_by_description"]:
        note = (
            f"DESCRIPTION CONFLICT: the described chain does not fire {check['dropped_by_description']}, "
            f"which the known {name} chemistry does. Treat the description as unreliable: cap every "
            "structure-derived endpoint at grade D and say so in the summary. " + chain["limits"]
        )
    if margins:
        note += (
            " exposure gives each impurity's intake against the ICH M7 acceptable intake for this "
            "treatment duration. Quote ug_per_dosing_day, acceptable_intake_ug_per_day and margin "
            "verbatim, and the basis caveat with them. status 'above' means the specification allows "
            "more than the benchmark: verdict 'Data gap: test' — lot data or a compound-specific "
            "assessment is needed. 'not_computed' means say why, from its reason field."
        )
    if impurities:
        note += (
            " Each resolved residual impurity was screened as its own molecule; report every one "
            "whose alerts fired, with its level quoted verbatim. The level is NOT used to scale any "
            "severity; its only use is the exposure margin, when one was computed."
        )

    return {
        "resolved": True,
        "surrogate": False,
        "structure_basis": "polymer_description",
        "max_structure_grade": ceiling,
        "smiles": chain["smiles"],
        "inchikey": None,
        "source": "user polymer description (repeat unit + end groups)",
        "repeat_units_screened": chain["repeat_units_screened"],
        "dp": chain["dp"],
        "mn_estimate": chain["mn_estimate"],
        "description_alerts": described,
        "surrogate_check": check,
        "impurities": impurities,
        "exposure": margins,
        "note": note,
    }


def resolve_identity_tool(ctx: RunContext[ScreenDeps], name_or_smiles: str) -> dict:
    result = (
        describe_polymer(name_or_smiles, ctx.deps.polymer, ctx.deps.exposure)
        if ctx.deps.polymer
        else resolve_identity(name_or_smiles)
    )
    # Recorded before the model sees it; the output validator reads the ceiling
    # from here, not from anything the dossier says about itself.
    ctx.deps.identity_calls.append(result)
    return result


resolve_identity_tool.__doc__ = (
    (resolve_identity.__doc__ or "")
    + "\n\nIf the user supplied a polymer description with this request, it is used instead: "
    "the result carries the described chain's SMILES, max_structure_grade, and any residual "
    "impurities screened as separate molecules."
)


def structural_alerts(smiles: str) -> list[dict]:
    """Flag a small set of reactive-group structural alerts using RDKit SMARTS matching."""
    alerts = {
        "Michael acceptor (acrylate/acrylamide)": "[CX3]=[CX3][CX3]=O",
        "Epoxide": "[OX2]1[CX4][CX4]1",
        # H1,H2 not H1: formaldehyde is CH2=O and was missed by [CX3H1]=O, which
        # is a poor look for a tool that flags Lys-reactive carbonyls. Ketones
        # (no H on the carbonyl carbon) still correctly do not match.
        "Aldehyde": "[CX3;H1,H2]=O",
        "Organomercury (thiol-reactive)": "[Hg]",
        "Maleimide (thiol-reactive)": "O=C1[#6]=[#6]C(=O)[NX3]1",
        "Peroxide": "[OX2][OX2]",
        # A fresh polyether has no peroxide group — it forms one on storage.
        # Matching only [OX2][OX2] would miss the single most common real-world
        # excipient liability there is (polysorbate/PEG oxidising Met and Trp),
        # so the oxyethylene repeat itself is the alert.
        "Polyether chain (autoxidises to peroxides on storage)": "[OX2][CX4H2][CX4H2][OX2]",
        "Hydrolysable ester": "[CX3](=O)[OX2][CX4]",
        # Reducing sugars are drawn by PubChem as the cyclic hemiacetal, which has
        # no free [CX3H1]=O — so the plain aldehyde alert never fires for lactose,
        # maltose or glucose, and glycation of Lys would be missed entirely. Match
        # the anomeric carbon instead: ring O, ring CH, free OH. Sucrose and
        # trehalose have both anomeric carbons tied up glycosidically and so do
        # not match, which is exactly the distinction a formulator cares about.
        "Reducing sugar (cyclic hemiacetal, opens to an aldehyde)": "[OX2;R][CX4;R;H1][OX2H1]",
    }
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return [{"alert": "invalid SMILES — cannot check structural alerts", "found": False}]
    hits = []
    for name, smarts in alerts.items():
        patt = Chem.MolFromSmarts(smarts)
        found = bool(patt) and mol.HasSubstructMatch(patt)
        hits.append({"alert": name, "found": found})
    return hits


# ---------------------------------------------------------------------------
# Published alert catalogs — breadth beyond the hand-written SMARTS above.
# ---------------------------------------------------------------------------
#
# RDKit ships several published filter sets. Most of them are actively harmful
# here, because they were built to triage drug-discovery SCREENING LIBRARIES,
# and excipients are a different population: small, polar, polyol-rich, often
# long-chain surfactants. Measured false-positive rate on a 19-excipient benign
# panel (sucrose, trehalose, mannitol, glycine, histidine, polysorbates, PEG...):
#
#   CHEMBL_SureChEMBL    5%   <- used
#   CHEMBL_Inpharmatica 11%   <- used
#   ZINC                16%
#   CHEMBL_Glaxo        16%
#   NIH                 26%   flags SUCROSE and TREHALOSE (gte_7_aliphatic_OH)
#   CHEMBL_BMS          26%   same
#   BRENK               32%   flags polysorbate + arginine (Aliphatic_long_chain)
#   CHEMBL_Dundee       32%   same
#   CHEMBL_LINT         47%   flags every amino-acid excipient for being one
#   CHEMBL_MLSMR        63%   flags sucrose/trehalose as acetals
#   PAINS                0%   no hits either way — assay interference, not reactivity
#
# Flagging sucrose and trehalose — the two canonical protein stabilisers — is
# worse than no screen at all, so the noisy catalogs stay out. Do not add them
# back without re-running the benchmark.
ADVISORY_CATALOGS = ("CHEMBL_SureChEMBL", "CHEMBL_Inpharmatica")

_catalog = None


def _get_catalog():
    global _catalog
    if _catalog is None:
        from rdkit.Chem import FilterCatalog
        from rdkit.Chem.FilterCatalog import FilterCatalogParams

        params = FilterCatalogParams()
        for name in ADVISORY_CATALOGS:
            params.AddCatalog(getattr(FilterCatalogParams.FilterCatalogs, name))
        _catalog = FilterCatalog.FilterCatalog(params)
    return _catalog


def published_alert_screen(smiles: str) -> dict:
    """Secondary breadth screen against published substructure filter sets
    (SureChEMBL and Inpharmatica, as shipped with RDKit).

    ADVISORY ONLY. These catalogs are not excipient-specific and not
    protein-specific: a hit means a published screening filter objects to some
    substructure, not that the excipient will react with this protein. Report
    hits at grade D as something to look at, never as a liability, and never let
    one raise a severity on its own.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {"usable": False, "hits": [], "note": "invalid SMILES"}

    hits = []
    for match in _get_catalog().GetMatches(mol):
        try:
            catalog = match.GetProp("Scope")
        except Exception:
            catalog = "published filter set"
        hits.append({"alert": match.GetDescription(), "catalog": catalog})

    return {
        "usable": True,
        "hits": hits,
        "note": (
            "Advisory only. Generic screening-library filters, not excipient- or "
            "protein-specific. Grade D at best; a hit is a prompt to look, not a finding. "
            "Long-chain and polyol hits in particular are usually irrelevant for an excipient."
        ),
    }


RULE_TABLE = [
    ("Peroxide", "Met", "oxidation", "high",
     "Use a low-peroxide excipient grade; consider methionine as a scavenger"),
    ("Peroxide", "Trp", "oxidation", "moderate",
     "Use a low-peroxide excipient grade"),
    ("Aldehyde", "Lys", "glycation / Schiff base formation", "moderate",
     "Avoid reducing-sugar-derived excipients; control storage temperature"),
    ("Michael acceptor (acrylate/acrylamide)", "Cys", "Michael addition", "high",
     "Avoid this excipient class if an alternative exists; if not, monitor by peptide mapping"),
    ("Michael acceptor (acrylate/acrylamide)", "Lys", "Michael addition", "moderate",
     "Monitor by peptide mapping"),
    ("Epoxide", "His", "alkylation", "moderate",
     "Confirm the residual-monomer specification is low"),
    ("Polyether chain (autoxidises to peroxides on storage)", "Met",
     "peroxide-mediated oxidation on storage", "high",
     "Specify a peroxide limit on incoming material and use a low-peroxide grade; "
     "consider methionine as a sacrificial scavenger"),
    ("Polyether chain (autoxidises to peroxides on storage)", "Trp",
     "peroxide-mediated oxidation on storage", "moderate",
     "Use a low-peroxide grade; protect from light and headspace oxygen"),
    ("Hydrolysable ester", "Asn",
     "ester hydrolysis shifts local pH, promoting deamidation", "moderate",
     "Buffer adequately and track pH and free fatty acid across the stability study"),
    ("Reducing sugar (cyclic hemiacetal, opens to an aldehyde)", "Lys",
     "glycation / Maillard: Schiff base then Amadori rearrangement", "high",
     "Switch to a non-reducing sugar (sucrose or trehalose); if unavoidable, hold at 2-8 C "
     "and monitor glycation by intact mass or boronate affinity chromatography"),
    ("Reducing sugar (cyclic hemiacetal, opens to an aldehyde)", "His",
     "glycation at the imidazole nitrogen", "low",
     "Covered by the same mitigation as the Lys glycation flag"),
    ("Organomercury (thiol-reactive)", "Cys",
     "mercaptide formation at free thiols", "high",
     "Avoid entirely if the protein carries an unpaired cysteine; use a phenolic preservative "
     "instead and confirm free-thiol content before and after"),
    ("Maleimide (thiol-reactive)", "Cys",
     "Michael addition at free thiols", "high",
     "Avoid unless deliberate conjugation is intended; confirm free-thiol content"),
]


THREE_TO_ONE = {"Met": "M", "Trp": "W", "Lys": "K", "Cys": "C", "His": "H", "Asn": "N"}

# A liability on a wholly buried residue is downgraded one step, never deleted:
# a static structure doesn't capture thermal breathing, partial unfolding, or
# what happens at the air-liquid interface during shaking.
DOWNGRADE = {"high": "moderate", "moderate": "low", "low": "low"}


def protein_liability_scan(
    protein_sequence: str,
    excipient_smiles: str,
    structure_id: str = "",
    extra_sources: list[tuple[str, str]] | None = None,
) -> dict:
    """Residue-level liability flags for an excipient against a protein.

    Pass the SMILES that resolve_identity returned. The structural alerts are
    re-derived here rather than handed in, so the reactive groups cannot be lost
    in transit.

    structure_id is optional. Give it a UniProt accession (e.g. P01857) to use the
    AlphaFold model, or a 4-character PDB ID (e.g. 1IGT) for an experimental
    structure, and each flag is weighted by relative solvent accessibility.
    Without it, the scan falls back to counting residue types in the sequence,
    which cannot tell an exposed Met from a buried one.
    """
    # Derived here, not passed in. This used to take the list of alert names and
    # trust the model to carry it over from the structural_alerts call, and the
    # model would occasionally hand back an empty list — which produced a
    # confident dossier with zero liabilities for an excipient that had just
    # tripped two alerts. A silently empty scan is the worst failure this tool
    # has, so the data no longer makes the round trip through the model.
    active_alerts = _fired(excipient_smiles)
    # Residual impurities are screened as their own molecules: residual ethylene
    # oxide is an epoxide whatever the polymer around it looks like.
    sources = [("excipient", active_alerts)] + [
        (label, _fired(smiles)) for label, smiles in (extra_sources or [])
    ]

    sequence = "".join(protein_sequence.split()).upper()

    access = None
    structure_note = "No structure supplied — flags are raw sequence counts, not accessibility-weighted."
    if structure_id.strip():
        try:
            access = accessibility.get_accessibility(structure_id)
            structure_note = f"Accessibility from {access['source']}, {access['n_chains']} chain(s)."
            if sequence and sequence != access["sequence"]:
                # No alignment is attempted — say so rather than quietly picking one.
                structure_note += (
                    f" NOTE: the pasted sequence ({len(sequence)} aa) differs from the "
                    f"structure's ({len(access['sequence'])} aa); accessibility is reported "
                    "for the structure, and the two were not aligned."
                )
        except Exception as e:
            structure_note = (
                f"Structure '{structure_id}' could not be used ({type(e).__name__}: {e}). "
                "Falling back to raw sequence counts — say so in the rationale."
            )

    flags = []
    for (source, alerts), (alert, residue, reaction, severity, mitigation) in product(
        sources, RULE_TABLE
    ):
        if alert not in alerts:
            continue
        one = THREE_TO_ONE.get(residue)
        if not one:
            continue

        if access:
            sites = accessibility.sites_for(access, one)
            if not sites:
                continue
            desc = accessibility.describe_sites(sites, access["predicted"])
            final_severity = DOWNGRADE[severity] if desc["all_buried"] else severity
            label = f"{residue} (x{desc['n_total']}, {desc['n_exposed']} exposed)"
            note = desc["note"]
            if desc["all_buried"]:
                note += f" — all buried, severity downgraded from {severity}"
        else:
            count = sequence.count(one)
            if count == 0:
                continue
            final_severity = severity
            label = f"{residue} (x{count} in sequence)"
            note = "not modelled — no structure supplied"

        flags.append(
            {
                "residue": label,
                "reaction_class": reaction,
                "severity": final_severity,
                "mitigation": mitigation,
                "accessibility": note,
                "source": source,
            }
        )

    return {
        "structure": structure_note,
        # Reported so the dossier can say which reactive groups drove the scan,
        # and so an empty scan is visibly an excipient with no matching alerts
        # rather than a scan that quietly ran on nothing.
        "alerts_applied": active_alerts,
        "impurity_alerts_applied": {label: alerts for label, alerts in sources[1:]},
        "flags": flags,
    }


def protein_liability_scan_tool(
    ctx: RunContext[ScreenDeps], protein_sequence: str, excipient_smiles: str, structure_id: str = ""
) -> dict:
    # Impurities come from what resolve_identity recorded this run, not from the
    # model, for the same reason the alerts are re-derived: a list the model has
    # to carry across is a list it can drop.
    return protein_liability_scan(
        protein_sequence, excipient_smiles, structure_id, ctx.deps.impurity_sources()
    )


protein_liability_scan_tool.__doc__ = (
    (protein_liability_scan.__doc__ or "")
    + "\n\nAny residual impurities from a polymer description are applied automatically; each "
    "flag's source field says which molecule produced it."
)


def regulatory_precedent(
    ctx: RunContext[ScreenDeps], excipient: str, route: str, concentration: str = ""
) -> dict:
    """Has this excipient been used in an approved drug product by this route?

    Reads the FDA Inactive Ingredient Database (route, dosage form and maximum
    potency on record) and openFDA's substance registry (UNII, and the CFR
    citations behind a GRAS affirmation).

    Pass the excipient name, CAS number or the trade name the user typed, and the
    route they asked about. This is a NAME lookup, so it is unaffected by whether
    the structure came back as a surrogate.

    concentration is the EXCIPIENT's concentration or amount exactly as the user
    wrote it ("0.02% w/v", "10 mg/mL", "5 mg per dose"), or "" if they gave none.
    Never pass the protein's dose here.
    """
    result = precedent.look_up(excipient, route, concentration)
    # Recorded before it reaches the model. The output validator reads this, and
    # nothing in the dossier can talk it into a claim the lookup did not make.
    ctx.deps.precedent_calls.append(result)
    return result


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a formulation-safety triage assistant for biologic excipients.

You NEVER invent hazard data, regulatory precedent, or structural findings. You only report what
the tools return, plus your own reasoning about severity, prioritisation, and phrasing.

Workflow:
1. Call resolve_identity on whatever excipient identifier the user gave you.
2. If resolved, call structural_alerts on the SMILES it returned.
3. Also call published_alert_screen on the same SMILES. Its hits are ADVISORY: report them in a
   separate endpoint ("Generic reactive-group screen") at grade D, say plainly that these are
   generic screening-library filters that are neither excipient- nor protein-specific, and never
   turn one into a protein liability or use one to raise a severity. If it returns no hits, say
   that no published filter set objected — not that the excipient is clean.
4. Call regulatory_precedent with the excipient name the user typed, the route they asked
   about, and the excipient concentration exactly as they wrote it ("" if they gave none — the
   protein dose is NOT an excipient concentration). This is a name lookup against the FDA Inactive Ingredient Database, so it works even
   when resolve_identity fell back to a surrogate, and it is the ONLY thing in this system that
   can see regulatory precedent. Report it as its own endpoint, named exactly
   "Regulatory precedent, <route>" — the structure grade ceiling below exempts that name and no other.
   Take the grade ceiling from the tool's max_grade field and the verdict from its interpretation
   field — do not raise either on your own reading of the data. Quote dosage forms, potencies and
   route names verbatim; never convert a unit or combine two of them. If it reports
   precedent_level "route_match" you may grade that endpoint A and call it "Precedented"; every
   other level means you may not, on any endpoint. State the coverage limits from the tool's note
   whenever they bear on the answer — above all, that the IID under-represents BLA biologics, so a
   missing row means "not found in the IID", never "never used".
5. If the user gave a protein sequence or a structure identifier, call protein_liability_scan with
   excipient_smiles set to the SMILES resolve_identity returned, and structure_id set to any
   UniProt accession or PDB ID the user mentioned (pass "" if they gave none).
   protein_sequence takes LITERAL AMINO ACIDS ONLY. An accession like P01857 or a PDB ID like
   1IGT goes in structure_id and nowhere else — putting one in protein_sequence makes the tool
   read it as a six-residue protein. If the user gave an identifier and no sequence, pass
   protein_sequence="".
   The tool re-derives the structural alerts from the SMILES itself; you do not pass them.
   It returns {"structure": ..., "alerts_applied": [...], "flags": [...]}. Copy each flag's fields
   into the dossier verbatim — including accessibility — and never recompute, round or reword a
   number. If flags is empty, say which alerts were applied and that none of them mapped to a
   residue in this protein.
   Report what the "structure" field says in your rationale: whether accessibility was modelled,
   which structure was used, and any sequence mismatch or fetch failure it mentions.
6. If identity resolution fails, grade every structure-derived endpoint E and say so plainly in
   the summary — do not guess a structure. The precedent endpoint still stands on its own: a name
   lookup does not need a structure.
7. resolve_identity returns max_structure_grade: the best grade ANY endpoint other than
   "Regulatory precedent, <route>" may carry. It is enforced in code. By structure_basis:
   - "surrogate": a short repeat-unit stand-in for a polymer, not the real excipient. Ceiling D.
     Name the surrogate in the rationale, and say in the summary that the real material's chain
     length, polydispersity and residual monomers were NOT assessed.
   - "polymer_description": the user described the chain (repeat unit, end groups, DP). Ceiling C,
     or D when the result reports a DESCRIPTION CONFLICT — then say in the summary that the
     description dropped an alert the known chemistry fires. Say the structure is the user's
     description, quote dp and mn_estimate when given, and state that polydispersity and branching
     were not modelled. For each residual impurity that resolved and fired an alert, add an
     endpoint "Residual <name>" (ceiling applies) quoting its level verbatim, and say its level was
     not used to scale severity. An impurity that did not resolve is a data gap: say so.
   - "unresolved": ceiling E.
   - "pubchem": no ceiling from structure.

Verdict rules — use ONLY these four words, never "safe":
  Precedented, Supported without precedent, Data gap: test, Alert: avoid.

Grade A and the verdict "Precedented" are reserved for what regulatory_precedent actually
returned. You have read the literature in training and will be tempted to assert precedent from
memory for the excipients you know well — polysorbate 80, sucrose, histidine. Do not. If the tool
did not report precedent_level "route_match" for this excipient at this route, that is a data gap,
however confident you feel. This is checked in code and a violation is handed straight back to you.

Set needs_testing=True whenever any endpoint is below grade B, or any liability has severity "high".

The summary is SHORT — three to six sentences, 2000 characters at the outside. It is an
orientation, not the report. Every piece of detail, every number and every caveat that belongs to
one endpoint goes in that endpoint's rationale, where someone can act on it. A summary that tries
to carry the whole analysis runs out of room before the endpoints are written and the dossier is
rejected as incomplete.

Within that budget, the summary must always say: precedent here comes from FDA records that an
excipient has appeared in an approved product at a route and potency, which is not a finding about
THIS protein at the user's concentration; and there is still no protein-compatibility simulation.
Exposure margins, when present, cover residual impurities only, against a generic ICH M7 benchmark —
never the excipient itself. Precedent plus structural alerts is a triage, not a safety assessment.
"""

DESIGN_PROMPT = """You extract a formulation goal from what the user wrote. That is your only job.

Fill the fields from their words and nothing else:
  protein   - what they are formulating, in their words
  route     - route of administration if stated, else ""
  target_temp_c      - the storage temperature they need to survive, in Celsius. "room
              temperature" is 25, "ambient" 25, "warehouse"/"tropical"/"Zone IV" 30.
              Leave null if they gave none - do NOT invent one.
  duration_months    - how long it must hold, null if unstated
  format    - "lyophilised" if they mention lyophilised, freeze-dried, dried, powder or a
              cake; otherwise "liquid"
  exposed_residues   - only residues they explicitly say are exposed or liable (Met, Trp,
              Cys, Lys, His, Asn). Empty list if they did not say. Never guess from the
              protein's name.
  ph        - the formulation pH if they state one (e.g. "pH 5.5"), else null
  salt_mm   - the salt (ionic strength) in mM NaCl-equivalent if they state it:
              "150 mM NaCl" is 150, "0.1 M salt" is 100, "PBS" or "saline" is 150,
              "no salt"/"salt-free" is 0. Null if they do not mention salt or a buffer
              that implies it.
  polymer_wv_percent - the polymer or excipient concentration in % w/v if they state
              one ("2% polymer" is 2, "20 mg/mL" is 2). Null otherwise.
  notes     - anything else that constrains the formulation, briefly
  structure_id       - a PDB ID (4 characters, starting with a digit, e.g. 1IGT) or a
              UniProt accession (e.g. P01857) ONLY if they wrote one. Never look one up
              from the protein's name; leave "" if none was given.

You do NOT suggest polymers, excipients or mechanisms. Something else does that, from a
curated table. Inventing a temperature or a residue here silently changes which candidates
are ranked, so leave a field empty rather than filling it with a plausible value."""

_design_agent: Agent | None = None


# Named, so the history can record which model produced each result.
SCREEN_MODEL = "anthropic:claude-sonnet-5"
DESIGN_MODEL = "anthropic:claude-sonnet-5"


def get_design_agent() -> Agent:
    global _design_agent
    if _design_agent is None:
        _design_agent = Agent(
            DESIGN_MODEL,
            output_type=DesignGoal,
            system_prompt=DESIGN_PROMPT,
            model_settings={"max_tokens": 1000},
            retries=2,
        )
    return _design_agent


_agent: Agent | None = None


def get_agent() -> Agent:
    """Built on first use, not at import. Constructing it eagerly means a missing
    ANTHROPIC_API_KEY takes down the whole container at startup — /health included —
    so a bad .env on the box looks like a crashloop instead of a clear error."""
    global _agent
    if _agent is None:
        _agent = Agent(
            SCREEN_MODEL,
            output_type=Dossier,
            deps_type=ScreenDeps,
            system_prompt=SYSTEM_PROMPT,
            tools=[
                # Registered under the plain names the model and the tests use;
                # the wrappers read and record the per-run deps.
                Tool(resolve_identity_tool, name="resolve_identity"),
                structural_alerts,
                published_alert_screen,
                regulatory_precedent,
                Tool(protein_liability_scan_tool, name="protein_liability_scan"),
            ],
            # A full dossier with five endpoints and a dozen liabilities is a
            # lot of JSON. The default cap truncated it mid-object, which the
            # schema then reported as a missing `endpoints` field.
            model_settings={"max_tokens": 16000},
            # Three, not two: there are now two independent ways for a dossier
            # to be sent back (the schema and the precedent gate), and a run
            # that trips one on its first attempt still deserves a real chance.
            retries=3,
        )
        _agent.output_validator(enforce_precedent_evidence)
    return _agent


def enforce_precedent_evidence(ctx: RunContext[ScreenDeps], dossier: Dossier) -> Dossier:
    """The Stage 1 evidence gate: precedent must have been looked up, not recalled.

    Before Stage 1 this was a blanket ban on grade A, because nothing in the
    system could see precedent. Now something can, so the rule sharpens rather
    than relaxes: a dossier may claim precedent exactly when the precedent tool
    returned a route match during THIS run. The check reads the recorded tool
    results, never the dossier's own account of them, so a confident rationale
    cannot argue its way past it.
    """
    overclaimed = dossier.overclaims_precedent()
    if overclaimed and not ctx.deps.route_matched():
        raise _rejected(ctx,
            f"Endpoints {overclaimed} are graded A or called 'Precedented', but the "
            f"regulatory_precedent tool did not report a route match ({ctx.deps.summary()}). "
            "Precedent cannot come from your own recollection, however familiar the excipient. "
            "Call regulatory_precedent for this excipient and route if you have not, and "
            "otherwise regrade these endpoints to the tool's max_grade with the verdict its "
            "interpretation field calls for."
        )

    ceiling, basis = ctx.deps.structure_ceiling()
    if ceiling:
        over = [
            f"{e.endpoint} ({e.evidence_grade})"
            for e in dossier.endpoints
            if e.evidence_grade < ceiling and not _is_precedent_endpoint(e.endpoint)
        ]
        if over:
            raise _rejected(ctx,
                f"Endpoints {over} are graded above {ceiling}, the ceiling resolve_identity set for a "
                f"{basis!r} structure. Only the endpoint named 'Regulatory precedent, <route>' is "
                f"exempt, because precedent is a name lookup. Regrade these to {ceiling} or below."
            )
    # Set from the tool record and the request, whatever the model wrote.
    dossier.structure_basis = basis
    # The first call that resolved to a structure is the excipient itself; later
    # ones, if any, are the model looking up something else by name.
    dossier.structure_smiles = next(
        (c["smiles"] for c in ctx.deps.identity_calls if c.get("resolved") and c.get("smiles")), "")
    dossier.exposure = ctx.deps.exposure
    return dossier


def _rejected(ctx: RunContext[ScreenDeps], reason: str) -> ModelRetry:
    """Note the rejection on the run's record, then hand it back to the model."""
    ctx.deps.gate_rejections.append(reason)
    return ModelRetry(reason)


def _is_precedent_endpoint(name: str) -> bool:
    return name.strip().lower().startswith("regulatory precedent")

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def _ensure_tables() -> str | None:
    """Create the sign-in and campaign tables. Returns why it could not, or None.

    Run at startup because the web app's first sign-in writes to `users` and
    `sessions`, and nothing else would have created them yet: every other request
    that touches the database needs a signed-in user first.
    """
    try:
        history.connect().close()  # users, then campaigns, then the history tables
        return None
    except Exception as e:
        return _scrub(f"{type(e).__name__}: {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Never fatal: a database that is down at boot must still leave /health up to
    # say so, and /health retries the same call on every hit until it succeeds.
    if err := _ensure_tables():
        print(f"warning: could not create tables at startup: {err}", flush=True)
    yield


app = FastAPI(title="Excipient Screen API", lifespan=lifespan)

# Kept for local dev (web on :3000, api on :8000 directly). In the deployed
# stack, Caddy puts both on one origin, so this middleware is mostly a
# convenience rather than a requirement — see root README.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("WEB_ORIGIN", "http://localhost:3000")],
    # Credentials because the session cookie is how the API knows who is asking
    # (users.py). Only safe with an explicit origin list, which this is.
    allow_credentials=True,
    # GET as well as POST since the campaign and queue views are reads.
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _scrub(message: str) -> str:
    """Strip credentials out of a connection error. /health is public, and
    psycopg errors can quote the connection string back."""
    message = re.sub(r"://[^@\s/]*@", "://***@", message)
    return re.sub(r"password=\S+", "password=***", message)


@app.get("/health")
def health():
    # model_configured is the first thing you want to know when the box is up
    # but every screen is failing; the precedent index is the second, since a
    # box that can't reach fda.gov quietly grades everything as a data gap.
    # tg_model is the third and fails the same quiet way: the pickle is a build
    # artefact and is not in the repo, so a box without it falls back to the
    # backbone handbook table and still answers every request.
    return {
        "ok": True,
        "model_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "precedent_lookup": PRECEDENT_LOOKUP_AVAILABLE,
        "precedent_index": precedent.index_status(),
        "tg_model": tg_model.available(),
        # Fourth thing to check, and the one that now fails loudest: campaigns and
        # measurements live in Postgres, so a box that cannot reach it answers
        # /health but fails every design iteration.
        "database": _database_status(),
    }


def _database_status() -> dict:
    """Reachable or not, and why not. Never raises — /health must always answer.

    Reachable means the tables exist too, since sign-in cannot work without them:
    if the database was down when the API started, this is where they get made.
    """
    if err := _ensure_tables():
        return {"reachable": False, "error": err}
    return {"reachable": True}


_render_budget = depict.RenderBudget()


@app.get("/structure.svg")
def structure_svg(
    request: Request,
    smiles: str = Query(..., min_length=1, max_length=depict.MAX_SMILES),
    w: int = Query(240, ge=depict.MIN_SIDE, le=depict.MAX_SIDE),
    h: int = Query(180, ge=depict.MIN_SIDE, le=depict.MAX_SIDE),
):
    """A 2D drawing of one molecule or repeat unit. Public on purpose; see depict.py."""
    # Caddy sets X-Forwarded-For to the real client and drops one sent by the
    # client; the direct address is Caddy's own, so it is only the fallback.
    client = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
              or (request.client.host if request.client else "unknown"))
    if not _render_budget.allow(client):
        raise HTTPException(status_code=429, detail="too many drawings; try again in a minute")
    drawing = depict.svg(smiles, w, h)
    if drawing is None:
        raise HTTPException(status_code=422, detail="not a SMILES RDKit can read")
    # A drawing never changes for the same input, so the browser may keep it.
    return Response(drawing, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=604800, immutable"})


@app.get("/structure/conformer")
def structure_conformer(
    request: Request,
    smiles: str = Query(..., min_length=1, max_length=depict.MAX_SMILES),
    user_id: int = Depends(users.current_user),
):
    """A 3D model of one molecule (a candidate's screened chain) for the 3D view.
    Signed-in, unlike the 2D drawing: embedding a long chain in 3D costs seconds,
    not milliseconds. Cached, and counted against the same per-client budget."""
    client = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
              or (request.client.host if request.client else "unknown"))
    if not _render_budget.allow(client):
        raise HTTPException(status_code=429, detail="too many drawings; try again in a minute")
    block = depict.conformer_molblock(smiles)
    if block is None:
        raise HTTPException(status_code=422, detail="could not build a 3D model of this structure")
    return Response(block, media_type="chemical/x-mdl-molfile",
                    headers={"Cache-Control": "private, max-age=604800"})


@app.post("/design")
async def design_candidates(req: DesignRequest, user_id: int = Depends(users.current_user)):
    """Rank polymer candidates for stabilising a biologic above fridge temperature.

    The model, if it is used at all, only reads the goal out of the user's words.
    Every candidate, its chemistry, its alerts and its ranking come from
    design.py deterministically - so this endpoint cannot invent a stabiliser.
    """
    goal = req.goal
    if goal is None:
        if not req.prompt.strip():
            raise HTTPException(status_code=422, detail="give either a prompt or a goal")
        try:
            agent = get_design_agent()
        except UserError as e:
            log.error("design agent not configured: %s", e)
            raise HTTPException(status_code=503, detail=UNAVAILABLE) from e
        try:
            goal = (await agent.run(req.prompt)).output
        except Exception as e:
            log.exception("design goal parse failed")
            raise HTTPException(status_code=502, detail=NO_GOAL) from e

    result = design.design(goal, req.limit)
    # Said once here as well as in every candidate, because this is the claim
    # most likely to be over-read: a ranking is not a prediction.
    result["verdict"] = "Data gap: test"
    result["max_grade"] = "D"
    return result


async def _resolve_goal(prompt: str, goal: DesignGoal | None) -> DesignGoal:
    """A parsed goal wins; otherwise the model reads one out of the prompt. Same
    contract as /design, so a caller pays for a model round trip only when it has
    not already done the parsing itself."""
    if goal is not None:
        return goal
    if not prompt.strip():
        raise HTTPException(status_code=422, detail="give either a prompt or a goal")
    try:
        agent = get_design_agent()
    except UserError as e:
        log.error("design agent not configured: %s", e)
        raise HTTPException(status_code=503, detail=UNAVAILABLE) from e
    try:
        return (await agent.run(prompt)).output
    except Exception as e:
        log.exception("design goal parse failed")
        raise HTTPException(status_code=502, detail=NO_GOAL) from e


@app.post("/design/iterate")
async def design_iterate(req: IterateRequest, user_id: int = Depends(users.current_user)):
    """One active-learning iteration over the copolymer space.

    Starts a campaign from a goal/prompt, or continues one by campaign_id, proposing
    a fresh batch that is screened and stored. Every candidate stays grade D — the
    loop optimises the transparent triage proxy, not stabilisation (see active.py).

    Every change lands in the user's history in the same transaction as the
    change itself (history.py): started, iterated, and reopened if the campaign
    had been ended with "New experiment".
    """
    conn = history.connect()
    try:
        if req.campaign_id:
            state = candidates.campaign_state(conn, req.campaign_id, owner_id=user_id)
            if state is None:
                raise HTTPException(status_code=404, detail="no such campaign")
            goal = DesignGoal(**state["goal"])
            campaign_id = req.campaign_id
            prior = [active.PriorCandidate(p["backbone_key"], p["components"], p["score"])
                     for p in candidates.prior_for(conn, campaign_id)]
        else:
            # Outside the transaction: this may be a model call, and a
            # transaction should not stay open across one.
            goal = await _resolve_goal(req.prompt, req.goal)
            campaign_id, prior = None, []

        proposal = active.propose_batch(goal, prior, k=req.batch_size)

        with db.tx(conn):
            if campaign_id is None:
                campaign_id = candidates.create_campaign(
                    conn, goal.model_dump(), owner_id=user_id, prompt=req.prompt.strip())
                history.log_event(conn, user_id, "campaign.started", "campaign", campaign_id, {
                    "prompt": req.prompt.strip(), "goal": goal.model_dump(),
                    # Which model read the goal out of the words, if one did.
                    "goal_parsed_by": DESIGN_MODEL if req.goal is None else None,
                })
            elif candidates.reopen_campaign(conn, campaign_id, owner_id=user_id):
                history.log_event(conn, user_id, "campaign.reopened", "campaign", campaign_id)

            iteration = candidates.next_iteration(conn, campaign_id)
            items = []
            for rank, cand in enumerate(proposal.candidates, 1):
                items.append({
                    "payload": design.candidate_dict(cand, goal, rank),
                    "backbone_key": cand.backbone.key,
                    "components": [[p.key, round(f, 2)] for p, f in cand.components],
                    "score": cand.score,
                })
            stored = candidates.add_candidates(conn, campaign_id, iteration, items)
            candidates.record_metrics(conn, campaign_id, iteration, proposal.metrics)
            history.log_event(conn, user_id, "campaign.iterated", "campaign", campaign_id, {
                "iteration": iteration, "n_candidates": len(stored), "metrics": proposal.metrics,
            })

        metrics_hist = candidates.metrics_history(conn, campaign_id)
        return {
            "campaign_id": campaign_id,
            "iteration": iteration,
            "goal": goal.model_dump(),
            "candidates": stored,
            "metrics": proposal.metrics,
            "metrics_history": metrics_hist,
            # The advisory campaign controller's read of where the run stands.
            "recommendation": orchestrator.recommend(metrics_hist),
            "queue_summary": candidates.queue_summary(conn, owner_id=user_id, campaign_id=campaign_id),
            "limits": active.ACTIVE_LIMITS,
            "orchestration": orchestrator.ORCHESTRATION_NOTE,
            "verdict": "Data gap: test",
            "max_grade": "D",
        }
    finally:
        conn.close()


@app.post("/design/queue")
def design_queue(req: QueueRequest, user_id: int = Depends(users.current_user)):
    """Send benchmarked candidates to the OpenMM simulation backlog. A position in
    the queue is a triage decision, not evidence — nothing here upgrades a grade."""
    conn = history.connect()
    try:
        structure_id = req.structure_id
        if not structure_id:
            # Fall back to the structure the campaign's goal names. Every campaign
            # the request touches must agree on one, or the caller must say which.
            goals = conn.execute(
                "SELECT DISTINCT cp.goal, cp.prompt FROM candidate c JOIN campaign cp "
                "ON cp.id=c.campaign_id WHERE c.id = ANY(%s) AND cp.owner_id=%s",
                (req.candidate_ids, user_id)).fetchall()
            parsed = [json.loads(g["goal"]) for g in goals]
            found = {(g.get("structure_id") or "").strip().upper() for g in parsed}
            # The biologic as the goal names it, or else as the user's prompt does.
            named = {((json.loads(g["goal"]).get("protein") or g["prompt"] or "")).strip()
                     for g in goals} - {""}
            if len(found) == 1 and "" not in found:
                structure_id = found.pop()
            elif len(found) == 1 and len(named) == 1:
                # The goal names the biologic but no structure: look it up rather
                # than asking. Nothing found still asks (below).
                try:
                    hit = structures.resolve(named.pop())
                except httpx.HTTPError:
                    hit = None
                if hit:
                    structure_id = hit["structure_id"]
            if not structure_id and goals:
                raise HTTPException(status_code=422, detail={
                    "code": "structure_required",
                    "message": "Give the biologic's PDB ID or UniProt accession: the simulation "
                               "runs the polymer against the protein you are stabilising."})
        # One simulation waiting or running per user, admins aside: each is hours
        # of paid GPU time, and sign-up is open.
        cap = None if users.is_admin(conn, user_id) else 1
        with db.tx(conn):
            try:
                queued = candidates.enqueue_rows(conn, req.candidate_ids, owner_id=user_id,
                                                 structure_id=structure_id, max_active=cap)
            except candidates.QueueFull as e:
                names = ", ".join(a["name"] or a["id"] for a in e.active)
                raise HTTPException(status_code=409, detail={
                    "code": "one_at_a_time",
                    "message": ("You can have one simulation waiting or running at a time"
                                + (f", and {names} already is." if names else
                                   "; queue a single candidate.")),
                    "active": e.active})
            if structure_id:
                # Remembered on each campaign whose goal named none, so the user is
                # asked (or it is looked up) once per campaign, not per candidate.
                for row in conn.execute(
                        "SELECT DISTINCT cp.id, cp.goal FROM candidate c JOIN campaign cp "
                        "ON cp.id=c.campaign_id WHERE c.id = ANY(%s) AND cp.owner_id=%s",
                        (req.candidate_ids, user_id)).fetchall():
                    g = json.loads(row["goal"])
                    if not g.get("structure_id"):
                        conn.execute("UPDATE campaign SET goal=%s WHERE id=%s",
                                     (json.dumps({**g, "structure_id": structure_id}), row["id"]))
            # Logged per campaign, and only for what was actually queued.
            by_campaign: dict[str, list[dict]] = {}
            for q in queued:
                by_campaign.setdefault(q["campaign_id"], []).append(
                    {"id": q["id"], "name": q["name"], "score": q["score"]})
            for cid, rows in by_campaign.items():
                history.log_event(conn, user_id, "candidate.queued", "campaign", cid,
                                  {"candidates": rows, "structure_id": structure_id})
        return {"queued": len(queued), "queue_summary": candidates.queue_summary(conn, owner_id=user_id)}
    finally:
        conn.close()


@app.post("/design/campaign/{campaign_id}/end")
def design_campaign_end(campaign_id: str, user_id: int = Depends(users.current_user)):
    """The "New experiment" button: close this campaign. It stays in the user's
    history and can be continued later, which reopens it. Idempotent."""
    conn = history.connect()
    try:
        with db.tx(conn):
            outcome = candidates.end_campaign(conn, campaign_id, owner_id=user_id)
            if outcome is None:
                raise HTTPException(status_code=404, detail="no such campaign")
            if outcome == "ended":
                history.log_event(conn, user_id, "campaign.ended", "campaign", campaign_id)
        return {"campaign_id": campaign_id, "ended": True, "already": outcome == "already"}
    finally:
        conn.close()


@app.get("/design/campaign/{campaign_id}")
def design_campaign(campaign_id: str, user_id: int = Depends(users.current_user)):
    conn = history.connect()
    try:
        state = candidates.campaign_state(conn, campaign_id, owner_id=user_id)
        if state is None:
            raise HTTPException(status_code=404, detail="no such campaign")
        state["limits"] = active.ACTIVE_LIMITS
        state["recommendation"] = orchestrator.recommend(state["metrics_history"])
        state["orchestration"] = orchestrator.ORCHESTRATION_NOTE
        # The campaign's provenance: every start, batch, queue and end, in order.
        state["events"] = history.events_for(conn, campaign_id, owner_id=user_id)
        return state
    finally:
        conn.close()


@app.get("/history")
def history_list(kind: Literal["all", "screen", "campaign"] = "all",
                 before: str | None = Query(None, max_length=200),
                 limit: int = Query(20, ge=1, le=50),
                 user_id: int = Depends(users.current_user)):
    """The signed-in user's screens and campaigns, newest first, a page at a time.
    Pass `next` from one page as `before` to get the following one."""
    conn = history.connect()
    try:
        return history.list_history(conn, owner_id=user_id, kind=kind, before=before, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        conn.close()


@app.get("/history/screens/{screen_id}")
def history_screen(screen_id: str, user_id: int = Depends(users.current_user)):
    """One screen in full: what was asked, what came back, and how it was produced."""
    conn = history.connect()
    try:
        record = history.screen_record(conn, screen_id, owner_id=user_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no such screen")
        record["events"] = history.events_for(conn, screen_id, owner_id=user_id)
        return record
    finally:
        conn.close()


@app.get("/design/queue")
def design_queue_view(limit: int = 50, user_id: int = Depends(users.current_user)):
    """The signed-in user's simulation backlog, highest triage score first, across
    all of their campaigns. A finished run is recorded through the measurement store
    as a source='simulation' observation, closing the loop against a real label."""
    conn = candidates.connect()
    try:
        return {
            "queue": candidates.queue(conn, owner_id=user_id, limit=limit),
            "summary": candidates.queue_summary(conn, owner_id=user_id),
            "note": active.ACTIVE_LIMITS,
            # Whether to show the approvals panel.
            "admin": users.is_admin(conn, user_id),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Approving simulations (admins only; ADMIN_EMAILS)
# ---------------------------------------------------------------------------
# A queued simulation is a request, not a run: nothing reaches the worker until an
# admin approves it, because each one is hours of paid GPU time. Approving also
# starts the worker's RunPod pod, which stops itself again once the queue is empty.

class Decline(BaseModel):
    reason: str = Field(default="declined by an admin", min_length=1, max_length=500)


class Approve(BaseModel):
    # 'gpu': the full run on the RunPod GPU. 'cpu': a short preview on a CPU
    # worker, for when no GPU is available; not converged, never a measurement.
    tier: Literal["gpu", "cpu"] = "gpu"


# How long a CPU preview runs. A full 20 ns run takes days on a CPU; this is
# enough to see where the polymer sits, not to converge Gamma23.
CPU_PREVIEW_NS = float(os.environ.get("SIM_CPU_PREVIEW_NS", "1.0"))
CPU_PREVIEW_EQUIL_NS = float(os.environ.get("SIM_CPU_PREVIEW_EQUIL_NS", "0.1"))


# Which workers have asked for work lately, and on what. In memory: it answers
# "is a CPU or GPU worker actually online right now?", which needs no history.
_WORKERS: dict[str, dict] = {}


def _seen(name: str, platform: str | None = None) -> None:
    w = _WORKERS.setdefault(name, {"platform": platform or "?"})
    if platform:
        w["platform"] = platform
        w["tier"] = "cpu" if platform.strip().upper() in ("CPU", "REFERENCE") else "gpu"
    w["last_seen"] = datetime.now(timezone.utc).isoformat()


@app.get("/design/simulations")
def simulations_view(admin_id: int = Depends(users.current_admin)):
    """The admin screen: every user's waiting and running jobs, the most recent
    finished or denied ones, and which workers have checked in."""
    conn = candidates.connect()
    try:
        return {"jobs": candidates.all_active(conn), "recent": candidates.recent_finished(conn),
                "workers": [{"name": k, **v} for k, v in sorted(
                    _WORKERS.items(), key=lambda kv: kv[1]["last_seen"], reverse=True)],
                "pod_autostart": runpod.configured(), "cpu_preview_ns": CPU_PREVIEW_NS}
    finally:
        conn.close()


@app.post("/design/simulations/{job_id}/approve")
def simulation_approve(job_id: str, body: Approve | None = None,
                       admin_id: int = Depends(users.current_admin)):
    tier = (body or Approve()).tier
    conn = history.connect()
    try:
        job = candidates.approve(conn, job_id, admin_id, tier)
        if job is None:
            raise HTTPException(status_code=409, detail=(
                "only a waiting job with a structure can be approved, and not twice for the same "
                "kind of run; a running job can only be stopped"))
        _log_sim(conn, job, "candidate.approved", {"tier": tier})
    finally:
        conn.close()
    # Only a GPU run needs the RunPod pod; a CPU preview goes to a CPU worker.
    pod = runpod.start_pod() if tier == "gpu" else "not needed: a CPU worker runs this preview"
    return {"ok": True, "tier": tier, "pod": pod}


@app.get("/design/admin/stats")
def admin_stats(days: int = Query(90, ge=7, le=365), admin_id: int = Depends(users.current_admin)):
    """The admin dashboard: platform-wide counts, trends and distributions.
    Aggregates only -- see stats.py for what may and may not appear here."""
    # history.connect has campaigns, candidates, screens and events; the measurement
    # table is the one it does not create.
    conn = history.connect()
    try:
        db.init_schema(conn, measurements.SCHEMA)
        return stats.dashboard(conn, days)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A user's own simulations (the Simulations tab)
# ---------------------------------------------------------------------------

@app.get("/design/my-simulations")
def my_simulations(user_id: int = Depends(users.current_user)):
    conn = candidates.connect()
    try:
        return {"simulations": candidates.simulations_for(conn, owner_id=user_id),
                "cpu_preview_ns": CPU_PREVIEW_NS}
    finally:
        conn.close()


@app.get("/design/my-results")
def my_results(user_id: int = Depends(users.current_user)):
    """The Results tab: the user's screens, the candidates their campaigns
    generated, and their simulations, each summarised with its metrics."""
    conn = history.connect()
    try:
        return {**results.for_user(conn, owner_id=user_id), "cpu_preview_ns": CPU_PREVIEW_NS}
    finally:
        conn.close()


@app.get("/design/my-simulations/{job_id}")
def my_simulation(job_id: str, user_id: int = Depends(users.current_user)):
    """One simulation with its own slice of the campaign's history: queued,
    approved, each attempt, and how it ended. 404 for someone else's."""
    conn = history.connect()
    try:
        sim = candidates.simulation(conn, job_id, owner_id=user_id)
        if sim is None:
            raise HTTPException(status_code=404, detail="no such simulation")
        return _simulation_view(conn, sim, owner_id=user_id)
    finally:
        conn.close()


def _simulation_view(conn, sim: dict, *, owner_id: int) -> dict:
    """A simulation with its own slice of its campaign's history."""
    job_id = sim["id"]

    def about(e: dict) -> bool:
        d = e.get("data") or {}
        if isinstance(d, str):
            d = json.loads(d)
        return (d.get("candidate") or {}).get("id") == job_id or \
            any(c.get("id") == job_id for c in d.get("candidates") or [])
    events = [e for e in history.events_for(conn, sim["campaign_id"], owner_id=owner_id)
              if e["kind"].startswith("candidate.") and about(e)]
    return {"simulation": sim, "events": events, "cpu_preview_ns": CPU_PREVIEW_NS}


@app.get("/design/simulations/{job_id}")
def simulation_admin_view(job_id: str, admin_id: int = Depends(users.current_admin)):
    """Any user's simulation, in full, for an admin: the same view its owner
    gets, plus whose it is. So a finished run is never out of the admin's
    reach because it was queued from another account."""
    conn = history.connect()
    try:
        sim = candidates.simulation_any(conn, job_id)
        if sim is None:
            raise HTTPException(status_code=404, detail="no such simulation")
        return _simulation_view(conn, sim, owner_id=sim["owner_id"])
    finally:
        conn.close()


@app.get("/design/simulations/{job_id}/snapshot.pdb")
def simulation_admin_snapshot(job_id: str, admin_id: int = Depends(users.current_admin)):
    """Any run's last frame, for an admin's 3D view."""
    conn = candidates.connect()
    try:
        pdb = candidates.snapshot_any(conn, job_id)
    finally:
        conn.close()
    if pdb is None:
        raise HTTPException(status_code=404, detail="no snapshot for this simulation")
    return Response(pdb, media_type="chemical/x-pdb", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/design/my-simulations/{job_id}/snapshot.pdb")
def my_simulation_snapshot(job_id: str, user_id: int = Depends(users.current_user)):
    """The last frame of one of your runs, protein and polymer, for the 3D view."""
    conn = candidates.connect()
    try:
        pdb = candidates.snapshot_for(conn, job_id, owner_id=user_id)
    finally:
        conn.close()
    if pdb is None:
        raise HTTPException(status_code=404, detail="no snapshot for this simulation")
    return Response(pdb, media_type="chemical/x-pdb", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/structure/resolve")
def structure_resolve(q: str = Query(..., min_length=1, max_length=300),
                      user_id: int = Depends(users.current_user)):
    """The structure to simulate for a biologic named in the user's own words
    ("adalimumab", "human insulin", a sentence, or an ID): what it is, why it
    was picked, and a few alternatives. {"found": false} when nothing fits,
    rather than a stand-in. See structures.py."""
    try:
        hit = structures.resolve(q.strip())
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502,
                            detail="the structure databases did not answer; try again in a minute") from e
    return {"found": hit is not None, **(hit or {})}


@app.get("/structure/model/{structure_id}")
def structure_model(structure_id: str, user_id: int = Depends(users.current_user)):
    """A biologic's 3D structure as mmCIF, from RCSB or AlphaFold DB -- the same
    fetch the liability scan and the simulation use -- for the 3D view. Behind
    sign-in so it is not an open proxy; cached, since a structure does not change."""
    sid = structure_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,20}", sid):
        raise HTTPException(status_code=422, detail="not a PDB ID or UniProt accession")
    try:
        cif, source, predicted = accessibility.fetch_cached(sid.upper())
    except Exception as e:
        log.warning("structure fetch %s failed: %r", sid, e)
        raise HTTPException(status_code=502, detail=f"could not fetch {sid}; try again in a minute") from e
    # The first model only, as PDB; the mmCIF itself when PDB cannot hold it.
    pdb = _first_model(sid.upper(), cif)
    return Response(pdb or cif, media_type="chemical/x-pdb" if pdb else "chemical/x-mmcif",
                    headers={"Cache-Control": "private, max-age=86400", "X-Structure-Source": source})


@functools.lru_cache(maxsize=16)
def _first_model(sid: str, cif: str) -> str | None:
    return accessibility.first_model_pdb(cif)


@app.post("/design/simulations/{job_id}/decline")
def simulation_decline(job_id: str, body: Decline, admin_id: int = Depends(users.current_admin)):
    conn = history.connect()
    try:
        job = candidates.decline(conn, job_id, body.reason)
        if job is None:
            raise HTTPException(status_code=409, detail="only a waiting, approved or running job can be denied")
        stopped = job["was"] == "simulating"
        _log_sim(conn, job, "candidate.stopped" if stopped else "candidate.declined",
                 {"reason": body.reason})
        return {"ok": True, "stopped": stopped}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The OpenMM simulation worker (worker/). Stage 3.
# ---------------------------------------------------------------------------
# The worker is a separate process -- a conda image with OpenMM, on a GPU box or
# anywhere else -- that pulls queued candidates from here over HTTP. It holds no
# database credentials: it can only claim the next job and report on the job it
# holds, authenticated by a shared WORKER_TOKEN. Unset, these routes are off
# (503), which is the right default for a deployment with no worker.

def _worker_auth(request: Request) -> None:
    import hmac
    expected = os.environ.get("WORKER_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="simulation worker is not enabled (WORKER_TOKEN unset)")
    got = request.headers.get("authorization", "")
    if not hmac.compare_digest(got.encode(), f"Bearer {expected}".encode()):
        raise HTTPException(status_code=401, detail="bad worker token")


class WorkerHello(BaseModel):
    # Names the worker in the job record, so a result can be traced to the box
    # and build that produced it.
    worker: str = Field(min_length=1, max_length=120)


class WorkerClaim(WorkerHello):
    # The OpenMM platform it will run on. CUDA, OpenCL or HIP take GPU jobs; CPU
    # takes CPU previews. A worker from before this field existed is a GPU worker.
    platform: str = Field(default="CUDA", max_length=20)

    @property
    def tier(self) -> str:
        return "cpu" if self.platform.strip().upper() in ("CPU", "REFERENCE") else "gpu"


class WorkerProgress(WorkerHello):
    progress: str = Field(default="", max_length=500)


class WorkerFailure(WorkerHello):
    error: str = Field(min_length=1, max_length=2000)


class SimulationResult(BaseModel):
    """What a finished run reports. Checked, because it is written into the
    measurement store as an observation and shown to the user."""

    # Preferential interaction coefficient, in polymer chains per protein.
    gamma23: float = Field(ge=-1e4, le=1e4)
    gamma23_se: float | None = Field(default=None, ge=0)
    # Per-block values, so the spread behind the mean is on the record.
    gamma23_blocks: list[float] = Field(default_factory=list, max_length=100)
    # Gamma23 at several local-domain cutoffs (nm -> value): whether it has
    # plateaued, i.e. whether the local domain holds every perturbed molecule.
    gamma23_profile: dict[str, float] = Field(default_factory=dict, max_length=20)
    production_ns: float = Field(gt=0)
    temperature_k: float = Field(gt=200, lt=400)
    n_chains: int = Field(ge=1)
    n_frames: int = Field(ge=1)
    r_local_nm: float = Field(gt=0)
    r_bulk_nm: float = Field(gt=0)
    # Bulk polymer concentration the protein actually saw, from the far domain.
    bulk_chain_molar: float | None = None
    # Residues the polymer touched most, as {"residue": "LYS 45 A", "fraction": 0.4}.
    contacts: list[dict] = Field(default_factory=list, max_length=50)
    engine: str = Field(max_length=200)            # "OpenMM 8.2.0, CUDA"
    forcefields: str = Field(max_length=300)       # "amber14 / OpenFF 2.2.0 (NAGL) / TIP3P"
    structure_source: str = Field(default="", max_length=300)
    n_atoms: int = Field(ge=1)
    wall_seconds: float = Field(ge=0)
    # A deliberately tiny run (the CPU smoke test). Recorded on the candidate but
    # never written to the measurement store: it is a check of the pipeline, not
    # an observation.
    smoke: bool = False
    # A short CPU run, not converged. Kept on the candidate, never a measurement.
    preview: bool = False
    notes: list[str] = Field(default_factory=list, max_length=20)
    # The conditions the worker actually applied (conditions.py keys): what ran,
    # which may differ from what was asked if the worker predates a condition.
    conditions: dict[str, float] = Field(default_factory=dict, max_length=20)
    # The last frame, protein and polymer heavy atoms only, as PDB text
    # (worker/snapshot.py): the 3D view. Stored apart from the result.
    snapshot_pdb: str | None = Field(default=None, max_length=8_000_000)


class WorkerReport(WorkerHello):
    result: SimulationResult


def _sim_detail(r: SimulationResult) -> str:
    return (f"{r.engine}; {r.forcefields}; {r.production_ns:g} ns at {r.temperature_k:.0f} K; "
            f"{r.n_chains} chains; two-domain r_local {r.r_local_nm:g} nm / r_bulk {r.r_bulk_nm:g} nm; "
            f"{r.structure_source}")[:500]


def _record_simulation(job: dict, r: SimulationResult) -> str | None:
    """Write the run into the measurement store as a source='simulation'
    observation against the target biologic. Returns the measurement id."""
    goal, cand = job["goal"], job["candidate"]
    sid = cand["simulation"]["structure_id"]
    name = (goal.get("protein") or sid)[:200]
    mconn = measurements.connect()
    try:
        row = mconn.execute("SELECT id FROM biologic WHERE structure_id=%s AND name=%s LIMIT 1",
                            (sid, name)).fetchone()
        bid = row["id"] if row else measurements.add_biologic(
            mconn, measurements.Biologic(name=name, structure_id=sid,
                                         notes="Created by the simulation worker."))
        return measurements.add_measurement(mconn, measurements.Measurement(
            biologic_id=bid,
            excipient=cand.get("name", "designed polymer")[:200],
            excipient_smiles=(cand.get("screened_oligomer_smiles") or "")[:4000],
            concentration=(f"{r.bulk_chain_molar * 1000:.1f} mM chains (bulk)"
                           if r.bulk_chain_molar else f"{r.n_chains} chains in the box"),
            quantity="gamma23", value=r.gamma23, sd=r.gamma23_se,
            n_replicates=max(1, len(r.gamma23_blocks)),
            # What the worker applied; a worker from before it reported them ran
            # the old fixed 0.15 M NaCl and the goal's pH.
            buffer=f"water, {r.conditions.get('salt_mm', 150.0):g} mM NaCl (simulated)",
            ph=r.conditions.get("ph", goal.get("ph") or 7.0),
            format="liquid", stress=f"{r.temperature_k - 273.15:.0f} C",
            method="MD preferential interaction (two-domain)", instrument=r.engine[:120],
            source="simulation", simulation_detail=_sim_detail(r),
            observed_by="simulation worker", prediction_ref=f"candidate:{cand['id']}",
        ))
    finally:
        mconn.close()


def _log_sim(conn, job: dict, kind: str, data: dict) -> None:
    if job.get("owner_id") is not None:
        c = job["candidate"]
        history.log_event(conn, job["owner_id"], kind, "campaign", job["campaign_id"],
                          {"candidate": {"id": c["id"], "name": c.get("name", "")}, **data})


@app.post("/worker/claim")
def worker_claim(hello: WorkerClaim, request: Request):
    """The next job, or 204 when the queue is empty. Jobs whose worker went
    silent are requeued first. The target structure is fetched here (RCSB or
    AlphaFold, the same code the liability scan uses) and handed over whole, so
    the worker needs no network access beyond this API."""
    _worker_auth(request)
    _seen(hello.worker, hello.platform)
    conn = history.connect()
    try:
        for row in candidates.requeue_stale(conn):
            owner = conn.execute("SELECT owner_id FROM campaign WHERE id=%s",
                                 (row["campaign_id"],)).fetchone()
            if owner and owner["owner_id"] is not None:
                history.log_event(conn, owner["owner_id"], "candidate.simulation_failed"
                                  if row["status"] == "failed" else "candidate.requeued",
                                  "campaign", row["campaign_id"],
                                  {"candidate": {"id": row["id"]}, "error": row["sim_error"]})
        # A job whose structure cannot be fetched fails and the next is tried, so
        # one bad accession never blocks the queue.
        for _ in range(10):
            job = candidates.claim_next(conn, hello.worker, hello.tier)
            if job is None:
                return Response(status_code=204)
            cand = job["candidate"]
            sid = cand["simulation"]["structure_id"]
            try:
                cif, source, predicted = accessibility._fetch(sid)
                # A crystal with several copies of the molecule, too big for the
                # worker whole: send one copy, and say so in the source.
                cif, trimmed = structures.fit_for_worker(cif)
                if trimmed:
                    source = f"{source}; {trimmed}"
            except Exception as e:
                err = f"could not fetch structure {sid}: {type(e).__name__}: {e}"[:500]
                done = candidates.finish(conn, cand["id"], hello.worker, error=err)
                if done:
                    _log_sim(conn, done, "candidate.simulation_failed", {"error": err})
                continue
            _log_sim(conn, job, "candidate.simulating", {"worker": hello.worker, "structure_id": sid,
                                                        "attempt": cand["simulation"]["attempts"],
                                                        "tier": hello.tier, "platform": hello.platform})
            goal = job["goal"]
            # The API, not the worker, decides how long a run is: a CPU preview is
            # short whatever the worker's own defaults say.
            settings = ({"tier": "cpu", "production_ns": CPU_PREVIEW_NS,
                         "equilibration_ns": CPU_PREVIEW_EQUIL_NS}
                        if hello.tier == "cpu" else {"tier": "gpu"})
            cond = conditions.for_goal(goal)
            return {
                "job_id": cand["id"],
                "candidate": {k: cand.get(k) for k in (
                    "id", "name", "screened_oligomer_smiles", "screened_units",
                    "repeat_unit_smiles", "composition", "charge")},
                # Simulated at the conditions the user asked for (conditions.py):
                # temperature and pH also at the top level, for a worker built
                # before "conditions" existed.
                "temperature_c": cond["values"]["temperature_c"],
                "ph": cond["values"]["ph"],
                "conditions": cond,
                "format": goal.get("format", "liquid"),
                "protein": goal.get("protein", ""),
                "structure": {"id": sid, "source": source, "predicted": predicted, "mmcif": cif},
                "attempt": cand["simulation"]["attempts"],
                "settings": settings,
            }
        return Response(status_code=204)
    finally:
        conn.close()


@app.post("/worker/jobs/{job_id}/heartbeat")
def worker_heartbeat(job_id: str, body: WorkerProgress, request: Request):
    """409 means the job is no longer this worker's: stop and drop it."""
    _worker_auth(request)
    _seen(body.worker)
    conn = candidates.connect()
    try:
        if not candidates.heartbeat(conn, job_id, body.worker, body.progress):
            raise HTTPException(status_code=409, detail="job is not held by this worker")
        return {"ok": True}
    finally:
        conn.close()


@app.post("/worker/jobs/{job_id}/result")
def worker_result(job_id: str, report: WorkerReport, request: Request):
    _worker_auth(request)
    body = report.result
    conn = history.connect()
    try:
        result = body.model_dump(exclude={"snapshot_pdb"}) | {"has_snapshot": bool(body.snapshot_pdb)}
        done = candidates.finish(conn, job_id, report.worker, result=result, snapshot=body.snapshot_pdb)
        if done is None:
            raise HTTPException(status_code=409, detail="job is not held by this worker")
        # Decided by what was approved, not by what the worker says: a CPU
        # preview never becomes a measurement even if a worker forgets the flag.
        preview = body.preview or done["candidate"]["simulation"].get("tier") == "cpu"
        mid = None if (body.smoke or preview) else _record_simulation(done, body)
        _log_sim(conn, done, "candidate.simulated", {
            "gamma23": body.gamma23, "gamma23_se": body.gamma23_se,
            "production_ns": body.production_ns, "measurement_id": mid, "smoke": body.smoke,
            "preview": preview})
        return {"ok": True, "measurement_id": mid}
    finally:
        conn.close()


@app.post("/worker/jobs/{job_id}/fail")
def worker_fail(job_id: str, body: WorkerFailure, request: Request):
    _worker_auth(request)
    conn = history.connect()
    try:
        done = candidates.finish(conn, job_id, body.worker, error=body.error)
        if done is None:
            raise HTTPException(status_code=409, detail="job is not held by this worker")
        _log_sim(conn, done, "candidate.simulation_failed", {"error": body.error[:500]})
        return {"ok": True}
    finally:
        conn.close()


@app.post("/screen", response_model=Dossier)
async def screen(req: ScreenRequest, user_id: int = Depends(users.current_user)):
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is empty")

    try:
        agent = get_agent()
    except UserError as e:
        # Config problem, not a transient one — usually a missing ANTHROPIC_API_KEY.
        log.error("screen agent not configured: %s", e)
        raise HTTPException(status_code=503, detail=UNAVAILABLE) from e

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    # Fresh deps per request: the evidence gate must not see what a previous
    # screen's precedent lookup returned.
    deps = ScreenDeps(polymer=req.polymer)
    result, error = None, None
    # Captured so a failed run still has its tool sequence and token usage on
    # the record; a successful run's result carries the same messages.
    with capture_run_messages() as messages:
        try:
            if req.polymer and req.polymer.impurities:
                # Before the run, from the request alone: the model reports these
                # numbers and has no way to change them.
                deps.exposure = exposure.assess(req.polymer.impurities, req.exposure)
            result = await agent.run(prompt, deps=deps)
        except Exception as e:
            error = f"agent run failed: {e}"

    dossier = result.output if result is not None else None
    history_id = _record_screen(user_id, req, deps, result, messages, error, started,
                                time.perf_counter() - t0)
    if dossier is None:
        # The agent run is the only call here that leaves the box; surface the
        # failure as a 502 so the frontend can show something more useful than 500.
        # The full error is on the history record and in the log; the user gets
        # what they can act on.
        log.error("screen failed: %s", error)
        raise HTTPException(status_code=502, detail="the screen did not finish; please try again")
    dossier.history_id = history_id
    return dossier


def _record_screen(user_id: int, req: ScreenRequest, deps: "ScreenDeps", result, messages: list,
                   error: str | None, started: datetime, seconds: float) -> str | None:
    """Save the run to the user's history. Returns its id, or None if saving
    failed -- which is logged and otherwise swallowed: the user still gets the
    dossier they waited for, marked unsaved. Everything, including assembling
    the record, is inside the try for that reason."""
    try:
        messages = result.all_messages() if result is not None else messages
        provenance = {
            "model": SCREEN_MODEL,
            "app_version": history.APP_VERSION,
            "duration_s": round(seconds, 2),
            "usage": _usage(messages),
            # Exactly what each tool returned during this run: the same record the
            # evidence gate judged the dossier against.
            "identity_calls": deps.identity_calls,
            "precedent_calls": deps.precedent_calls,
            "exposure": deps.exposure,
            "precedent_index": precedent.index_status(),
            "tool_trace": _tool_trace(messages),
            "gate_rejections": deps.gate_rejections,
            "schema_rejections": _schema_rejections(messages),
        }
        request = req.model_dump(mode="json")
        dossier = result.output.model_dump(mode="json") if result is not None else None
        conn = history.connect()
        try:
            return history.record_screen(
                conn, user_id, request=request, dossier=dossier, error=error,
                provenance=_jsonable(provenance), started_at=started,
                finished_at=datetime.now(timezone.utc), candidate_id=req.candidate_id)
        finally:
            conn.close()
    except Exception as e:
        print(f"warning: screen not saved to history: {_scrub(f'{type(e).__name__}: {e}')}", flush=True)
        return None


def _usage(messages: list) -> dict | None:
    """Model calls and tokens, summed from the responses: the same whether the
    run succeeded or ran out of retries part-way."""
    from pydantic_ai.messages import ModelResponse
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    if not responses:
        return None
    total = lambda k: sum(getattr(r.usage, k, 0) or 0 for r in responses)
    return {"requests": len(responses), "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "tool_calls": len(_tool_trace(messages))}


def _schema_rejections(messages: list) -> list[str]:
    """Every time the dossier failed its schema and went back to the model, and
    why -- the other way a run ends in "exceeded maximum retries". Evidence-gate
    rejections are also retries, so those already in gate_rejections are left out."""
    from pydantic_ai.messages import RetryPromptPart
    reasons = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, RetryPromptPart) and part.tool_name == "final_result":
                if isinstance(part.content, str):
                    continue  # a ModelRetry from the evidence gate
                reasons += [f"{'.'.join(map(str, e.get('loc', ())))}: {e.get('msg', '')}"[:300]
                            for e in part.content]
    return reasons


def _tool_trace(messages: list) -> list[dict]:
    """Which tools the model called, in order, with what arguments. The results
    are already in identity_calls / precedent_calls; this is the sequence."""
    from pydantic_ai.messages import ToolCallPart
    trace = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolCallPart):
                args = part.args if isinstance(part.args, dict) else part.args_as_dict()
                trace.append({"tool": part.tool_name, "args": args})
    return trace


def _jsonable(value):
    """Round-trip through JSON so anything a tool returned (tuples, sets, dates)
    lands in JSONB as plain data rather than failing the insert."""
    return json.loads(json.dumps(value, default=str))
