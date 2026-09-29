"""Which structure is this biologic? From the name a user wrote to an ID.

    resolve(text) -> {"structure_id", "title", "source", "residues", "why",
                      "alternatives"} or None

The user names a biologic in their own words -- "adalimumab", "Humira, a TNF
antibody", "human insulin", or an ID outright -- and the simulation has to run
against THAT protein, not a stand-in. So:

1. An ID in the text wins: a PDB ID (1IGT) or a UniProt accession (P01857).
2. Otherwise search the PDB for entries whose protein descriptions name it,
   and rank them. An entry qualifies only if every protein in it IS the
   biologic (a chain that names anything else is a binding partner, and would
   be simulated too) and it fits the worker (MAX_RESIDUES, all chains counted,
   as the worker counts them). Among those: not a mutant, complex or fusion;
   human; any resolution to 2.5 A; then the smallest, since every residue is
   simulation time and a crystal often holds several copies.
3. Otherwise UniProt (reviewed entries, human first); the worker runs its
   accession as the AlphaFold model.

Nothing is invented: when none of this finds anything the answer is None and
the user is asked for an ID. Every answer says what the entry is and why it
was picked, so a wrong guess is visible and can be changed.
"""

from __future__ import annotations

import re
from functools import lru_cache

import httpx

# The worker's own limit (worker/simulate.py Settings.max_residues).
MAX_RESIDUES = 1500

# Not preceded or followed by a letter or digit: a whole token. Spelt out
# rather than with the word-boundary escape, which is easy to mangle.
_W0, _W1 = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
_PDB_ID = re.compile(_W0 + r"([0-9][A-Za-z0-9]{3})" + _W1)
_UNIPROT = re.compile(_W0 + r"([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9][A-Z][A-Z0-9]{2}[0-9])" + _W1)

# Words that describe a biologic without naming it; searching for them finds
# every antibody in the PDB rather than this one.
_GENERIC = {
    "a", "an", "the", "my", "our", "of", "for", "with", "and", "or", "in", "at", "to", "is",
    "human", "humanized", "humanised", "recombinant", "monoclonal", "antibody", "antibodies",
    "mab", "igg", "igg1", "igg2", "igg4", "protein", "biologic", "drug", "therapeutic",
    "formulation", "stored", "storage", "liquid", "lyophilised", "lyophilized", "fab", "fc",
    "subcutaneous", "intravenous", "months", "weeks", "room", "temperature",
    # The rest of a request around the name: "find stabilisers for human insulin
    # that could maintain its stability at 35 degrees".
    "find", "search", "suggest", "design", "propose", "give", "need", "want", "help", "keep",
    "maintain", "could", "would", "should", "can", "will", "that", "this", "which", "its", "their",
    "from", "under", "over", "during", "against", "when", "while", "than", "into", "about",
    "stable", "stabilise", "stabilize", "stabiliser", "stabilizer", "stabilisers", "stabilizers",
    "stability", "polymer", "polymers", "excipient", "excipients", "candidate", "candidates",
    "degree", "degrees", "celsius", "celcius", "fahrenheit", "fridge", "frozen", "refrigerated",
    "shelf", "life", "year", "years", "month", "week", "days", "hours", "long", "term", "high",
    "low", "heat", "thermal", "aggregation", "prevent", "reduce", "improve", "better", "best",
}

# Word shapes that are never a protein's name: "stability", "maintaining".
_NOT_A_NAME = re.compile(r"(?:ity|ing|ize|ise|izer|iser|izers|isers|ation|ness|ment)$", re.I)

# What else a description of the biologic itself may say: which chain, which
# fragment, where it came from. Any other word names a different protein.
_OWN_WORDS = {
    "heavy", "light", "chain", "chains", "fab", "fv", "scfv", "fc", "fragment", "antibody", "igg",
    "igg1", "igg2", "igg4", "kappa", "lambda", "human", "humanized", "recombinant", "protein",
    "precursor", "isoform", "domain", "egg", "white", "hen", "serum", "mature", "form", "wild",
    "type", "wildtype", "efab", "variable", "constant", "region", "subunit", "monomer", "of",
    "the", "and", "a", "b", "c", "h", "l", "hc", "lc",
}

# In a title: an engineered or altered molecule, or one bound to something.
_ALTERED = re.compile(
    r"mutant|variant|mutation|engineered|fusion|chimer|conjugat|complex|bound|linked|"
    r"in the presence|anti-idiotyp|masking|" + _W0 + r"(?:di|tri|tetra|poly)-?(?:mer|meric)",
    re.I)
_POINT_MUTATION = re.compile(_W0 + r"[A-Z][0-9]{1,4}[A-Z]" + _W1)

_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
_GRAPHQL = "https://data.rcsb.org/graphql"
_UNIPROT_SEARCH = "https://rest.uniprot.org/uniprotkb/search"

_ENTRY_FIELDS = """
{ rcsb_id
  struct { title }
  exptl { method }
  rcsb_entry_info { deposited_polymer_monomer_count resolution_combined }
  polymer_entities {
    rcsb_polymer_entity { pdbx_description }
    rcsb_polymer_entity_container_identifiers { asym_ids }
    entity_poly { rcsb_entity_polymer_type rcsb_mutation_count rcsb_sample_sequence_length }
    rcsb_entity_source_organism { scientific_name }
  }
}"""


def explicit_id(text: str) -> str | None:
    """A PDB ID or UniProt accession written in the text itself."""
    m = _UNIPROT.search(text)
    if m:
        return m.group(1)
    for m in _PDB_ID.finditer(text):
        tok = m.group(1)
        # "2024", "25C" are not IDs: a PDB ID has a letter after its digit, and
        # a number with a unit in front of it is a quantity.
        if re.search(r"[A-Za-z]", tok[1:]) and not re.fullmatch(r"[0-9]+[A-Za-z]{1,2}", tok):
            return tok.upper()
    return None


def search_terms(text: str) -> list[str]:
    """What to search for, most specific first: each naming word, longest
    first, then the naming words together."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]+", text)
             if w.lower() not in _GENERIC and not _NOT_A_NAME.search(w)]
    terms = sorted({w for w in words if len(w) >= 4}, key=len, reverse=True)
    if len(words) > 1:
        terms.append(" ".join(words[:4]))
    seen, out = set(), []
    for t in terms:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:4]


# ---------------------------------------------------------------------------
# A misspelt name
# ---------------------------------------------------------------------------
# Neither the PDB nor UniProt tolerates a typo: searching either for
# "adamalimumab" returns nothing at all, so one wrong letter used to dead-end
# the whole simulation with "no structure found". These are the biologics a
# formulation user is likely to name, and they are used ONLY to propose a
# spelling when the text as written found nothing.
#
# A correction is a hypothesis, never an answer: it is searched for like any
# other term, so it only reaches the user if a real PDB or UniProt entry backs
# it, and resolve() then says which spelling it read. The cutoff is set so that
# no name below turns into a DIFFERENT name below -- mistaking one antibody for
# another would simulate the wrong drug (test_structures.py checks this).
KNOWN_BIOLOGICS = (
    # Monoclonal antibodies and fragments.
    "adalimumab", "infliximab", "rituximab", "trastuzumab", "bevacizumab", "cetuximab",
    "pembrolizumab", "nivolumab", "atezolizumab", "durvalumab", "avelumab", "ipilimumab",
    "ustekinumab", "secukinumab", "ixekizumab", "brodalumab", "guselkumab", "risankizumab",
    "omalizumab", "denosumab", "eculizumab", "ravulizumab", "natalizumab", "tocilizumab",
    "sarilumab", "golimumab", "certolizumab", "vedolizumab", "dupilumab", "tralokinumab",
    "evolocumab", "alirocumab", "ocrelizumab", "daratumumab", "isatuximab", "elotuzumab",
    "ramucirumab", "panitumumab", "obinutuzumab", "ofatumumab", "palivizumab", "nirsevimab",
    "basiliximab", "abciximab", "belimumab", "canakinumab", "mepolizumab", "benralizumab",
    "reslizumab", "emicizumab", "caplacizumab", "erenumab", "fremanezumab", "galcanezumab",
    "romosozumab", "burosumab", "teprotumumab", "tezepelumab", "bimekizumab", "dostarlimab",
    "cemiplimab", "polatuzumab", "enfortumab", "sacituzumab", "margetuximab", "tafasitamab",
    "lecanemab", "aducanumab", "donanemab", "teplizumab", "inebilizumab", "satralizumab",
    # Other therapeutic proteins and peptides.
    "insulin", "glargine", "lispro", "aspart", "detemir", "degludec", "somatropin",
    "erythropoietin", "epoetin", "darbepoetin", "filgrastim", "pegfilgrastim", "interferon",
    "etanercept", "abatacept", "aflibercept", "romiplostim", "rilonacept", "albumin",
    "lysozyme", "hemoglobin", "haemoglobin", "myoglobin", "ubiquitin", "chymotrypsin",
    "trypsin", "asparaginase", "alteplase", "tenecteplase", "dornase", "agalsidase",
    "imiglucerase", "laronidase", "idursulfase", "rasburicase", "pegloticase", "follitropin",
    "teriparatide", "liraglutide", "semaglutide", "dulaglutide", "exenatide", "calcitonin",
    "glucagon", "oxytocin", "vasopressin", "desmopressin", "lactoferrin", "papain",
)

# Calibrated against the names above: the typos in test_structures.py are all
# caught, and ordinary English words are not mistaken for a drug.
_TYPO_CUTOFF = 0.85

# How far ahead the best match must be before it is trusted over the runner-up.
# Below this the word is left alone rather than resolved to one of two drugs.
_TYPO_MARGIN = 0.04


def corrected_terms(text: str) -> list[tuple[str, str]]:
    """[(spelling we know, word the user wrote)] for naming words that look like
    a near-miss for a biologic in KNOWN_BIOLOGICS. A word spelt correctly, or
    nothing like any of them, yields nothing."""
    import difflib

    out: list[tuple[str, str]] = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]+", text):
        low = w.lower()
        if len(low) < 5 or low in _GENERIC or _NOT_A_NAME.search(low):
            continue
        if low in KNOWN_BIOLOGICS:      # spelt right: nothing to correct
            continue
        near = difflib.get_close_matches(low, KNOWN_BIOLOGICS, n=2, cutoff=_TYPO_CUTOFF)
        if not near:
            continue
        # Antibody names differ by a syllable -- satralizumab and natalizumab
        # are 0.87 alike -- so a typo can sit between two real drugs. Correcting
        # to the wrong one would silently simulate a different molecule, which is
        # worse than not correcting at all: when the best match is not clearly
        # ahead, say nothing and let the user give the ID.
        if len(near) > 1:
            best, second = (_ratio(low, near[0]), _ratio(low, near[1]))
            if best - second < _TYPO_MARGIN:
                continue
        if not any(near[0] == c for c, _ in out):
            out.append((near[0], w))
    return out[:2]


def _ratio(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def naming_phrase(text: str) -> str:
    """The words that name the biologic, in order, keeping "human" and the like."""
    keep = {"human", "bovine", "hen", "egg", "white", "serum"}
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9-]+", text)
             if w.lower() in keep or (w.lower() not in _GENERIC and not _NOT_A_NAME.search(w))]
    return " ".join(words[:4]).lower()


def _query(node: dict, client: httpx.Client) -> list[str]:
    q = {"query": node, "return_type": "entry",
         "request_options": {"paginate": {"start": 0, "rows": 100}}}
    r = client.post(_SEARCH, json=q)
    if r.status_code == 204:
        return []
    r.raise_for_status()
    return [x["identifier"] for x in r.json().get("result_set", [])]


def _pdb_hits(term: str, client: httpx.Client) -> list[str]:
    """Entries with a protein whose description names the term, then entries
    whose title does: an antibody's chains are often just "heavy chain"."""
    by_chain = _query({"type": "terminal", "service": "text", "parameters": {
        "attribute": "rcsb_polymer_entity.pdbx_description",
        "operator": "contains_words", "value": term}}, client)
    by_title = _query({"type": "terminal", "service": "text", "parameters": {
        "attribute": "struct.title", "operator": "contains_words", "value": term}}, client)
    return list(dict.fromkeys(by_chain + by_title))[:150]


def _entries(ids: list[str], client: httpx.Client) -> list[dict]:
    q = "{ entries(entry_ids: %s) %s }" % (str(ids).replace("'", '"'), _ENTRY_FIELDS)
    r = client.post(_GRAPHQL, json={"query": q})
    r.raise_for_status()
    return [e for e in (r.json().get("data") or {}).get("entries") or [] if e]


def is_the_biologic(desc: str, name: str) -> bool:
    """Is this protein description the biologic itself -- and not a relative
    ("insulin receptor", "ubiquitin-conjugating enzyme") or a partner?"""
    # "anti-HER2 Fab": what it binds describes the antibody, it is not a partner.
    d = re.sub(r"anti-[a-z0-9]+", " ", desc.lower())
    n = name.lower()
    if not re.search(r"(?<![a-z0-9-])" + re.escape(n) + r"(?![a-z0-9-])", d):
        return False
    rest = re.findall(r"[a-z]+", d.replace(n, " "))
    return all(w in _OWN_WORDS or len(w) <= 2 for w in rest)


def _rank(entries: list[dict], term: str, phrase: str = "") -> list[dict]:
    # The name as the user wrote it ("human insulin"): a title that says just
    # that is the plain molecule more often than one that only mentions it.
    phrase = (phrase or term).lower()
    picks = []
    for e in entries:
        info = e.get("rcsb_entry_info") or {}
        n_res = info.get("deposited_polymer_monomer_count") or 0
        prots = [p for p in e.get("polymer_entities") or []
                 if (p.get("entity_poly") or {}).get("rcsb_entity_polymer_type") == "Protein"]
        if not prots or not n_res:
            continue
        # Over the limit as deposited, one copy may still fit: the API sends the
        # worker one copy of such a crystal (one_copy below).
        copies = max(len((p.get("rcsb_polymer_entity_container_identifiers") or {}).get("asym_ids") or [1])
                     for p in prots)
        if n_res > MAX_RESIDUES:
            n_res = sum((p.get("entity_poly") or {}).get("rcsb_sample_sequence_length") or 0 for p in prots)
            if copies < 2 or not n_res or n_res > MAX_RESIDUES:
                continue
        else:
            copies = 1
        descs = [((p.get("rcsb_polymer_entity") or {}).get("pdbx_description") or "") for p in prots]
        title = (e.get("struct") or {}).get("title") or ""
        # Each chain names the biologic, or is just "heavy chain" / "light
        # chain" in an entry whose title names it.
        named_title = bool(re.search(r"(?<![a-z0-9-])" + re.escape(term.lower()) + r"(?![a-z0-9-])",
                                     title.lower()))
        if not all(is_the_biologic(d, term) or (named_title and is_the_biologic(f"{term} {d}", term))
                   for d in descs):
            continue
        altered = bool(_ALTERED.search(title) or _POINT_MUTATION.search(title))
        mutations = sum((p.get("entity_poly") or {}).get("rcsb_mutation_count") or 0 for p in prots)
        human = any("sapiens" in (o.get("scientific_name") or "").lower()
                    for p in prots for o in p.get("rcsb_entity_source_organism") or [])
        res = (info.get("resolution_combined") or [None])[0] or 9.9
        method = ((e.get("exptl") or [{}])[0].get("method") or "").lower()
        picks.append({
            "structure_id": e["rcsb_id"], "title": title, "residues": n_res,
            "source": f"PDB {e['rcsb_id']}, {method}" + (f" at {res:.2f} A" if res < 9 else ""),
            "_key": (altered or mutations > 0, phrase not in title.lower(), not human,
                     res > 2.5, n_res, res),
            "why": "; ".join(filter(None, [
                f"every protein chain in it is {term}",
                "unmodified" if not (altered or mutations) else "the closest available; its title "
                "or annotation notes a modification",
                "human" if human else "",
                (f"the crystal holds {copies} copies; one, {n_res} residues, is simulated" if copies > 1
                 else f"{n_res} residues, within the simulation limit of {MAX_RESIDUES}"),
            ])),
        })
    picks.sort(key=lambda p: p["_key"])
    for p in picks:
        del p["_key"]
    return picks


def _uniprot(term: str, client: httpx.Client) -> dict | None:
    r = client.get(_UNIPROT_SEARCH, params={
        "query": f'protein_name:"{term}" AND reviewed:true',
        "fields": "accession,protein_name,organism_name,length", "size": 10, "format": "json"})
    r.raise_for_status()
    hits = r.json().get("results") or []
    hits = [h for h in hits if (h.get("sequence") or {}).get("length", 0) <= MAX_RESIDUES]
    if not hits:
        return None
    hits.sort(key=lambda h: "sapiens" not in ((h.get("organism") or {}).get("scientificName") or "").lower())
    h = hits[0]
    name = (((h.get("proteinDescription") or {}).get("recommendedName") or {})
            .get("fullName") or {}).get("value", term)
    org = (h.get("organism") or {}).get("scientificName", "")
    return {"structure_id": h["primaryAccession"], "title": f"{name} ({org})",
            "source": f"UniProt {h['primaryAccession']}, AlphaFold model (predicted)",
            "residues": (h.get("sequence") or {}).get("length"),
            "why": f"no experimental structure of {term} alone fits; the reviewed UniProt entry"
                   + (", human" if "sapiens" in org.lower() else "")
                   + ", run as its predicted AlphaFold model"}


@lru_cache(maxsize=256)
def resolve(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    sid = explicit_id(text)
    if sid:
        return {"structure_id": sid, "title": "", "source": "given in the text", "residues": None,
                "why": "the ID was written in the text", "alternatives": []}
    terms = search_terms(text)
    with httpx.Client(timeout=20) as client:
        for term in terms:
            ids = _pdb_hits(term, client)
            picks = _rank(_entries(ids, client), term, naming_phrase(text)) if ids else []
            if picks:
                return {**picks[0], "alternatives": picks[1:4]}
        for term in terms:
            hit = _uniprot(term, client)
            if hit:
                return {**hit, "alternatives": []}
        # Nothing matched the words as written. One wrong letter is enough for
        # that -- neither database tolerates a typo -- so try the spelling we
        # know, and say which one was read. See corrected_terms().
        for fixed, typed in corrected_terms(text):
            ids = _pdb_hits(fixed, client)
            picks = _rank(_entries(ids, client), fixed, fixed) if ids else []
            hit = picks[0] if picks else _uniprot(fixed, client)
            if hit:
                return {**hit, "read_as": fixed, "as_written": typed,
                        "why": f"read as “{fixed}”, since “{typed}” matched "
                               f"nothing in either database; {hit['why']}",
                        "alternatives": picks[1:4] if picks else []}
    return None


# ---------------------------------------------------------------------------
# One copy of a crystal's contents
# ---------------------------------------------------------------------------
# A crystal's asymmetric unit often holds several copies of the molecule (4NYL,
# the adalimumab Fab, has four). The simulation wants one: the rest are crystal
# packing, not the drug in its vial, and they would quadruple the run. So keep
# one chain of each protein entity -- for a Fab, a heavy chain and the light
# chain actually paired with it, the nearest one -- and drop the rest.

def _protein_chains(cif_text: str) -> tuple[dict[str, str], dict[str, int]]:
    """auth chain id -> entity id, and residues per chain, for protein atoms of model 1."""
    import io
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict

    d = MMCIF2Dict(io.StringIO(cif_text))
    ent_type = dict(zip(d.get("_entity_poly.entity_id", []), d.get("_entity_poly.type", [])))
    chain_entity: dict[str, str] = {}
    residues: dict[str, set] = {}
    models = d.get("_atom_site.pdbx_PDB_model_num") or ["1"] * len(d["_atom_site.auth_asym_id"])
    first = models[0]
    for chain, ent, seq, group, model in zip(
            d["_atom_site.auth_asym_id"], d["_atom_site.label_entity_id"],
            d["_atom_site.auth_seq_id"], d["_atom_site.group_PDB"], models):
        if model != first or group != "ATOM" or "polypeptide" not in ent_type.get(ent, ""):
            continue
        chain_entity.setdefault(chain, ent)
        residues.setdefault(chain, set()).add(seq)
    return chain_entity, {c: len(s) for c, s in residues.items()}


def one_copy(cif_text: str) -> tuple[str, str | None]:
    """(mmCIF of one copy, a note saying what was kept), or the text unchanged
    and None when every protein entity already appears once."""
    import io
    from Bio.PDB import MMCIFIO, MMCIFParser, Select

    chain_entity, sizes = _protein_chains(cif_text)
    by_entity: dict[str, list[str]] = {}
    for c, ent in chain_entity.items():
        by_entity.setdefault(ent, []).append(c)
    if all(len(cs) == 1 for cs in by_entity.values()):
        return cif_text, None

    st = MMCIFParser(QUIET=True).get_structure("x", io.StringIO(cif_text))
    model = next(iter(st))

    def cas(chain_id):
        return [a.coord for r in model[chain_id] for a in r if a.get_id() == "CA"]

    # Start from the first chain of the largest entity; add, from each other
    # entity, the chain closest to what is already kept.
    order = sorted(by_entity, key=lambda e: -max(sizes[c] for c in by_entity[e]))
    keep = [by_entity[order[0]][0]]
    import numpy as np
    for ent in order[1:]:
        kept = np.array([x for c in keep for x in cas(c)])
        def gap(c):
            pts = np.array(cas(c))
            if not len(pts) or not len(kept):
                return float("inf")
            return float(np.min(np.linalg.norm(kept[:, None, :] - pts[None, :, :], axis=-1)))
        keep.append(min(by_entity[ent], key=gap))

    class _One(Select):
        def accept_model(self, m):
            return m.get_id() == model.get_id()

        def accept_chain(self, c):
            return c.get_id() in keep

    out = io.StringIO()
    io_ = MMCIFIO()
    io_.set_structure(st)
    io_.save(out, _One())
    n_copies = max(len(cs) for cs in by_entity.values())
    return out.getvalue(), (f"one copy of the {n_copies} in the crystal (chains {', '.join(keep)}), "
                            f"{sum(sizes[c] for c in keep)} residues")


def fit_for_worker(cif_text: str) -> tuple[str, str | None]:
    """The structure as the worker should get it: unchanged if it fits, one copy
    if the whole asymmetric unit is over the worker's limit but a copy is not."""
    try:
        _, sizes = _protein_chains(cif_text)
    except Exception:
        return cif_text, None  # not something this can read: the worker will say
    if sum(sizes.values()) <= MAX_RESIDUES:
        return cif_text, None
    return one_copy(cif_text)
