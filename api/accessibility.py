"""Solvent accessibility for the liability scan.

Fetches a structure (AlphaFold model by UniProt accession, or an experimental
structure by PDB ID), runs Shrake-Rupley, and returns per-residue relative
solvent accessibility (RSA).

Deliberately kept out of the agent's hands: the LLM passes an identifier in and
gets formatted findings back. It never sees or manipulates a number.

Limits worth knowing:
  - RSA comes from one static structure. Thermal breathing, partial unfolding
    and conformational change at the air-liquid interface can expose a residue
    this says is buried, which is why nothing here ever deletes a liability.
  - An AlphaFold model is a prediction. Low-pLDDT regions are reported as
    low-confidence rather than trusted.
  - No alignment is attempted between the structure and a pasted sequence. If
    both are supplied and they differ, that is reported, not silently patched.
"""

import re
from io import StringIO

import httpx
from Bio.PDB import MMCIFParser
from Bio.PDB.SASA import ShrakeRupley

# Theoretical maximum ASA per residue, Tien et al. 2013 (PLoS ONE 8:e80635).
# RSA = observed SASA / this. Using theoretical rather than empirical values
# keeps RSA from exceeding 1.0 on terminal and highly exposed residues.
MAX_ASA = {
    "ALA": 129.0, "ARG": 274.0, "ASN": 195.0, "ASP": 193.0, "CYS": 167.0,
    "GLN": 225.0, "GLU": 223.0, "GLY": 104.0, "HIS": 224.0, "ILE": 197.0,
    "LEU": 201.0, "LYS": 236.0, "MET": 224.0, "PHE": 240.0, "PRO": 159.0,
    "SER": 155.0, "THR": 172.0, "TRP": 285.0, "TYR": 263.0, "VAL": 174.0,
}

THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q",
    "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K",
    "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
    "TYR": "Y", "VAL": "V",
}

# 20% is the conventional exposed/buried split for RSA.
EXPOSED_RSA = 0.20
# Below this, an AlphaFold model's local geometry isn't worth weighting on.
MIN_PLDDT = 70.0

_cache: dict[str, dict] = {}
_CACHE_MAX = 8


def _looks_like_pdb_id(s: str) -> bool:
    # PDB IDs are 4 chars, first is a digit: 1IGT, 4HKZ. UniProt accessions
    # are 6 or 10 and always start with a letter: P01857, A0A0B4J2F0.
    return bool(re.fullmatch(r"[0-9][A-Za-z0-9]{3}", s.strip()))


def _fetch(structure_id: str) -> tuple[str, str, bool]:
    """Returns (mmCIF text, human-readable source, is_predicted)."""
    sid = structure_id.strip()

    if _looks_like_pdb_id(sid):
        r = httpx.get(f"https://files.rcsb.org/download/{sid.upper()}.cif", timeout=60)
        r.raise_for_status()
        return r.text, f"RCSB PDB {sid.upper()} (experimental)", False

    meta = httpx.get(
        f"https://alphafold.ebi.ac.uk/api/prediction/{sid.upper()}", timeout=60
    )
    meta.raise_for_status()
    entries = meta.json()
    if not entries:
        raise ValueError(f"No AlphaFold model for '{sid}'")
    entry = entries[0]
    cif = httpx.get(entry["cifUrl"], timeout=120)
    cif.raise_for_status()
    version = entry.get("latestVersion", "")
    return (
        cif.text,
        f"AlphaFold DB {entry.get('modelEntityId', sid.upper())} v{version} (predicted)",
        True,
    )


def _compute(structure_id: str) -> dict:
    """Per-residue RSA for every standard amino acid in the first model."""
    cif_text, source, predicted = _fetch(structure_id)

    parser = MMCIFParser(QUIET=True)
    structure = parser.get_structure(structure_id, StringIO(cif_text))
    model = next(structure.get_models())

    ShrakeRupley().compute(model, level="R")

    residues = []
    for chain in model:
        for res in chain:
            name = res.get_resname().upper()
            if name not in MAX_ASA:
                continue  # skips waters, ligands, glycans, non-standard residues
            rsa = res.sasa / MAX_ASA[name]
            # AlphaFold stores per-residue pLDDT in the B-factor column.
            plddt = None
            if predicted and "CA" in res:
                plddt = float(res["CA"].get_bfactor())
            residues.append(
                {
                    "one": THREE_TO_ONE[name],
                    "chain": chain.id,
                    "number": res.id[1],
                    "rsa": round(rsa, 3),
                    "plddt": plddt,
                }
            )

    if not residues:
        raise ValueError(f"No standard amino acids found in structure '{structure_id}'")

    return {
        "source": source,
        "predicted": predicted,
        "residues": residues,
        "sequence": "".join(r["one"] for r in residues),
        "n_chains": len({r["chain"] for r in residues}),
    }


def get_accessibility(structure_id: str) -> dict:
    """Cached wrapper. Structures are big and the same one gets hit repeatedly
    across endpoints within a single screen."""
    key = structure_id.strip().upper()
    if key not in _cache:
        if len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[key] = _compute(key)
    return _cache[key]


def sites_for(accessibility: dict, one_letter: str) -> list[dict]:
    """Every occurrence of one residue type, most exposed first."""
    hits = [r for r in accessibility["residues"] if r["one"] == one_letter]
    return sorted(hits, key=lambda r: -r["rsa"])


def describe_sites(sites: list[dict], predicted: bool) -> dict:
    """Turn a residue type's sites into the numbers the dossier reports.

    Returns the exposed count, the worst (most exposed) sites named, and
    whether the whole set is buried — which is what triggers a downgrade.
    """
    exposed = [s for s in sites if s["rsa"] >= EXPOSED_RSA]
    low_conf = [s for s in sites if s["plddt"] is not None and s["plddt"] < MIN_PLDDT]

    top = sites[:3]
    named = ", ".join(
        f"{s['chain']}{s['number']} (RSA {round(s['rsa'] * 100)}%)" for s in top
    )

    # ASCII only — this string ends up in logs, and Windows consoles default to cp1252.
    note = f"{len(exposed)} of {len(sites)} exposed at RSA >= {int(EXPOSED_RSA * 100)}%"
    if named:
        note += f"; most exposed: {named}"
    if predicted and low_conf:
        note += f"; {len(low_conf)} site(s) in low-confidence regions (pLDDT < {int(MIN_PLDDT)})"

    return {
        "n_total": len(sites),
        "n_exposed": len(exposed),
        "all_buried": len(sites) > 0 and not exposed,
        "note": note,
    }
