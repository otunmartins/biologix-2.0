"""Breadth test — does the screen work for excipients other than polysorbate?

    python test_breadth.py

Runs a panel of real formulation excipients through identity resolution and both
alert layers, and asserts on the cases where getting it wrong would actually
mislead a formulator:

  - the two canonical stabilisers (sucrose, trehalose) must stay clean
  - reducing sugars must be flagged, because they glycate Lys
  - the archetypal reactive carbonyls and the thiol-reactive preservative must fire
  - polymer grade numbers must resolve to a family surrogate rather than grade E

This hits PubChem, so it needs a network and takes a minute or so. No API key,
no model call, no cost.
"""

import sys

from rdkit import RDLogger

RDLogger.DisableLog("rdApp.*")

import main

# name -> alerts that MUST fire (substring match), or () for "must stay clean"
EXPECTED = {
    # Stabilisers and tonicity agents: a false positive here would push a
    # formulator away from the right excipient.
    "Sucrose": (),
    "Trehalose": (),
    "Mannitol": (),
    "Sorbitol": (),
    "Glycerol": (),
    "Glycine": (),
    "L-Histidine": (),
    "L-Arginine": (),
    "L-Methionine": (),
    "Sodium chloride": (),
    "Sodium citrate": (),
    "Propylene glycol": (),
    "Tromethamine": (),
    # Reducing sugars: cyclic hemiacetals, no free C=O, so these were silently
    # missed until the anomeric-carbon alert was added.
    "Lactose": ("Reducing sugar",),
    "Maltose": ("Reducing sugar",),
    "D-Glucose": ("Reducing sugar",),
    # Reactive carbonyls. Formaldehyde is CH2=O and needs [CX3;H1,H2]=O.
    "Formaldehyde": ("Aldehyde",),
    "Glutaraldehyde": ("Aldehyde",),
    "Hydrogen peroxide": ("Peroxide",),
    "Thimerosal": ("Organomercury",),
    # Polymers: grade numbers are molecular weights, not different chemistry.
    "Polysorbate 80": ("Polyether chain", "Hydrolysable ester"),
    "Polysorbate 20": ("Polyether chain", "Hydrolysable ester"),
    "Poloxamer 407": ("Polyether chain",),
    "PEG 3350": ("Polyether chain",),
    "Macrogol 4000": ("Polyether chain",),
    # Cellulosics are capped as methyl glycosides: only one chain end is
    # reducing, so the whole polymer must not look like a free sugar.
    "Hypromellose": (),
    "Carboxymethylcellulose sodium": (),
    "Polyvinyl alcohol": (),
    "Dextran 40": (),
}

MUST_RESOLVE = set(EXPECTED)


def run():
    failures, unresolved = [], []
    print(f"{'excipient':32} {'resolved':10} alerts")
    print("-" * 96)

    for name, expected in EXPECTED.items():
        r = main.resolve_identity(name)
        if not r["resolved"]:
            unresolved.append(name)
            print(f"{name:32} {'UNRESOLVED':10}")
            continue

        how = "surrogate" if r["surrogate"] else "pubchem"
        fired = [a["alert"] for a in main.structural_alerts(r["smiles"]) if a["found"]]
        short = [a.split(" (")[0] for a in fired]

        for want in expected:
            if not any(want in a for a in fired):
                failures.append(f"{name}: expected {want!r}, got {short or 'nothing'}")
        if not expected and fired:
            failures.append(f"{name}: expected no alerts, got {short}")

        print(f"{name:32} {how:10} {', '.join(short) or '-'}")

    print()

    # The advisory catalogs exist to add breadth, not noise. If someone enables
    # a catalog that flags stabilisers, this is where it shows up.
    noisy = []
    for name in ("Sucrose", "Trehalose", "Mannitol", "Glycine", "L-Arginine"):
        r = main.resolve_identity(name)
        if r["resolved"] and main.published_alert_screen(r["smiles"])["hits"]:
            noisy.append(name)
    if noisy:
        failures.append(f"advisory catalogs fired on benign excipients: {noisy}")
    else:
        print(f"ok  advisory catalogs silent on 5 benign excipients")

    if unresolved:
        failures.append(f"failed to resolve: {unresolved}")

    resolved = len(EXPECTED) - len(unresolved)
    print(f"ok  resolved {resolved}/{len(EXPECTED)}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nall breadth checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
