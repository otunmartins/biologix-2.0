"""
Excipient Screen API — wraps the PydanticAI agent behind a single endpoint
that the Next.js frontend calls.

What's real: PubChem identity resolution, RDKit structural alerts, a small
protein-liability rule table.

What's simplified: no regulatory precedent lookup (FDA IID / GRAS / DailyMed),
and the protein scan is weighted by solvent accessibility only when a structure
identifier is supplied. See root README.

Run standalone:
  pip install -r requirements.txt
  export ANTHROPIC_API_KEY=sk-ant-...
  uvicorn main:app --reload --port 8000
"""

import os
import re
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent
from pydantic_ai.exceptions import UserError
from rdkit import Chem

import accessibility

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


# Flip to True only once Stage 1 (FDA IID / GRAS / DailyMed) actually exists.
# Until then nothing in this system can see regulatory precedent, so nothing may
# claim it — see the validator below.
PRECEDENT_LOOKUP_AVAILABLE = False


class Dossier(BaseModel):
    excipient: str
    protein: str
    route: str
    summary: str
    endpoints: list[EndpointResult]
    liabilities: list[LiabilityFlag]
    needs_testing: bool

    @model_validator(mode="after")
    def enforce_evidence_floor(self):
        """Hard guard, not a prompt instruction. A ValueError here is handed back
        to the model by PydanticAI as a retry, so it corrects itself."""
        if not PRECEDENT_LOOKUP_AVAILABLE:
            overclaimed = [
                e.endpoint
                for e in self.endpoints
                if e.evidence_grade == "A" or e.verdict == "Precedented"
            ]
            if overclaimed:
                raise ValueError(
                    f"Endpoints {overclaimed} claim regulatory precedent, but no precedent "
                    "database is wired up (no FDA IID / GRAS / DailyMed lookup). Precedent "
                    "cannot come from your own recollection: regrade these as 'Data gap: test' "
                    "at grade C, D, or E."
                )

        # Derived, never taken from the model: below grade B, or any high-severity liability.
        self.needs_testing = any(
            e.evidence_grade not in ("A", "B") for e in self.endpoints
        ) or any(l.severity == "high" for l in self.liabilities)
        return self


class ScreenRequest(BaseModel):
    prompt: str


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
        "note": (
            "Could not resolve via PubChem — treat as unidentified and grade E on every "
            "endpoint. Do not guess a structure. Polymers and mixtures often have no single "
            "PubChem record; tell the user they can re-run with the repeat-unit SMILES "
            "instead of a trade name, which this tool accepts directly."
        ),
    }


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
    protein_sequence: str, active_alerts: list[str], structure_id: str = ""
) -> dict:
    """Residue-level liability flags for the excipient's active structural alerts.

    structure_id is optional. Give it a UniProt accession (e.g. P01857) to use the
    AlphaFold model, or a 4-character PDB ID (e.g. 1IGT) for an experimental
    structure, and each flag is weighted by relative solvent accessibility.
    Without it, the scan falls back to counting residue types in the sequence,
    which cannot tell an exposed Met from a buried one.
    """
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
    for alert, residue, reaction, severity, mitigation in RULE_TABLE:
        if alert not in active_alerts:
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
            }
        )

    return {"structure": structure_note, "flags": flags}


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
4. If the user gave a protein sequence or a structure identifier, call protein_liability_scan with
   the sequence, the names of every alert that came back found=True, and structure_id set to any
   UniProt accession or PDB ID the user mentioned (pass "" if they gave none).
   The tool returns {"structure": ..., "flags": [...]}. Copy each flag's fields into the dossier
   verbatim — including accessibility — and never recompute, round or reword a number.
   Report what the "structure" field says in your rationale: whether accessibility was modelled,
   which structure was used, and any sequence mismatch or fetch failure it mentions.
5. If identity resolution fails, grade every endpoint E and say so plainly in the summary —
   do not guess a structure.
6. If resolve_identity returns surrogate=True, the structure you screened is a short repeat-unit
   stand-in for a polymer, not the real excipient. Cap every structure-derived endpoint at grade D,
   name the surrogate in the rationale, and say in the summary that the real material's chain
   length, polydispersity and residual monomers were NOT assessed.

Verdict rules — use ONLY these four words, never "safe":
  Precedented, Supported without precedent, Data gap: test, Alert: avoid.

There is no regulatory-precedent database wired up yet, so no endpoint may be graded A and none
may be called "Precedented" on the strength of your own recollection — that is a data gap.

Set needs_testing=True whenever any endpoint is below grade B, or any liability has severity "high".

IMPORTANT — state this in the summary field every time: this system has NO regulatory-precedent
database (no FDA IID / GRAS / DailyMed lookup yet) and NO protein-compatibility simulation.
Every result here is a chemistry-only triage on structural alerts and a small liability rule table —
not a full safety assessment.
"""

_agent: Agent | None = None


def get_agent() -> Agent:
    """Built on first use, not at import. Constructing it eagerly means a missing
    ANTHROPIC_API_KEY takes down the whole container at startup — /health included —
    so a bad .env on the box looks like a crashloop instead of a clear error."""
    global _agent
    if _agent is None:
        _agent = Agent(
            "anthropic:claude-sonnet-5",
            output_type=Dossier,
            system_prompt=SYSTEM_PROMPT,
            tools=[
                resolve_identity,
                structural_alerts,
                published_alert_screen,
                protein_liability_scan,
            ],
            retries=2,  # room for the evidence-floor validator to bounce a bad dossier back
        )
    return _agent

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

app = FastAPI(title="Excipient Screen API")

# Kept for local dev (web on :3000, api on :8000 directly). In the deployed
# stack, Caddy puts both on one origin, so this middleware is mostly a
# convenience rather than a requirement — see root README.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("WEB_ORIGIN", "http://localhost:3000")],
    allow_methods=["POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    # model_configured is the first thing you want to know when the box is up
    # but every screen is failing.
    return {"ok": True, "model_configured": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@app.post("/screen", response_model=Dossier)
async def screen(req: ScreenRequest):
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is empty")

    try:
        agent = get_agent()
    except UserError as e:
        # Config problem, not a transient one — usually a missing ANTHROPIC_API_KEY.
        raise HTTPException(status_code=503, detail=f"agent not configured: {e}") from e

    try:
        result = await agent.run(prompt)
    except Exception as e:
        # The agent run is the only call here that leaves the box; surface the
        # failure as a 502 so the frontend can show something more useful than 500.
        raise HTTPException(status_code=502, detail=f"agent run failed: {e}") from e
    return result.output
