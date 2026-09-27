"""One user's results across everything they have run: excipient screens, the
candidates their design campaigns generated, and the simulations those led to.
Served to the Results tab (main.py's /design/my-results).

Unlike stats.py this is the user's OWN work, so names, proteins and prompts
appear freely -- but every query takes the owner and filters on it, like the
stores it reads (candidates.py, history.py).

WHAT THE SCORES MEAN, AND DO NOT.
- Evidence coverage (screens): the share of a dossier's endpoints that some
  evidence speaks for -- Precedented or Supported without precedent. It measures
  how much is already known, not how safe anything is: a screen at 100% coverage
  has still not shown compatibility with the protein.
- Triage score (candidates): design.py's additive ranking. It is only comparable
  WITHIN one campaign (the goal sets its terms), so it is summarised per
  campaign, never pooled or averaged across them.
- Gamma23 call (simulations): the same two-standard-error rule the candidate card
  reads a result with (readGamma in DesignResults.tsx). Excluded means Gamma23 +
  2 SE is below zero; accumulated means Gamma23 - 2 SE is above it; anything else,
  or a run with no error bar, is no clear preference. Smoke tests are left out;
  CPU previews are counted but flagged, since they are not converged.
"""

from __future__ import annotations

import json
import statistics

import psycopg

VERDICTS = ["Precedented", "Supported without precedent", "Data gap: test", "Alert: avoid"]
SUPPORTED = {"Precedented", "Supported without precedent"}
GRADES = ["A", "B", "C", "D", "E"]
SEVERITIES = ["high", "moderate", "low"]
STATUSES = ["benchmarked", "queued", "simulating", "simulated", "failed"]


def _coverage(endpoints: list[dict]) -> float | None:
    if not endpoints:
        return None
    return round(sum(1 for e in endpoints if e.get("verdict") in SUPPORTED) / len(endpoints), 3)


def gamma_call(g: float, se: float | None) -> str:
    """'excluded', 'accumulated' or 'unclear', by the two-standard-error rule."""
    if se is None:
        return "unclear"
    if g + 2 * se < 0:
        return "excluded"
    if g - 2 * se > 0:
        return "accumulated"
    return "unclear"


# ---------------------------------------------------------------------------
# Excipient screens
# ---------------------------------------------------------------------------

def _screens(conn: psycopg.Connection, owner_id: int) -> dict:
    rows = conn.execute(
        "SELECT id, created_at, status, excipient, protein, route, worst_verdict, needs_testing, "
        "dossier->'endpoints' AS endpoints, dossier->'liabilities' AS liabilities "
        "FROM screen_run WHERE owner_id=%s ORDER BY created_at DESC", (owner_id,)).fetchall()

    worst = {v: 0 for v in VERDICTS}
    verdicts = {v: 0 for v in VERDICTS}
    grades = {g: 0 for g in GRADES}
    severity = {s: 0 for s in SEVERITIES}
    coverages: list[float] = []
    # Every screen of the same excipient, folded together: the latest one opens.
    by_excipient: dict[str, dict] = {}
    for r in rows:
        if r["status"] != "ok":
            continue
        eps = r["endpoints"] or []
        liabs = r["liabilities"] or []
        if r["worst_verdict"] in worst:
            worst[r["worst_verdict"]] += 1
        for e in eps:
            if e.get("verdict") in verdicts:
                verdicts[e["verdict"]] += 1
            if e.get("evidence_grade") in grades:
                grades[e["evidence_grade"]] += 1
        for f in liabs:
            if f.get("severity") in severity:
                severity[f["severity"]] += 1
        cov = _coverage(eps)
        if cov is not None:
            coverages.append(cov)

        name = (r["excipient"] or "Unnamed excipient").strip()
        key = name.casefold()
        x = by_excipient.get(key)
        if x is None:
            # Rows arrive newest first, so the first seen is the latest screen.
            x = by_excipient[key] = {
                "excipient": name, "latest_id": r["id"], "latest_at": r["created_at"].isoformat(),
                "protein": r["protein"] or "", "route": r["route"] or "",
                "n_screens": 0, "coverage": cov, "worst_verdict": r["worst_verdict"],
                "n_endpoints": len(eps),
                "n_gaps": sum(1 for e in eps if e.get("verdict") == "Data gap: test"),
                "n_alerts": sum(1 for e in eps if e.get("verdict") == "Alert: avoid"),
                "n_high_liabilities": sum(1 for f in liabs if f.get("severity") == "high"),
            }
        x["n_screens"] += 1

    # Most already known first; among equals, the fewest alerts, then gaps.
    excipients = sorted(by_excipient.values(), key=lambda x: (
        -(x["coverage"] if x["coverage"] is not None else -1), x["n_alerts"], x["n_gaps"],
        x["excipient"].casefold()))

    ok = sum(1 for r in rows if r["status"] == "ok")
    return {
        "total": len(rows),
        "ok": ok,
        "failed": len(rows) - ok,
        "needs_testing": sum(1 for r in rows if r["status"] == "ok" and r["needs_testing"]),
        "coverage_mean": round(statistics.fmean(coverages), 3) if coverages else None,
        "worst_verdict": worst,
        "endpoint_verdicts": verdicts,
        "grades": grades,
        "liabilities": severity,
        "excipients": excipients[:50],
    }


# ---------------------------------------------------------------------------
# Design campaigns and the candidates they generated
# ---------------------------------------------------------------------------

def _campaigns(conn: psycopg.Connection, owner_id: int) -> dict:
    camps = conn.execute(
        "SELECT id, goal, prompt, created_at, ended_at FROM campaign WHERE owner_id=%s "
        "ORDER BY created_at DESC", (owner_id,)).fetchall()
    cands = conn.execute(
        "SELECT c.campaign_id, c.id, c.iteration, c.score, c.status, c.payload "
        "FROM candidate c JOIN campaign cp ON cp.id=c.campaign_id WHERE cp.owner_id=%s",
        (owner_id,)).fetchall()
    iters = conn.execute(
        "SELECT i.campaign_id, i.iteration, i.metrics FROM iteration i "
        "JOIN campaign cp ON cp.id=i.campaign_id WHERE cp.owner_id=%s ORDER BY i.iteration",
        (owner_id,)).fetchall()

    per: dict[str, list[dict]] = {}
    status = {s: 0 for s in STATUSES}
    alert_free = in_domain = domain_known = 0
    for c in cands:
        p = json.loads(c["payload"])
        per.setdefault(c["campaign_id"], []).append({**c, "p": p})
        status[c["status"]] = status.get(c["status"], 0) + 1
        alert_free += not p.get("alerts_fired")
        if p.get("tg_in_model_domain") is not None:
            domain_known += 1
            in_domain += bool(p["tg_in_model_domain"])

    # The loop's own learning curve: best score so far, iteration by iteration.
    curve: dict[str, list[float]] = {}
    for r in iters:
        best = json.loads(r["metrics"]).get("best_score_so_far")
        if best is not None:
            curve.setdefault(r["campaign_id"], []).append(best)

    out = []
    for cp in camps:
        rows = per.get(cp["id"], [])
        goal = json.loads(cp["goal"])
        scores = sorted((r["score"] for r in rows), reverse=True)
        top = max(rows, key=lambda r: r["score"]) if rows else None
        out.append({
            "id": cp["id"], "created_at": cp["created_at"], "ended_at": cp["ended_at"],
            "prompt": cp["prompt"] or "", "protein": goal.get("protein", ""),
            "format": goal.get("format", ""), "target_temp_c": goal.get("target_temp_c"),
            "n_candidates": len(rows),
            "n_iterations": len({r["iteration"] for r in rows}),
            "best_score": scores[0] if scores else None,
            "median_score": round(statistics.median(scores), 2) if scores else None,
            "alert_free": (round(sum(1 for r in rows if not r["p"].get("alerts_fired")) / len(rows), 3)
                           if rows else None),
            "n_sent": sum(1 for r in rows if r["status"] != "benchmarked"),
            "n_simulated": sum(1 for r in rows if r["status"] == "simulated"),
            "best_by_iteration": curve.get(cp["id"], []),
            "top": top and {"id": top["id"], "name": top["p"].get("name", ""), "score": top["score"],
                            "status": top["status"]},
        })

    n = len(cands)
    return {
        "campaigns": len(camps),
        "candidates": n,
        "iterations": sum(c["n_iterations"] for c in out),
        "by_status": status,
        "alert_free": round(alert_free / n, 3) if n else None,
        "tg_in_domain": round(in_domain / domain_known, 3) if domain_known else None,
        "per_campaign": out,
    }


# ---------------------------------------------------------------------------
# Simulations
# ---------------------------------------------------------------------------

def _simulations(conn: psycopg.Connection, owner_id: int) -> dict:
    rows = conn.execute(
        "SELECT c.id, c.campaign_id, c.status, c.score, c.payload, c.sim_tier, c.sim_result, "
        "  c.sim_finished_at, cp.goal "
        "FROM candidate c JOIN campaign cp ON cp.id=c.campaign_id "
        "WHERE cp.owner_id=%s AND (c.status IN ('queued','simulating') "
        "  OR c.sim_attempts > 0 OR c.sim_error IS NOT NULL OR c.sim_result IS NOT NULL)",
        (owner_id,)).fetchall()
    state = {"waiting": 0, "running": 0, "done": 0, "failed": 0}
    calls = {"excluded": 0, "accumulated": 0, "unclear": 0}
    runs = []
    for r in rows:
        state[{"queued": "waiting", "simulating": "running", "simulated": "done"}
              .get(r["status"], "failed")] += 1
        if r["status"] != "simulated" or not r["sim_result"]:
            continue
        res = json.loads(r["sim_result"])
        if res.get("smoke"):
            continue
        g, se = res["gamma23"], res.get("gamma23_se")
        call = gamma_call(g, se)
        calls[call] += 1
        runs.append({
            "id": r["id"], "campaign_id": r["campaign_id"],
            "name": json.loads(r["payload"]).get("name", ""),
            "protein": json.loads(r["goal"]).get("protein", ""),
            "triage_score": r["score"], "gamma23": g, "gamma23_se": se, "call": call,
            "preview": bool(res.get("preview")) or r["sim_tier"] == "cpu",
            "production_ns": res.get("production_ns"),
            "finished_at": r["sim_finished_at"].isoformat() if r["sim_finished_at"] else None,
        })
    # Most excluded first: the order the lab would want to test them in.
    runs.sort(key=lambda x: x["gamma23"])
    return {"by_state": state, "calls": calls, "runs": runs}


def for_user(conn: psycopg.Connection, *, owner_id: int) -> dict:
    return {"screens": _screens(conn, owner_id), "design": _campaigns(conn, owner_id),
            "simulations": _simulations(conn, owner_id)}
