"""Smoke test — runs the whole agent loop with a scripted model in place of
Claude, so it needs no API key and costs nothing.

    python test_smoke.py

It covers the parts that break quietly: the tools, the Dossier schema, and the
evidence gate. The gate is run through both ways — a dossier claiming precedent
without having looked any up must be bounced and retried, and the same dossier
must be accepted once the precedent tool has actually returned a route match.
"""

import asyncio
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
                "endpoint": "Oxidation of Met/Trp",
                "verdict": verdict,
                "evidence_grade": grade,
                "rationale": "Polyether chain forms peroxides on storage.",
                "sources": ["PubChem", "local rule table"],
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
