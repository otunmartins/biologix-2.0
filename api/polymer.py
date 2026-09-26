"""User-described polymers: repeat unit, end groups, chain length, residuals.

Without this, a polymeric excipient is screened on a fixed 3-mer surrogate
(SURROGATES in main.py) and every structure-derived finding is capped at grade
D. A description lets the screen run on the chemistry the user actually has —
an ester end group or not, a hydroxyl or a methoxy cap — and lets residual
monomers and degradants be screened as the real molecules they are.

What it does NOT do, and says so in every result:
  - Polydispersity is not modelled. DP is an average; the screen runs on one
    representative chain.
  - Architecture is linear. A polysorbate's four sorbitan arms are described as
    one chain with a sorbitan-ester end group, which carries the same reactive
    groups but not the same shape.
  - An impurity's level is echoed, never used to scale a severity. There is no
    exposure model yet.

Structural alerts are substructure-presence tests, so a chain longer than a few
repeat units fires exactly the same alerts. The representative oligomer is
therefore capped at OLIGOMER_MAX units; Mn is still computed from the full DP.
"""

from rdkit import Chem
from rdkit.Chem import Descriptors
from pydantic import BaseModel, Field, field_validator, model_validator

OLIGOMER_MAX = 10


class Impurity(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # As the user wrote it ("<= 1 ppm", "0.5 meq/kg"). Reported, not interpreted.
    level: str = Field(default="", max_length=100)


class PolymerSpec(BaseModel):
    repeat_unit: str = Field(description="SMILES with exactly two [*] attachment points, head first")
    end_group_a: str = Field(default="[*][H]", description="SMILES with one [*], joined to the head")
    end_group_b: str = Field(default="[*][H]", description="SMILES with one [*], joined to the tail")
    dp: float | None = Field(default=None, gt=0, le=100000, description="approximate number-average DP")
    impurities: list[Impurity] = Field(default_factory=list, max_length=10)

    @field_validator("repeat_unit")
    @classmethod
    def _repeat(cls, v: str) -> str:
        _parse(v, 2, "repeat unit")
        return v.strip()

    @field_validator("end_group_a", "end_group_b")
    @classmethod
    def _end(cls, v: str) -> str:
        _parse(v, 1, "end group")
        return v.strip()

    @model_validator(mode="after")
    def _builds(self):
        # Fail at the request, with a readable message, rather than inside a tool
        # call halfway through an agent run.
        build(self, 1)
        return self


def _parse(smiles: str, n_dummies: int, what: str) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles.strip()) if smiles and smiles.strip() else None
    if mol is None:
        raise ValueError(f"{what} {smiles!r} is not valid SMILES")
    found = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 0)
    if found != n_dummies:
        raise ValueError(
            f"{what} {smiles!r} needs exactly {n_dummies} [*] attachment point"
            f"{'s' if n_dummies > 1 else ''}, found {found}"
        )
    return mol


def _labelled(smiles: str, labels: list[int]) -> Chem.Mol:
    mol = Chem.Mol(Chem.MolFromSmiles(smiles))
    dummies = [a for a in mol.GetAtoms() if a.GetAtomicNum() == 0]
    for atom, label in zip(dummies, labels):
        atom.SetAtomMapNum(label)
    return mol


def build_sequence(units: list[str], end_group_a: str = "[*][H]",
                   end_group_b: str = "[*][H]") -> Chem.Mol:
    """end_a-(unit_0)(unit_1)...(unit_k)-end_b, joined head to tail with molzip.

    Each entry in units is a repeat-unit SMILES with exactly two [*]. A copolymer
    is just a chain whose units are not all the same string, so this is the general
    case build() specialises. Alerts are substructure-presence tests, so which units
    appear (not how many times) is what fires them — see design.build_copolymer_chain.
    """
    if not units:
        raise ValueError("build_sequence needs at least one repeat unit")
    combined = _labelled(end_group_a, [1])
    for i, unit in enumerate(units):
        combined = Chem.CombineMols(combined, _labelled(unit, [i + 1, i + 2]))
    combined = Chem.CombineMols(combined, _labelled(end_group_b, [len(units) + 1]))
    mol = Chem.molzip(combined)
    mol = Chem.RemoveHs(mol)
    Chem.SanitizeMol(mol)
    return mol


def build(spec: PolymerSpec, n: int) -> Chem.Mol:
    """end_a-(repeat)n-end_b, joined head to tail with molzip."""
    return build_sequence([spec.repeat_unit] * n, spec.end_group_a, spec.end_group_b)


def describe(spec: PolymerSpec) -> dict:
    """The representative chain and what can honestly be said about it."""
    dp = spec.dp
    n = max(1, min(round(dp), OLIGOMER_MAX)) if dp else OLIGOMER_MAX
    chain = build(spec, n)
    unit_mw = Descriptors.MolWt(build(spec, 2)) - Descriptors.MolWt(build(spec, 1))
    ends_mw = Descriptors.MolWt(build(spec, 1)) - unit_mw
    return {
        "smiles": Chem.MolToSmiles(chain),
        "repeat_units_screened": n,
        "dp": dp,
        "repeat_unit_mw": round(unit_mw, 2),
        "mn_estimate": round(ends_mw + dp * unit_mw, 1) if dp else None,
        "limits": (
            f"Representative linear chain of {n} repeat unit(s) as described by the user. "
            "Structural alerts are substructure-presence tests, so a longer chain fires the same "
            "alerts. Polydispersity and branching architecture are NOT modelled; the description "
            "is the user's, and was not checked against a supplier specification."
        ),
    }
