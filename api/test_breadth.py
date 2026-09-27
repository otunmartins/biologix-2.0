"""Breadth test — does the screen work for excipients other than polysorbate?

    python test_breadth.py

Runs a panel of real formulation excipients through identity resolution and both
alert layers, and asserts on the cases where getting it wrong would actually
mislead a formulator:

  - the two canonical stabilisers (sucrose, trehalose) must stay clean
  - reducing sugars must be flagged, because they glycate Lys
  - the archetypal reactive carbonyls and the thiol-reactive preservative must fire
  - polymer grade numbers must resolve to a family surrogate rather than grade E
  - precedent must be found for the excipients that plainly have it, and must
    not be conjured for the ones that don't

This hits PubChem, the FDA Inactive Ingredient Database and openFDA, so it needs
a network and takes a minute or so. No API key, no model call, no cost.
"""

import sys

from rdkit import RDLogger

RDLogger.DisableLog("rdApp.*")

import main
import precedent

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

# Precedent, as a FLOOR rather than an exact level. The IID is republished every
# quarter and rows get added; a test that pins the exact level would start
# failing the first time FDA approves a product that widens one of these. What
# must not happen is the level dropping below the floor, or a hit appearing for
# something that has no approved-drug precedent at all.
PRECEDENT_RANK = ["none", "gras_only", "other_route_only", "parenteral_match",
                  "systemic_injection_match", "route_match"]

PRECEDENT_FLOOR = {
    # Bread-and-butter parenteral excipients: these have route precedent, and a
    # regression that loses it would silently cap every dossier at grade B.
    ("Polysorbate 80", "subcutaneous"): "route_match",
    ("Polysorbate 20", "subcutaneous"): "route_match",
    ("Sucrose", "subcutaneous"): "route_match",
    ("Trehalose", "subcutaneous"): "route_match",
    ("Sodium chloride", "intravenous"): "route_match",
    ("Mannitol", "intravenous"): "route_match",
    ("Sorbitol", "intravenous"): "route_match",
    ("Glycerol", "intravenous"): "route_match",
    ("L-Histidine", "intravenous"): "route_match",
    ("L-Methionine", "intravenous"): "route_match",
    ("Glycine", "intravenous"): "route_match",
    ("PEG 3350", "intravenous"): "route_match",
    ("Benzyl alcohol", "intramuscular"): "route_match",
    ("m-Cresol", "subcutaneous"): "route_match",
    ("Phenol", "subcutaneous"): "route_match",
    ("Sodium acetate", "intravenous"): "route_match",
    ("Chlorobutanol", "intravenous"): "route_match",
    # The naming-convention cases, each of which needed the matcher to do
    # something other than a prefix comparison. A regression here is silent:
    # precedent simply stops being found.
    ("Sodium citrate", "intravenous"): "route_match",       # filed as TRISODIUM CITRATE DIHYDRATE
    ("Disodium edetate", "intravenous"): "route_match",     # filed as EDETATE DISODIUM
    ("Potassium phosphate", "intravenous"): "route_match",  # filed as MONOBASIC POTASSIUM PHOSPHATE
    ("Sodium phosphate", "intravenous"): "route_match",     # filed as SODIUM PHOSPHATE, DIBASIC
    ("EDTA", "intravenous"): "route_match",                 # an abbreviation, not a name
    ("Captisol", "intravenous"): "route_match",             # filed as BETADEX SULFOBUTYL ETHER SODIUM
    # The IID has no subcutaneous row for any of these, and all three are in
    # approved subcutaneous biologics. They are the case the label source
    # exists for: on the IID alone they cap at grade B, which is wrong.
    ("Poloxamer 188", "subcutaneous"): "route_match",
    ("L-Arginine", "subcutaneous"): "route_match",
    ("L-Proline", "subcutaneous"): "route_match",
    ("Dextran 40", "subcutaneous"): "systemic_injection_match",
    # A tablet binder. Oral precedent is not parenteral precedent, and no
    # approved injectable lists it either.
    ("Hypromellose", "subcutaneous"): "other_route_only",
}

# The other direction. A floor cannot catch over-grading, and over-grading is
# the failure that matters: it hands out grade A for precedent nobody
# established. These are the cases where the tempting answer is too generous.
PRECEDENT_CEILING = {
    # Systemic injection precedent does not bridge into a closed compartment.
    # Thimerosal is in approved IM and IV products; that is not an argument for
    # putting it in an eye.
    ("Thimerosal", "intravitreal"): "parenteral_match",
    # Oral and topical precedent must never reach a parenteral route.
    ("Hypromellose", "subcutaneous"): "other_route_only",
    # A food-additive citation is not a drug precedent at any route.
    ("Acrylamide", "subcutaneous"): "gras_only",
}

# The other half of the job: these must NOT come back with route precedent for
# an injection, whatever the model thinks it remembers about them.
NO_PARENTERAL_PRECEDENT = ["Acrylamide", "Glutaraldehyde", "Formaldehyde"]

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

    # --- regulatory precedent ------------------------------------------------
    print()
    for (name, route), floor in PRECEDENT_FLOOR.items():
        r = precedent.look_up(name, route)
        level = r["precedent_level"]
        if PRECEDENT_RANK.index(level) < PRECEDENT_RANK.index(floor):
            failures.append(f"{name} ({route}): precedent {level!r}, expected at least {floor!r}")
        print(f"{name + ' / ' + route:44} {level:26} grade<={r['max_grade']}")

    for (name, route), ceiling in PRECEDENT_CEILING.items():
        r = precedent.look_up(name, route)
        level = r["precedent_level"]
        if PRECEDENT_RANK.index(level) > PRECEDENT_RANK.index(ceiling):
            failures.append(f"{name} ({route}): precedent {level!r}, must be no more than {ceiling!r}")
    print(f"ok  no over-grading on {len(PRECEDENT_CEILING)} cases where the generous answer is wrong")

    for name in NO_PARENTERAL_PRECEDENT:
        r = precedent.look_up(name, "subcutaneous")
        if r["precedent_level"] == "route_match":
            failures.append(f"{name}: claimed subcutaneous route precedent")
        if r["max_grade"] == "A":
            failures.append(f"{name}: grade A ceiling for a subcutaneous route")
    print(f"ok  no parenteral precedent invented for {len(NO_PARENTERAL_PRECEDENT)} reactive agents")

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
