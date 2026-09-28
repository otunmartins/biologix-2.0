"""2D structure drawings, as SVG, by the same RDKit that screens the molecules.

Drawn here rather than in the browser so the picture is of exactly what the
screen parsed -- one toolkit, one reading of the SMILES -- and so the frontend
needs no chemistry library at all.

Polymer repeat units carry [*] attachment points. dummiesAreAttachments draws
them as the wavy bond ends a chemist expects on a repeat unit, instead of
atoms labelled '*'.

PUBLIC, NOT SIGNED-IN. A drawing is a pure function of the SMILES: it reveals
nothing about any user, and checking the session would cost a database round
trip per thumbnail (twenty for one page of history). What needs guarding is
CPU, so input size is capped, results are cached, and each client address gets
a budget of drawings per minute (main.py).
"""

import time
from collections import deque
from functools import lru_cache

from rdkit import Chem, RDLogger
from rdkit.Chem.Draw import rdMolDraw2D

RDLogger.DisableLog("rdApp.*")

# The longest thing drawn is a screened oligomer, a few hundred characters.
MAX_SMILES = 1000
MIN_SIDE, MAX_SIDE = 32, 800


@lru_cache(maxsize=4096)
def svg(smiles: str, width: int, height: int) -> str | None:
    """The drawing, or None if RDKit cannot parse the SMILES."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    d = rdMolDraw2D.MolDraw2DSVG(width, height)
    o = d.drawOptions()
    o.dummiesAreAttachments = True
    # Transparent, so the drawing sits on whatever card it is placed in.
    o.clearBackground = False
    o.padding = 0.08
    # A ceiling on bond length, so a small repeat unit is not blown up to fill
    # its box and every candidate is drawn at a comparable scale.
    o.fixedBondLength = 30
    o.bondLineWidth = 1.6
    o.additionalAtomLabelPadding = 0.12
    rdMolDraw2D.PrepareAndDrawMolecule(d, mol)
    d.FinishDrawing()
    return d.GetDrawingText()


@lru_cache(maxsize=256)
def conformer_molblock(smiles: str) -> str | None:
    """One 3D conformer of a molecule, heavy atoms only, as an MDL molblock: the
    candidate's chain in the 3D view before any simulation has placed it. ETKDG
    from random coordinates, which embeds long flexible chains that the default
    start fails on, then a short force-field clean-up. Seeded, so the same
    chain always looks the same. None if RDKit cannot parse or embed it."""
    from rdkit.Chem import AllChem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    mol = Chem.AddHs(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = 7
    params.useRandomCoords = True
    params.maxIterations = 200
    if AllChem.EmbedMolecule(mol, params) != 0:
        return None
    try:
        AllChem.MMFFOptimizeMolecule(mol, maxIters=200)
    except Exception:  # noqa: BLE001 - no MMFF parameters: keep the embedded geometry
        pass
    return Chem.MolToMolBlock(Chem.RemoveHs(mol))


class RenderBudget:
    """At most `limit` drawings per client per minute, cached or not. A page of
    history or a long campaign loads a few dozen; this is for a script hammering
    the endpoint with fresh SMILES. In memory: one box, and a limit this short
    does not need to survive a restart."""

    def __init__(self, limit: int = 300, window: float = 60.0):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = {}

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        q = self._hits.setdefault(client, deque())
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        # Forget clients idle for a whole window so the table cannot grow
        # without bound.
        if len(self._hits) > 10_000:
            for k in [k for k, v in self._hits.items() if now - v[-1] > self.window]:
                del self._hits[k]
        return True
