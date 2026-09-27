"""The last frame of a run as a small PDB, for the 3D view of protein and polymer.

Only what a person looks at: the protein's heavy atoms and each polymer chain's,
no water, no ions, no hydrogens -- a few hundred kilobytes rather than the
megabytes of the solvated box. Numpy only, so it is tested without OpenMM.

PERIODIC IMAGES. OpenMM keeps each molecule whole but wraps its centre into the
box, so a chain sitting against the protein across the boundary is drawn a box
length away from it. Each chain is moved to its image nearest the protein's
centre, and everything is centred on the protein, which is how the chains were
actually arranged around it.
"""

from __future__ import annotations

import numpy as np

POLYMER_RESNAME = "POL"
POLYMER_CHAINS = "PQRSTUVWXYZ"


def nearest_image(xyz: np.ndarray, centre: np.ndarray, box: np.ndarray) -> np.ndarray:
    """xyz (n, 3) shifted by whole box lengths so its centroid is nearest centre."""
    shift = np.round((xyz.mean(axis=0) - centre) / box) * box
    return xyz - shift


def _line(record: str, serial: int, name: str, resname: str, chain: str, resseq: int,
          xyz: np.ndarray, element: str) -> str:
    # PDB fixed columns; atom names under four characters start in column 14.
    name = name[:4]
    name = f" {name:<3}" if len(name) < 4 else name
    x, y, z = (xyz * 10.0).tolist()  # nm -> Angstrom
    return (f"{record:<6}{serial % 100000:>5} {name}{' '}{resname[:3]:>3} {chain}{resseq % 10000:>4}    "
            f"{x:>8.3f}{y:>8.3f}{z:>8.3f}{1.0:>6.2f}{0.0:>6.2f}          {element[:2]:>2}")


def pdb(*, xyz: np.ndarray, box: np.ndarray,
        protein: list[tuple[int, str, str, str, int, str]],
        polymer_chains: list[list[tuple[int, str, str]]]) -> str:
    """The frame as PDB text.

    xyz is every atom's position in nm; box the orthorhombic box lengths.
    protein is (atom index, atom name, residue name, chain id, residue number,
    element) per heavy atom; polymer_chains is, per chain, (atom index, atom
    name, element) per heavy atom.
    """
    pidx = np.array([p[0] for p in protein])
    centre = xyz[pidx].mean(axis=0)
    lines, serial = [], 1
    for (i, name, resname, chain, resseq, element), p in zip(protein, xyz[pidx] - centre):
        lines.append(_line("ATOM", serial, name, resname, chain or "A", resseq, p, element))
        serial += 1
    lines.append("TER")
    for k, chain in enumerate(polymer_chains):
        idx = np.array([a[0] for a in chain])
        moved = nearest_image(xyz[idx], centre, box) - centre
        # Chain letters cycle; the residue number tells chains apart for good.
        cid = POLYMER_CHAINS[k % len(POLYMER_CHAINS)]
        for (_, name, element), p in zip(chain, moved):
            lines.append(_line("HETATM", serial, name, POLYMER_RESNAME, cid, k + 1, p, element))
            serial += 1
        lines.append("TER")
    lines.append("END")
    return "\n".join(lines) + "\n"
