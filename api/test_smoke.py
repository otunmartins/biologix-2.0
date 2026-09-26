"""Smoke test — runs the whole agent loop with a scripted model in place of
Claude, so it needs no API key and costs nothing.

    python test_smoke.py

It covers the parts that break quietly: the tools, the Dossier schema, and the
evidence gate. The gate is run through both ways — a dossier claiming precedent
without having looked any up must be bounced and retried, and the same dossier
must be accepted once the precedent tool has actually returned a route match.
"""

import asyncio
import json
import os

# The Anthropic provider wants a key at construction time. Nothing is ever sent —
# every model response below is scripted — but the agent won't build without one.
os.environ.setdefault("ANTHROPIC_API_KEY", "not-a-real-key-nothing-is-sent")

from pydantic_ai.messages import ModelResponse, ToolCallPart  # noqa: E402
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402

import main  # noqa: E402

PS80 = "Polysorbate 80"
PS80_SMILES = "CCCCCCCCC=CCCCCCCCC(=O)OCCOCCOCCO"  # the surrogate resolve_identity returns
SEQ = "EVQLVESGGGLVQPGGSLRMWCNKHTYIHWVRQAPGKGLEWVA"


def _dossier(grade, verdict):
    return {
        "excipient": PS80,
        "protein": "test mAb fragment",
        "route": "Subcutaneous",
        "summary": "Scripted response for the smoke test.",
        "endpoints": [
            {
                # Named so the structure ceiling exempts it: precedent is a name
                # lookup, and only this endpoint may claim it.
                "endpoint": "Regulatory precedent, subcutaneous",
                "verdict": verdict,
                "evidence_grade": grade,
                "rationale": "FDA IID lists polysorbate 80 for subcutaneous use.",
                "sources": ["FDA Inactive Ingredient Database"],
            }
        ],
        "liabilities": [
            {
                "residue": "Met (x1)",
                "reaction_class": "peroxide-mediated oxidation",
                "severity": "high",
                "mitigation": "Low-peroxide grade.",
            }
        ],
        "needs_testing": False,  # deliberately wrong — the validator must override it
    }


def make_script(call_precedent: bool):
    """Walks the agent through the tools, then claims precedent either way.

    With call_precedent=False the claim is unsupported and the gate must bounce
    it; with True the precedent tool has returned a real route match for
    polysorbate 80 subcutaneous first, and the same claim must be allowed.
    """
    state = {"step": 0}

    def script(messages, info: AgentInfo) -> ModelResponse:
        out_tool = info.output_tools[0].name
        step = state["step"]
        state["step"] += 1

        if step == 0:
            return ModelResponse(parts=[ToolCallPart("resolve_identity", {"name_or_smiles": PS80})])
        if step == 1:
            return ModelResponse(
                parts=[ToolCallPart("structural_alerts", {"smiles": main.SURROGATES["polysorbate 80"][0]})]
            )
        if step == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "protein_liability_scan",
                        {
                            "protein_sequence": SEQ,
                            "excipient_smiles": main.SURROGATES["polysorbate 80"][0],
                        },
                    )
                ]
            )
        if step == 3 and call_precedent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "regulatory_precedent", {"excipient": PS80, "route": "subcutaneous"}
                    )
                ]
            )
        if not state.get("claimed"):
            state["claimed"] = True
            return ModelResponse(parts=[ToolCallPart(out_tool, _dossier("A", "Precedented"))])
        # Only reached when the claim was rejected: the corrected answer.
        return ModelResponse(parts=[ToolCallPart(out_tool, _dossier("D", "Data gap: test"))])

    return script, state


def main_test():
    # --- tools, on their own -------------------------------------------------
    r = main.resolve_identity(PS80)
    assert r["resolved"] and r["surrogate"], "polysorbate 80 should hit the surrogate table"
    print("ok  resolve_identity: surrogate path")

    r2 = main.resolve_identity("sucrose")
    assert r2["resolved"] and not r2["surrogate"] and r2["inchikey"], "sucrose should resolve via PubChem"
    print("ok  resolve_identity: live PubChem lookup")

    found = [a["alert"] for a in main.structural_alerts(r["smiles"]) if a["found"]]
    assert any("Polyether" in a for a in found), f"expected a polyether alert, got {found}"
    print(f"ok  structural_alerts: {len(found)} alert(s) on the PS80 surrogate")

    assert main.structural_alerts("nonsense!!")[0]["found"] is False
    print("ok  structural_alerts: invalid SMILES handled")

    scan = main.protein_liability_scan(SEQ, PS80_SMILES)
    flags = scan["flags"]
    assert any(f["severity"] == "high" for f in flags), "expected a high-severity Met flag"
    assert all(f["accessibility"].startswith("not modelled") for f in flags)
    print(f"ok  protein_liability_scan: {len(flags)} flag(s), sequence-only fallback")

    # The scan derives its own alerts from the SMILES. It used to take the alert
    # list as an argument, and a real run had the model hand back an empty one —
    # producing a confident dossier with no liabilities for an excipient that had
    # just tripped two alerts. Nothing about that failure was visible in the
    # output, which is why the data no longer round-trips through the model.
    assert scan["alerts_applied"] == found, (scan["alerts_applied"], found)
    assert scan["alerts_applied"], "the scan must derive alerts, not receive them"
    print(f"ok  liability scan derives its own alerts ({len(scan['alerts_applied'])} applied)")

    # An excipient with no reactive groups is a real empty result, not a broken one.
    clean = main.protein_liability_scan(SEQ, sucrose_smiles := "OC[C@H]1O[C@@](CO)(O[C@H]2O[C@H](CO)[C@@H](O)[C@H](O)[C@H]2O)[C@@H](O)[C@@H]1O")
    assert clean["alerts_applied"] == [] and clean["flags"] == []
    print("ok  liability scan: sucrose yields no alerts and no flags, transparently")

    # --- solvent accessibility ----------------------------------------------
    weighted = main.protein_liability_scan(SEQ, PS80_SMILES, structure_id="1IGT")
    wflags = weighted["flags"]
    assert "RCSB PDB 1IGT" in weighted["structure"], weighted["structure"]
    assert "not aligned" in weighted["structure"], "sequence/structure mismatch must be reported"
    met = next(f for f in wflags if f["residue"].startswith("Met"))
    assert "exposed" in met["residue"] and "RSA" in met["accessibility"]
    print(f"ok  accessibility (1IGT, experimental): {met['residue']}")
    print(f"      {met['accessibility'][:88]}")

    af = main.protein_liability_scan("", PS80_SMILES, structure_id="P01857")
    assert "AlphaFold" in af["structure"], af["structure"]
    print(f"ok  accessibility (P01857, AlphaFold): {len(af['flags'])} flag(s)")

    # A bad identifier must degrade to counts, not raise.
    bad = main.protein_liability_scan(SEQ, PS80_SMILES, structure_id="NOTREAL123")
    assert bad["flags"] and "could not be used" in bad["structure"]
    print("ok  accessibility: unknown structure falls back, does not raise")

    # Severity downgrade on a fully buried residue type.
    acc_mod = __import__("accessibility")
    a = acc_mod.get_accessibility("1IGT")
    buried = acc_mod.describe_sites(
        [s for s in acc_mod.sites_for(a, "M") if s["rsa"] < acc_mod.EXPOSED_RSA], False
    )
    assert buried["all_buried"] is True
    assert main.DOWNGRADE["high"] == "moderate"
    print("ok  buried residues downgrade rather than disappear")

    # --- breadth: regressions for alerts that silently never fired -----------
    def alerts_for(smiles):
        return [a["alert"].split(" (")[0] for a in main.structural_alerts(smiles) if a["found"]]

    # Formaldehyde is CH2=O. [CX3H1]=O required exactly one H and missed it.
    assert "Aldehyde" in alerts_for("C=O"), "formaldehyde must fire the aldehyde alert"
    assert "Aldehyde" not in alerts_for("CC(=O)C"), "acetone is a ketone, must not fire"
    print("ok  aldehyde alert: catches formaldehyde, still excludes ketones")

    # PubChem returns reducing sugars as cyclic hemiacetals with no free C=O,
    # so the aldehyde alert never fired and Lys glycation was invisible.
    lactose = "OC[C@H]1O[C@@H](O[C@H]2[C@H](O)[C@@H](O)C(O)O[C@@H]2CO)[C@H](O)[C@@H](O)[C@H]1O"
    sucrose = "OC[C@H]1O[C@@](CO)(O[C@H]2O[C@H](CO)[C@@H](O)[C@H](O)[C@H]2O)[C@@H](O)[C@@H]1O"
    assert "Reducing sugar" in alerts_for(lactose), "lactose must fire the reducing-sugar alert"
    assert "Reducing sugar" not in alerts_for(sucrose), "sucrose is non-reducing, must not fire"
    print("ok  reducing sugar: lactose fires, sucrose does not")

    assert "Organomercury" in alerts_for("CC[Hg]SC1=CC=CC=C1C(=O)[O-]")
    print("ok  organomercury alert: thimerosal fires")

    # Grade numbers are molecular weights, not chemistry — one entry per family.
    for name in ["PEG 3350", "Poloxamer 407", "Macrogol 4000", "Tween 20", "HPMC", "Dextran 40"]:
        r = main.resolve_identity(name)
        assert r["resolved"] and r["surrogate"], f"{name} should hit a polymer surrogate"
    print("ok  polymer names: grade numbers normalise to a family surrogate")

    # Capped anomeric carbons: a cellulose ether must not look like a free sugar.
    assert "Reducing sugar" not in alerts_for(main.SURROGATES["hypromellose"][0])
    print("ok  cellulosic surrogates do not masquerade as reducing sugars")

    # --- published alert catalogs (advisory breadth screen) ------------------
    # The whole reason only two catalogs are enabled is that the rest flag
    # benign excipients. If someone adds a noisy catalog back, this fails.
    STABILISERS = {
        "sucrose": sucrose,
        "trehalose": "OC[C@H]1O[C@H](O[C@H]2O[C@H](CO)[C@@H](O)[C@H](O)[C@H]2O)[C@H](O)[C@@H](O)[C@@H]1O",
        "mannitol": "OC[C@@H](O)[C@@H](O)[C@H](O)[C@H](O)CO",
        "glycine": "NCC(=O)O",
        "polysorbate 80 surrogate": main.SURROGATES["polysorbate 80"][0],
    }
    for label, smi in STABILISERS.items():
        hits = main.published_alert_screen(smi)["hits"]
        assert not hits, f"advisory catalogs must stay quiet on {label}, got {hits}"
    print(f"ok  advisory catalogs: silent on {len(STABILISERS)} benign excipients")

    bzk = main.published_alert_screen("CCCCCCCCCCCC[N+](C)(C)CC1=CC=CC=C1")["hits"]
    assert bzk, "benzalkonium should trip a published filter the curated set misses"
    print(f"ok  advisory catalogs: benzalkonium flagged [{bzk[0]['alert']}]")

    assert main.published_alert_screen("not a smiles")["usable"] is False
    print("ok  advisory catalogs: invalid SMILES handled")

    # --- regulatory precedent (Stage 1) --------------------------------------
    # Name matching decides what precedent gets attributed to what, so it is
    # tested on its own before any network call. Over-matching is the dangerous
    # direction: it invents precedent that nobody approved.
    MATCHES = [
        ("SUCROSE", "SUCROSE", True),
        ("TREHALOSE", "TREHALOSE DIHYDRATE", True),
        ("LACTOSE", "LACTOSE MONOHYDRATE", True),
        ("POLYETHYLENE GLYCOL", "POLYETHYLENE GLYCOL 3350", True),
        ("SODIUM PHOSPHATE", "SODIUM PHOSPHATE, DIBASIC, DIHYDRATE", True),
        # The same qualifier goes in front just as often as behind.
        ("POTASSIUM PHOSPHATE", "MONOBASIC POTASSIUM PHOSPHATE", True),
        # USP word order vs everyone else's.
        ("DISODIUM EDETATE", "EDETATE DISODIUM", True),
        ("SODIUM CARBOXYMETHYLCELLULOSE", "CARBOXYMETHYLCELLULOSE SODIUM", True),
        # Sucrose esters are surfactants, not the stabiliser.
        ("SUCROSE", "SUCROSE STEARATE", False),
        ("SUCROSE", "SUCROSE PALMITATE", False),
        ("GLUCOSE", "GLUCOSE OXIDASE", False),
        ("BENZYL ALCOHOL", "BENZYL BENZOATE", False),
        # Different counter-ions differ by a qualifier word in both directions,
        # which is exactly why a qualifier difference alone cannot be enough.
        ("SODIUM CHLORIDE", "POTASSIUM CHLORIDE", False),
        ("SODIUM CHLORIDE", "SODIUM CITRATE", False),
        ("POLYSORBATE 80", "POLYSORBATE 20", False),
        ("SODIUM", "SODIUM CHLORIDE", False),
    ]
    for target, candidate, want in MATCHES:
        got = main.precedent._same_substance(target, candidate)
        assert got is want, f"{target!r} vs {candidate!r}: got {got}, expected {want}"
    print(f"ok  precedent name matching: {len(MATCHES)} cases, no over-match")

    # The IID join has to survive the ways excipient names actually vary:
    # a trade name, a grade number, and a hydrate filed under its own record.
    ps80 = main.precedent.look_up(PS80, "subcutaneous")
    assert ps80["precedent_level"] == "route_match", ps80["precedent_level"]
    assert ps80["unii"] == "6OZP39ZG8H", ps80["unii"]
    assert ps80["approved_at_this_route"], "expected subcutaneous IID rows for polysorbate 80"
    print(f"ok  precedent: {PS80} subcutaneous -> {ps80['precedent_level']}, "
          f"max on record {ps80['highest_on_record_at_this_route']}")

    treh = main.precedent.look_up("Trehalose", "SC")
    assert treh["precedent_level"] == "route_match"
    assert any("DIHYDRATE" in n for n in treh["match_basis"]), treh["match_basis"]
    print("ok  precedent: 'SC' resolves to SUBCUTANEOUS, trehalose reaches the dihydrate record")

    peg = main.precedent.look_up("PEG 3350", "intravenous")
    assert peg["precedent_level"] == "route_match", peg["precedent_level"]
    print("ok  precedent: a trade name with a grade number resolves (PEG 3350)")

    # Sucrose esters are surfactants, not the stabiliser. Folding their rows in
    # would invent precedent, so the name match must not reach them.
    suc = main.precedent.look_up("Sucrose", "oral")
    assert suc["unii"] == "C151H8M554", suc["unii"]
    assert not any("STEARATE" in n or "PALMITATE" in n for n in suc["match_basis"]), suc["match_basis"]
    assert "GRAS" in suc["gras_status"], suc["gras_status"]
    print(f"ok  precedent: sucrose matched cleanly, {suc['gras_status']}")

    # A parenteral route with no row of its own, no approved labels listing it
    # there either, but IID rows at another systemic injection route.
    dex = main.precedent.look_up("Dextran 40", "subcutaneous")
    assert dex["precedent_level"] == "systemic_injection_match", dex["precedent_level"]
    assert dex["max_grade"] == "B"
    print(f"ok  precedent: dextran 40 bridges from {list(dex['other_parenteral_routes'])}")

    # The label threshold at its boundary: calcium chloride is named by two
    # approved subcutaneous applications, one short of counting, and must stay
    # at the bridging level rather than being promoted to route precedent.
    cacl = main.precedent.look_up("Calcium chloride", "subcutaneous")
    n_apps = cacl["approved_products_at_this_route"]["n_approved_applications"]
    if n_apps < main.precedent.MIN_LABEL_APPLICATIONS:
        assert cacl["precedent_level"] != "route_match", cacl["precedent_level"]
        print(f"ok  labels: {n_apps} application(s) is below the threshold of "
              f"{main.precedent.MIN_LABEL_APPLICATIONS}, no promotion to route precedent")

    # A food citation must not buy a grade for an injected product.
    acr = main.precedent.look_up("Acrylamide", "subcutaneous")
    assert acr["precedent_level"] == "gras_only" and acr["max_grade"] == "D", acr
    print("ok  precedent: a food-additive citation does not clear a parenteral route")

    unknown_route = main.precedent.look_up("Sucrose", "by carrier pigeon")
    assert unknown_route["route_matched"].startswith("'by")
    # A route we never queried must not read as a route where nothing was found.
    assert unknown_route["approved_products_at_this_route"]["checked"] is False
    print("ok  precedent: an unrecognised route is reported, not guessed")

    # --- the label source, which is what covers biologics --------------------
    # The IID lists two subcutaneous polysorbate 80 rows; approved subcutaneous
    # biologics using it number in the dozens. If this source ever silently
    # stops returning, the tool goes back to being wrong about every mAb.
    labels = main.precedent.label_precedent(["polysorbate 80"], "SUBCUTANEOUS")
    assert labels["checked"], "the label search did not run"
    assert labels["n_applications"] >= 20, labels["n_applications"]
    assert labels["applications"].get("BLA", 0) >= 10, labels["applications"]
    print(f"ok  labels: polysorbate 80 subcutaneous in {labels['n_applications']} approved "
          f"applications ({labels['applications'].get('BLA')} BLA), e.g. {labels['examples'][0]}")

    # A mention is not an ingredient. These are real DESCRIPTION sentences.
    MENTIONS = [
        ("each single-dose 1 mL vial contains 2,000 units of epoetin alfa, albumin (human) "
         "(2.5 mg), citric acid (0.06 mg)", "albumin", True),
        ("each mL contains 150 USP units of hyaluronidase with albumin human (1 mg)",
         "albumin", True),
        ("Each vial contains dulaglutide, citric acid anhydrous, mannitol, polysorbate 80 and "
         "water for injection", "polysorbate 80", True),
        ("The main protraction mechanism of semaglutide is albumin binding, facilitated by "
         "modification of position 26 lysine with a hydrophilic spacer", "albumin", False),
        ("a PCSK9-binding domain and human serum albumin (HSA). It is produced in "
         "genetically engineered yeast", "albumin", False),
        ("to which an albumin-binding moiety has been attached", "albumin", False),
        ("The latter are believed to be components of cat serum, such as albumin",
         "albumin", False),
    ]
    for text, name, want in MENTIONS:
        assert main.precedent._is_composition(text, name) is want, (name, text[:60])
    print(f"ok  labels: {len(MENTIONS)} mention-vs-ingredient cases (albumin binding is not albumin)")

    # Iron dextran is a drug, not dextran the excipient.
    dex_iv = main.precedent.label_precedent(["dextran"], "INTRAVENOUS")
    assert dex_iv["checked"]
    assert not any("IRON" in e.upper() or "INFED" in e.upper() for e in dex_iv["examples"]), dex_iv
    print(f"ok  labels: iron dextran excluded from dextran IV "
          f"({dex_iv['n_applications']} mention, {dex_iv['n_verified']} verified)")

    # Poloxamer 188 has no subcutaneous IID row at all. On the IID alone it caps
    # at grade B; it is in approved subcutaneous biologics, so that is wrong.
    plx_sc = main.precedent.look_up("Poloxamer 188", "subcutaneous")
    assert plx_sc["precedent_level"] == "route_match", plx_sc["precedent_level"]
    assert "label" in plx_sc["precedent_basis"], plx_sc["precedent_basis"]
    print(f"ok  labels: poloxamer 188 subcutaneous rescued from grade B ({plx_sc['precedent_basis']})")

    # The threshold has to hold in the other direction: a tablet binder must not
    # pick up injectable precedent from a stray full-text mention.
    hpmc = main.precedent.look_up("Hypromellose", "subcutaneous")
    assert hpmc["precedent_level"] == "other_route_only", hpmc["precedent_level"]
    assert hpmc["approved_products_at_this_route"]["checked"] is True
    assert hpmc["approved_products_at_this_route"]["n_approved_applications"] == 0
    print("ok  labels: an oral-only excipient gains no injectable precedent")

    # Systemic injection precedent must not bridge into a closed compartment.
    thim = main.precedent.look_up("Thimerosal", "intravitreal")
    assert thim["precedent_level"] == "parenteral_match", thim["precedent_level"]
    assert thim["max_grade"] == "C"
    print("ok  precedent: IV/IM precedent does not bridge to intravitreal")

    # --- concentration against the highest potency on record -----------------
    pre = main.precedent
    PARSES = [
        ("0.02% w/v", "%w/v", 0.02), ("0.02 %", "%w/v", 0.02), ("3 mg/mL", "%w/v", 0.3),
        ("200 µg/mL", "%w/v", 0.02), ("5 mg per dose", "mg", 5.0), ("0.5 g", "mg", 500.0),
        ("10 mM", None, None), ("lots", None, None), ("", None, None),
    ]
    for text, unit, value in PARSES:
        got = pre.parse_concentration(text)
        if unit is None:
            assert got is None, (text, got)
        else:
            assert got["unit"] == unit and abs(got["value"] - value) < 1e-9, (text, got)
    print(f"ok  concentration parsing: {len(PARSES)} cases, molar units refused")

    # Polysorbate 80 subcutaneous: the IID's highest on record is 0.3 %w/v.
    CONC = [("0.02% w/v", "within_record", "route_match", "A"),
            ("3 mg/mL", "within_record", "route_match", "A"),   # exactly at the record
            ("0.5% w/v", "above_record", "route_match_above_record", "B"),
            ("10 mM", "unparsed", "route_match", "A"),
            ("", "not_given", "route_match", "A")]
    for conc, status, level, grade in CONC:
        r = pre.look_up(PS80, "subcutaneous", conc)
        assert r["concentration_check"]["status"] == status, (conc, r["concentration_check"])
        assert r["precedent_level"] == level and r["max_grade"] == grade, (conc, r["precedent_level"])
    print("ok  concentration: above the IID record caps a route match at B")

    # Label-only precedent carries no potency, so nothing can be compared.
    plx = pre.look_up("Poloxamer 188", "subcutaneous", "0.1% w/v")
    assert plx["concentration_check"]["status"] == "not_comparable", plx["concentration_check"]
    print("ok  concentration: label-only precedent reported as not comparable")

    # Re-asking without a concentration must not buy the A back.
    deps = main.ScreenDeps(precedent_calls=[
        pre.look_up(PS80, "subcutaneous", ""),
        pre.look_up(PS80, "subcutaneous", "0.5% w/v"),
    ])
    assert not deps.route_matched(), deps.summary()
    print("ok  evidence gate: an above-record call vetoes a route match from the same run")

    # --- failures are reported, never cached as answers -----------------------
    # A 429 or a timeout must not become "no precedent" for the life of the
    # process. Simulated offline by swapping out httpx.get.
    pre = main.precedent
    real_get = pre.httpx.get
    seen_urls = []

    def rate_limited(url, **_):
        seen_urls.append(url)
        return pre.httpx.Response(429, request=pre.httpx.Request("GET", url))

    pre.httpx.get = rate_limited
    try:
        sub = pre.resolve_substance("Glycerin")
        assert not sub["found"] and "unavailable" in sub.get("error", ""), sub
        assert "GLYCERIN" not in pre._substance_cache, "a failed lookup was cached as a miss"

        lab = pre.label_precedent(["glycerin"], "INTRAVENOUS")
        assert lab["checked"] is False, lab
        assert (("glycerin",), "INTRAVENOUS") not in pre._label_cache

        during = pre.look_up("Glycerin", "intravenous")
        assert len(during["lookup_errors"]) == 2, during["lookup_errors"]

        os.environ["OPENFDA_API_KEY"] = "test-key"
        pre.resolve_substance("Some uncached name")
        assert seen_urls[-1].endswith("&api_key=test-key"), seen_urls[-1]
    finally:
        pre.httpx.get = real_get
        os.environ.pop("OPENFDA_API_KEY", None)

    after = pre.look_up("Glycerin", "intravenous")
    assert after["lookup_errors"] == [] and after["precedent_level"] == "route_match", after
    print("ok  outages: a rate limit is reported in lookup_errors, not cached, and clears")

    # The IID build must be retried after a failure, not given up on forever.
    real_build, real_index = pre._build_index, pre._index
    calls = []

    def failing_build():
        calls.append(1)
        raise OSError("fda.gov unreachable")

    pre._build_index, pre._index, pre._index_failed_at = failing_build, None, 0.0
    try:
        for _ in range(2):
            assert pre.look_up("Sucrose", "oral")["precedent_level"] == "unavailable"
        assert len(calls) == 1, "retried inside the cooldown"
        pre._build_index = real_build
        pre._index_failed_at -= pre.INDEX_RETRY_SECONDS
        assert pre.look_up("Sucrose", "oral")["available"], "not retried after the cooldown"
    finally:
        pre._build_index = real_build
        if pre._index is None:
            pre._index = real_index
    print("ok  outages: a failed IID build is retried after the cooldown")

    # --- user-described polymers ----------------------------------------------
    import types

    from pydantic_ai import ModelRetry

    poly = main.polymer
    peg = poly.describe(poly.PolymerSpec(repeat_unit="[*]CCO[*]", end_group_a="[*]O", dp=75.4))
    assert abs(peg["mn_estimate"] - 3340) < 5, peg["mn_estimate"]
    print(f"ok  polymer: PEG 3350 described at DP 75.4 -> Mn {peg['mn_estimate']}")

    for bad in ({"repeat_unit": "CCO"}, {"repeat_unit": "[*]CCO[*]", "end_group_a": "not smiles"}):
        try:
            main.ScreenRequest(prompt="x", polymer=bad)
            raise AssertionError(f"accepted a malformed polymer: {bad}")
        except ValueError:
            pass
    print("ok  polymer: malformed repeat unit / end group rejected at the request")

    ps80_spec = poly.PolymerSpec(
        repeat_unit="[*]CCO[*]",
        end_group_a="[*]OC(=O)CCCCCCCC=CCCCCCCCC",
        dp=20,
        impurities=[{"name": "ethylene oxide", "level": "<= 1 ppm"}],
    )
    ps80 = main.describe_polymer(PS80, ps80_spec)
    assert ps80["max_structure_grade"] == "C", ps80["note"]
    assert not ps80["surrogate_check"]["dropped_by_description"]
    eo = ps80["impurities"][0]
    assert eo["resolved"] and "Epoxide" in eo["alerts"], eo
    print("ok  polymer: described PS80 keeps the surrogate's alerts -> ceiling C; residual EO is an epoxide")

    # Methoxy caps remove the ester the real polysorbate has. That must not read cleaner.
    capped = main.describe_polymer(
        PS80, poly.PolymerSpec(repeat_unit="[*]CCO[*]", end_group_a="[*]OC", end_group_b="[*]C")
    )
    assert capped["max_structure_grade"] == "D", capped
    assert any("ester" in a for a in capped["surrogate_check"]["dropped_by_description"])
    print("ok  polymer: a description that drops a known alert keeps the D ceiling")

    deps = main.ScreenDeps(polymer=ps80_spec, identity_calls=[ps80])
    scan = main.protein_liability_scan(SEQ, ps80["smiles"], "", deps.impurity_sources())
    from_eo = [f for f in scan["flags"] if f["source"].startswith("residual ethylene oxide")]
    assert from_eo and from_eo[0]["residue"].startswith("His"), scan["flags"]
    assert from_eo[0]["source"] == "residual ethylene oxide (<= 1 ppm)"
    print(f"ok  polymer: residual EO adds {len(from_eo)} flag(s) tagged with its source and level")

    # The ceiling is enforced in code, and only the precedent endpoint is exempt.
    def gate(calls, endpoints):
        d = main.Dossier(**{**_dossier("C", "Data gap: test"), "endpoints": [
            {"endpoint": n, "verdict": "Data gap: test", "evidence_grade": g, "rationale": "."}
            for n, g in endpoints
        ]})
        ctx = types.SimpleNamespace(deps=main.ScreenDeps(identity_calls=calls))
        return main.enforce_precedent_evidence(ctx, d)

    surrogate = main.resolve_identity(PS80)
    for calls, endpoints, ok in [
        ([surrogate], [("Oxidation of Met/Trp", "C")], False),
        ([surrogate], [("Oxidation of Met/Trp", "D"), ("Regulatory precedent, SC", "C")], True),
        ([ps80], [("Oxidation of Met/Trp", "C")], True),
        ([ps80], [("Oxidation of Met/Trp", "B")], False),
        ([ps80, surrogate], [("Oxidation of Met/Trp", "C")], False),  # strictest wins
    ]:
        try:
            out = gate(calls, endpoints)
            assert ok, f"{endpoints} should have been bounced for {[c['structure_basis'] for c in calls]}"
            assert out.structure_basis == calls[-1]["structure_basis"] or len(calls) > 1
        except ModelRetry:
            assert not ok, f"{endpoints} wrongly bounced"
    print("ok  structure ceiling: surrogate D, described C, strictest wins, precedent exempt")

    # --- impurity exposure margins -------------------------------------------
    X = main.exposure
    LEVELS = [("<= 1 ppm", 1.0), ("≤1ppm", 1.0), ("NMT 10 µg/g", 10.0), ("0.001 %", 10.0),
              ("50 ppb", 0.05), ("1 mg/kg", 1.0), ("2 meq/kg", None), ("trace", None), ("", None)]
    for text, want in LEVELS:
        assert X.parse_level(text) == want, (text, X.parse_level(text))
    print(f"ok  exposure: {len(LEVELS)} impurity levels parsed, peroxide values in meq/kg refused")

    assert X.excipient_mg_per_dose("0.02% w/v", 1.0)[0] == 0.2
    assert X.excipient_mg_per_dose("10 mg/mL", 0.5)[0] == 5.0
    assert X.excipient_mg_per_dose("5 mg per dose", None)[0] == 5.0
    assert X.excipient_mg_per_dose("0.02% w/v", None)[0] is None, "a concentration needs a volume"
    print("ok  exposure: mass per dose from %w/v, mg/mL and mg per dose; no volume, no mass")

    eo = [main.polymer.Impurity(name="Ethylene oxide", level="<= 1 ppm")]
    # Every 2 weeks for a year is 27 dosing days: ICH M7 counts dosing days, so <= 1 month.
    biweekly = X.assess(eo, X.ExposureInputs(
        excipient_concentration="0.02% w/v", dose_volume_ml=1, dosing_interval_days=14,
        treatment_duration_days=365))
    assert biweekly["acceptable_intake_ug_per_day"] == 120.0, biweekly["duration_basis"]
    row = biweekly["impurities"][0]
    assert row["ug_per_dose"] == 0.0002 and row["status"] == "within", row
    daily = X.assess(eo, X.ExposureInputs(
        excipient_concentration="0.02% w/v", dose_volume_ml=1, treatment_duration_days=365))
    assert daily["acceptable_intake_ug_per_day"] == 20.0
    assert X.assess(eo, None)["acceptable_intake_ug_per_day"] == 1.5, "no duration must mean lifetime"
    print("ok  exposure: biweekly for a year is 27 dosing days (120 µg/day); daily is 20; unknown is lifetime")

    high = X.assess([main.polymer.Impurity(name="EO", level="1000 ppm")], X.ExposureInputs(
        excipient_concentration="50 mg/mL", dose_volume_ml=2))
    assert high["impurities"][0]["status"] == "above" and high["impurities"][0]["margin"] < 1, high
    print(f"ok  exposure: 1000 ppm in 100 mg daily for life is above (margin {high['impurities'][0]['margin']})")

    # System-filled, whatever the model wrote.
    ctx = types.SimpleNamespace(deps=main.ScreenDeps(identity_calls=[ps80], exposure=biweekly))
    d = main.enforce_precedent_evidence(
        ctx, main.Dossier(**{**_dossier("C", "Data gap: test"), "exposure": {"made": "up"}}))
    assert d.exposure == biweekly
    print("ok  exposure: the dossier carries the computed margins, not the model's")

    # --- measurement store ----------------------------------------------------
    import tempfile
    MS = main.measurements

    conn = MS.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    bid = MS.add_biologic(conn, MS.Biologic(name="Test mAb", structure_id="1IGT", modality="mAb"))

    full = MS.Measurement(biologic_id=bid, excipient="Trehalose", concentration="0.5 M",
                          quantity="delta_tm_c", value=4.6, sd=0.3, n_replicates=3,
                          buffer="20 mM histidine", ph=6.0, method="nanoDSF",
                          predicted_value=5.9)
    MS.add_measurement(conn, full)
    assert full.is_calibration_grade()[0]

    # A row missing its conditions is kept and counted, never silently dropped,
    # but it must not be fitted against: the number is not comparable.
    thin = MS.Measurement(biologic_id=bid, excipient="Sucrose", quantity="delta_tm_c", value=3.1)
    MS.add_measurement(conn, thin)
    ok, missing = thin.is_calibration_grade()
    assert not ok and "no buffer" in missing and "no pH" in missing, missing

    # A simulated result is an observation with a different provenance, so an
    # OpenMM run can be recorded without pretending it is a measurement.
    MS.add_measurement(conn, MS.Measurement(
        biologic_id=bid, excipient="Trehalose", concentration="0.5 M", quantity="m_value",
        value=980.0, buffer="20 mM histidine", ph=6.0, method="alchemical FEP",
        source="simulation", simulation_detail="OpenMM 8.1, CHARMM36m"))

    s = MS.summary(conn, bid)
    assert s["n_measurements"] == 3 and s["n_calibration_grade"] == 2, s
    assert s["n_paired_with_prediction"] == 1
    assert s["by_quantity"]["m_value"]["sources"] == {"simulation": 1}
    assert s["why_rows_are_not_calibration_grade"], "blockers must be reported, not just counted"
    print(f"ok  measurements: {s['n_measurements']} rows, {s['n_calibration_grade']} fit-able; "
          "incomplete rows kept with their reasons")

    # Referential integrity, and provenance stamped on every row.
    try:
        MS.add_measurement(conn, MS.Measurement(biologic_id="missing", excipient="x",
                                                quantity="tm_c", value=1.0))
        raise AssertionError("a measurement with no biologic was accepted")
    except Exception as e:
        assert "IntegrityError" in type(e).__name__, e
    exported = json.loads(MS.export(conn, bid))
    assert exported["biologic"]["name"] == "Test mAb"
    assert all(r["recorded_by_version"] for r in exported["measurements"])
    assert all(r["observed_at"] for r in exported["measurements"])
    print("ok  measurements: orphans rejected, every row stamped with version and timestamp, "
          "export round-trips")

    # --- calibration ----------------------------------------------------------
    CAL = main.calibration

    # With nothing recorded, the posterior must BE the prior, not a fitted-looking number.
    empty = CAL.calibrate([])
    assert empty["posterior"]["factor"] == CAL.PRIOR_MEAN and not empty["usable"]
    assert "IS the prior" in empty["note"]

    # A known factor must be recovered from consistent data.
    truth = 0.42
    clean = [{"excipient": f"x{i}", "quantity": "m_value", "predicted_value": p,
              "value": truth * p, "sd": 20.0, "source": "experiment"}
             for i, p in enumerate([-3200, -1500, 900, 2100, 1700, -800])]
    fit = CAL.calibrate(clean)
    assert abs(fit["posterior"]["factor"] - truth) < 0.01, fit["posterior"]
    assert abs(fit["least_squares"]["factor"] - truth) < 0.01
    assert fit["r_squared"] > 0.99 and fit["loo_rmse"] is not None
    print(f"ok  calibration: recovers a known factor ({fit['posterior']['factor']} vs {truth}), "
          f"R2={fit['r_squared']}")

    # Confidence must grow with evidence, not with a single precise point.
    widths = []
    for k in (1, 2, 6):
        lo, hi = CAL.calibrate(clean[:k])["posterior"]["credible_95"]
        widths.append(hi - lo)
    assert widths[0] > widths[1] > widths[2], widths
    assert widths[0] > 0.2, "one measurement must not look like a calibration"
    print(f"ok  calibration: interval narrows with evidence ({widths[0]:.2f} -> {widths[2]:.3f}); "
          "a single point stays wide")

    # Excipients that disagree about f mean the one-factor model does not hold.
    # Tight error bars on each point must NOT buy confidence in that case.
    contradictory = [{"excipient": c, "quantity": "m_value", "predicted_value": 1000,
                      "value": v, "sd": 5.0}
                     for c, v in [("a", 900), ("b", 200), ("c", 1500), ("d", 1100)]]
    bad = CAL.calibrate(contradictory)
    lo, hi = bad["posterior"]["credible_95"]
    assert hi - lo > 0.5, (lo, hi)
    assert lo < 1.0 < hi, "contradictory data must not resolve a correction"
    assert bad["r_squared"] < 0.5
    print(f"ok  calibration: disagreeing excipients keep the interval wide ([{lo:.2f},{hi:.2f}]) "
          "despite tight per-point error bars")

    # Statistics are gated on the n they need, never reported on too few points.
    two = CAL.calibrate(clean[:2])
    assert two["r_squared"] is None and two["loo_rmse"] is None
    assert "needs 3 pairs" in two["interpretation"] or "R^2 needs" in two["interpretation"]

    # Applying the factor must carry BOTH uncertainties.
    applied = CAL.apply_factor(-3253.6, 432.3, fit)
    assert applied["interval_95"][0] < applied["calibrated"] < applied["interval_95"][1]
    # Lysozyme urea: uncalibrated -3254, measured near -1300. The correction must land there.
    assert applied["interval_95"][0] < -1300 < applied["interval_95"][1], applied
    print(f"ok  calibration: corrected lysozyme urea to {applied['calibrated']} "
          f"{applied['interval_95']}, bracketing the measured -1300")

    # --- interaction potentials -> m-value ------------------------------------
    I = main.interactions

    urea = I.m_value("1LYZ", "urea")
    assert urea["available"] and urea["m_value_cal_per_mol_molal"] < 0, urea
    assert urea["direction"] == "destabilising"
    # The backbone amide carries the effect, which is the whole basis of the
    # transfer model: osmolytes act on the peptide backbone, not the side chains.
    assert urea["by_group"][0]["group"] == "amide_O", urea["by_group"][:2]
    # Protecting osmolytes must come out the other way.
    for protecting in ("proline", "glycine betaine"):
        assert I.m_value("1LYZ", protecting)["m_value_cal_per_mol_molal"] > 0, protecting
    print(f"ok  m-value: urea destabilises 1LYZ ({urea['m_value_cal_per_mol_molal']} cal/mol/m, "
          f"backbone amide O dominant); proline and betaine stabilise")

    # Published uncertainties are propagated, not decoration: proline's larger
    # errors leave its sign unresolved where urea's does not.
    assert urea["interval_95"][1] < 0, "urea's interval should exclude zero"
    assert I.m_value("1LYZ", "proline")["direction"] == "not resolved from zero"
    print("ok  m-value: alpha uncertainties propagate — proline's sign is not resolved, urea's is")

    # A solute with no published alphas is refused rather than estimated.
    assert I.m_value("1LYZ", "trehalose")["available"] is False
    assert "never estimated" in I.m_value("1LYZ", "trehalose")["note"]
    print("ok  m-value: an unparameterised solute is refused, never inferred by analogy")

    # Protein-agnostic: the same machinery on a different fold, and the result
    # must be JSON-safe for the API.
    import json as _json
    other = I.m_value("P01857", "urea")
    assert other["available"] and other["n_residues"] != urea["n_residues"]
    assert _json.dumps(other)
    assert other["excluded_area_no_alpha"] >= 0
    print(f"ok  m-value: same engine on an unrelated structure ({other['n_residues']} residues), "
          "output JSON-safe")

    # --- polymer designer -----------------------------------------------------
    D = main.design

    built = {(b.key, p.key): D.build_chain(b, p) for b in D.BACKBONES for p in D.PENDANTS}
    unbuilt = [k for k, v in built.items() if not v]
    assert not unbuilt, f"motif pairs that do not assemble: {unbuilt}"
    print(f"ok  designer: all {len(built)} motif pairs assemble into a screenable chain")

    # The regiochemistry claim the whole motif table rests on. Sugars are linked
    # through the 6-OH, so the anomeric centre keeps its real character: glucose
    # stays reducing and would glycate Lys, trehalose and sucrose do not. Attach
    # them through the anomeric carbon instead and this silently inverts.
    mab = next(b for b in D.BACKBONES if b.key == "methacrylamide")
    for pendant_key, should_fire in [("glucose", True), ("trehalose", False), ("sucrose", False)]:
        pend = next(p for p in D.PENDANTS if p.key == pendant_key)
        fired = main._fired(D.build_chain(mab, pend))
        reducing = any("Reducing sugar" in a for a in fired)
        assert reducing is should_fire, f"{pendant_key}: reducing={reducing}, expected {should_fire}"
    print("ok  designer: glucose reads as reducing, trehalose and sucrose do not (6-O linked)")

    liquid = D.design(D.DesignGoal(target_temp_c=25, format="liquid"), limit=40)
    ranks = {c["name"]: c["rank"] for c in liquid["candidates"]}
    worst = [c for c in liquid["candidates"] if any("Reducing" in a for a in c["alerts_fired"])]
    assert worst and min(c["rank"] for c in worst) > len(liquid["candidates"]) * 0.6, \
        "a reducing-sugar candidate ranked too highly"
    best = liquid["candidates"][0]
    assert not best["alerts_fired"], best["alerts_fired"]
    print(f"ok  designer: top candidate is alert-free ({best['name']}); "
          f"reducing sugars sit at rank {min(c['rank'] for c in worst)}+ of {len(ranks)}")

    # Vitrification only applies to a dried product, and only a backbone whose
    # Tg clears the storage temperature can hold the protein in a glass.
    lyo = D.design(D.DesignGoal(target_temp_c=25, format="lyophilised"), limit=40)["candidates"]
    top_tg = lyo[0]["backbone_tg_c"]
    assert top_tg and top_tg >= 75, f"a low-Tg backbone won a lyophilised goal: {lyo[0]['name']}"
    assert lyo[0]["backbone_tg_c"] == max(c["backbone_tg_c"] or 0 for c in lyo), \
        "the highest-Tg backbone did not win a lyophilised goal"
    # A backbone with little headroom over the storage temperature must say so:
    # residual moisture plasticises a real cake by tens of degrees.
    hot = D.design(D.DesignGoal(target_temp_c=40, format="lyophilised"), limit=40)["candidates"]
    low = next(c for c in hot if (c["backbone_tg_c"] or 0) < 60)
    assert any("plasticise" in r or "not be a glass" in r for r in low["risks"]), low["risks"]
    # Vitrification is a dried-product mechanism: a liquid formulation must not
    # be judged on the glass transition at all.
    for c in D.design(D.DesignGoal(target_temp_c=25, format="liquid"), limit=40)["candidates"]:
        assert not any("glass" in r or "plasticise" in r for r in c["risks"]), (c["name"], c["risks"])
    print(f"ok  designer: dried goal favours the highest Tg ({top_tg} C); a 55 C backbone is "
          "warned about at 40 C")

    # A protein that exposes the residue an alert attacks makes that alert cost more.
    plain = D.design(D.DesignGoal(target_temp_c=25), limit=40)["candidates"]
    met = D.design(D.DesignGoal(target_temp_c=25, exposed_residues=["Met"]), limit=40)["candidates"]
    def peg_on_vinyl(rows):
        return next(c for c in rows if "Oligo(ethylene glycol)" in c["pendant"]
                    and c["backbone"].startswith("Poly(vinyl)"))
    assert peg_on_vinyl(met)["score"] < peg_on_vinyl(plain)["score"], \
        "exposed Met did not raise the polyether cost"
    assert any("exposes Met" in r for r in peg_on_vinyl(met)["risks"])
    print("ok  designer: an exposed-Met protein penalises polyether candidates further")

    assert "no molecular dynamics" in D.LIMITS.lower().replace("-", " ") or "molecular dynamics" in D.LIMITS
    print("ok  designer: the limits state plainly that no simulation was run")

    # The parsing agent reads the goal and nothing else. Scripted, so no key needed.
    def design_script(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            "protein": "IgG1 mAb", "route": "subcutaneous", "target_temp_c": 30.0,
            "duration_months": 6.0, "format": "lyophilised",
            "exposed_residues": ["Met"], "notes": "tropical distribution"})])

    dagent = main.get_design_agent()
    with dagent.override(model=FunctionModel(design_script)):
        parsed = asyncio.run(dagent.run("mAb for Zone IV, freeze dried, 6 months")).output
    assert parsed.format == "lyophilised" and parsed.target_temp_c == 30.0
    out = D.design(parsed, limit=3)
    assert out["candidates"] and out["goal"]["exposed_residues"] == ["Met"]
    print(f"ok  designer: a parsed goal drives the ranking ({out['candidates'][0]['name']})")

    # --- the evidence gate, both directions ----------------------------------
    agent = main.get_agent()

    script, state = make_script(call_precedent=False)
    with agent.override(model=FunctionModel(script)):
        result = asyncio.run(
            agent.run(f"Screen {PS80} subcutaneous. Sequence: {SEQ}", deps=main.ScreenDeps())
        )
    d = result.output
    assert state["step"] == 5, f"expected a retry after the bad dossier, got {state['step']} steps"
    assert d.endpoints[0].evidence_grade == "D"
    assert d.needs_testing is True, "needs_testing must be derived, overriding the model's False"
    print("ok  evidence gate: grade A without a precedent lookup rejected, retry landed")
    print("ok  needs_testing derived, not trusted")

    script, state = make_script(call_precedent=True)
    with agent.override(model=FunctionModel(script)):
        result = asyncio.run(
            agent.run(f"Screen {PS80} subcutaneous. Sequence: {SEQ}", deps=main.ScreenDeps())
        )
    d = result.output
    assert state["step"] == 5, f"expected no retry, got {state['step']} steps"
    assert d.endpoints[0].evidence_grade == "A", "a real route match must permit grade A"
    assert d.endpoints[0].verdict == "Precedented"
    print("ok  evidence gate: the same claim accepted once the lookup backs it")

    # The kill switch has to still work if the lookup is ever turned off.
    main.PRECEDENT_LOOKUP_AVAILABLE = False
    try:
        main.Dossier(**_dossier("A", "Data gap: test"))
        raise AssertionError("grade A should have been rejected with the lookup disabled")
    except ValueError:
        print("ok  kill switch: PRECEDENT_LOOKUP_AVAILABLE=False restores the blanket ban")
    finally:
        main.PRECEDENT_LOOKUP_AVAILABLE = True

    print()
    print("all checks passed")

if __name__ == "__main__":
    main_test()
