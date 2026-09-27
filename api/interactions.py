"""m-values from measured solute interaction potentials and buried surface area.

The physical core. For any protein and any parameterised solute:

    m-value = dmu23 = RT * SUM_i ( alpha_i * dASA_i )

where alpha_i is the measured intrinsic interaction potential of functional
group i per unit water-accessible surface area, and dASA_i is how much of that
group's surface the protein buries on folding. Positive m means the solute is
excluded from the surface and stabilises the folded state; negative means it
binds the surface and unfolds it.

This is protein-agnostic by construction. Nothing here knows or cares whether
the structure is insulin, an IgG or a vaccine antigen - it reads atoms, sorts
them into functional groups, and measures area. The alphas are properties of
the solute; the dASA is a property of the molecule you gave it.

PARAMETERS ARE MEASURED, NOT FITTED HERE. The alpha values come from Diehl et
al., "Quantifying Additive Interactions of the Osmolyte Proline with Individual
Functional Groups of Proteins" (Record lab), and each carries its published
standard error. Those errors are propagated, so a prediction arrives as an
interval rather than a number. A solute with no published alphas returns
nothing - it is never estimated by analogy to one that has them.

KNOWN APPROXIMATIONS, all reported in the output rather than hidden:
  - The unfolded state is modelled as each residue fully exposed, normalised to
    the Tien et al. extended Gly-X-Gly reference already used elsewhere in this
    app. A real denatured ensemble retains residual structure, so dASA here is
    an upper bound and |m| is correspondingly an overestimate.
  - Sulfur (Cys SG, Met SD) has no published alpha and is excluded from the sum,
    not set to zero. The excluded area is reported.
  - Histidine ring nitrogens are counted as cationic N. Near neutral pH the
    imidazole is only partly protonated, so this overstates that contribution.
"""

from io import StringIO

from Bio.PDB import ShrakeRupley
from Bio.PDB.MMCIFParser import MMCIFParser

import accessibility

R_CAL = 1.9872  # cal / (mol K)

# Intrinsic interaction potentials, 1e-4 m^-1 A^-2, as (value, standard error).
# Sign convention: positive = excluded from that surface = stabilising.
ALPHA = {
    "proline": {
        "aliphatic_C": (5.3, 1.3), "aromatic_C": (-9.2, 0.9), "hydroxyl_O": (-0.7, 1.3),
        "amide_O": (14.5, 4.5), "amide_N": (-11.8, 3.2), "carboxyl_O": (16.6, 4.3),
        "phosphate_O": (18.0, 5.2), "cationic_N": (-12.6, 4.3),
    },
    "glycine betaine": {
        "aliphatic_C": (3.0, 3.0), "aromatic_C": (-23.0, 4.0), "hydroxyl_O": (1.0, 2.0),
        "amide_O": (28.0, 10.0), "amide_N": (-20.0, 7.0), "carboxyl_O": (29.0, 2.0),
        "phosphate_O": (49.0, 4.0), "cationic_N": (-12.0, 4.0),
    },
    "urea": {
        "aliphatic_C": (-1.1, 0.5), "aromatic_C": (-8.9, 0.5), "hydroxyl_O": (-2.5, 0.6),
        "amide_O": (-8.5, 1.8), "amide_N": (-3.7, 1.6), "carboxyl_O": (-3.7, 1.6),
        "phosphate_O": (-5.8, 1.2), "cationic_N": (1.6, 1.7),
    },
}
ALPHA_SCALE = 1e-4
ALPHA_SOURCE = ("Diehl et al., Record lab: intrinsic interaction potentials per unit "
                "water-accessible surface area, with published standard errors.")

# Atoms carrying no published alpha. Excluded from the sum and reported.
UNPARAMETERISED = {"SG", "SD"}

# Side-chain atom -> functional group. Backbone N/O/C are handled separately.
_SIDE = {
    "ARG": {"NE": "cationic_N", "NH1": "cationic_N", "NH2": "cationic_N"},
    "ASN": {"OD1": "amide_O", "ND2": "amide_N"},
    "ASP": {"OD1": "carboxyl_O", "OD2": "carboxyl_O"},
    "GLN": {"OE1": "amide_O", "NE2": "amide_N"},
    "GLU": {"OE1": "carboxyl_O", "OE2": "carboxyl_O"},
    # Imidazole: counted cationic, which overstates it at neutral pH.
    "HIS": {"ND1": "cationic_N", "NE2": "cationic_N",
            "CG": "aromatic_C", "CD2": "aromatic_C", "CE1": "aromatic_C"},
    "LYS": {"NZ": "cationic_N"},
    "PHE": {"CG": "aromatic_C", "CD1": "aromatic_C", "CD2": "aromatic_C",
            "CE1": "aromatic_C", "CE2": "aromatic_C", "CZ": "aromatic_C"},
    "SER": {"OG": "hydroxyl_O"},
    "THR": {"OG1": "hydroxyl_O"},
    "TRP": {"CG": "aromatic_C", "CD1": "aromatic_C", "CD2": "aromatic_C",
            "CE2": "aromatic_C", "CE3": "aromatic_C", "CZ2": "aromatic_C",
            "CZ3": "aromatic_C", "CH2": "aromatic_C", "NE1": "amide_N"},
    "TYR": {"CG": "aromatic_C", "CD1": "aromatic_C", "CD2": "aromatic_C",
            "CE1": "aromatic_C", "CE2": "aromatic_C", "CZ": "aromatic_C",
            "OH": "hydroxyl_O"},
}


def classify(resname: str, atom: str) -> str | None:
    """Functional group for one atom, or None if it carries no alpha."""
    if atom in UNPARAMETERISED:
        return None
    if atom == "N":
        return "amide_N"
    if atom in ("O", "OXT"):
        return "amide_O"          # OXT is a terminal carboxylate, a rounding error
    group = _SIDE.get(resname, {}).get(atom)
    if group:
        return group
    if atom.startswith("C"):
        return "aliphatic_C"      # includes the backbone carbonyl carbon
    if atom.startswith("O"):
        return "hydroxyl_O"
    if atom.startswith("N"):
        return "amide_N"
    return None


def group_areas(structure_id: str) -> dict:
    """Native and unfolded ASA per functional group, in A^2.

    Native is measured atom by atom on the real structure. Unfolded takes each
    residue as fully exposed, normalised to the Tien extended-state reference,
    so the two are on one scale.
    """
    cif_text, source, predicted = accessibility._fetch(structure_id)
    model = next(MMCIFParser(QUIET=True).get_structure(
        structure_id, StringIO(cif_text)).get_models())
    ShrakeRupley().compute(model, level="A")

    native: dict[str, float] = {}
    unfolded: dict[str, float] = {}
    excluded_native = 0.0
    n_res = 0

    for chain in model:
        for res in chain:
            name = res.get_resname().upper()
            if name not in accessibility.MAX_ASA:
                continue
            n_res += 1
            # Per residue, split the extended-state reference area across its
            # groups in proportion to each atom's own surface, so the unfolded
            # total per residue is exactly the Tien value.
            atoms = [a for a in res if a.element != "H"]
            total = float(sum(a.sasa for a in atoms)) or 1.0
            ref = accessibility.MAX_ASA[name]
            for atom in atoms:
                group = classify(name, atom.get_id())
                if group is None:
                    excluded_native += float(atom.sasa)
                    continue
                native[group] = native.get(group, 0.0) + float(atom.sasa)
                unfolded[group] = unfolded.get(group, 0.0) + float(ref * atom.sasa / total)

    if not n_res:
        raise ValueError(f"No standard amino acids found in '{structure_id}'")

    return {
        "structure": structure_id, "source": source, "predicted": predicted,
        "n_residues": n_res, "native": native, "unfolded": unfolded,
        "excluded_area_no_alpha": round(excluded_native, 1),
    }


def m_value(structure_id: str, solute: str, temp_k: float = 298.15) -> dict:
    """Predicted m-value in cal/mol/molal, with propagated uncertainty.

    Positive: the solute is excluded from the surface buried on folding, so it
    stabilises. Negative: it binds that surface and destabilises.
    """
    key = solute.strip().lower()
    if key not in ALPHA:
        return {"available": False, "solute": solute,
                "note": (f"no published interaction potentials for {solute!r}. Parameterised: "
                         f"{sorted(ALPHA)}. A value is never estimated by analogy.")}

    areas = group_areas(structure_id)
    rt = R_CAL * temp_k
    total, variance, rows = 0.0, 0.0, []

    for group, (alpha, err) in ALPHA[key].items():
        d_asa = areas["unfolded"].get(group, 0.0) - areas["native"].get(group, 0.0)
        term = rt * alpha * ALPHA_SCALE * d_asa
        # Errors on each alpha are independent, so variances add.
        variance += (rt * err * ALPHA_SCALE * d_asa) ** 2
        total += term
        rows.append({"group": group, "alpha": alpha, "alpha_sd": err,
                     "delta_asa": round(d_asa, 1), "contribution": round(term, 1)})

    sd = variance ** 0.5
    rows.sort(key=lambda r: -abs(r["contribution"]))
    return {
        "available": True,
        "solute": solute,
        "structure": structure_id,
        "predicted_by": areas["source"],
        "m_value_cal_per_mol_molal": round(total, 1),
        "uncertainty_sd": round(sd, 1),
        "interval_95": [round(total - 1.96 * sd, 1), round(total + 1.96 * sd, 1)],
        "direction": ("stabilising" if total - 1.96 * sd > 0 else
                      "destabilising" if total + 1.96 * sd < 0 else
                      "not resolved from zero"),
        "by_group": rows,
        "n_residues": areas["n_residues"],
        "excluded_area_no_alpha": areas["excluded_area_no_alpha"],
        "alpha_source": ALPHA_SOURCE,
        "calibration_note": (
            "Against published urea m-values this comes out roughly 2-2.5x high on a small "
            "globular protein (lysozyme: predicted -3254, measured near -1300 cal/mol/M). The "
            "cause is the fully-exposed unfolded model, which overstates dASA. The offset is "
            "one-directional, so the SIGN and the RANKING between solutes are usable now; the "
            "magnitude is not, until the unfolded state is calibrated against measured m-values."
        ),
        "limits": (
            "The unfolded state is modelled as every residue fully exposed, so dASA is an "
            "upper bound and |m| is an overestimate; a real denatured ensemble keeps residual "
            "structure. Sulfur has no published alpha and is excluded from the sum, not "
            "counted as zero. Histidine ring nitrogens are counted as cationic, which "
            "overstates them near neutral pH. The interval covers the published uncertainty "
            "in the alphas only - not the unfolded-state model, which is the larger error."
        ),
    }
