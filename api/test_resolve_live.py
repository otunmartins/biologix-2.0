"""Breadth test — does the structure lookup work for what users actually type?

    python test_resolve_live.py

A panel of real requests: INN names, the brand name on the vial, recombinant
proteins the databases file under another name, whole sentences, typos, and
things that name no molecule at all.

"no" means the app SHOULD find nothing, and that is as much a result as a hit:
a fusion protein has no structure of its own, and a drug the PDB holds only
bound to its receptor must not be simulated WITH that receptor. Answering those
with a stand-in would quietly simulate the wrong thing, so the honest answer is
to ask the user for an ID.

This hits the RCSB search, RCSB GraphQL and UniProt, so it needs a network and
takes a minute or so. No API key, no model call, no cost. Like test_breadth.py
it is kept out of CI on purpose -- it stays live, to catch the databases
drifting. Run it by hand before a release that touches the lookup.
"""
import concurrent.futures as cf
import sys

import structures as S

PANEL = [
    # --- plain INN names
    ("adalimumab", True), ("trastuzumab", True), ("pembrolizumab", True),
    ("rituximab", True), ("bevacizumab", True), ("infliximab", True),
    ("nivolumab", True), ("ustekinumab", True), ("omalizumab", True),
    ("lysozyme", True), ("asparaginase", True), ("glucagon", True),
    # --- brand names
    ("Humira", True), ("Herceptin", True), ("Keytruda", True), ("Avastin", True),
    ("Rituxan", True), ("Remicade", True), ("Stelara", True), ("Xolair", True),
    ("Neupogen", True), ("Neulasta", True), ("Genotropin", True), ("Lantus", True),
    ("Humulin", True), ("Epogen", True),
    # --- recombinant proteins the databases call something else
    ("filgrastim", True), ("somatropin", True), ("epoetin", True),
    ("rasburicase", True), ("dornase alfa", True), ("aldesleukin", True),
    # --- other proteins
    ("human insulin", True), ("insulin glargine", True), ("human serum albumin", True),
    ("erythropoietin", True), ("human growth hormone", True), ("interferon alpha", True),
    ("factor VIII", True),
    # --- full sentences
    ("find polymers to stabilize adalimumab at 40 degree celcius", True),
    ("stabilise human insulin at room temperature for 12 months", True),
    ("I need excipients for a trastuzumab liquid formulation", True),
    ("keep my lysozyme stable at 25 C", True),
    ("polymers for subcutaneous Humira that survive 6 months", True),
    ("something to keep Keytruda stable that will last a year", True),
    ("excipients that protect my filgrastim during freeze drying", True),
    # --- typos, of molecules and of brands
    ("adamalimumab", True), ("trastuzimab", True), ("insulan", True),
    ("lysozime", True), ("pembrolizimab", True), ("Humria", True), ("Keytruada", True),
    # --- case and punctuation
    ("ADALIMUMAB", True), ("Adalimumab.", True), ("insulin,", True),
    # --- must NOT resolve: nothing specific is named
    ("a 150 mg/mL IgG1 monoclonal antibody", False),
    ("my monoclonal antibody", False),
    ("a therapeutic protein", False),
    ("polymers that survive 6 months", False),
    ("keep it stable for longer", False),
    # --- must NOT resolve: fusion proteins, no structure of the drug alone
    ("etanercept", False), ("Enbrel", False), ("abatacept", False), ("aflibercept", False),
    # --- must NOT resolve: the PDB holds these only bound to their receptor
    ("tocilizumab", False), ("teriparatide", False), ("semaglutide", False),
    ("denosumab", False),
]


def one(item):
    q, must = item
    try:
        return q, must, S.resolve(q), None
    except Exception as e:                      # noqa: BLE001
        return q, must, None, f"{type(e).__name__}: {e}"


def main():
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(one, PANEL))

    good, bad = 0, []
    for q, must, r, err in rows:
        got = err if err else (f"{r['structure_id']} ({r.get('residues')}r)" if r else "NONE")
        ok = (r is not None) == must and not err
        if ok:
            good += 1
        else:
            bad.append((q, must, got))
        mark = "ok " if ok else "FAIL"
        extra = f"   [read as {r['read_as']}]" if r and r.get("read_as") else ""
        print(f"{mark} {q[:52]:52} {'yes' if must else 'no ':3} {got}{extra}")
    print("-" * 100)
    print(f"{good} ok, {len(bad)} FAIL, of {len(rows)}")
    for q, must, got in bad:
        print(f"   FAIL {q!r}: wanted {'a structure' if must else 'NONE'}, got {got}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
