"""What a named biologic actually is, and what that rules in or out.

Insulin, adalimumab and a vaccine antigen are not interchangeable inputs. Each
carries inherent properties - a net charge at the formulation pH, a surface
hydrophobicity, particular exposed residues, free thiols - and those properties
decide which stabilisers are plausible and which are chemically wrong before
anything is ranked.

Everything here is COMPUTED from the sequence and the structure the user gave.
Nothing is looked up by name, so an unpublished construct profiles exactly like
a marketed one, and nothing depends on the model having heard of the molecule.

  from sequence   pI, net charge at pH, molecular weight, GRAVY hydropathy,
                  aromaticity, instability index, cysteine parity
  from structure  which liability residues are actually exposed, and how much,
                  via the same Shrake-Rupley calculation used elsewhere here

The constraints it emits are chemical consequences, not preferences:

  net charge  A protein carrying net positive charge at the formulation pH will
              bind a polyanion electrostatically. That is not stabilisation; it
              is complexation, and it changes the conformational equilibrium.
              Zwitterionic pendants hydrate strongly while carrying no net
              charge, which is why they survive this constraint and ionic ones
              do not.

  pI proximity  Colloidal stability is lowest where the net charge is lowest,
              because there is no electrostatic repulsion holding molecules
              apart. Formulating within ~1 pH unit of the pI is the single
              most common avoidable aggregation problem.

  exposed residues  An alert only matters if the residue it attacks is on the
              surface. Buried Met cannot be oxidised by a peroxide that cannot
              reach it, so exposure is what converts a hazard into a liability.

  free thiol  An odd cysteine count implies an unpaired thiol, which is the
              most reactive handle on most proteins.

WHAT IT DOES NOT DO. It does not predict aggregation, stability or shelf life.
Instability index and GRAVY are sequence heuristics with known limits, reported
as what they are. Cysteine parity infers a free thiol from a count, which a
structure with explicit disulfides would answer properly and a sequence cannot.
"""

from Bio.SeqUtils.ProtParam import ProteinAnalysis

import accessibility

# Residues the excipient rule table can attack, and what reaches them.
LIABILITY_RESIDUES = {
    "M": ("Met", "oxidised by peroxides from autoxidising polyethers"),
    "W": ("Trp", "oxidised by peroxides, and photo-labile"),
    "C": ("Cys", "alkylated by Michael acceptors, maleimides and organomercury"),
    "K": ("Lys", "glycated by reducing sugars and aldehydes"),
    "H": ("His", "alkylated by epoxides such as residual ethylene oxide"),
    "N": ("Asn", "deamidated, accelerated by local pH shifts from ester hydrolysis"),
}

STANDARD_AA = set("ACDEFGHIKLMNPQRSTVWY")
EXPOSED_RSA = 0.20          # the threshold used elsewhere in this app
NEAR_PI_UNITS = 1.0         # how close to the pI counts as a colloidal risk


def _clean(sequence: str) -> str:
    return "".join(c for c in "".join(sequence.split()).upper() if c in STANDARD_AA)


def profile(sequence: str = "", structure_id: str = "", ph: float = 6.0) -> dict:
    """Inherent properties of one biologic, plus the constraints they impose."""
    seq = _clean(sequence)
    # exposed_residues is set up front so every return path carries it: the
    # designer reads this key, and an early return that omitted it would fail
    # there rather than here.
    out: dict = {"ph": ph, "from_sequence": None, "from_structure": None,
                 "constraints": [], "limits": [], "exposed_residues": []}

    if not seq and not structure_id.strip():
        out["limits"].append(
            "Neither a sequence nor a structure was given, so nothing could be computed. "
            "Candidate ranking will be generic rather than specific to this molecule.")
        return out

    # --- structure first: it can supply the sequence if the user did not ------
    residues = None
    if structure_id.strip():
        try:
            access = accessibility.get_accessibility(structure_id)
            residues = access["residues"]
            out["from_structure"] = {
                "structure": structure_id,
                "source": access["source"],
                "predicted": access["predicted"],
                "n_residues": len(residues),
                "n_chains": access["n_chains"],
            }
            if not seq:
                seq = _clean(access["sequence"])
                out["limits"].append(
                    "No sequence was given; the structure's own sequence was used, so "
                    "sequence-derived values describe the deposited construct.")
            elif seq != _clean(access["sequence"]):
                out["limits"].append(
                    f"The sequence given ({len(seq)} aa) differs from the structure's "
                    f"({len(_clean(access['sequence']))} aa). They were not aligned: "
                    "sequence properties come from yours, exposure from the structure.")
        except Exception as e:
            out["limits"].append(
                f"Structure {structure_id!r} could not be used ({type(e).__name__}). "
                "Exposure is unknown, so every liability residue is treated as possibly "
                "exposed rather than assumed buried.")

    # --- sequence properties --------------------------------------------------
    if seq:
        a = ProteinAnalysis(seq)
        pi = a.isoelectric_point()
        charge = a.charge_at_pH(ph)
        n_cys = seq.count("C")
        out["from_sequence"] = {
            "length": len(seq),
            "molecular_weight_da": round(a.molecular_weight(), 1),
            "isoelectric_point": round(pi, 2),
            "net_charge_at_ph": round(charge, 2),
            "gravy_hydropathy": round(a.gravy(), 3),
            "aromaticity": round(a.aromaticity(), 3),
            "instability_index": round(a.instability_index(), 1),
            "n_cysteine": n_cys,
            "cysteine_parity": "odd - implies a free thiol" if n_cys % 2 else "even",
        }
        out["constraints"].extend(_charge_constraints(pi, charge, ph))
        if n_cys % 2:
            out["constraints"].append({
                "constraint": "avoid thiol-reactive chemistry",
                "because": (f"{n_cys} cysteines is odd, which implies one unpaired thiol - the "
                            "most reactive handle on the molecule"),
                "affects": "maleimide, Michael acceptor and organomercury motifs",
                "confidence": "inferred from sequence; a structure with explicit disulfides "
                              "would settle it",
            })
        if a.gravy() > 0:
            out["constraints"].append({
                "constraint": "expect interfacial aggregation; a surfactant is likely needed",
                "because": f"GRAVY {a.gravy():.2f} is positive, i.e. hydrophobic on average",
                "affects": "favours amphiphilic candidates alongside the excluded-volume ones",
                "confidence": "sequence heuristic, not a measurement",
            })

    # --- exposure decides which liabilities are real --------------------------
    exposed: list[str] = []
    if residues is not None:
        counts = {}
        for one, (three, why) in LIABILITY_RESIDUES.items():
            sites = [r for r in residues if r["one"] == one]
            hot = [r for r in sites if r["rsa"] >= EXPOSED_RSA]
            if sites:
                counts[three] = {"total": len(sites), "exposed": len(hot),
                                 "max_rsa": round(max(r["rsa"] for r in sites), 2), "risk": why}
            if hot:
                exposed.append(three)
        out["from_structure"]["liability_residues"] = counts
        for three in exposed:
            out["constraints"].append({
                "constraint": f"penalise motifs that attack {three}",
                "because": (f"{counts[three]['exposed']} of {counts[three]['total']} {three} "
                            f"are solvent-exposed at RSA >= {EXPOSED_RSA:.0%}; "
                            f"{counts[three]['risk']}"),
                "affects": f"any candidate whose alerts reach {three}",
                "confidence": "measured on the supplied structure",
            })
    elif seq:
        # No structure: every present liability residue must be treated as
        # possibly exposed. Assuming burial would quietly clear real hazards.
        exposed = [LIABILITY_RESIDUES[o][0] for o in LIABILITY_RESIDUES if o in seq]
        out["limits"].append(
            "Without a structure, exposure was not modelled: every liability residue present "
            "in the sequence is treated as possibly exposed, which is deliberately pessimistic.")

    # This is the field the designer consumes, so the two stay in step.
    out["exposed_residues"] = exposed
    out["limits"].append(
        "These are computed properties and the chemical constraints that follow from them. "
        "Nothing here predicts aggregation, stability or shelf life. Instability index and "
        "GRAVY are sequence heuristics; a measured Tm or a stability study outranks both.")
    return out


def _charge_constraints(pi: float, charge: float, ph: float) -> list[dict]:
    out = []
    if abs(ph - pi) <= NEAR_PI_UNITS:
        out.append({
            "constraint": f"colloidal stability is poor at pH {ph:g}",
            "because": (f"the pI is {pi:.2f}, within {NEAR_PI_UNITS:g} unit of the formulation "
                        "pH, so net charge is near zero and there is little electrostatic "
                        "repulsion holding molecules apart"),
            "affects": "consider moving the pH before choosing a stabiliser; this is usually "
                       "a bigger effect than the excipient",
            "confidence": "computed from sequence composition",
        })
    if charge > 1:
        out.append({
            "constraint": "avoid anionic pendants",
            "because": (f"net charge is {charge:+.1f} at pH {ph:g}, so a polyanion would bind "
                        "electrostatically - complexation, not stabilisation"),
            "affects": "carboxylate and sulfonate motifs; zwitterions remain available because "
                       "they hydrate strongly while carrying no net charge",
            "confidence": "computed from sequence composition",
        })
    elif charge < -1:
        out.append({
            "constraint": "avoid cationic pendants",
            "because": (f"net charge is {charge:+.1f} at pH {ph:g}, so a polycation would bind "
                        "electrostatically - complexation, not stabilisation"),
            "affects": "quaternary ammonium motifs; zwitterions remain available",
            "confidence": "computed from sequence composition",
        })
    return out
