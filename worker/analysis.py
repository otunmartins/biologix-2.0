"""Preferential interaction coefficient, from MD frames. No OpenMM in here.

WHAT IS MEASURED. Gamma23 (Timasheff's preferential interaction coefficient) says
whether an excipient crowds up at a protein's surface or stays away from it,
relative to water. Negative: the polymer is excluded and the protein is
preferentially hydrated -- the signature of the classic stabilisers (sucrose,
trehalose, glycerol), because excluding a cosolute costs more free energy the
more surface the protein exposes, which pushes it toward the compact native
state. Positive: the polymer accumulates at the surface, which is what binders
and many denaturants do.

HOW. The two-domain method (Record; Shukla & Trout, J Phys Chem B 2011):

    Gamma23 = < n3_local - n1_local * (n3_bulk / n1_bulk) >

n = number of cosolute (3) or water (1) in the LOCAL domain -- within r_local of
any protein heavy atom -- or the BULK domain beyond r_bulk. Averaged over frames,
with the standard error from block averages so correlated frames are not
counted as independent samples.

POLYMER COUNTING. Gamma23 is defined per molecule, but a 20-mer is 5 nm long and
straddles both domains at once, so counting whole chains by their centre would
be arbitrary. Instead each polymer HEAVY ATOM is counted where it sits and the
total divided by heavy atoms per chain: n3 is in chain equivalents, and a chain
half inside the local domain counts as half. Water is counted by its oxygen.

WHAT IT IS NOT. This is the native state only. An m-value (how the excipient
shifts unfolding free energy) needs Gamma23 of the unfolded state as well; a
negative native-state Gamma23 is consistent with stabilisation, not a proof of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

# Bulk water, mol/L near room temperature. Converts the bulk-domain ratio
# n3/n1 into a concentration without needing the domain's volume.
WATER_MOLAR = 55.3
# A polymer heavy atom within this of a residue's heavy atoms is a contact.
CONTACT_NM = 0.45
# Gamma23 is also reported at these local-domain cutoffs, so whether it has
# plateaued (the local domain holds every perturbed molecule) is on the record.
PROFILE_NM = (0.4, 0.6, 0.8, 1.0, 1.2)


@dataclass
class Accumulator:
    """Frame-by-frame counts; call add() per frame, then result()."""

    heavy_per_chain: int
    residue_labels: list[str]          # one per protein residue, e.g. "LYS 45 A"
    residue_of_atom: np.ndarray        # protein heavy atom -> residue index
    r_local: float = 1.0
    r_bulk: float = 1.2
    gamma: list[float] = field(default_factory=list)
    profile: dict[float, list[float]] = field(default_factory=dict)
    bulk_ratio: list[float] = field(default_factory=list)
    contact_frames: np.ndarray | None = None

    def add(self, protein: np.ndarray, polymer: np.ndarray, water_o: np.ndarray,
            box: np.ndarray) -> float:
        """One frame. Positions in nm, box as the three orthorhombic edge lengths.
        Returns this frame's Gamma23."""
        box = np.asarray(box, dtype=float)
        wrap = lambda x: np.mod(x, box)
        tree = cKDTree(wrap(protein), boxsize=box)
        d3, _ = tree.query(wrap(polymer), k=1)
        d1, _ = tree.query(wrap(water_o), k=1)

        n1_bulk = np.count_nonzero(d1 > self.r_bulk)
        n3_bulk = np.count_nonzero(d3 > self.r_bulk) / self.heavy_per_chain
        if n1_bulk == 0:
            raise ValueError("no water beyond r_bulk: the box is too small for the bulk domain")
        ratio = n3_bulk / n1_bulk
        self.bulk_ratio.append(ratio)

        def gamma_at(r: float) -> float:
            n3 = np.count_nonzero(d3 < r) / self.heavy_per_chain
            n1 = np.count_nonzero(d1 < r)
            return n3 - n1 * ratio

        g = gamma_at(self.r_local)
        self.gamma.append(g)
        for r in PROFILE_NM:
            self.profile.setdefault(r, []).append(gamma_at(r))

        # Which residues the polymer touched this frame.
        if self.contact_frames is None:
            self.contact_frames = np.zeros(len(self.residue_labels), dtype=int)
        if len(polymer):
            ptree = cKDTree(wrap(polymer), boxsize=box)
            near = ptree.query_ball_point(wrap(protein), r=CONTACT_NM, return_length=True)
            touched = np.unique(self.residue_of_atom[near > 0])
            self.contact_frames[touched] += 1
        return g

    def result(self, n_blocks: int = 5) -> dict:
        g = np.asarray(self.gamma)
        if len(g) == 0:
            raise ValueError("no frames analysed")
        blocks = [float(b.mean()) for b in np.array_split(g, min(n_blocks, len(g))) if len(b)]
        se = float(np.std(blocks, ddof=1) / np.sqrt(len(blocks))) if len(blocks) > 1 else None
        n = len(g)
        order = np.argsort(-self.contact_frames)
        contacts = [{"residue": self.residue_labels[i],
                     "fraction": round(float(self.contact_frames[i]) / n, 3)}
                    for i in order[:20] if self.contact_frames[i] > 0]
        return {
            "gamma23": round(float(g.mean()), 3),
            "gamma23_se": round(se, 3) if se is not None else None,
            "gamma23_blocks": [round(b, 3) for b in blocks],
            "n_frames": n,
            "r_local_nm": self.r_local,
            "r_bulk_nm": self.r_bulk,
            "bulk_chain_molar": round(float(np.mean(self.bulk_ratio)) * WATER_MOLAR, 5),
            "gamma23_profile": {f"{r:g}": round(float(np.mean(v)), 3)
                                for r, v in sorted(self.profile.items())},
            "contacts": contacts,
        }
