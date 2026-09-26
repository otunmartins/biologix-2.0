"""Persistent store for active-learning design campaigns and the OpenMM queue.

WHAT THIS IS. The other half of the active-learning loop (active.py): the loop
proposes and screens candidates, and this keeps them — one campaign per design
goal, every candidate it ever proposed, and the per-iteration benchmark metrics —
so a run can be reopened, extended, and its promising candidates put in a queue.

THE QUEUE IS THE HAND-OFF TO STAGE 3. A candidate that scores well on the cheap
triage proxy has earned an expensive look, not a verdict. Marking it 'queued'
places it in a backlog ordered by score, which is exactly what a future GPU worker
pulls when OpenMM is available. When a simulation finishes it is recorded through
measurements.py as an observation with source='simulation' — the schema there was
written for precisely this (see its DESIGN NOTES) — so the loop can later be closed
against a real label without a migration. Being queued is a triage decision, never
evidence, and nothing here upgrades a candidate above grade D.

Mirrors measurements.py deliberately: sqlite with TEXT uuid primary keys, explicit
timestamps, portable SQL, and the app version stamped on every row, so moving to
Postgres is a connection change rather than a data-model migration.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Literal

DB_PATH = os.environ.get("CAMPAIGN_DB") or os.path.join(
    os.environ.get("IID_CACHE_DIR") or ".", "campaigns.db")

APP_VERSION = os.environ.get("APP_VERSION", "dev")

# The lifecycle of a candidate. 'benchmarked' the moment it is screened and stored;
# 'queued' once a user sends it for simulation; the rest are for the GPU worker that
# does not exist yet, kept here so it slots in without a schema change.
Status = Literal["proposed", "benchmarked", "queued", "simulating", "simulated", "failed"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaign (
    id          TEXT PRIMARY KEY,
    goal        TEXT NOT NULL,          -- the DesignGoal, as JSON
    created_at  TEXT NOT NULL,
    app_version TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS iteration (
    campaign_id TEXT NOT NULL REFERENCES campaign(id),
    iteration   INTEGER NOT NULL,
    metrics     TEXT NOT NULL,          -- the benchmark metrics for this batch, as JSON
    created_at  TEXT NOT NULL,
    PRIMARY KEY (campaign_id, iteration)
);
CREATE TABLE IF NOT EXISTS candidate (
    id           TEXT PRIMARY KEY,
    campaign_id  TEXT NOT NULL REFERENCES campaign(id),
    iteration    INTEGER NOT NULL,
    backbone_key TEXT NOT NULL,
    -- The recipe the active loop needs to refit its surrogate and dedupe:
    -- [[pendant_key, fraction], ...]. Kept separate from the display payload so
    -- reopening a campaign never has to map a pendant name back to its key.
    components   TEXT NOT NULL,
    score        REAL NOT NULL,
    -- The full wire dict (design.candidate_dict output), so the frontend gets an
    -- identical shape to a fresh proposal.
    payload      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'benchmarked',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    app_version  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS candidate_by_campaign ON candidate(campaign_id, iteration);
CREATE INDEX IF NOT EXISTS candidate_by_status ON candidate(status, score);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def create_campaign(conn: sqlite3.Connection, goal: dict) -> str:
    cid = str(uuid.uuid4())
    conn.execute("INSERT INTO campaign (id, goal, created_at, app_version) VALUES (?,?,?,?)",
                 (cid, json.dumps(goal), _now(), APP_VERSION))
    conn.commit()
    return cid


def add_candidates(conn: sqlite3.Connection, campaign_id: str, iteration: int,
                   items: list[dict]) -> list[dict]:
    """Store one screened batch. Each item carries the display payload plus the
    recipe the loop needs. Returns the stored rows, payload enriched with id/status.

    An item is {"payload": <candidate_dict>, "backbone_key": str,
                "components": [[pendant_key, fraction], ...], "score": float}.
    """
    now = _now()
    stored = []
    for item in items:
        cid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO candidate (id, campaign_id, iteration, backbone_key, components, "
            "score, payload, status, created_at, updated_at, app_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, campaign_id, iteration, item["backbone_key"],
             json.dumps(item["components"]), item["score"], json.dumps(item["payload"]),
             "benchmarked", now, now, APP_VERSION))
        stored.append(_row_to_candidate(cid, iteration, "benchmarked", item["payload"]))
    conn.commit()
    return stored


def record_metrics(conn: sqlite3.Connection, campaign_id: str, iteration: int,
                   metrics: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO iteration (campaign_id, iteration, metrics, created_at) "
        "VALUES (?,?,?,?)", (campaign_id, iteration, json.dumps(metrics), _now()))
    conn.commit()


def enqueue(conn: sqlite3.Connection, candidate_ids: list[str]) -> int:
    """Send benchmarked candidates to the simulation queue. Only a benchmarked
    candidate can be queued, so re-queuing or queuing a running one is a no-op."""
    now = _now()
    n = 0
    for cid in candidate_ids:
        cur = conn.execute(
            "UPDATE candidate SET status='queued', updated_at=? "
            "WHERE id=? AND status='benchmarked'", (now, cid))
        n += cur.rowcount
    conn.commit()
    return n


def mark_status(conn: sqlite3.Connection, candidate_id: str, status: Status) -> None:
    conn.execute("UPDATE candidate SET status=?, updated_at=? WHERE id=?",
                 (status, _now(), candidate_id))
    conn.commit()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _row_to_candidate(cid: str, iteration: int, status: str, payload: dict) -> dict:
    """Payload as stored, with the fields only the store knows merged in."""
    return {**payload, "id": cid, "iteration": iteration, "status": status}


def _hydrate(row: sqlite3.Row) -> dict:
    return _row_to_candidate(row["id"], row["iteration"], row["status"],
                             json.loads(row["payload"]))


def next_iteration(conn: sqlite3.Connection, campaign_id: str) -> int:
    row = conn.execute("SELECT MAX(iteration) AS m FROM candidate WHERE campaign_id=?",
                       (campaign_id,)).fetchone()
    return 1 if row["m"] is None else row["m"] + 1


def candidates_for(conn: sqlite3.Connection, campaign_id: str,
                   iteration: int | None = None) -> list[dict]:
    sql = "SELECT * FROM candidate WHERE campaign_id=?"
    args: list = [campaign_id]
    if iteration is not None:
        sql += " AND iteration=?"
        args.append(iteration)
    sql += " ORDER BY iteration, score DESC"
    return [_hydrate(r) for r in conn.execute(sql, args)]


def prior_for(conn: sqlite3.Connection, campaign_id: str) -> list[dict]:
    """Everything benchmarked in this campaign, in the compact shape the active
    loop's PriorCandidate expects — backbone key, (pendant_key, fraction) pairs,
    and the score — so the surrogate can be refit without touching the payloads."""
    rows = conn.execute(
        "SELECT backbone_key, components, score FROM candidate WHERE campaign_id=?",
        (campaign_id,)).fetchall()
    return [{"backbone_key": r["backbone_key"],
             "components": [tuple(c) for c in json.loads(r["components"])],
             "score": r["score"]} for r in rows]


def metrics_history(conn: sqlite3.Connection, campaign_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT iteration, metrics FROM iteration WHERE campaign_id=? ORDER BY iteration",
        (campaign_id,)).fetchall()
    return [{"iteration": r["iteration"], **json.loads(r["metrics"])} for r in rows]


def queue(conn: sqlite3.Connection, limit: int = 50,
          campaign_id: str | None = None) -> list[dict]:
    """The simulation backlog a GPU worker would pull, highest triage score first.
    Scoped to one campaign when given, else across all of them."""
    sql = "SELECT * FROM candidate WHERE status='queued'"
    args: list = []
    if campaign_id:
        sql += " AND campaign_id=?"
        args.append(campaign_id)
    sql += " ORDER BY score DESC LIMIT ?"
    args.append(limit)
    return [_hydrate(r) for r in conn.execute(sql, args)]


def queue_summary(conn: sqlite3.Connection, campaign_id: str | None = None) -> dict:
    """Counts by status, plus the top of the queue — enough for the panel to say
    'N queued for OpenMM, waiting for GPU' without pulling every row."""
    sql = "SELECT status, COUNT(*) AS n FROM candidate"
    args: list = []
    if campaign_id:
        sql += " WHERE campaign_id=?"
        args.append(campaign_id)
    sql += " GROUP BY status"
    counts = {r["status"]: r["n"] for r in conn.execute(sql, args)}
    top = queue(conn, limit=5, campaign_id=campaign_id)
    return {
        "n_queued": counts.get("queued", 0),
        "n_benchmarked": counts.get("benchmarked", 0),
        "n_simulated": counts.get("simulated", 0),
        "by_status": counts,
        "top": [{"id": c["id"], "name": c["name"], "score": c["score"]} for c in top],
        "note": ("Queued candidates are ordered by triage score and wait for an OpenMM run "
                 "when a GPU is available. A queue position is a triage decision, not evidence."),
    }


def campaign_state(conn: sqlite3.Connection, campaign_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM campaign WHERE id=?", (campaign_id,)).fetchone()
    if row is None:
        return None
    cands = candidates_for(conn, campaign_id)
    by_iteration: dict[int, list[dict]] = {}
    for c in cands:
        by_iteration.setdefault(c["iteration"], []).append(c)
    return {
        "campaign_id": campaign_id,
        "goal": json.loads(row["goal"]),
        "created_at": row["created_at"],
        "n_candidates": len(cands),
        "n_iterations": len(by_iteration),
        "candidates_by_iteration": by_iteration,
        "metrics_history": metrics_history(conn, campaign_id),
        "queue_summary": queue_summary(conn, campaign_id),
    }
