"""Smoke test — runs the whole agent loop with a scripted model in place of
Claude, so it needs no API key and costs nothing.

    python test_smoke.py

It covers the parts that break quietly: the tools, the Dossier schema, and the
evidence-floor validator's retry. The first scripted response deliberately claims
regulatory precedent; the test passes only if that gets rejected and the retry
lands.
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


def make_script():
    """Walks the agent through every tool, then fails the evidence floor once."""
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
                            "active_alerts": ["Polyether chain (autoxidises to peroxides on storage)"],
                        },
                    )
                ]
            )
        if step == 3:
            # Overclaims precedent — the validator must bounce this back.
            return ModelResponse(parts=[ToolCallPart(out_tool, _dossier("A", "Precedented"))])
        # The corrected answer after the retry.
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

    scan = main.protein_liability_scan(SEQ, found)
    flags = scan["flags"]
    assert any(f["severity"] == "high" for f in flags), "expected a high-severity Met flag"
    assert all(f["accessibility"].startswith("not modelled") for f in flags)
    print(f"ok  protein_liability_scan: {len(flags)} flag(s), sequence-only fallback")

    # --- solvent accessibility ----------------------------------------------
    weighted = main.protein_liability_scan(SEQ, found, structure_id="1IGT")
    wflags = weighted["flags"]
    assert "RCSB PDB 1IGT" in weighted["structure"], weighted["structure"]
    assert "not aligned" in weighted["structure"], "sequence/structure mismatch must be reported"
    met = next(f for f in wflags if f["residue"].startswith("Met"))
    assert "exposed" in met["residue"] and "RSA" in met["accessibility"]
    print(f"ok  accessibility (1IGT, experimental): {met['residue']}")
    print(f"      {met['accessibility'][:88]}")

    af = main.protein_liability_scan("", found, structure_id="P01857")
    assert "AlphaFold" in af["structure"], af["structure"]
    print(f"ok  accessibility (P01857, AlphaFold): {len(af['flags'])} flag(s)")

    # A bad identifier must degrade to counts, not raise.
    bad = main.protein_liability_scan(SEQ, found, structure_id="NOTREAL123")
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

    # --- the guard, directly -------------------------------------------------
    try:
        main.Dossier(**_dossier("A", "Data gap: test"))
        raise AssertionError("grade A should have been rejected")
    except ValueError:
        print("ok  evidence floor: grade A rejected")

    # --- the whole loop, with a scripted model -------------------------------
    script, state = make_script()
    agent = main.get_agent()
    with agent.override(model=FunctionModel(script)):
        result = asyncio.run(agent.run(f"Screen {PS80} subcutaneous. Sequence: {SEQ}"))

    d = result.output
    assert state["step"] == 5, f"expected a retry after the bad dossier, got {state['step']} steps"
    assert d.endpoints[0].evidence_grade == "D"
    assert d.needs_testing is True, "needs_testing must be derived, overriding the model's False"
    print("ok  agent loop: 3 tool calls, 1 rejected dossier, 1 retry")
    print("ok  needs_testing derived, not trusted")
    print("\nall checks passed")


if __name__ == "__main__":
    main_test()
