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

EVERY CAMPAIGN HAS AN OWNER. owner_id is the signed-in user who started it
(users.py), and every read and write below that a request can reach takes the
owner and filters on it, so one user can neither see nor queue another's
candidates. The owner is a required argument rather than an optional filter:
forgetting it is a TypeError, not a quiet leak across users. Rows from before
sign-in existed have no owner and are visible to nobody.

Mirrors measurements.py deliberately: TEXT uuid primary keys, explicit
timestamps, portable SQL, and the app version stamped on every row, so moving to
Postgres is a connection change rather than a data-model migration.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

import psycopg

import db
import users

APP_VERSION = os.environ.get("APP_VERSION", "dev")

# The lifecycle of a candidate. 'benchmarked' the moment it is screened and stored;
# 'queued' once a user sends it for simulation; then 'simulating' while a worker
# (worker/) holds it, and 'simulated' or 'failed' when it reports back.
Status = Literal["proposed", "benchmarked", "queued", "simulating", "simulated", "failed"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaign (
    id          TEXT PRIMARY KEY,
    goal        TEXT NOT NULL,          -- the DesignGoal, as JSON
    created_at  TEXT NOT NULL,
    app_version TEXT NOT NULL DEFAULT '',
    owner_id    INTEGER REFERENCES users(id)
);
-- For a campaign table created before sign-in existed. A no-op on a fresh one.
ALTER TABLE campaign ADD COLUMN IF NOT EXISTS owner_id INTEGER REFERENCES users(id);
-- Provenance (history.py): the words the user typed, before the model read a
-- goal out of them, and when they closed the campaign with "New experiment".
-- Ended is a marker, not a lock: continuing an ended campaign reopens it.
ALTER TABLE campaign ADD COLUMN IF NOT EXISTS prompt TEXT;
ALTER TABLE campaign ADD COLUMN IF NOT EXISTS ended_at TEXT;
CREATE INDEX IF NOT EXISTS campaign_by_owner ON campaign(owner_id);
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
    -- score is DOUBLE PRECISION, not REAL: Postgres REAL is 4 bytes, where
    -- sqlite's REAL was 8, and the triage score is ordered on.
    -- The recipe the active loop needs to refit its surrogate and dedupe:
    -- [[pendant_key, fraction], ...]. Kept separate from the display payload so
    -- reopening a campaign never has to map a pendant name back to its key.
    components   TEXT NOT NULL,
    score        DOUBLE PRECISION NOT NULL,
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
-- The OpenMM job (Stage 3), on the candidate it is about. sim_structure_id is the
-- target biologic's PDB ID or UniProt accession, fixed when it is queued: the
-- polymer is simulated against the protein the user is trying to stabilise.
-- Heartbeats are TIMESTAMPTZ and compared with the database's own now(), so a
-- worker on another machine with a skewed clock cannot make a live job look
-- dead or a dead one look live.
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS sim_structure_id TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS sim_worker TEXT;
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS sim_started_at TIMESTAMPTZ;
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS sim_heartbeat_at TIMESTAMPTZ;
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS sim_finished_at TIMESTAMPTZ;
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS sim_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS sim_progress TEXT;
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS sim_result TEXT;     -- JSON, see worker/
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS sim_error TEXT;
-- Each run costs GPU hours, so a queued job waits until an admin approves it
-- (ADMIN_EMAILS, users.py); only an approved job can be claimed. Queuing again
-- clears the approval: what was approved was that job, not the candidate.
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS sim_approved_at TIMESTAMPTZ;
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS sim_approved_by INTEGER REFERENCES users(id);
-- Which kind of worker the admin approved it for: 'gpu' (the full run) or 'cpu'
-- (a short preview when no GPU is available). Only a worker of that kind claims it.
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS sim_tier TEXT;
-- The last frame of a finished run, protein and polymer only, as PDB text: what
-- the 3D view draws. Its own table so the lists of runs, which read candidate
-- rows by the hundred, never carry a few hundred kilobytes each.
CREATE TABLE IF NOT EXISTS sim_snapshot (
    candidate_id TEXT PRIMARY KEY REFERENCES candidate(id),
    pdb          TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# A job whose worker has not checked in for this long is presumed dead (the box
# was stopped, the container OOM-killed) and goes back in the queue. Workers
# heartbeat every minute or so, so this is many missed beats, not one slow one.
STALE_AFTER_MINUTES = 20
# After this many claims a job is failed rather than requeued: a system that
# crashes the worker every time would otherwise loop forever.
MAX_ATTEMPTS = 3
# What counts against a user's one-simulation-at-a-time allowance: waiting for
# approval, approved and waiting for the worker, or running.
ACTIVE = ("queued", "simulating")
# pg_advisory_xact_lock's first key, naming this lock; the second is the owner.
_QUEUE_LOCK = 5316
# The two kinds of run. 'gpu' is the full simulation; 'cpu' is a short preview for
# when no GPU is available -- a CPU would take days over the full length.
TIERS = ("gpu", "cpu")


class QueueFull(Exception):
    """Queuing these would leave the user with more active simulations than
    they are allowed. Raised inside the transaction, so nothing was queued."""

    def __init__(self, allowed: int, active: list[dict]):
        super().__init__(f"at most {allowed} simulation(s) at a time")
        self.allowed, self.active = allowed, active


def connect(url: str | None = None) -> psycopg.Connection:
    conn = db.connect(url)
    users.ensure_schema(conn)  # campaign.owner_id references users(id)
    db.init_schema(conn, SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def create_campaign(conn: psycopg.Connection, goal: dict, *, owner_id: int,
                    prompt: str = "") -> str:
    cid = str(uuid.uuid4())
    with db.tx(conn):
        conn.execute("INSERT INTO campaign (id, goal, created_at, app_version, owner_id, prompt) "
                     "VALUES (%s,%s,%s,%s,%s,%s)",
                     (cid, json.dumps(goal), _now(), APP_VERSION, owner_id, prompt or None))
    return cid


def end_campaign(conn: psycopg.Connection, campaign_id: str, *, owner_id: int) -> str | None:
    """Mark a campaign ended. Returns 'ended', 'already' if it was, or None if the
    user has no such campaign. Nothing is deleted: it stays in their history."""
    with db.tx(conn):
        row = conn.execute("SELECT ended_at FROM campaign WHERE id=%s AND owner_id=%s FOR UPDATE",
                           (campaign_id, owner_id)).fetchone()
        if row is None:
            return None
        if row["ended_at"]:
            return "already"
        conn.execute("UPDATE campaign SET ended_at=%s WHERE id=%s", (_now(), campaign_id))
    return "ended"


def reopen_campaign(conn: psycopg.Connection, campaign_id: str, *, owner_id: int) -> bool:
    """Clear ended_at. True if the campaign had been ended, so the caller logs it."""
    with db.tx(conn):
        cur = conn.execute("UPDATE campaign SET ended_at=NULL "
                           "WHERE id=%s AND owner_id=%s AND ended_at IS NOT NULL",
                           (campaign_id, owner_id))
    return cur.rowcount > 0


def add_candidates(conn: psycopg.Connection, campaign_id: str, iteration: int,
                   items: list[dict]) -> list[dict]:
    """Store one screened batch. Each item carries the display payload plus the
    recipe the loop needs. Returns the stored rows, payload enriched with id/status.

    An item is {"payload": <candidate_dict>, "backbone_key": str,
                "components": [[pendant_key, fraction], ...], "score": float}.
    """
    now = _now()
    stored = []
    with db.tx(conn):
        for item in items:
            cid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO candidate (id, campaign_id, iteration, backbone_key, components, "
                "score, payload, status, created_at, updated_at, app_version) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (cid, campaign_id, iteration, item["backbone_key"],
                 json.dumps(item["components"]), item["score"], json.dumps(item["payload"]),
                 "benchmarked", now, now, APP_VERSION))
            stored.append(_row_to_candidate(cid, iteration, "benchmarked", item["payload"]))
    return stored


def record_metrics(conn: psycopg.Connection, campaign_id: str, iteration: int,
                   metrics: dict) -> None:
    with db.tx(conn):
        conn.execute(
            "INSERT INTO iteration (campaign_id, iteration, metrics, created_at) "
            "VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (campaign_id, iteration) DO UPDATE "
            "SET metrics = EXCLUDED.metrics, created_at = EXCLUDED.created_at",
            (campaign_id, iteration, json.dumps(metrics), _now()))


def enqueue(conn: psycopg.Connection, candidate_ids: list[str], *, owner_id: int,
            structure_id: str = "") -> int:
    """Send benchmarked candidates to the simulation queue. Only a benchmarked
    (or failed) candidate can be queued, so re-queuing or queuing a running one is
    a no-op -- and so is naming another user's candidate."""
    return len(enqueue_rows(conn, candidate_ids, owner_id=owner_id, structure_id=structure_id))


def enqueue_rows(conn: psycopg.Connection, candidate_ids: list[str], *,
                 owner_id: int, structure_id: str = "",
                 max_active: int | None = None) -> list[dict]:
    """enqueue(), returning what was actually queued -- id, campaign and score --
    so the caller can log exactly that and nothing it merely asked for.

    structure_id is the target protein the worker simulates against. A worker
    only claims jobs that have one, so a candidate queued without one (before
    structures were asked for) waits; queuing it again WITH a structure attaches
    it. That is the one case where queuing an already-queued candidate acts. A
    failed job can be queued again, which starts its attempts afresh.

    max_active caps how many of the owner's candidates may be queued or running
    once this is done; over it, QueueFull and nothing changes. The check runs
    after the writes, under a per-owner lock, so two requests at once cannot
    both slip under the cap."""
    now = _now()
    queued = []
    with db.tx(conn):
        if max_active is not None:
            conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_QUEUE_LOCK, owner_id))
        for cid in candidate_ids:
            row = conn.execute(
                "UPDATE candidate SET status='queued', updated_at=%s, sim_structure_id=%s, "
                "sim_attempts=0, sim_error=NULL, sim_result=NULL, sim_progress=NULL, "
                "sim_approved_at=NULL, sim_approved_by=NULL, sim_tier=NULL "
                "WHERE id=%s AND (status IN ('benchmarked', 'failed') "
                "     OR (status='queued' AND sim_structure_id='' AND %s <> '')) "
                "AND campaign_id IN (SELECT id FROM campaign WHERE owner_id=%s) "
                "RETURNING id, campaign_id, score, payload",
                (now, structure_id, cid, structure_id, owner_id)).fetchone()
            if row:
                conn.execute("DELETE FROM sim_snapshot WHERE candidate_id=%s", (row["id"],))
                queued.append({"id": row["id"], "campaign_id": row["campaign_id"],
                               "score": row["score"],
                               "name": json.loads(row["payload"]).get("name", "")})
        if max_active is not None:
            active = [{"id": r["id"], "status": r["status"],
                       "name": json.loads(r["payload"]).get("name", "")}
                      for r in conn.execute(
                          "SELECT id, status, payload FROM candidate WHERE status = ANY(%s) "
                          "AND campaign_id IN (SELECT id FROM campaign WHERE owner_id=%s)",
                          (list(ACTIVE), owner_id))]
            if len(active) > max_active:
                newly = {q["id"] for q in queued}
                raise QueueFull(max_active, [a for a in active if a["id"] not in newly])
    return queued


def mark_status(conn: psycopg.Connection, candidate_id: str, status: Status) -> None:
    with db.tx(conn):
        conn.execute("UPDATE candidate SET status=%s, updated_at=%s WHERE id=%s",
                     (status, _now(), candidate_id))


# ---------------------------------------------------------------------------
# The simulation worker's side of the queue (worker/, via main.py's /worker/*)
# ---------------------------------------------------------------------------
# These are the only functions here that do NOT take an owner: the worker serves
# every user's queue, and is authenticated by WORKER_TOKEN rather than a session.
# What it can do is narrow -- claim the next job, report on the job it holds --
# and every report is checked against the worker that claimed it.

def requeue_stale(conn: psycopg.Connection) -> list[dict]:
    """Jobs whose worker went silent: back to 'queued', or 'failed' once they
    have used up their attempts. Returns what changed, for the history."""
    with db.tx(conn):
        rows = conn.execute(
            "UPDATE candidate SET "
            "  status = CASE WHEN sim_attempts >= %s THEN 'failed' ELSE 'queued' END, "
            "  sim_error = CASE WHEN sim_attempts >= %s "
            "    THEN 'the simulation worker stopped responding on every attempt' "
            "    ELSE 'the simulation worker stopped responding; requeued' END, "
            "  sim_worker = NULL, updated_at = %s "
            "WHERE status='simulating' AND sim_heartbeat_at < now() - make_interval(mins => %s) "
            "RETURNING id, campaign_id, status, sim_error",
            (MAX_ATTEMPTS, MAX_ATTEMPTS, _now(), STALE_AFTER_MINUTES)).fetchall()
    return rows


def claim_next(conn: psycopg.Connection, worker: str, tier: str = "gpu") -> dict | None:
    """Atomically take the highest-scoring job approved for this kind of worker
    ('gpu' or 'cpu') that has a target structure.
    SKIP LOCKED lets several workers pull from one queue without ever taking the
    same job. Returns the candidate, its campaign's goal and its owner, or None."""
    with db.tx(conn):
        row = conn.execute(
            "WITH nxt AS ("
            "  SELECT id FROM candidate WHERE status='queued' AND sim_structure_id <> '' "
            "  AND sim_approved_at IS NOT NULL AND sim_tier=%s "
            "  ORDER BY score DESC, updated_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "UPDATE candidate c SET status='simulating', sim_worker=%s, sim_started_at=now(), "
            "  sim_heartbeat_at=now(), sim_attempts=c.sim_attempts+1, sim_error=NULL, "
            "  sim_progress=NULL, updated_at=%s "
            "FROM nxt WHERE c.id=nxt.id RETURNING c.*",
            (tier, worker, _now())).fetchone()
        if row is None:
            return None
        camp = conn.execute("SELECT goal, owner_id FROM campaign WHERE id=%s",
                            (row["campaign_id"],)).fetchone()
    return {"candidate": _hydrate(row), "goal": json.loads(camp["goal"]),
            "owner_id": camp["owner_id"], "campaign_id": row["campaign_id"]}


def heartbeat(conn: psycopg.Connection, candidate_id: str, worker: str,
              progress: str = "") -> bool:
    """The worker is alive and still on this job. False if the job is no longer
    its own (requeued after it went quiet, and perhaps claimed by another): the
    worker should then abandon it rather than report a result nobody asked for."""
    with db.tx(conn):
        cur = conn.execute(
            "UPDATE candidate SET sim_heartbeat_at=now(), sim_progress=%s "
            "WHERE id=%s AND status='simulating' AND sim_worker=%s",
            (progress[:500] or None, candidate_id, worker))
    return cur.rowcount > 0


def finish(conn: psycopg.Connection, candidate_id: str, worker: str, *,
           result: dict | None = None, error: str | None = None,
           snapshot: str | None = None) -> dict | None:
    """Record the outcome of the job this worker holds: 'simulated' with its
    result, or 'failed' with why. None if it does not hold the job."""
    status = "failed" if error else "simulated"
    with db.tx(conn):
        row = conn.execute(
            "UPDATE candidate SET status=%s, sim_result=%s, sim_error=%s, "
            "  sim_finished_at=now(), sim_progress=NULL, updated_at=%s "
            "WHERE id=%s AND status='simulating' AND sim_worker=%s "
            "RETURNING *",
            (status, json.dumps(result) if result is not None else None,
             (error or "")[:2000] or None, _now(), candidate_id, worker)).fetchone()
        if row is None:
            return None
        if snapshot and status == "simulated":
            conn.execute("INSERT INTO sim_snapshot (candidate_id, pdb) VALUES (%s,%s) "
                         "ON CONFLICT (candidate_id) DO UPDATE SET pdb=EXCLUDED.pdb, created_at=now()",
                         (candidate_id, snapshot))
        camp = conn.execute("SELECT goal, owner_id FROM campaign WHERE id=%s",
                            (row["campaign_id"],)).fetchone()
    return {"candidate": _hydrate(row), "goal": json.loads(camp["goal"]),
            "owner_id": camp["owner_id"], "campaign_id": row["campaign_id"]}


def snapshot_for(conn: psycopg.Connection, candidate_id: str, *, owner_id: int) -> str | None:
    """The 3D snapshot of one of this user's runs, or None if there is none or
    the run is someone else's."""
    row = conn.execute(
        "SELECT s.pdb FROM sim_snapshot s JOIN candidate c ON c.id=s.candidate_id "
        "JOIN campaign cp ON cp.id=c.campaign_id WHERE s.candidate_id=%s AND cp.owner_id=%s",
        (candidate_id, owner_id)).fetchone()
    return row["pdb"] if row else None


# ---------------------------------------------------------------------------
# The admin's side: approving runs (main.py's /design/simulations)
# ---------------------------------------------------------------------------
# Owner-free too, for the same reason as the worker's: an admin decides on every
# user's jobs. The routes check the caller is an admin before any of these run.

def _job_info(conn: psycopg.Connection, row: dict) -> dict:
    camp = conn.execute("SELECT owner_id FROM campaign WHERE id=%s", (row["campaign_id"],)).fetchone()
    return {"candidate": _hydrate(row), "campaign_id": row["campaign_id"],
            "owner_id": camp["owner_id"] if camp else None}


def approve(conn: psycopg.Connection, candidate_id: str, admin_id: int,
            tier: str = "gpu") -> dict | None:
    """Let a worker of this tier run this job, or move an approved job that no
    worker has taken yet to the other tier. None unless it is queued, has a
    structure to run against, and is not already approved for this tier."""
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}")
    with db.tx(conn):
        row = conn.execute(
            "UPDATE candidate SET sim_approved_at=now(), sim_approved_by=%s, sim_tier=%s, "
            "  updated_at=%s "
            "WHERE id=%s AND status='queued' AND sim_structure_id <> '' "
            "  AND (sim_approved_at IS NULL OR sim_tier IS DISTINCT FROM %s) "
            "RETURNING *", (admin_id, tier, _now(), candidate_id, tier)).fetchone()
        return _job_info(conn, row) if row else None


def decline(conn: psycopg.Connection, candidate_id: str, reason: str) -> dict | None:
    """Deny any active job: waiting, approved, or running. It ends 'failed' with
    the reason, so the user sees why and can queue it again. A running job is
    stopped: its worker is cleared, so the worker's next heartbeat (within a
    minute) gets a 409 and it abandons the run, and a late result is refused.
    Returns the job with 'was' set to the status it had, or None."""
    with db.tx(conn):
        prev = conn.execute("SELECT status FROM candidate WHERE id=%s FOR UPDATE",
                            (candidate_id,)).fetchone()
        if prev is None or prev["status"] not in ACTIVE:
            return None
        why = "Stopped by an admin" if prev["status"] == "simulating" else "Not approved"
        row = conn.execute(
            "UPDATE candidate SET status='failed', sim_error=%s, sim_worker=NULL, "
            "  sim_approved_at=NULL, sim_approved_by=NULL, sim_tier=NULL, sim_progress=NULL, "
            "  sim_finished_at=CASE WHEN status='simulating' THEN now() ELSE sim_finished_at END, "
            "  updated_at=%s "
            "WHERE id=%s RETURNING *",
            (f"{why}: {reason}"[:2000], _now(), candidate_id)).fetchone()
        return {**_job_info(conn, row), "was": prev["status"]}


def recent_finished(conn: psycopg.Connection, limit: int = 20) -> list[dict]:
    """Every user's most recently finished or denied jobs, for the admin."""
    rows = conn.execute(
        "SELECT c.*, u.email AS owner_email FROM candidate c "
        "JOIN campaign cp ON cp.id=c.campaign_id LEFT JOIN users u ON u.id=cp.owner_id "
        "WHERE c.status IN ('simulated','failed') "
        "  AND (c.sim_attempts > 0 OR c.sim_error IS NOT NULL OR c.sim_result IS NOT NULL) "
        "ORDER BY c.updated_at DESC LIMIT %s", (limit,)).fetchall()
    return [{**_hydrate(r), "campaign_id": r["campaign_id"], "owner_email": r["owner_email"],
             "updated_at": r["updated_at"]} for r in rows]


def all_active(conn: psycopg.Connection, limit: int = 200) -> list[dict]:
    """Every user's waiting and running jobs, for the admin: awaiting approval
    first, then approved, then running, each best score first."""
    rows = conn.execute(
        "SELECT c.*, u.email AS owner_email FROM candidate c "
        "JOIN campaign cp ON cp.id=c.campaign_id LEFT JOIN users u ON u.id=cp.owner_id "
        "WHERE c.status = ANY(%s) "
        "ORDER BY CASE WHEN c.status='simulating' THEN 2 "
        "              WHEN c.sim_approved_at IS NULL THEN 0 ELSE 1 END, c.score DESC "
        "LIMIT %s", (list(ACTIVE), limit)).fetchall()
    return [{**_hydrate(r), "campaign_id": r["campaign_id"], "owner_email": r["owner_email"]}
            for r in rows]


def simulations_for(conn: psycopg.Connection, *, owner_id: int, limit: int = 200) -> list[dict]:
    """Every candidate of this user's that was ever sent for simulation, with the
    campaign it belongs to: running first, then waiting, then the rest newest first."""
    rows = conn.execute(
        "SELECT c.*, cp.goal AS campaign_goal FROM candidate c "
        "JOIN campaign cp ON cp.id=c.campaign_id "
        "WHERE cp.owner_id=%s AND c.status IN ('queued','simulating','simulated','failed') "
        "  AND (c.sim_structure_id <> '' OR c.sim_attempts > 0 OR c.sim_error IS NOT NULL "
        "       OR c.status='queued') "
        "ORDER BY CASE c.status WHEN 'simulating' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, "
        "  c.updated_at DESC LIMIT %s", (owner_id, limit)).fetchall()
    return [_with_campaign(r) for r in rows]


def simulation(conn: psycopg.Connection, candidate_id: str, *, owner_id: int) -> dict | None:
    """One of this user's simulations, or None if absent or someone else's."""
    row = conn.execute(
        "SELECT c.*, cp.goal AS campaign_goal FROM candidate c "
        "JOIN campaign cp ON cp.id=c.campaign_id WHERE c.id=%s AND cp.owner_id=%s",
        (candidate_id, owner_id)).fetchone()
    return _with_campaign(row) if row else None


def _with_campaign(row: dict) -> dict:
    goal = json.loads(row["campaign_goal"]) if row.get("campaign_goal") else {}
    return {**_hydrate(row), "campaign_id": row["campaign_id"], "updated_at": row["updated_at"],
            "campaign": {"protein": goal.get("protein", ""), "format": goal.get("format", ""),
                         "target_temp_c": goal.get("target_temp_c")}}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _row_to_candidate(cid: str, iteration: int, status: str, payload: dict) -> dict:
    """Payload as stored, with the fields only the store knows merged in."""
    return {**payload, "id": cid, "iteration": iteration, "status": status}


def _iso(t) -> str | None:
    return t.isoformat() if t is not None else None


def _hydrate(row: dict) -> dict:
    c = _row_to_candidate(row["id"], row["iteration"], row["status"], json.loads(row["payload"]))
    # The simulation job, once there is one. Absent for a candidate never queued,
    # so an unqueued candidate's shape is exactly what it always was.
    if row.get("sim_structure_id") or row.get("sim_attempts") or row.get("sim_error"):
        c["simulation"] = {
            "structure_id": row.get("sim_structure_id") or "",
            "attempts": row.get("sim_attempts") or 0,
            "started_at": _iso(row.get("sim_started_at")),
            "heartbeat_at": _iso(row.get("sim_heartbeat_at")),
            "finished_at": _iso(row.get("sim_finished_at")),
            "progress": row.get("sim_progress"),
            "result": json.loads(row["sim_result"]) if row.get("sim_result") else None,
            "error": row.get("sim_error"),
            "approved_at": _iso(row.get("sim_approved_at")),
            "tier": row.get("sim_tier"),
        }
    return c


def next_iteration(conn: psycopg.Connection, campaign_id: str) -> int:
    row = conn.execute("SELECT MAX(iteration) AS m FROM candidate WHERE campaign_id=%s",
                       (campaign_id,)).fetchone()
    return 1 if row["m"] is None else row["m"] + 1


def candidates_for(conn: psycopg.Connection, campaign_id: str,
                   iteration: int | None = None) -> list[dict]:
    sql = "SELECT * FROM candidate WHERE campaign_id=%s"
    args: list = [campaign_id]
    if iteration is not None:
        sql += " AND iteration=%s"
        args.append(iteration)
    sql += " ORDER BY iteration, score DESC"
    return [_hydrate(r) for r in conn.execute(sql, args)]


def prior_for(conn: psycopg.Connection, campaign_id: str) -> list[dict]:
    """Everything benchmarked in this campaign, in the compact shape the active
    loop's PriorCandidate expects — backbone key, (pendant_key, fraction) pairs,
    and the score — so the surrogate can be refit without touching the payloads."""
    rows = conn.execute(
        "SELECT backbone_key, components, score FROM candidate WHERE campaign_id=%s",
        (campaign_id,)).fetchall()
    return [{"backbone_key": r["backbone_key"],
             "components": [tuple(c) for c in json.loads(r["components"])],
             "score": r["score"]} for r in rows]


def metrics_history(conn: psycopg.Connection, campaign_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT iteration, metrics FROM iteration WHERE campaign_id=%s ORDER BY iteration",
        (campaign_id,)).fetchall()
    return [{"iteration": r["iteration"], **json.loads(r["metrics"])} for r in rows]


def queue(conn: psycopg.Connection, *, owner_id: int, limit: int = 50,
          campaign_id: str | None = None) -> list[dict]:
    """One user's simulation backlog, highest triage score first. Scoped to one
    of their campaigns when given, else across all of them."""
    sql = ("SELECT * FROM candidate WHERE status='queued' "
           "AND campaign_id IN (SELECT id FROM campaign WHERE owner_id=%s)")
    args: list = [owner_id]
    if campaign_id:
        sql += " AND campaign_id=%s"
        args.append(campaign_id)
    sql += " ORDER BY score DESC LIMIT %s"
    args.append(limit)
    return [_hydrate(r) for r in conn.execute(sql, args)]


def queue_summary(conn: psycopg.Connection, *, owner_id: int,
                  campaign_id: str | None = None) -> dict:
    """Counts by status, plus the top of the queue — enough for the panel to say
    'N queued, M running' without pulling every row."""
    sql = ("SELECT status, COUNT(*) AS n FROM candidate "
           "WHERE campaign_id IN (SELECT id FROM campaign WHERE owner_id=%s)")
    args: list = [owner_id]
    if campaign_id:
        sql += " AND campaign_id=%s"
        args.append(campaign_id)
    sql += " GROUP BY status"
    counts = {r["status"]: r["n"] for r in conn.execute(sql, args)}
    top = queue(conn, owner_id=owner_id, limit=5, campaign_id=campaign_id)
    return {
        "n_queued": counts.get("queued", 0),
        "n_benchmarked": counts.get("benchmarked", 0),
        "n_simulated": counts.get("simulated", 0),
        "n_simulating": counts.get("simulating", 0),
        "n_failed": counts.get("failed", 0),
        "by_status": counts,
        "top": [{"id": c["id"], "name": c["name"], "score": c["score"]} for c in top],
        "note": ("An admin approves each simulation before it runs, and you can have one "
                 "waiting or running at a time. Approved runs go in triage-score order. A "
                 "queue position is a triage decision, not evidence."),
    }


def campaign_state(conn: psycopg.Connection, campaign_id: str, *,
                   owner_id: int) -> dict | None:
    """The whole campaign, or None if it does not exist OR belongs to someone
    else -- the caller cannot tell the two apart, so ids cannot be probed."""
    row = conn.execute("SELECT * FROM campaign WHERE id=%s AND owner_id=%s",
                       (campaign_id, owner_id)).fetchone()
    if row is None:
        return None
    cands = candidates_for(conn, campaign_id)
    by_iteration: dict[int, list[dict]] = {}
    for c in cands:
        by_iteration.setdefault(c["iteration"], []).append(c)
    return {
        "campaign_id": campaign_id,
        "goal": json.loads(row["goal"]),
        "prompt": row.get("prompt") or "",
        "created_at": row["created_at"],
        "ended_at": row.get("ended_at"),
        "app_version": row["app_version"],
        "n_candidates": len(cands),
        "n_iterations": len(by_iteration),
        "candidates_by_iteration": by_iteration,
        "metrics_history": metrics_history(conn, campaign_id),
        "queue_summary": queue_summary(conn, owner_id=owner_id, campaign_id=campaign_id),
    }
