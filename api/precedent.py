"""Regulatory precedent lookup — the Stage 1 gap.

Three free, keyless federal sources, because no one of them is enough:

  - **FDA Inactive Ingredient Database (IID)**, the quarterly CDER file. One row
    per (ingredient, route, dosage form) that has appeared in an approved drug
    product, with the maximum potency on record. Curated, precise, and blind to
    biologics.
  - **Approved product labels** (openFDA SPL), full text over the DESCRIPTION
    section, counted by distinct FDA application number. This is the source that
    covers what the IID does not: polysorbate 80 subcutaneous is two IID rows
    and eighty distinct BLAs here. Weaker per hit, far better coverage.
  - **openFDA's substance registry** (the GSRS mirror), for UNII resolution —
    which is what makes the IID join trustworthy — and for the CFR citations
    that record a GRAS affirmation.

The two precedent sources have opposite failure modes, which is the point: the
IID is a curated assertion with a population gap, the label search is a text
match over the right population. A finding says which one carried it.

Kept out of the agent's hands the same way `accessibility` is: the LLM passes an
excipient name and a route in, and gets back a fixed verdict plus verbatim
strings. It never sees a raw table and never computes a number.

Limits that decide how much a hit is worth, all reported in the payload:

  - **The IID is a CDER database.** Biologics licensed under a BLA are poorly
    represented, so a missing IID row is weak evidence of absence and must never
    read as "never used". The label source exists to cover this.
  - **The label search is full text.** A composition sentence and a passing
    mention look alike to it, so it requires MIN_LABEL_APPLICATIONS distinct
    approved applications and returns the application numbers to be checked.
  - **A route is not a dose.** A hit means the excipient has been in an approved
    product by that route, at or below the potency shown. It does not clear the
    excipient at the concentration the user is asking about, and it says nothing
    about this protein.
  - **GRAS is a food determination.** 21 CFR 182/184/186 covers ingestion. It is
    close to irrelevant to a parenteral route and is graded accordingly.
"""

import csv
import io
import os
import re
import tempfile
import threading
import time
import zipfile
from urllib.parse import quote

import httpx

# The IID landing page lists the current file plus every quarterly archive. The
# current one is the only link whose text has no month in it. Scraping that is
# better than pinning a media id, because the id rotates every quarter — but the
# pin is kept as a fallback for the day FDA reshuffles the page markup.
IID_PAGE = (
    "https://www.fda.gov/drugs/drug-approvals-and-databases/"
    "inactive-ingredients-database-download"
)
IID_PINNED_URL = "https://www.fda.gov/media/193784/download?attachment"
IID_MEMBER = "IIR_OCOMM.csv"

SUBSTANCE = "https://api.fda.gov/other/substance.json"
LABELS = "https://api.fda.gov/drug/label.json"

# How many distinct approved applications must list an excipient before a label
# search counts as precedent. The search is full text over the DESCRIPTION
# section, which is where a biologic states its composition — but one stray
# mention is not a formulation. Three independent approved applications is not
# a stray mention. Real excipients clear this by an order of magnitude:
# polysorbate 80 subcutaneous returns 80 distinct BLAs.
MIN_LABEL_APPLICATIONS = 3

# The file is ~380 KB zipped and changes quarterly, so a month-old copy is
# always still current. Re-downloading it per screen would be rude to FDA and
# slow for the user.
CACHE_DIR = os.environ.get("IID_CACHE_DIR") or tempfile.gettempdir()
CACHE_TTL_SECONDS = 30 * 24 * 3600

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# The IID uses 59 route strings. Rather than mirror all of them, map the
# abbreviations and synonyms a formulator actually types, then fall back to
# matching the user's text against the file's own vocabulary.
ROUTE_ALIASES = {
    "sc": "SUBCUTANEOUS", "subq": "SUBCUTANEOUS", "sub-q": "SUBCUTANEOUS",
    "subcut": "SUBCUTANEOUS", "subcutaneously": "SUBCUTANEOUS",
    "iv": "INTRAVENOUS", "intravenously": "INTRAVENOUS", "infusion": "INTRAVENOUS",
    "im": "INTRAMUSCULAR", "intramuscularly": "INTRAMUSCULAR",
    "id": "INTRADERMAL", "it": "INTRATHECAL",
    "ivt": "INTRAVITREAL", "intravitreous": "INTRAVITREAL",
    "ia": "INTRA-ARTICULAR", "intraarticular": "INTRA-ARTICULAR",
    "ip": "INTRAPERITONEAL",
    "po": "ORAL", "per os": "ORAL", "peroral": "ORAL", "enteral": "ORAL",
    "inhaled": "RESPIRATORY (INHALATION)",
    "inhalation": "RESPIRATORY (INHALATION)",
    "pulmonary": "RESPIRATORY (INHALATION)",
    "intranasal": "NASAL",
    "eye": "OPHTHALMIC", "ocular": "OPHTHALMIC",
    "ear": "AURICULAR (OTIC)", "otic": "AURICULAR (OTIC)",
    "dermal": "TOPICAL", "cutaneous": "TOPICAL", "skin": "TOPICAL",
    "injection": "PARENTERAL", "injectable": "PARENTERAL",
}

# Anything delivered through a needle. Precedent at one of these is at least
# relevant to another; precedent at an oral or topical route is not.
PARENTERAL_ROUTES = {
    "SUBCUTANEOUS", "INTRAVENOUS", "INTRAMUSCULAR", "INTRADERMAL", "INTRATHECAL",
    "EPIDURAL", "INTRA-ARTICULAR", "INTRALESIONAL", "INTRAVITREAL", "INTRAOCULAR",
    "INTRAVASCULAR", "INTRA-ARTERIAL", "INTRAPERITONEAL", "INTRACARDIAC",
    "INTRACAVITARY", "INTRASYNOVIAL", "INTRABURSAL", "INTRACAVERNOUS",
    "PERINEURAL", "INFILTRATION", "SOFT TISSUE", "SUBMUCOSAL", "PARENTERAL",
}

# The systemic, whole-body-exposure injection routes. Bridging between these is
# a real argument; bridging from an intravitreal or intra-articular row — where
# the dose is microlitres into a closed compartment — is not, and gets a lower
# ceiling even though both are parenteral.
SYSTEMIC_INJECTION = {
    "SUBCUTANEOUS", "INTRAVENOUS", "INTRAMUSCULAR", "INTRADERMAL",
    "INFILTRATION", "PARENTERAL",
}

# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

# Trade and short names a formulator types that the IID files under something
# else. Values are matched as prefixes, so "POLYETHYLENE GLYCOL" picks up every
# graded entry (300, 3350, 8000...) in one go.
NAME_ALIASES = [
    (r"^(?:tween|polysorbate)\s*80\b", "POLYSORBATE 80"),
    (r"^(?:tween|polysorbate)\s*(20|21|40|60|65|85)\b", r"POLYSORBATE \1"),
    (r"^(?:peg|macrogol|polyethylene\s*glycol|polyoxyethylene)\b", "POLYETHYLENE GLYCOL"),
    (r"^(?:pvp|povidone|polyvinylpyrrolidone|kollidon)\b", "POVIDONE"),
    (r"^(?:pva|polyvinyl\s*alcohol)\b", "POLYVINYL ALCOHOL"),
    (r"^(?:hpmc|hypromellose|hydroxypropyl\s*methylcellulose)\b", "HYPROMELLOSE"),
    (r"^(?:cmc|carmellose|(?:sodium\s+)?carboxymethylcellulose)\b", "CARBOXYMETHYLCELLULOSE"),
    (r"^poloxamer\b", "POLOXAMER"),
    (r"^dextran\b", "DEXTRAN"),
    (r"^(?:tris|tromethamine|trometamol)\b", "TROMETHAMINE"),
    (r"^(?:wfi|water\s+for\s+injection)\b", "WATER"),
    # Cyclodextrins: the IID files the beta series under "BETADEX". SBECD is
    # Captisol, which is in a long list of approved parenteral products, and
    # nobody types its IID name.
    (r"^(?:sbecd|captisol|sulfo(?:butyl)?\s*ether[\s-]*(?:beta|b)?[\s-]*cyclodextrin)\b",
     "BETADEX SULFOBUTYL ETHER SODIUM"),
    (r"^(?:hp-?b-?cd|hydroxypropyl[\s-]*(?:beta|b)?[\s-]*cyclodextrin)\b",
     "HYDROXYPROPYL BETADEX"),
    (r"^(?:beta|b)[\s-]*cyclodextrin\b", "BETADEX"),
    # "Sodium citrate" in a buffer means the trisodium salt.
    (r"^(?:sodium\s+citrate|trisodium\s+citrate)\b", "TRISODIUM CITRATE"),
    (r"^(?:edta|edetic\s+acid|ethylenediaminetetraacetic\s+acid)\b", "EDETATE"),
]

# CFR parts that record a GRAS determination, versus ones that record a
# permitted-but-regulated food additive. Both are food, neither is a licence to
# inject anything.
GRAS_PARTS = {"182": "GRAS (21 CFR 182)",
              "184": "affirmed GRAS, direct food substance (21 CFR 184)",
              "186": "affirmed GRAS, indirect food substance (21 CFR 186)"}
FOOD_ADDITIVE_PARTS = {"172": "permitted direct food additive (21 CFR 172)",
                       "173": "permitted secondary direct food additive (21 CFR 173)"}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).upper()


def canonical_name(raw: str) -> str:
    """Fold a trade or short name onto the IID's own ingredient naming."""
    name = re.sub(r"\s+", " ", raw.strip().lower())
    for pattern, replacement in NAME_ALIASES:
        if re.match(pattern, name):
            return re.sub(pattern, replacement, name, count=1).upper()
    return _normalise(raw)


def normalise_route(raw: str, known_routes: set[str]) -> str:
    """Map whatever the user wrote onto one of the IID's route strings.

    Returns "" when nothing matches, which is reported rather than guessed —
    screening against the wrong route is worse than screening against none.
    """
    text = re.sub(r"[.\s]+", " ", raw.strip().lower()).strip()
    if not text:
        return ""
    if text in ROUTE_ALIASES:
        return ROUTE_ALIASES[text]

    upper = _normalise(text)
    if upper in known_routes:
        return upper
    # "intra-articular" vs "intra articular", "otic" vs "auricular (otic)".
    squashed = re.sub(r"[^A-Z]", "", upper)
    for route in known_routes:
        if re.sub(r"[^A-Z]", "", route) == squashed:
            return route
    for route in known_routes:
        if squashed and squashed in re.sub(r"[^A-Z]", "", route):
            return route
    return ""


# ---------------------------------------------------------------------------
# The IID file
# ---------------------------------------------------------------------------

_index: dict | None = None
_index_lock = threading.Lock()
_index_error: str | None = None
_index_failed_at = 0.0

# After a failed build, wait this long before trying fda.gov again. Short enough
# that an outage clears itself; long enough that every screen during one isn't
# stalled behind a three-minute download timeout.
INDEX_RETRY_SECONDS = 300


def _current_iid_url() -> str:
    """The current download link off the FDA landing page, or the pinned one.

    The page lists the live file first, labelled without a month; every archive
    below it is labelled "January 2026: ...". Taking the first /media/ link
    whose text carries no month is what distinguishes them.
    """
    try:
        page = httpx.get(IID_PAGE, timeout=30, follow_redirects=True)
        page.raise_for_status()
        months = (
            "january|february|march|april|may|june|july|august|september|"
            "october|november|december"
        )
        for match in re.finditer(
            r'<a[^>]+href="(/media/[^"]+)"[^>]*>(.*?)</a>', page.text, re.S | re.I
        ):
            label = re.sub(r"<[^>]+>", " ", match.group(2))
            if "ingredient" not in label.lower():
                continue
            if re.search(months, label, re.I):
                continue  # a quarterly archive, not the live file
            return "https://www.fda.gov" + match.group(1)
    except Exception:
        pass
    return IID_PINNED_URL


def _download_iid() -> bytes:
    """The zip bytes, from a fresh-enough disk cache when there is one."""
    path = os.path.join(CACHE_DIR, "fda_iid.zip")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL_SECONDS:
        with open(path, "rb") as fh:
            return fh.read()

    try:
        response = httpx.get(_current_iid_url(), timeout=180, follow_redirects=True)
        response.raise_for_status()
        payload = response.content
        zipfile.ZipFile(io.BytesIO(payload))  # reject an HTML error page before caching it
    except Exception:
        # The file changes quarterly, so an expired copy is almost certainly
        # still right and far better than no precedent at all.
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read()
        raise

    try:
        with open(path, "wb") as fh:
            fh.write(payload)
    except OSError:
        pass  # a read-only cache dir is not a reason to fail the lookup
    return payload


def _build_index() -> dict:
    raw = zipfile.ZipFile(io.BytesIO(_download_iid())).read(IID_MEMBER)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", "replace"))))

    by_unii: dict[str, list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    for row in rows:
        record = {
            "name": _normalise(row["INGREDIENT_NAME"]),
            "route": _normalise(row["ROUTE"]),
            "dosage_form": row["DOSAGE_FORM"].strip(),
            "unii": row["UNII"].strip().upper(),
            # The file strips the hyphens out of CAS numbers; put them back so
            # the value matches what every other source prints.
            "cas": _format_cas(row["CAS_NUMBER"]),
            "potency": row["POTENCY_AMOUNT"].strip(),
            "potency_unit": row["POTENCY_UNIT"].strip(),
            "max_daily": row["MAXIMUM_DAILY_EXPOSURE"].strip(),
            "max_daily_unit": row["MAXIMUM_DAILY_EXPOSURE_UNIT"].strip(),
        }
        if record["unii"]:
            by_unii.setdefault(record["unii"], []).append(record)
        by_name.setdefault(record["name"], []).append(record)

    return {
        "by_unii": by_unii,
        "by_name": by_name,
        "names": sorted(by_name),
        "routes": {_normalise(row["ROUTE"]) for row in rows},
        "n_rows": len(rows),
    }


def _format_cas(digits: str) -> str:
    """9005656 -> 9005-65-6. CAS check digit is last, then two, then the rest."""
    d = re.sub(r"\D", "", digits or "")
    return f"{d[:-3]}-{d[-3:-1]}-{d[-1]}" if len(d) >= 5 else ""


def get_index() -> dict:
    """Lazily built, then held for the life of the process.

    Built on first tool call rather than at import for the same reason the agent
    is: a network hiccup at startup should not take the container down. A failed
    build is retried after INDEX_RETRY_SECONDS rather than never, so one bad
    minute at fda.gov does not mean no precedent until the container restarts.
    """
    global _index, _index_error, _index_failed_at
    with _index_lock:
        if _index is None and time.time() - _index_failed_at >= INDEX_RETRY_SECONDS:
            try:
                _index = _build_index()
                _index_error = None
            except Exception as exc:
                _index_error = f"{type(exc).__name__}: {exc}"
                _index_failed_at = time.time()
        if _index is None:
            raise RuntimeError(f"FDA IID unavailable ({_index_error})")
        return _index


def index_status() -> dict:
    """What /health reports. Deliberately does NOT build the index — a health
    probe that pulls a file off fda.gov is a health probe that fails when FDA
    is slow, which is the opposite of useful."""
    if _index is not None:
        return {"loaded": True, "rows": _index["n_rows"]}
    cached = os.path.join(CACHE_DIR, "fda_iid.zip")
    return {
        "loaded": False,
        "cached_on_disk": os.path.exists(cached),
        "last_error": _index_error,
    }


# ---------------------------------------------------------------------------
# UNII and GRAS, from openFDA's substance registry
# ---------------------------------------------------------------------------

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def _openfda_get(url: str) -> httpx.Response:
    """GET an openFDA URL, with the API key when one is configured.

    Without a key openFDA allows about 1,000 requests a day per IP, and one
    excipient lookup can spend ten. A key is free and lifts that to 120,000.
    """
    key = os.environ.get("OPENFDA_API_KEY", "").strip()
    if key:
        url += f"&api_key={quote(key)}"
    return httpx.get(url, timeout=30)


class LookupFailed(Exception):
    """openFDA could not answer — a timeout, a 429, a 5xx. Not the same as a miss,
    and never cached as one: a rate-limit blip must not become "no precedent"
    for the life of the process."""


def _substance_search(query: str) -> dict | None:
    """The first matching record, or None on a definitive miss (404).
    Raises LookupFailed when openFDA could not answer."""
    try:
        response = _openfda_get(f"{SUBSTANCE}?search={query}&limit=1")
    except httpx.HTTPError as exc:
        raise LookupFailed(f"{type(exc).__name__}: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise LookupFailed(f"HTTP {response.status_code}")
    results = response.json().get("results") or []
    return results[0] if results else None


_substance_cache: dict[str, dict] = {}
_SUBSTANCE_CACHE_MAX = 256


def resolve_substance(name_or_cas: str) -> dict:
    """UNII, the registry's display name, and any CFR citations.

    The UNII is what makes the IID join trustworthy: excipient names are written
    a dozen ways and the registry knows the synonyms. The CFR citations are the
    GRAS signal — 21 CFR 184.1854 on sucrose is the affirmation itself.

    `names.name` is a case-SENSITIVE exact-string field, and the registry's
    casing is not consistent: trehalose is filed as "TREHALOSE" and sucrose as
    "Sucrose", so a single uppercase query silently misses half the excipients
    anyone will type. Hence the casing ladder, then a free-text fallback whose
    hit is only accepted if one of its names really is the thing we asked for —
    free text over 100k substances will always return something.
    """
    raw = name_or_cas.strip()
    key = _normalise(raw)
    if key in _substance_cache:
        return _substance_cache[key]

    folded = canonical_name(raw)

    if _CAS_RE.match(raw):
        candidates = [f'codes.code:"{raw}"']
    else:
        spellings = dict.fromkeys([_normalise(raw), raw.title(), folded, folded.title(), raw])
        candidates = [f'names.name:"{quote(s)}"' for s in spellings if s]

    record = None
    try:
        for query in candidates:
            record = _substance_search(query)
            if record:
                break

        if record is None and not _CAS_RE.match(raw):
            loose = _substance_search(quote(f'"{raw}"'))
            wanted = {_normalise(raw), folded}
            if loose and any(
                _normalise(n.get("name", "")) in wanted for n in loose.get("names", [])
            ):
                record = loose
    except LookupFailed as exc:
        # Reported, not cached: the next screen asks again.
        return {"found": False, "unii": "", "display_name": "", "cas": "", "cfr": [],
                "gras": "", "error": f"openFDA substance registry unavailable ({exc})"}

    if record is None:
        result = {"found": False, "unii": "", "display_name": "", "cas": "", "cfr": [], "gras": ""}
    else:
        cfr = [c["code"] for c in record.get("codes", []) if c.get("code_system") == "CFR"]
        cas = [c["code"] for c in record.get("codes", []) if c.get("code_system") == "CAS"]
        display = [n["name"] for n in record.get("names", []) if n.get("display_name")]
        result = {
            "found": True,
            "unii": record.get("unii", ""),
            "display_name": display[0] if display else "",
            "cas": cas[0] if cas else "",
            "cfr": cfr,
            "gras": _classify_cfr(cfr),
        }

    # A miss costs up to five sequential round trips through the casing ladder,
    # so definitive misses are cached too — the same unknown name should not pay
    # twice. Failures returned above and are not.
    if len(_substance_cache) >= _SUBSTANCE_CACHE_MAX:
        _substance_cache.pop(next(iter(_substance_cache)))
    _substance_cache[key] = result
    return result


def _classify_cfr(citations: list[str]) -> str:
    """GRAS or food-additive status from the CFR part number, or "" for neither."""
    parts = {m.group(1) for c in citations if (m := re.search(r"21 CFR (\d+)", c))}
    for part, label in GRAS_PARTS.items():
        if part in parts:
            return label
    for part, label in FOOD_ADDITIVE_PARTS.items():
        if part in parts:
            return label
    return ""


# ---------------------------------------------------------------------------
# Approved product labels — the source that covers what the IID does not
# ---------------------------------------------------------------------------

_label_cache: dict[tuple, dict] = {}


# A mention of the excipient counts as composition only when it reads like one:
# an amount beside it ("albumin (human) (2.5 mg)"), or a sentence that states
# contents ("each mL contains..."). A phrase search cannot tell "polysorbate 80
# (0.2 mg)" from "the main protraction mechanism of semaglutide is albumin
# binding", or a fusion protein built on human serum albumin from one
# formulated with it — this does.
_AMOUNT = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|%|mm|mmol|meq|ml|units?)\b")
_COMPOSITION_CUES = re.compile(
    r"\b(?:contains?|containing|each|per|inactive|excipients?|formulated|composed|consists?)\b"
)
_NOT_AN_INGREDIENT = re.compile(r"^[\s-]*(?:binding|conjugate|fusion|receptor|domain)\b")
LABEL_PAGE = 10    # full labels run to ~200 KB each, so read them a few at a time
LABEL_PAGES = 5    # and stop after 50 without reaching the threshold


def _is_composition(description: str, name: str) -> bool:
    text = re.sub(r"\s+", " ", description.lower())
    for m in re.finditer(r"(?<![a-z0-9-])" + re.escape(name.lower()) + r"(?![a-z0-9])", text):
        if _NOT_AN_INGREDIENT.match(text[m.end():]):
            continue
        start = text.rfind(". ", 0, m.start()) + 1
        end = text.find(". ", m.end())
        sentence = text[start:end if end >= 0 else len(text)]
        near = text[max(0, m.start() - 60):m.end() + 60]
        if _AMOUNT.search(near) or _COMPOSITION_CUES.search(sentence):
            return True
    return False


def _verify_composition(query: str, name: str) -> dict | None:
    """Read matching labels until MIN_LABEL_APPLICATIONS distinct approved
    applications are confirmed to list the excipient as an ingredient.

    Stops at the threshold rather than reading every label, so n_verified is a
    floor, not a census. Returns None if openFDA stopped answering part-way.
    """
    verified: set[str] = set()
    examples: list[str] = []
    read = 0
    for page in range(LABEL_PAGES):
        try:
            response = _openfda_get(
                f"{LABELS}?search={query}&limit={LABEL_PAGE}&skip={page * LABEL_PAGE}"
            )
        except Exception:
            return None
        if response.status_code == 404:
            break
        if response.status_code != 200:
            return None
        rows = response.json().get("results", [])
        for row in rows:
            read += 1
            meta = row.get("openfda", {})
            application = (meta.get("application_number") or [""])[0]
            if not application or application in verified:
                continue
            if _is_composition(" ".join(row.get("description", [])), name):
                verified.add(application)
                brand = (meta.get("brand_name") or meta.get("generic_name") or [""])[0]
                if brand and len(examples) < 5:
                    examples.append(f"{brand} ({application})")
        if len(verified) >= MIN_LABEL_APPLICATIONS or len(rows) < LABEL_PAGE:
            break
    return {"n_verified": len(verified), "labels_read": read, "examples": examples}


def label_precedent(names: list[str], route: str) -> dict:
    """Approved drug products whose label lists this excipient, by route.

    This exists because the IID has one blind spot big enough to invert an
    answer: it is a CDER database, so BLA biologics barely appear. Polysorbate
    80 subcutaneous is two rows in the IID and eighty distinct BLAs here —
    Trulicity, Keytruda, Aranesp, Crysvita. For a tool screening excipients
    against biologics, that is the population that matters most.

    The search is full text over the SPL DESCRIPTION section, which is where a
    biologic states its composition ("...L-histidine (1.55 mg), polysorbate 80
    (0.5 mg), D-sorbitol..."). Three guards keep that from being sloppy:

      - DESCRIPTION only. A hypersensitivity warning naming an excipient lives
        in WARNINGS or CONTRAINDICATIONS, not here.
      - The product must carry an FDA application number, so unapproved and
        unregistered listings drop out.
      - Distinct applications are counted, not label documents, and
        MIN_LABEL_APPLICATIONS of them are required before this counts as
        precedent at all.
      - Products where the name is the ACTIVE ingredient are excluded, and the
        labels are then read to confirm the excipient is listed as an
        ingredient rather than mentioned — see _is_composition.

    It is still weaker evidence per hit than an IID row, which is a curated
    assertion rather than a text match. It is reported separately for that
    reason, and the application numbers are returned so a reviewer can check.
    """
    key = (tuple(names), route)
    if key in _label_cache:
        return _label_cache[key]

    # "checked" is tracked separately from the hit count on purpose: a route we
    # never queried and a route we queried and found nothing in are different
    # findings, and collapsing them would let a network failure read as absence.
    result = {"checked": False, "applications": {}, "n_applications": 0,
              "n_verified": 0, "labels_read": 0, "examples": [], "matched_name": ""}

    for name in names:
        if not name or not route:
            break
        phrase = quote(name.lower())
        # Products whose ACTIVE ingredient carries the name are not excipient
        # precedent: iron dextran is not dextran the excipient, iron sucrose is
        # not sucrose, and an amino-acid infusion's glycine is the drug.
        query = (f'description:"{phrase}"+AND+openfda.route:"{quote(route)}"'
                 f'+AND+NOT+openfda.substance_name:"{phrase}"')
        try:
            counts = _openfda_get(
                f"{LABELS}?search={query}&count=openfda.application_number.exact&limit=1000"
            )
        except Exception:
            # Network trouble, not a miss. Clear checked even if an earlier
            # spelling came back 404: a half-asked question is not a "no".
            result["checked"] = False
            break
        if counts.status_code == 404:
            result["checked"] = True  # a definitive "no products", not a failure
            continue
        if counts.status_code != 200:
            result["checked"] = False
            break

        applications = [row["term"] for row in counts.json().get("results", [])]
        if not applications:
            continue

        tally: dict[str, int] = {}
        for application in applications:
            tally[application[:3]] = tally.get(application[:3], 0) + 1

        verification = _verify_composition(query, name)
        if verification is None:
            result["checked"] = False  # the count ran but the check could not
            break

        result = {
            "checked": True,
            "applications": dict(sorted(tally.items(), key=lambda kv: -kv[1])),
            "n_applications": len(applications),
            **verification,
            "matched_name": name,
        }
        break

    # An unchecked result is a network failure, not an answer — cache it and a
    # single 429 would read as "no approved labels" until the process restarts.
    if not result["checked"]:
        return result
    if len(_label_cache) >= _SUBSTANCE_CACHE_MAX:
        _label_cache.pop(next(iter(_label_cache)))
    _label_cache[key] = result
    return result


# ---------------------------------------------------------------------------
# The lookup
# ---------------------------------------------------------------------------

# What each finding is worth. The ceiling is advisory to the model; the hard
# gate in main.py only cares whether the level is "route_match".
CEILINGS = {
    "route_match": "A",
    "route_match_above_record": "B",
    "systemic_injection_match": "B",
    "parenteral_match": "C",
    "other_route_only": "C",
    "gras_only": "C",
    "none": "D",
    "unavailable": "D",
}


def _ceiling(level: str, route: str, gras: str) -> str:
    """The grade ceiling, which for a food citation depends on the route.

    A food determination earns its C only when the product is eaten. Acrylamide
    carries a 21 CFR 173 citation (for polyacrylamide in food processing) and
    would otherwise come back at grade C for a subcutaneous injection, which is
    exactly the kind of borrowed authority this tool exists to prevent. A
    permitted food ADDITIVE is also a weaker statement than a GRAS affirmation,
    so it never reaches C on its own either.
    """
    if level != "gras_only":
        return CEILINGS[level]
    if route == "ORAL" and gras in GRAS_PARTS.values():
        return "C"
    return "D"


# Trailing words that mean "the same substance, differently filed": a grade
# number, a hydrate, a salt, a compendial suffix. Anything else after the name
# is a different chemical, and matching on it would be a serious error — the
# IID files SUCROSE STEARATE and SUCROSE PALMITATE, which are surfactants, not
# the stabiliser, and folding their rows into sucrose's would invent precedent.
NAME_QUALIFIERS = {
    "ANHYDROUS", "HYDRATE", "MONOHYDRATE", "DIHYDRATE", "TRIHYDRATE",
    "HEMIHYDRATE", "PENTAHYDRATE", "HEPTAHYDRATE", "DODECAHYDRATE",
    "SESQUIHYDRATE", "SODIUM", "POTASSIUM", "CALCIUM", "MAGNESIUM", "AMMONIUM",
    "HYDROCHLORIDE", "USP", "NF", "FCC", "SOLUTION", "UNSPECIFIED", "FORM",
    # The IID files buffer salts as "SODIUM PHOSPHATE, DIBASIC, DIHYDRATE".
    # Someone asking about sodium phosphate means all of them.
    "MONOBASIC", "DIBASIC", "TRIBASIC",
}

# Words that carry no identity on their own, so they are ignored when two names
# are compared as token sets. Without this, "carboxymethylcellulose sodium" and
# "sodium carboxymethylcellulose" look like different substances.
FILLER_TOKENS = {"ACID", "SALT", "USP", "NF", "FCC", "UNSPECIFIED", "FORM"}


def _tokens(name: str) -> frozenset[str]:
    """The significant words in a name, order and punctuation discarded."""
    words = re.split(r"[\s,;()]+", name)
    return frozenset(w for w in words if w and w not in FILLER_TOKENS)


def _is_qualifier(word: str) -> bool:
    # A bare number is a grade or a molecular weight: POLYETHYLENE GLYCOL 3350.
    return word in NAME_QUALIFIERS or word.replace(".", "", 1).isdigit()


def _same_substance(target: str, candidate: str) -> bool:
    """Is this IID ingredient name the excipient that was asked for?

    Compared as sets of significant words, because word order carries no
    meaning across naming conventions and punctuation carries none at all. The
    IID files "EDETATE DISODIUM" and every formulator types "disodium edetate";
    it writes "SODIUM PHOSPHATE, DIBASIC, DIHYDRATE" and "DIBASIC POTASSIUM
    PHOSPHATE" in the same file, putting the very same qualifier on either side
    of the name.

    So: the same words, or one name's words plus qualifiers only — a grade
    number, a hydrate, a salt, a basicity. "TREHALOSE" has to reach "TREHALOSE
    DIHYDRATE", since the dihydrate is what is actually in the approved
    products.

    The extra words must be entirely qualifiers AND one set must contain the
    other. Requiring containment is what keeps SODIUM CHLORIDE away from
    POTASSIUM CHLORIDE — their difference is qualifiers in both directions, but
    neither contains the other. And no rule here matches on a shared head word
    alone: SUCROSE STEARATE is a surfactant, not sucrose, and folding its rows
    into sucrose's would invent precedent out of nothing.
    """
    if candidate == target:
        return True

    ct, tt = _tokens(candidate), _tokens(target)
    if not ct or not tt:
        return False
    if ct == tt:
        return True
    if ct < tt or tt < ct:
        return all(_is_qualifier(w) for w in ct ^ tt)
    return False


def _match_rows(index: dict, unii: str, name: str) -> tuple[list[dict], list[str]]:
    """Every IID row for this excipient, by UNII and by name.

    Both, unioned, on purpose. The UNII join is exact but misses hydrate and
    salt forms filed under their own code — trehalose dihydrate does not share
    a UNII with trehalose, and it is the dihydrate that is actually in the
    approved products. The name join catches those; the UNII join catches the
    synonyms the name join would miss. Every matched name is reported back so
    the widening is visible rather than silent.
    """
    rows: list[dict] = []
    basis: list[str] = []

    if unii and unii in index["by_unii"]:
        rows.extend(index["by_unii"][unii])
        basis.append(f"UNII {unii}")

    target = canonical_name(name)
    if target:
        matched = [n for n in index["names"] if _same_substance(target, n)]
        for hit in matched:
            rows.extend(index["by_name"][hit])
        if matched:
            basis.append(f"name match on {', '.join(sorted(set(matched))[:6])}")

    # UNII and name can return the same physical row; key on the tuple.
    seen, unique = set(), []
    for row in rows:
        key = (row["name"], row["route"], row["dosage_form"], row["potency"], row["max_daily"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique, basis


def _potency_summary(rows: list[dict]) -> dict:
    """Highest potency on record, per unit.

    Per unit because the file mixes %w/w, %w/v and mg in the same column and
    there is no safe conversion between them without a density and a fill
    volume. Rows carrying no number at all (pH adjusters, "NA") are skipped.
    """
    best: dict[str, float] = {}
    for row in rows:
        for amount, unit in (
            (row["potency"], row["potency_unit"]),
            (row["max_daily"], row["max_daily_unit"] and row["max_daily_unit"] + "/day"),
        ):
            if not amount or not unit or unit in ("NA", "ADJ PH"):
                continue
            try:
                value = float(amount)
            except ValueError:
                continue
            if value > best.get(unit, float("-inf")):
                best[unit] = value
    # Trailing zeros off, so 47.750 reads as 47.75 the way the file writes it.
    return {unit: f"{value:g}" for unit, value in sorted(best.items())}


# What a formulator types, mapped onto the two potency units the IID uses at
# injected routes: %w/v (a concentration) and mg (an amount per dosage unit).
# Only conversions that are definitions are made — 1 %w/v is 10 mg/mL by
# definition. Molar units need a molecular weight and a per-dose amount cannot
# become a concentration without a fill volume, so neither is attempted.
_CONC_UNITS = {
    "%w/v": ("%w/v", 1.0), "%": ("%w/v", 1.0),
    "mg/ml": ("%w/v", 0.1), "g/l": ("%w/v", 0.1),
    "ug/ml": ("%w/v", 1e-4), "mcg/ml": ("%w/v", 1e-4), "µg/ml": ("%w/v", 1e-4),
    "g/100ml": ("%w/v", 1.0),
    "mg": ("mg", 1.0), "g": ("mg", 1000.0),
    "ug": ("mg", 1e-3), "mcg": ("mg", 1e-3), "µg": ("mg", 1e-3),
}


def parse_concentration(text: str) -> dict | None:
    """'0.02% w/v', '10 mg/mL', '47 mg' -> a value in an IID unit, or None."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?|\.\d+)\s*(.*?)\s*$", text or "")
    if not m:
        return None
    unit = re.sub(r"[\s()]", "", m.group(2).lower()).replace("μ", "µ")
    # "5 mg per dose", "5 mg/vial": the IID's mg column is already per dosage unit.
    unit = re.sub(r"(?:per|/)(?:dose|vial|unit|injection|syringe)$", "", unit)
    if unit not in _CONC_UNITS:
        return None
    iid_unit, factor = _CONC_UNITS[unit]
    value = float(m.group(1)) * factor
    return {"value": value, "unit": iid_unit, "interpreted_as": f"{value:g} {iid_unit}"}


def _check_concentration(concentration: str, same: list[dict], label_backed: bool) -> dict:
    """Is the requested concentration within the highest on record at this route?

    "above_record" costs the route match its grade A. Everything short of a
    comparison that actually ran — nothing given, nothing parseable, no IID
    potency in a comparable unit — leaves A available but is reported, so the
    rationale can say the concentration was not checked.
    """
    check = {"as_given": concentration or "", "interpreted_as": "", "highest_on_record": ""}
    if not concentration.strip():
        return {**check, "status": "not_given",
                "note": "No concentration given, so precedent covers the route only. Say so."}
    parsed = parse_concentration(concentration)
    if parsed is None:
        return {**check, "status": "unparsed",
                "note": ("Could not read this as %w/v, mg/mL or mg per dose, so it was not "
                         "compared. Say the concentration was not checked against precedent.")}
    check["interpreted_as"] = parsed["interpreted_as"]
    on_record = _potency_summary(same).get(parsed["unit"])
    if on_record is None:
        where = ("approved product labels, which carry no concentrations"
                 if label_backed and not same else
                 f"IID rows that state no potency in {parsed['unit']}")
        return {**check, "status": "not_comparable",
                "note": (f"Precedent at this route comes from {where}, so the concentration "
                         "could not be compared. Say it was not checked.")}
    check["highest_on_record"] = f"{on_record} {parsed['unit']}"
    # The tolerance is for unit conversion, not leniency: 3 mg/mL is 0.3 %w/v,
    # but 3 * 0.1 is 0.30000000000000004 in floating point.
    if parsed["value"] > float(on_record) * (1 + 1e-9):
        return {**check, "status": "above_record",
                "note": ("Above the highest potency the IID records at this route. Route "
                         "precedent does not reach this level; grade B at best. The IID "
                         "under-represents biologics and some rows state no potency, so this "
                         "is 'above what is on record', not 'above what has been approved'.")}
    return {**check, "status": "within_record",
            "note": "At or below the highest potency the IID records at this route."}


def look_up(excipient: str, route: str, concentration: str = "") -> dict:
    """Precedent for one excipient at one route. The agent's Stage 1 tool.

    Never raises: an unreachable IID comes back as level "unavailable", which
    the evidence gate treats exactly like no precedent at all.
    """
    try:
        index = get_index()
    except RuntimeError as exc:
        return {
            "available": False,
            "precedent_level": "unavailable",
            "max_grade": CEILINGS["unavailable"],
            "note": (
                f"The FDA Inactive Ingredient Database could not be loaded ({exc}). "
                "No precedent was checked. Grade as a data gap and say in the rationale "
                "that the precedent lookup failed — do not fall back to recollection."
            ),
        }

    substance = resolve_substance(excipient)
    rows, basis = _match_rows(index, substance["unii"], excipient)
    wanted = normalise_route(route, index["routes"])

    same = [r for r in rows if r["route"] == wanted] if wanted else []
    elsewhere: dict[str, int] = {}
    for row in rows:
        if row["route"] != wanted:
            elsewhere[row["route"]] = elsewhere.get(row["route"], 0) + 1

    parenteral_elsewhere = {r: n for r, n in elsewhere.items() if r in PARENTERAL_ROUTES}
    systemic_elsewhere = {r: n for r, n in elsewhere.items() if r in SYSTEMIC_INJECTION}

    # The second source, and the one that carries biologics. Ask under the
    # registry's display name as well as the user's, since the label text
    # follows the registry's spelling more often than a trade name.
    labels = label_precedent(
        list(dict.fromkeys(
            n for n in (substance["display_name"], excipient, canonical_name(excipient)) if n
        )),
        wanted,
    )
    label_backed = labels["n_verified"] >= MIN_LABEL_APPLICATIONS

    if same:
        level, source = "route_match", "FDA Inactive Ingredient Database"
    elif label_backed:
        # No IID row at this route, but approved products at this route list it.
        # This is the polysorbate-80-subcutaneous case, and refusing it would
        # mean the tool is wrong about the excipients biologics actually use.
        level = "route_match"
        source = (f"approved product labels ({labels['n_verified']}+ verified as an "
                  f"ingredient, {labels['n_applications']} mention it)")
    elif wanted in SYSTEMIC_INJECTION and systemic_elsewhere:
        # Both ends must be systemic. IV precedent bridges to SC; it does not
        # bridge to intravitreal, where the dose goes into a small closed
        # compartment with its own tolerance and its own failure mode.
        level, source = "systemic_injection_match", "FDA Inactive Ingredient Database"
    elif wanted in PARENTERAL_ROUTES and parenteral_elsewhere:
        level, source = "parenteral_match", "FDA Inactive Ingredient Database"
    elif elsewhere:
        level, source = "other_route_only", "FDA Inactive Ingredient Database"
    elif substance["gras"]:
        level, source = "gras_only", "CFR citation only"
    else:
        level, source = "none", "nothing found in either source"

    concentration_check = None
    if level == "route_match":
        concentration_check = _check_concentration(concentration, same, label_backed)
        if concentration_check["status"] == "above_record":
            level = "route_match_above_record"

    # A source that failed pushes the level down, never up, so this is not a
    # safety problem — but a grade lowered by an outage must say so, or it reads
    # as evidence of absence.
    lookup_errors = []
    if substance.get("error"):
        lookup_errors.append(substance["error"] + "; UNII join and GRAS check skipped")
    if wanted and not labels["checked"]:
        lookup_errors.append(
            "openFDA label search unavailable; approved-label precedent not checked"
        )

    return {
        "available": True,
        "lookup_errors": lookup_errors,
        "source": f"FDA Inactive Ingredient Database ({index['n_rows']} rows), openFDA substance registry",
        "excipient_as_asked": excipient,
        "unii": substance["unii"],
        "registry_name": substance["display_name"],
        "cas": substance["cas"],
        "match_basis": basis or ["no IID row matched this excipient"],
        "route_requested": route,
        "route_matched": wanted or f"'{route}' did not match any FDA route term",
        "precedent_level": level,
        "precedent_basis": source,
        "concentration_check": concentration_check or {
            "status": "not_applicable",
            "note": "No route precedent, so there was no potency to compare against.",
        },
        "max_grade": _ceiling(level, wanted, substance["gras"]),
        "approved_products_at_this_route": {
            "checked": labels["checked"],
            "n_approved_applications": labels["n_applications"],
            "n_verified_as_ingredient": labels["n_verified"],
            "labels_read": labels["labels_read"],
            "by_application_type": labels["applications"],
            "examples": labels["examples"],
            "note": (
                "n_approved_applications counts distinct FDA applications whose DESCRIPTION "
                "section mentions this excipient at this route, excluding products where it is "
                "the active ingredient. n_verified_as_ingredient is how many of those were read "
                "and confirmed to list it as an ingredient (an amount beside it, or a sentence "
                "stating contents); reading stops once the threshold is met, so it is a floor. "
                "Only verified applications count as precedent. BLA = a licensed biologic, the "
                "population the Inactive Ingredient Database misses. Weaker per hit than an IID "
                "row; the examples carry application numbers so they can be checked."
            ),
        },
        "approved_at_this_route": [
            {
                "ingredient": r["name"],
                "dosage_form": r["dosage_form"],
                "max_potency": " ".join(x for x in (r["potency"], r["potency_unit"]) if x)
                or "not stated",
                "max_daily_exposure": " ".join(
                    x for x in (r["max_daily"], r["max_daily_unit"]) if x
                )
                or "not stated",
            }
            for r in sorted(same, key=lambda r: r["dosage_form"])[:12]
        ],
        "highest_on_record_at_this_route": _potency_summary(same),
        "other_parenteral_routes": dict(sorted(parenteral_elsewhere.items())),
        "other_routes": dict(sorted(elsewhere.items())),
        "gras_status": substance["gras"] or "no GRAS or food-additive citation found",
        "cfr_citations": substance["cfr"],
        "interpretation": _INTERPRETATION[level],
        "note": _CAVEAT,
    }


_INTERPRETATION = {
    "route_match": (
        "PRECEDENTED at this route: the excipient appears in an approved drug product by "
        "this route. This is the one finding that supports a 'Precedented' verdict and a "
        "grade A. Say which source carried it — read precedent_basis — and name example "
        "products when approved_products_at_this_route lists them, since 'it is in these "
        "four approved products' is what a formulator can actually act on. Quote dosage "
        "forms and potencies verbatim, and say plainly that precedent at a route is not "
        "clearance at the user's concentration and says nothing about this protein. Read "
        "concentration_check: if its status is not 'within_record', say in the rationale "
        "that the concentration was not checked against precedent, and why."
    ),
    "route_match_above_record": (
        "The excipient is in approved products at this route, but the concentration asked "
        "about is above the highest potency the IID records there. Precedent at a route does "
        "not extend past the levels it was established at: grade B at best, verdict "
        "'Supported without precedent'. Quote the highest on record and the concentration as "
        "interpreted, verbatim, and repeat concentration_check's caveat about what the IID "
        "does not cover."
    ),
    "systemic_injection_match": (
        "No row at the requested route, but the excipient is approved at another systemic "
        "injection route. That is a real bridging argument and nothing more: grade B at "
        "best, verdict 'Supported without precedent', and name the routes it was found at."
    ),
    "parenteral_match": (
        "Approved only at a local or compartmental injection route (intravitreal, "
        "intra-articular and the like), where the dose is microlitres into a closed space. "
        "That does not bridge to a systemic injection. Grade C, verdict "
        "'Supported without precedent', and name the routes."
    ),
    "other_route_only": (
        "Approved only at non-injected routes. Oral or topical use is not evidence for a "
        "parenteral one — the exposure, the immune context and the tolerated impurity "
        "profile all differ. Grade C at best, verdict 'Data gap: test', and name the routes."
    ),
    "gras_only": (
        "No approved-drug precedent at any route. There is a GRAS or food-additive citation, "
        "which covers ingestion only and is close to irrelevant to an injected product — a "
        "permitted food additive is weaker still than a GRAS affirmation. Take the ceiling "
        "from max_grade rather than from the citation, verdict 'Data gap: test', and do not "
        "let a food clearance read as support for a parenteral route."
    ),
    "none": (
        "No approved-drug precedent in either source at any route, and no GRAS citation. "
        "Treat as a data gap: grade D, verdict 'Data gap: test'. Two independent sources "
        "missing it is a stronger signal than one, but it still means 'not found', not "
        "'never used' — say it that way."
    ),
    "unavailable": "The precedent lookup did not run. Treat exactly as if no precedent exists.",
}

_CAVEAT = (
    "Coverage limits, state these when they bear on the answer: (1) the two sources have "
    "different blind spots — the IID is a CDER database that represents BLA biologics "
    "poorly, and the label search is a full-text match that is weaker per hit than a "
    "curated IID row — so say which one carried the finding; (2) precedent means the "
    "excipient has been in an approved product at that route at or below the potency "
    "shown, which is not clearance at the user's concentration and not a statement about "
    "this protein; (3) GRAS is a food determination and does not transfer to a parenteral "
    "route; (4) neither source is a safety finding about the combination being screened. "
    "If lookup_errors is non-empty, part of the lookup failed: say so in the rationale, and "
    "say that a lower grade may reflect the outage rather than a lack of precedent."
)
