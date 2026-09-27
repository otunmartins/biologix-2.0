"""Aggregate numbers for the admin dashboard. Counts, never content.

An admin owns the platform, not the users' work: they may know HOW MUCH is being
done - screens, polymers, simulations, who is active - but not WHAT anyone
screened. So every query here aggregates, and none selects an excipient, a
protein, a prompt, a SMILES or a dossier's text. The distributions are over
the app's own vocabulary (verdicts, grades, routes, the designer's backbone
table, formats, the history log's event kinds), never over free text a user typed. test_smoke.py checks the
whole response for a screened excipient's name and structure.

Users have no sign-up timestamp (the Auth.js users table carries none), so a
user's start is their FIRST ACTIVITY: their earliest screen or history event.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import psycopg

import results

VERDICTS = ["Precedented", "Supported without precedent", "Data gap: test", "Alert: avoid"]
GRADES = ["A", "B", "C", "D", "E"]
# Gamma23 histogram edges, chains per protein; symmetric about zero.
GAMMA_EDGES = [-8, -4, -2, -1, -0.5, 0, 0.5, 1, 2, 4, 8]
TEMP_EDGES = [-80, 0, 10, 20, 30, 40, 50, 60]
# Evidence coverage per screen, as a fraction; the last bin is exactly 100%.
COVERAGE_EDGES = [0.2, 0.4, 0.6, 0.8, 1.0]
CANDIDATE_EDGES = [5, 10, 20, 40, 80]
ITERATION_EDGES = [2, 3, 4, 6, 10]
# The event kinds the history log writes (history.py, main.py): app vocabulary,
# so counting them says what kind of thing happened and nothing about what.
EVENT_KINDS = ["screen.completed", "screen.failed", "campaign.started", "campaign.iterated",
               "campaign.ended", "campaign.reopened", "candidate.queued", "candidate.approved",
               "candidate.declined", "candidate.simulating", "candidate.simulated",
               "candidate.simulation_failed", "candidate.requeued", "candidate.stopped"]


def _one(conn, sql: str, args=()) -> int | float:
    # SUM over bigint comes back as Decimal; hand the client plain numbers.
    v = conn.execute(sql, args).fetchone()["v"] or 0
    return int(v) if float(v).is_integer() else float(v)


def _counts(conn, sql: str, args=()) -> dict[str, int]:
    return {str(r["k"]): r["n"] for r in conn.execute(sql, args)}


def _histogram(values: list[float], edges: list[float]) -> list[dict]:
    """Counts per [lo, hi) bin, with open-ended bins below the first edge and at
    or above the last."""
    bins = [{"lo": None, "hi": edges[0], "n": 0}]
    bins += [{"lo": lo, "hi": hi, "n": 0} for lo, hi in zip(edges, edges[1:])]
    bins += [{"lo": edges[-1], "hi": None, "n": 0}]
    for v in values:
        if v < edges[0]:
            bins[0]["n"] += 1
        elif v >= edges[-1]:
            bins[-1]["n"] += 1
        else:
            i = next(i for i in range(len(edges) - 1) if edges[i] <= v < edges[i + 1])
            bins[i + 1]["n"] += 1
    return bins


def _daily(conn, sql: str, start: date, days: int, args=()) -> list[int]:
    """A per-day count over the window, zero-filled. The query returns (d, n)."""
    got = {r["d"]: r["n"] for r in conn.execute(sql, (start, *args))}
    return [got.get(start + timedelta(days=i), 0) for i in range(days)]


def dashboard(conn: psycopg.Connection, days: int = 90) -> dict:
    now = datetime.now(timezone.utc)
    today = now.date()
    start = today - timedelta(days=days - 1)
    prev_start = start - timedelta(days=days)

    # candidate.created_at is ISO text (portable schema); screen_run and event are timestamptz.
    cand_at = "c.created_at::timestamptz"

    totals = {
        "users": _one(conn, "SELECT COUNT(*) AS v FROM users"),
        "screens": _one(conn, "SELECT COUNT(*) AS v FROM screen_run"),
        "screens_ok": _one(conn, "SELECT COUNT(*) AS v FROM screen_run WHERE status='ok'"),
        "screens_failed": _one(conn, "SELECT COUNT(*) AS v FROM screen_run WHERE status='failed'"),
        "campaigns": _one(conn, "SELECT COUNT(*) AS v FROM campaign"),
        "iterations": _one(conn, "SELECT COUNT(*) AS v FROM iteration"),
        "polymers": _one(conn, "SELECT COUNT(*) AS v FROM candidate"),
        "simulations_requested": _one(
            conn, "SELECT COUNT(*) AS v FROM event WHERE kind='candidate.queued'"),
        "simulations_done": _one(conn, "SELECT COUNT(*) AS v FROM candidate WHERE status='simulated'"),
        "simulations_running": _one(conn, "SELECT COUNT(*) AS v FROM candidate WHERE status='simulating'"),
        "simulations_waiting": _one(
            conn, "SELECT COUNT(*) AS v FROM candidate WHERE status='queued' AND sim_approved_at IS NULL"),
        "simulations_approved": _one(
            conn, "SELECT COUNT(*) AS v FROM candidate WHERE status='queued' AND sim_approved_at IS NOT NULL"),
        "measurements": _one(conn, "SELECT COUNT(*) AS v FROM measurement"),
        "tokens_in": _one(conn, "SELECT SUM((provenance->'usage'->>'input_tokens')::bigint) AS v FROM screen_run"),
        "tokens_out": _one(conn, "SELECT SUM((provenance->'usage'->>'output_tokens')::bigint) AS v FROM screen_run"),
        "model_requests": _one(conn, "SELECT SUM((provenance->'usage'->>'requests')::bigint) AS v FROM screen_run"),
    }

    # Who was active: anyone with a screen or a history event, by day.
    activity = ("SELECT owner_id, created_at AS at FROM screen_run "
                "UNION ALL SELECT owner_id, at FROM event")
    for k, n in (("active_users_7d", 7), ("active_users_30d", 30)):
        totals[k] = _one(conn, f"SELECT COUNT(DISTINCT owner_id) AS v FROM ({activity}) a "
                               "WHERE at >= now() - make_interval(days => %s)", (n,))

    series = {
        "dates": [(start + timedelta(days=i)).isoformat() for i in range(days)],
        "screens": _daily(conn, "SELECT created_at::date AS d, COUNT(*) AS n FROM screen_run "
                                "WHERE created_at::date >= %s GROUP BY 1", start, days),
        "polymers": _daily(conn, f"SELECT ({cand_at})::date AS d, COUNT(*) AS n FROM candidate c "
                                 f"WHERE ({cand_at})::date >= %s GROUP BY 1", start, days),
        "campaigns": _daily(conn, "SELECT created_at::timestamptz::date AS d, COUNT(*) AS n FROM campaign "
                                  "WHERE created_at::timestamptz::date >= %s GROUP BY 1", start, days),
        "simulations": _daily(conn, "SELECT at::date AS d, COUNT(*) AS n FROM event "
                                    "WHERE kind='candidate.simulated' AND at::date >= %s GROUP BY 1",
                              start, days),
        "active_users": _daily(conn, f"SELECT at::date AS d, COUNT(DISTINCT owner_id) AS n FROM ({activity}) a "
                                     "WHERE at::date >= %s GROUP BY 1", start, days),
    }
    # The same window just before this one, so each headline can say which way it moved.
    previous = {
        "screens": _one(conn, "SELECT COUNT(*) AS v FROM screen_run WHERE created_at::date >= %s "
                              "AND created_at::date < %s", (prev_start, start)),
        "polymers": _one(conn, f"SELECT COUNT(*) AS v FROM candidate c WHERE ({cand_at})::date >= %s "
                               f"AND ({cand_at})::date < %s", (prev_start, start)),
        "campaigns": _one(conn, "SELECT COUNT(*) AS v FROM campaign WHERE created_at::timestamptz::date >= %s "
                                "AND created_at::timestamptz::date < %s", (prev_start, start)),
        "simulations": _one(conn, "SELECT COUNT(*) AS v FROM event WHERE kind='candidate.simulated' "
                                  "AND at::date >= %s AND at::date < %s", (prev_start, start)),
    }

    # Users by first activity, cumulative: how the user base has grown.
    firsts = conn.execute(f"SELECT MIN(at)::date AS d FROM ({activity}) a GROUP BY owner_id").fetchall()
    first_days = sorted(r["d"] for r in firsts)
    before = sum(1 for d in first_days if d < start)
    growth, running = [], before
    for i in range(days):
        day = start + timedelta(days=i)
        running += sum(1 for d in first_days if d == day)
        growth.append(running)
    series["users_cumulative"] = growth

    grades = _counts(conn, "SELECT e->>'evidence_grade' AS k, COUNT(*) AS n FROM screen_run, "
                           "jsonb_array_elements(dossier->'endpoints') e WHERE dossier IS NOT NULL GROUP BY 1")
    endpoint_verdicts = _counts(conn, "SELECT e->>'verdict' AS k, COUNT(*) AS n FROM screen_run, "
                                      "jsonb_array_elements(dossier->'endpoints') e "
                                      "WHERE dossier IS NOT NULL GROUP BY 1")
    durations = [r["s"] for r in conn.execute(
        "SELECT EXTRACT(EPOCH FROM finished_at - created_at) AS s FROM screen_run "
        "WHERE status='ok' ORDER BY 1")]
    # Share of each finished screen's endpoints that some evidence speaks for
    # (results.py's evidence coverage), computed in SQL from verdict labels only.
    coverage = [float(r["c"]) for r in conn.execute(
        "SELECT AVG(CASE WHEN e->>'verdict' = ANY(%s) THEN 1.0 ELSE 0.0 END) AS c "
        "FROM screen_run, jsonb_array_elements(dossier->'endpoints') e "
        "WHERE dossier IS NOT NULL GROUP BY screen_run.id", (sorted(results.SUPPORTED),))]
    liabilities = _counts(conn, "SELECT f->>'severity' AS k, COUNT(*) AS n FROM screen_run, "
                                "jsonb_array_elements(dossier->'liabilities') f "
                                "WHERE dossier IS NOT NULL GROUP BY 1")
    screens = {
        "coverage_mean": round(sum(coverage) / len(coverage), 3) if coverage else None,
        "coverage": _histogram(coverage, COVERAGE_EDGES),
        "liabilities": {k: liabilities.get(k, 0) for k in results.SEVERITIES},
        "worst_verdict": {v: 0 for v in VERDICTS} | _counts(
            conn, "SELECT worst_verdict AS k, COUNT(*) AS n FROM screen_run "
                  "WHERE worst_verdict IS NOT NULL GROUP BY 1"),
        "endpoint_verdicts": {v: endpoint_verdicts.get(v, 0) for v in VERDICTS},
        "grades": {g: grades.get(g, 0) for g in GRADES},
        # Case-folded: the form and free text spell the same route differently.
        "routes": _counts(conn, "SELECT COALESCE(NULLIF(INITCAP(LOWER(TRIM(route))),''),'Not stated') AS k, "
                                "COUNT(*) AS n FROM screen_run GROUP BY 1 ORDER BY 2 DESC"),
        "needs_testing": _one(conn, "SELECT COUNT(*) AS v FROM screen_run WHERE needs_testing"),
        "duration_median_s": round(float(durations[len(durations) // 2]), 1) if durations else None,
        "duration_p90_s": round(float(durations[int(len(durations) * 0.9)]), 1) if durations else None,
    }

    goal_temps = [float(r["t"]) for r in conn.execute(
        "SELECT (goal::jsonb->>'target_temp_c') AS t FROM campaign "
        "WHERE goal::jsonb->>'target_temp_c' IS NOT NULL")]
    per_campaign = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT iteration) AS it FROM candidate GROUP BY campaign_id").fetchall()
    alert_free = _one(conn, "SELECT COUNT(*) AS v FROM candidate WHERE "
                            "COALESCE(jsonb_array_length(payload::jsonb->'alerts_fired'), 0) = 0")
    polymers = {
        "candidates_per_campaign": _histogram([r["n"] for r in per_campaign], CANDIDATE_EDGES),
        "iterations_per_campaign": _histogram([r["it"] for r in per_campaign], ITERATION_EDGES),
        "alert_free": round(alert_free / totals["polymers"], 3) if totals["polymers"] else None,
        "by_backbone": _counts(conn, "SELECT backbone_key AS k, COUNT(*) AS n FROM candidate "
                                     "GROUP BY 1 ORDER BY 2 DESC"),
        "by_status": _counts(conn, "SELECT status AS k, COUNT(*) AS n FROM candidate GROUP BY 1"),
        "by_format": _counts(conn, "SELECT COALESCE(goal::jsonb->>'format','not stated') AS k, "
                                   "COUNT(*) AS n FROM campaign GROUP BY 1"),
        "target_temp_c": _histogram(goal_temps, TEMP_EDGES),
        "score_quartiles": [round(float(r["q"]), 2) for r in conn.execute(
            "SELECT unnest(percentile_cont(ARRAY[0.25,0.5,0.75]) WITHIN GROUP (ORDER BY score)) AS q "
            "FROM candidate")] if totals["polymers"] else [],
    }

    finished = conn.execute(
        "SELECT (sim_result::jsonb->>'gamma23')::float AS g, (sim_result::jsonb->>'gamma23_se')::float AS se, "
        "  COALESCE((sim_result::jsonb->>'preview')::boolean, false) OR sim_tier='cpu' AS preview "
        "FROM candidate WHERE status='simulated' AND sim_result IS NOT NULL "
        "AND COALESCE((sim_result::jsonb->>'smoke')::boolean, false) = false").fetchall()
    gammas = [r["g"] for r in finished]
    calls = {"excluded": 0, "accumulated": 0, "unclear": 0}
    for r in finished:
        calls[results.gamma_call(r["g"], r["se"])] += 1
    simulations = {
        "by_status": {k: totals[f"simulations_{k}"] for k in ("waiting", "approved", "running", "done")}
        | {"failed": _one(conn, "SELECT COUNT(*) AS v FROM candidate WHERE status='failed' "
                                "AND (sim_attempts > 0 OR sim_error IS NOT NULL)")},
        "by_tier": _counts(conn, "SELECT COALESCE(data->>'tier','gpu') AS k, COUNT(*) AS n FROM event "
                                 "WHERE kind='candidate.approved' GROUP BY 1"),
        "gamma23": _histogram(gammas, GAMMA_EDGES),
        "gamma23_excluded": sum(1 for g in gammas if g < 0),
        "gamma23_accumulated": sum(1 for g in gammas if g > 0),
        # The candidate card's two-standard-error reading of each run.
        "calls": calls,
        "previews": sum(1 for r in finished if r["preview"]),
    }

    # What kinds of thing happened: event types per day and over the period.
    series["events"] = _daily(conn, "SELECT at::date AS d, COUNT(*) AS n FROM event "
                                    "WHERE at::date >= %s GROUP BY 1", start, days)
    got = _counts(conn, "SELECT kind AS k, COUNT(*) AS n FROM event WHERE at::date >= %s "
                        "GROUP BY 1", (start,))
    events = {k: got.get(k, 0) for k in EVENT_KINDS}

    return {"generated_at": now.isoformat(), "window_days": days, "totals": totals,
            "previous": previous, "series": series, "screens": screens,
            "polymers": polymers, "simulations": simulations, "events": events}
