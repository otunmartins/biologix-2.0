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

# The stores are Postgres now, so the suite needs a database of its own: it DROPs
# and recreates the domain tables so a rerun is never polluted by the last one.
# Point TEST_DATABASE_URL at a separate Neon branch (or a second local database),
# never at the one the app uses.
TEST_DB = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")


def fresh_store(module):
    """A connection to the test database with this module's tables freshly made."""
    if not TEST_DB.strip():
        raise SystemExit(
            "set TEST_DATABASE_URL (or DATABASE_URL) to a Postgres connection string. "
            "Locally:  docker run -d --name biologix-pg -e POSTGRES_PASSWORD=devpass "
            "-e POSTGRES_USER=biologix -e POSTGRES_DB=biologix -p 5432:5432 postgres:16")
    conn = main.db.connect(TEST_DB)
    with main.db.tx(conn):
        # CASCADE and the order together: candidate references campaign,
        # measurement references biologic, campaign and sessions reference users.
        conn.execute("DROP TABLE IF EXISTS candidate, iteration, campaign, "
                     "measurement, biologic, screen_run, event, passwords, sessions, accounts, "
                     "verification_token, "
                     "users CASCADE")
    main.db.reset_schema_cache()
    conn.close()
    return module.connect(TEST_DB)


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
    MS = main.measurements

    conn = fresh_store(MS)
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
        assert isinstance(e, main.db.IntegrityError), f"{type(e).__name__}: {e}"
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

    # --- biologic profile -----------------------------------------------------
    PR, D = main.profile, main.design

    INSULIN = "GIVEQCCTSICSLYQLENYCNFVNQHLCGSHLVEALYLVCGERGFFYTPKT"
    LYSOZYME = ("KVFGRCELAAAMKRHGLDNYRGYSLGNWVCAAKFESNFNTQATNRNTDGSTDYGILQINSRWWCNDGRTPGSRN"
                "LCNIPCSALLSSDVSDDIMCAKKILDKVGINYWLAHKALCSEKLDQWLCEKL")

    # Different molecules must profile differently; that is the whole point.
    ins = PR.profile(sequence=INSULIN, ph=7.0)["from_sequence"]
    lys = PR.profile(sequence=LYSOZYME, ph=6.0)["from_sequence"]
    assert ins["net_charge_at_ph"] < 0 < lys["net_charge_at_ph"], (ins, lys)
    assert abs(ins["isoelectric_point"] - 5.4) < 0.5, ins["isoelectric_point"]
    assert abs(lys["isoelectric_point"] - 8.5) < 0.5, lys["isoelectric_point"]
    # Opposite net charges must produce opposite constraints.
    ins_c = " ".join(c["constraint"] for c in PR.profile(sequence=INSULIN, ph=7.0)["constraints"])
    lys_c = " ".join(c["constraint"] for c in PR.profile(sequence=LYSOZYME, ph=6.0)["constraints"])
    assert "cationic" in ins_c and "anionic" in lys_c, (ins_c, lys_c)
    print(f"ok  profile: insulin pI {ins['isoelectric_point']} ({ins['net_charge_at_ph']:+}) rules out "
          f"cationic; lysozyme pI {lys['isoelectric_point']} ({lys['net_charge_at_ph']:+}) rules out anionic")

    # A structure decides which liabilities are real. Lysozyme's Met is buried.
    structured = PR.profile(structure_id="1LYZ", ph=6.0)
    assert "Met" not in structured["exposed_residues"], structured["exposed_residues"]
    assert "Lys" in structured["exposed_residues"]
    # Without a structure it must be pessimistic, never permissive: assuming
    # burial would quietly clear hazards that were simply not modelled.
    seq_only = PR.profile(sequence=LYSOZYME, ph=6.0)
    assert set(structured["exposed_residues"]) < set(seq_only["exposed_residues"])
    assert any("pessimistic" in l for l in seq_only["limits"])
    print(f"ok  profile: 1LYZ exposure narrows liabilities to {structured['exposed_residues']}; "
          "sequence alone stays pessimistic")

    # Nothing supplied must be reported, not silently treated as a clean protein.
    blank = PR.profile()
    assert blank["from_sequence"] is None and blank["exposed_residues"] == []
    assert "nothing could be computed" in blank["limits"][0]

    # The profile must actually change the ranking, and do it symmetrically:
    # an anionic pendant is as wrong for a +ve protein as a cationic one is for
    # a -ve protein, so the penalties must mirror rather than merely both exist.
    def best_ionic(q):
        rows = D.design(D.DesignGoal(target_temp_c=25, net_charge=q, ph=6.0), limit=60)["candidates"]
        pick = lambda kind: next(c for c in rows if c["charge"] == kind)
        return pick("anionic"), pick("cationic")

    a_none, c_none = best_ionic(None)
    a_pos, c_pos = best_ionic(+4.2)
    a_neg, c_neg = best_ionic(-4.2)
    assert a_pos["score"] < c_pos["score"], "anionic must cost more against a +ve protein"
    assert c_neg["score"] < a_neg["score"], "cationic must cost more against a -ve protein"
    assert (a_pos["score"], c_pos["score"]) == (c_neg["score"], a_neg["score"]),         "the charge penalty must be symmetric in the sign of the protein's charge"
    # An unknown charge must sit between the two: penalised, but not condemned.
    assert a_pos["score"] < a_none["score"] < a_neg["score"], "unknown charge must hedge"
    assert any("complexation" in r for r in a_pos["risks"]), a_pos["risks"]
    print(f"ok  profile: charge drives scoring — anionic {a_pos['score']} vs cationic "
          f"{c_pos['score']} against a +ve protein, mirrored for a -ve one, "
          f"{a_none['score']} when unknown")

    # Buried liabilities must cost less than exposed ones.
    def peg_score(exposed):
        g = D.DesignGoal(target_temp_c=25, exposed_residues=exposed, net_charge=4.2, ph=6.0)
        return [c for c in D.design(g, limit=60)["candidates"]
                if "Oligo(ethylene" in c["pendant"]][0]["score"]
    assert peg_score(structured["exposed_residues"]) > peg_score(seq_only["exposed_residues"])
    print("ok  profile: a buried Met lowers the polyether penalty; an exposed one raises it")

    # --- polymer designer -----------------------------------------------------
    D = main.design
    # Sized from the table so adding a motif cannot quietly shrink coverage.
    ALL_PAIRS = len(D.BACKBONES) * len(D.PENDANTS)

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

    liquid = D.design(D.DesignGoal(target_temp_c=25, format="liquid"), limit=ALL_PAIRS)
    ranks = {c["name"]: c["rank"] for c in liquid["candidates"]}
    worst = [c for c in liquid["candidates"] if any("Reducing" in a for a in c["alerts_fired"])]
    assert worst and min(c["rank"] for c in worst) > len(liquid["candidates"]) * 0.6, \
        "a reducing-sugar candidate ranked too highly"
    best = liquid["candidates"][0]
    assert not best["alerts_fired"], best["alerts_fired"]
    print(f"ok  designer: top candidate is alert-free ({best['name']}); "
          f"reducing sugars sit at rank {min(c['rank'] for c in worst)}+ of {len(ranks)}")

    # Vitrification only applies to a dried product, and only a repeat unit whose
    # Tg clears the storage temperature can hold the protein in a glass. The
    # assertion is on tg_judged_c, the lower bound the screen actually scores:
    # backbone_tg_c is the bare backbone's handbook value and is reference only,
    # so testing it would pass while the decision was driven by something else.
    lyo = D.design(D.DesignGoal(target_temp_c=25, format="lyophilised"), limit=ALL_PAIRS)["candidates"]
    top = lyo[0]
    assert top["tg_judged_c"] and top["tg_judged_c"] >= 25, \
        f"a candidate with no glass headroom won a lyophilised goal: {top['name']}"
    # Every candidate above the winner on judged Tg must have lost for a reason
    # the report states, not silently.
    better_tg = [c for c in lyo if (c["tg_judged_c"] or -999) > top["tg_judged_c"]]
    assert all(c["risks"] for c in better_tg), \
        "a candidate with more glass headroom ranked lower with nothing said against it"
    # A repeat unit with little headroom over the storage temperature must say so:
    # residual moisture plasticises a real cake by tens of degrees.
    hot = D.design(D.DesignGoal(target_temp_c=40, format="lyophilised"), limit=ALL_PAIRS)["candidates"]
    low = next(c for c in hot if (c["tg_judged_c"] or 0) < 40)
    assert any("plasticise" in r or "not be a glass" in r or "may not be a glass" in r
               for r in low["risks"]), low["risks"]
    # Vitrification is a dried-product mechanism: a liquid formulation must not
    # be judged on the glass transition at all.
    for c in D.design(D.DesignGoal(target_temp_c=25, format="liquid"), limit=ALL_PAIRS)["candidates"]:
        assert not any("glass" in r or "plasticise" in r for r in c["risks"]), (c["name"], c["risks"])
    print(f"ok  designer: dried goal favours glass headroom (winner judged on "
          f"{top['tg_judged_c']} C, from its {top['tg_source']}); a candidate with none is "
          "warned about at 40 C")

    # The pendant is half the molecule, and the handbook value for the bare
    # backbone cannot see it. Where the model can, its prediction is what runs.
    predicted = [c for c in lyo if c["tg_in_model_domain"]]
    assert predicted, "the Tg model answered for nothing at all"
    assert any(abs(c["tg_c"] - c["backbone_tg_c"]) > 30 for c in predicted
               if c["backbone_tg_c"] is not None), \
        "no predicted Tg differs from its backbone's handbook value, so the pendant is invisible"
    # Falling outside the model's domain must not be an advantage. It used to be:
    # the uncertainty was charged only to candidates the model could speak to,
    # while an out-of-domain one kept the flattering handbook value at full
    # confidence and outranked everything that had actually been looked at.
    for c in lyo:
        if c["tg_in_model_domain"] is False and c["backbone_tg_c"] is not None:
            assert c["tg_judged_c"] < c["backbone_tg_c"], (
                f"{c['name']}: out-of-domain candidate judged on the full handbook value")
    print(f"ok  designer: Tg predicted per repeat unit for {len(predicted)}/{len(lyo)} candidates; "
          "an out-of-domain one cannot hide behind its backbone's handbook value")

    # The pickle is a build artefact and gitignored, so a fresh checkout - which
    # is what the deploy box runs - has no model. That path is the normal one in
    # production and has to degrade to the handbook table, not fail.
    real_predict, D._TG_CACHE = D.tg_model.predict, {}
    try:
        D.tg_model.predict = lambda _: {"available": False, "reason": "no trained Tg model"}
        fallback = D.design(D.DesignGoal(target_temp_c=25, format="lyophilised"),
                            limit=ALL_PAIRS)["candidates"]
    finally:
        D.tg_model.predict, D._TG_CACHE = real_predict, {}
    assert all(c["tg_in_model_domain"] is None for c in fallback), \
        "a candidate claimed a model domain with no model loaded"
    assert fallback[0]["tg_judged_c"] and fallback[0]["tg_judged_c"] >= 25, \
        "the model-less fallback stopped favouring glass headroom"
    assert all(c["tg_c"] == c["backbone_tg_c"] for c in fallback), \
        "the fallback did not fall back to the backbone handbook value"
    print(f"ok  designer: with no trained model the screen still runs on the handbook table "
          f"({fallback[0]['name'][:40]}... judged on {fallback[0]['tg_judged_c']} C)")

    # A protein that exposes the residue an alert attacks makes that alert cost more.
    plain = D.design(D.DesignGoal(target_temp_c=25), limit=ALL_PAIRS)["candidates"]
    met = D.design(D.DesignGoal(target_temp_c=25, exposed_residues=["Met"]), limit=ALL_PAIRS)["candidates"]
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

    # --- copolymers -----------------------------------------------------------
    # A copolymer is a backbone plus a weighted mixture of pendants. The built
    # chain must carry every motif, so it fires the UNION of the components' alerts:
    # a trehalose/oligo(ethylene glycol) blend is clean on the sugar but must still
    # trip the polyether autoxidation alert the glycol brings.
    treh = next(p for p in D.PENDANTS if p.key == "trehalose")
    oeg = next(p for p in D.PENDANTS if p.key == "oligoethylene_glycol")
    glu = next(p for p in D.PENDANTS if p.key == "glucose")
    goal_lyo = D.DesignGoal(target_temp_c=25, format="lyophilised")

    co = D.evaluate(mab, [(treh, 0.5), (oeg, 0.5)], goal_lyo)
    co_alerts = co.alerts
    treh_alerts = D.evaluate(mab, [(treh, 1.0)], goal_lyo).alerts
    oeg_alerts = D.evaluate(mab, [(oeg, 1.0)], goal_lyo).alerts
    assert set(co_alerts) == set(treh_alerts) | set(oeg_alerts), (co_alerts, treh_alerts, oeg_alerts)
    assert any("Polyether" in a for a in co_alerts), "the glycol half must still trip autoxidation"
    print(f"ok  copolymer: a blend fires the union of its motifs' alerts ({len(co_alerts)})")

    # Fox-blended Tg must sit between the two homopolymer Tgs (blending is in Kelvin,
    # so it lands between them) and reduce to the component's own Tg for one pendant.
    treh_tg = D.evaluate(mab, [(treh, 1.0)], goal_lyo).tg["tg_c"]
    oeg_tg = D.evaluate(mab, [(oeg, 1.0)], goal_lyo).tg["tg_c"]
    blend_tg = co.tg["tg_c"]
    assert min(treh_tg, oeg_tg) <= blend_tg <= max(treh_tg, oeg_tg), (treh_tg, oeg_tg, blend_tg)
    assert co.tg["source"].startswith("Fox blend")
    solo = D.evaluate(mab, [(treh, 1.0)], goal_lyo)
    assert solo.tg == D._tg_estimate(mab, D.repeat_unit(mab, treh)), "one pendant must not blend"
    print(f"ok  copolymer: Fox Tg {blend_tg} C between {treh_tg} and {oeg_tg}; single pendant unchanged")

    # The homopolymer path must be byte-for-byte what it was: evaluate() on a single
    # 1.0 pendant reproduces the exhaustive designer's scored candidate exactly.
    homo = D.evaluate(mab, [(treh, 1.0)], goal_lyo)
    all_pairs = len(D.BACKBONES) * len(D.PENDANTS)
    ref = next(c for c in D.design(goal_lyo, limit=all_pairs)["candidates"]
               if c["backbone"] == mab.name and c["pendant"] == treh.name)
    assert homo.score == ref["score"] and homo.tg["judged_tg_c"] == ref["tg_judged_c"]
    print(f"ok  copolymer: single-pendant reduces to the homopolymer score exactly ({homo.score})")

    # --- active learning ------------------------------------------------------
    import random as _random
    A = main.active
    rng = _random.Random(0)
    prior, history = [], []
    for _ in range(4):
        prop = A.propose_batch(goal_lyo, prior, k=10, rng=rng)
        history.append(prop.metrics)
        for c in prop.candidates:
            prior.append(A.PriorCandidate(c.backbone.key,
                                          [(p.key, round(f, 2)) for p, f in c.components], c.score))
    bests = [m["best_score_so_far"] for m in history]
    assert bests == sorted(bests), f"best score must never regress across iterations: {bests}"
    assert bests[-1] > bests[0], f"the loop must actually improve the best score: {bests}"
    keys = [p.key() for p in prior]
    assert len(keys) == len(set(keys)), "the loop proposed a duplicate composition"
    later = [m for m in history if not m["seeded"]]
    assert later and later[-1]["surrogate_cv_mae"] is not None, "surrogate error must be reported once fit"
    assert all(m["batch_diversity"] and m["batch_diversity"] > 0 for m in history), "batches must be diverse"
    print(f"ok  active learning: best score climbs {bests[0]} -> {bests[-1]} over {len(history)} "
          f"iterations, no duplicates, surrogate CV-MAE {later[-1]['surrogate_cv_mae']}")

    # The surrogate models the TRIAGE PROXY, and the loop says so, loudly.
    assert "not stabilisation" in A.ACTIVE_LIMITS.lower() or "not a prediction" in A.ACTIVE_LIMITS.lower()
    print("ok  active learning: the limits state the surrogate models the proxy, not stabilisation")

    # --- campaign store + simulation queue ------------------------------------
    CS = main.candidates
    conn = fresh_store(CS)

    def add_user(email):
        with main.db.tx(conn):
            return conn.execute("INSERT INTO users (name, email) VALUES (%s, %s) RETURNING id",
                                (email.split("@")[0], email)).fetchone()["id"]

    alice, bob = add_user("alice@example.org"), add_user("bob@example.org")
    cid = CS.create_campaign(conn, goal_lyo.model_dump(), owner_id=alice)
    prior2 = []
    for it in range(1, 4):
        loaded = [A.PriorCandidate(p["backbone_key"], p["components"], p["score"])
                  for p in CS.prior_for(conn, cid)]
        prop = A.propose_batch(goal_lyo, loaded, k=8, rng=rng)
        items = [{"payload": D.candidate_dict(c, goal_lyo, i), "backbone_key": c.backbone.key,
                  "components": [[p.key, round(f, 2)] for p, f in c.components], "score": c.score}
                 for i, c in enumerate(prop.candidates, 1)]
        CS.add_candidates(conn, cid, CS.next_iteration(conn, cid), items)
        CS.record_metrics(conn, cid, it, prop.metrics)
    allc = CS.candidates_for(conn, cid)
    assert len(allc) == 24 and CS.next_iteration(conn, cid) == 4, len(allc)
    assert all(c["status"] == "benchmarked" for c in allc)
    assert all(c["id"] and c["iteration"] for c in allc), "stored rows must carry id and iteration"

    # Queue the three highest-scoring, and the backlog must come back score-ordered.
    top3 = sorted(allc, key=lambda c: -c["score"])[:3]
    assert CS.enqueue(conn, [c["id"] for c in top3], owner_id=alice) == 3
    q = CS.queue(conn, owner_id=alice)
    assert [round(c["score"], 2) for c in q] == sorted((round(c["score"], 2) for c in q), reverse=True)
    assert CS.enqueue(conn, [top3[0]["id"]], owner_id=alice) == 0,         "re-queuing an already-queued candidate is a no-op"
    assert CS.queue_summary(conn, owner_id=alice, campaign_id=cid)["n_queued"] == 3
    print(f"ok  campaign store: {len(allc)} candidates over 3 iterations, top 3 queued, "
          "backlog score-ordered, re-queue is a no-op")

    # One user must never see, or act on, another's campaign.
    rest = [c["id"] for c in allc if c["id"] not in {t["id"] for t in top3}]
    assert CS.campaign_state(conn, cid, owner_id=bob) is None, "bob can read alice's campaign"
    assert CS.queue(conn, owner_id=bob) == [], "bob sees alice's queue"
    assert CS.queue_summary(conn, owner_id=bob)["n_queued"] == 0
    assert CS.enqueue(conn, rest, owner_id=bob) == 0, "bob queued alice's candidates"
    assert CS.queue_summary(conn, owner_id=alice)["n_queued"] == 3, "bob's attempt changed alice's queue"
    assert CS.campaign_state(conn, cid, owner_id=alice)["n_candidates"] == 24
    print("ok  campaign store: another user can neither read nor queue a campaign they do not own")

    # Provenance and referential integrity, as the measurement store demands.
    assert all(r["app_version"] for r in conn.execute("SELECT app_version FROM candidate"))
    try:
        CS.add_candidates(conn, "no-such-campaign", 1, [{
            "payload": {}, "backbone_key": mab.key, "components": [[treh.key, 1.0]], "score": 1.0}])
        raise AssertionError("a candidate with no campaign was accepted")
    except Exception as e:
        assert isinstance(e, main.db.IntegrityError), f"{type(e).__name__}: {e}"
    print("ok  campaign store: orphan candidates rejected, every row stamped with a version")

    # --- sign-in, end to end through the HTTP layer ---------------------------
    # The API trusts nothing but a live row in `sessions`, which is what Auth.js
    # writes when someone signs in with Google. So: no cookie, a made-up token and
    # an expired one must all be turned away before any route runs, and a real one
    # must only ever see its own user's data.
    from fastapi.testclient import TestClient

    with main.db.tx(conn):
        for uid, token, expires in [(alice, "tok-alice", "now() + interval '1 day'"),
                                    (bob, "tok-bob", "now() + interval '1 day'"),
                                    (alice, "tok-expired", "now() - interval '1 minute'")]:
            conn.execute(f'INSERT INTO sessions ("userId", expires, "sessionToken") '
                         f"VALUES (%s, {expires}, %s)", (uid, token))
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DB
    try:
        client = TestClient(main.app)

        def as_(token, prod=False):
            name = "__Secure-authjs.session-token" if prod else "authjs.session-token"
            return {"Cookie": f"{name}={token}"} if token else {}

        assert client.get("/health").status_code == 200, "/health must stay public"
        for label, token in [("no cookie", None), ("unknown token", "tok-made-up"),
                             ("expired session", "tok-expired")]:
            for method, path, body in [("get", "/design/queue", None),
                                       ("get", f"/design/campaign/{cid}", None),
                                       ("post", "/design/queue", {"candidate_ids": rest}),
                                       ("post", "/screen", {"prompt": "Screen sucrose IV"}),
                                       ("post", "/design", {"prompt": "stabilise an mAb"})]:
                r = client.request(method, path, json=body, headers=as_(token))
                assert r.status_code == 401, f"{label}: {method.upper()} {path} gave {r.status_code}"

        r = client.get("/design/queue", headers=as_("tok-alice"))
        assert r.status_code == 200 and len(r.json()["queue"]) == 3, r.text
        # The __Secure- name is what the browser sends once the site is on HTTPS.
        r = client.get(f"/design/campaign/{cid}", headers=as_("tok-alice", prod=True))
        assert r.status_code == 200 and r.json()["n_candidates"] == 24, r.text

        r = client.get("/design/queue", headers=as_("tok-bob"))
        assert r.status_code == 200 and r.json()["queue"] == [], "bob sees alice's queue over HTTP"
        r = client.get(f"/design/campaign/{cid}", headers=as_("tok-bob"))
        assert r.status_code == 404, "another user's campaign must look like no campaign at all"
        r = client.post("/design/queue", json={"candidate_ids": rest}, headers=as_("tok-bob"))
        assert r.status_code == 200 and r.json()["queued"] == 0, r.text
    finally:
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url
    print("ok  sign-in: no cookie, unknown and expired sessions get 401 on every route; "
          "a live one sees only its own campaigns; /health stays public")

    # --- history and provenance, end to end through the HTTP layer -----------
    os.environ["DATABASE_URL"] = TEST_DB
    try:
        client = TestClient(main.app)
        agent = main.get_agent()

        # Structure drawings are public and pure: no cookie needed, junk refused.
        r = client.get("/structure.svg", params={"smiles": "[*]N(CC[*])C(=O)CCO"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml"), r.status_code
        assert "<svg" in r.text
        assert client.get("/structure.svg", params={"smiles": "C1CC(("}).status_code == 422
        assert client.get("/structure.svg", params={"smiles": "C" * 1001}).status_code == 422
        print("ok  structures: repeat units draw as SVG without signing in; bad or oversized SMILES get 422")

        # A screen is saved with everything that produced it.
        form = {"excipient": PS80, "route": "subcutaneous", "dose": "150"}
        script, _ = make_script(call_precedent=True)
        with agent.override(model=FunctionModel(script)):
            r = client.post("/screen", headers=as_("tok-alice"),
                            json={"prompt": f"Screen {PS80} subcutaneous. Sequence: {SEQ}", "form": form})
        assert r.status_code == 200, r.text
        dossier = r.json()
        sid = dossier["history_id"]
        assert sid, "a saved screen must say where it was saved"
        assert dossier["structure_smiles"] == main.SURROGATES["polysorbate 80"][0], \
            "the drawn structure must be the one resolve_identity screened"
        rec = client.get(f"/history/screens/{sid}", headers=as_("tok-alice")).json()
        prov = rec["provenance"]
        assert rec["status"] == "ok" and rec["request"]["form"] == form
        assert rec["dossier"]["excipient"] == dossier["excipient"]
        assert prov["model"] == main.SCREEN_MODEL and prov["app_version"]
        assert prov["identity_calls"] and prov["identity_calls"][0]["smiles"] == dossier["structure_smiles"]
        assert prov["precedent_calls"], "what the FDA lookup returned must be on the record"
        assert [t["tool"] for t in prov["tool_trace"]][:4] == [
            "resolve_identity", "structural_alerts", "protein_liability_scan", "regulatory_precedent"]
        assert [e["kind"] for e in rec["events"]] == ["screen.completed"]
        print("ok  history: a screen is saved with its request, form, dossier, model, build, "
              "tool sequence and what each data source returned")

        # A failed run is history too.
        def broken(messages, info):
            raise RuntimeError("model unavailable")
        with agent.override(model=FunctionModel(broken)):
            r = client.post("/screen", headers=as_("tok-alice"), json={"prompt": "Screen sucrose IV"})
        assert r.status_code == 502
        failed = client.get("/history", params={"kind": "screen"}, headers=as_("tok-alice")).json()["items"][0]
        assert failed["status"] == "failed" and "model unavailable" in failed["error"]
        print("ok  history: a failed screen is recorded as failed, with its error")

        # If saving fails, the user still gets their dossier, marked unsaved.
        real_record = main.history.record_screen
        def unsavable(*a, **k):
            raise RuntimeError("database write failed")
        main.history.record_screen = unsavable
        try:
            script, _ = make_script(call_precedent=True)
            with agent.override(model=FunctionModel(script)):
                r = client.post("/screen", headers=as_("tok-alice"),
                                json={"prompt": f"Screen {PS80} subcutaneous. Sequence: {SEQ}"})
        finally:
            main.history.record_screen = real_record
        assert r.status_code == 200 and r.json()["history_id"] is None, r.text
        print("ok  history: when saving fails the dossier is still returned, with history_id null")

        # A campaign's whole life is in its event log.
        goal = {"protein": "IgG1 mAb", "route": "subcutaneous", "target_temp_c": 25,
                "duration_months": 24, "format": "liquid", "exposed_residues": [], "notes": ""}
        r = client.post("/design/iterate", headers=as_("tok-alice"),
                        json={"goal": goal, "prompt": "keep my mAb stable at 25 C", "batch_size": 4})
        assert r.status_code == 200, r.text
        camp = r.json()["campaign_id"]
        first = r.json()["candidates"][0]["id"]
        assert client.post(f"/design/campaign/{camp}/end", headers=as_("tok-alice")).json()["already"] is False
        assert client.post(f"/design/campaign/{camp}/end", headers=as_("tok-alice")).json()["already"] is True
        r = client.post("/design/iterate", headers=as_("tok-alice"), json={"campaign_id": camp, "batch_size": 4})
        assert r.status_code == 200 and r.json()["iteration"] == 2
        assert client.post("/design/queue", headers=as_("tok-alice"),
                           json={"candidate_ids": [first]}).json()["queued"] == 1
        state = client.get(f"/design/campaign/{camp}", headers=as_("tok-alice")).json()
        kinds = [e["kind"] for e in state["events"]]
        assert kinds == ["campaign.started", "campaign.iterated", "campaign.ended",
                         "campaign.reopened", "campaign.iterated", "candidate.queued"], kinds
        assert state["prompt"] == "keep my mAb stable at 25 C" and state["ended_at"] is None
        assert state["events"][0]["data"]["goal_parsed_by"] is None, "a given goal was not parsed by a model"
        assert state["events"][-1]["data"]["candidates"][0]["id"] == first
        print("ok  history: a campaign logs started, iterated, ended, reopened and queued, in order; "
              "ending twice logs once")

        # The list: newest first, both kinds, paginated, filtered.
        page = client.get("/history", headers=as_("tok-alice")).json()
        items = page["items"]
        assert [i["kind"] for i in items][:1] == ["campaign"] and items[0]["id"] == camp
        assert items[0]["n_iterations"] == 2 and items[0]["n_queued"] == 1 and items[0]["top"]["smiles"]
        assert {i["kind"] for i in items} == {"screen", "campaign"}
        seen, cursor = [], None
        while True:
            params = {"limit": 1, **({"before": cursor} if cursor else {})}
            pg = client.get("/history", params=params, headers=as_("tok-alice")).json()
            seen += [i["id"] for i in pg["items"]]
            cursor = pg["next"]
            if not cursor:
                break
        assert seen == [i["id"] for i in items], "paging one at a time must walk the same list"
        assert all(i["kind"] == "screen" for i in
                   client.get("/history", params={"kind": "screen"}, headers=as_("tok-alice")).json()["items"])
        assert client.get("/history", params={"before": "garbage"}, headers=as_("tok-alice")).status_code == 422
        print(f"ok  history: {len(items)} items newest first; paging one at a time walks the same list; "
              "filters work; a bad cursor is a 422")

        # None of it is anyone else's.
        assert client.get("/history", headers=as_("tok-bob")).json()["items"] == []
        assert client.get(f"/history/screens/{sid}", headers=as_("tok-bob")).status_code == 404
        assert client.post(f"/design/campaign/{camp}/end", headers=as_("tok-bob")).status_code == 404
        assert client.get("/history").status_code == 401
        print("ok  history: another user sees none of it and cannot end your campaign; signed out is 401")
    finally:
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url

    # --- orchestration (advisory campaign controller) -------------------------
    OR = main.orchestrator
    assert OR.recommend([])["phase"] == "seed"
    seeding = OR.recommend([{"iteration": 1, "best_score_so_far": 1.0, "seeded": True}])
    assert seeding["action"] == "continue" and seeding["phase"] == "seed"
    # A best score that stops climbing over PATIENCE iterations reads as converged,
    # and the controller then advises queuing rather than spending more iterations.
    flat = [{"iteration": i, "best_score_so_far": 2.60 + 0.001 * i, "seeded": False,
             "surrogate_cv_mae": 0.9, "batch_diversity": 2.0} for i in range(1, 5)]
    conv = OR.recommend(flat)
    assert conv["action"] == "stop" and conv["phase"] == "converged" and conv["suggest_queue"]
    # A score still climbing must keep the loop going, and never suggest queuing.
    climbing = [{"iteration": i, "best_score_so_far": 1.0 + 0.5 * i, "seeded": False,
                 "surrogate_cv_mae": 0.8, "batch_diversity": 2.0} for i in range(1, 4)]
    cont = OR.recommend(climbing)
    assert cont["action"] == "continue" and not cont["suggest_queue"]
    assert cont["phase"] == "exploit", "a low, settled surrogate error should tip toward exploiting"
    # The best score is naturally flat during the space-filling seed, then jumps once
    # the surrogate takes over — so a flat stretch that still includes seed iterations
    # must NOT be called converged, or the run stops right before exploitation pays off.
    seed_then_flat = [{"iteration": i, "best_score_so_far": 1.96, "seeded": i < 3,
                       "surrogate_cv_mae": (None if i < 3 else 2.0), "batch_diversity": 2.0}
                      for i in range(1, 4)]
    assert OR.recommend(seed_then_flat)["phase"] != "converged", "flat during seeding is not convergence"
    print(f"ok  orchestration: advises seed -> {cont['phase']} while climbing, "
          "stop-and-queue once the best score plateaus, never converged mid-seed")

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
