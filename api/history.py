"""Each user's history: every screen they ran, every campaign they started, and
an append-only log of what happened in them. This is the provenance record.

WHAT PROVENANCE MEANS HERE. For any result a user looks at, the record answers:
what exactly was asked (the words and form values as sent), what came back,
when, which build of this code produced it (APP_VERSION, the git commit), which
model, and what every data source actually returned during that run -- the
PubChem resolution, the FDA rows, the label counts, the lookup errors. Those
last come from ScreenDeps, the per-run record the evidence gate already keeps,
so the history shows the same facts the gate judged the dossier against.

APPEND-ONLY. screen_run rows are written once. Campaign changes (started,
iterated, queued, ended, reopened) are events, never edits to past rows. Ending
a campaign with "New experiment" sets a marker and logs it; nothing is deleted.

RECORDING NEVER COSTS A RESULT. main.py writes a screen's record after the run;
if the write fails the user still gets their dossier, marked unsaved.

Timestamps here are TIMESTAMPTZ and payloads JSONB. The campaign store predates
Postgres and keeps TEXT for both; its created_at is cast where the two meet.
"""

import base64
import json
import os
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

import candidates
import db
import users

APP_VERSION = os.environ.get("APP_VERSION", "dev")

SCHEMA = """
CREATE TABLE IF NOT EXISTS screen_run (
    id            TEXT PRIMARY KEY,
    owner_id      INTEGER NOT NULL REFERENCES users(id),
    created_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    -- What was sent: prompt, polymer, exposure, and the form as the user filled it.
    request       JSONB NOT NULL,
    dossier       JSONB,
    error         TEXT,
    -- How it was produced: model, app version, timings, token usage, and what
    -- each tool returned during this run.
    provenance    JSONB NOT NULL,
    -- Denormalised for the history list, so a page of it reads no JSON.
    excipient     TEXT,
    protein       TEXT,
    route         TEXT,
    worst_verdict TEXT,
    needs_testing BOOLEAN,
    smiles        TEXT,
    app_version   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS screen_run_by_owner ON screen_run(owner_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS event (
    id           BIGSERIAL PRIMARY KEY,
    owner_id     INTEGER NOT NULL REFERENCES users(id),
    at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind         TEXT NOT NULL,
    subject_kind TEXT NOT NULL,          -- 'screen' | 'campaign'
    subject_id   TEXT NOT NULL,
    data         JSONB NOT NULL DEFAULT '{}'::jsonb,
    app_version  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS event_by_owner ON event(owner_id, at DESC);
CREATE INDEX IF NOT EXISTS event_by_subject ON event(subject_id, at);
"""

# Most serious first: a history row shows the worst verdict its dossier reached.
VERDICT_ORDER = ["Alert: avoid", "Data gap: test", "Supported without precedent", "Precedented"]


def connect(url: str | None = None) -> psycopg.Connection:
    """The campaign store's connection, with history's tables too: history reads
    campaigns, and both need users first (candidates.connect sees to that)."""
    conn = candidates.connect(url)
    db.init_schema(conn, SCHEMA)
    return conn


def ensure_schema(conn: psycopg.Connection) -> None:
    users.ensure_schema(conn)
    db.init_schema(conn, SCHEMA)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def log_event(conn: psycopg.Connection, owner_id: int, kind: str, subject_kind: str,
              subject_id: str, data: dict | None = None) -> None:
    with db.tx(conn):
        conn.execute(
            "INSERT INTO event (owner_id, kind, subject_kind, subject_id, data, app_version) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (owner_id, kind, subject_kind, subject_id, Jsonb(data or {}), APP_VERSION))


def worst_verdict(dossier: dict | None) -> str | None:
    if not dossier:
        return None
    present = {e.get("verdict") for e in dossier.get("endpoints", [])}
    return next((v for v in VERDICT_ORDER if v in present), None)


def record_screen(conn: psycopg.Connection, owner_id: int, *, request: dict,
                  dossier: dict | None, error: str | None, provenance: dict,
                  started_at: datetime, finished_at: datetime) -> str:
    """Store one screen, success or failure, and log it. One transaction: a run
    is never in the history without its event, or the other way round."""
    sid = str(uuid.uuid4())
    status = "ok" if dossier is not None else "failed"
    d = dossier or {}
    with db.tx(conn):
        conn.execute(
            "INSERT INTO screen_run (id, owner_id, created_at, finished_at, status, request, "
            "dossier, error, provenance, excipient, protein, route, worst_verdict, "
            "needs_testing, smiles, app_version) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (sid, owner_id, started_at, finished_at, status, Jsonb(request),
             Jsonb(dossier) if dossier is not None else None, error, Jsonb(provenance),
             d.get("excipient"), d.get("protein"), d.get("route"), worst_verdict(dossier),
             d.get("needs_testing"), d.get("structure_smiles") or None, APP_VERSION))
        log_event(conn, owner_id, "screen.completed" if status == "ok" else "screen.failed",
                  "screen", sid, {"excipient": d.get("excipient"), "error": error})
    return sid


# ---------------------------------------------------------------------------
# Reads -- every one takes the owner and filters on it
# ---------------------------------------------------------------------------

def screen_record(conn: psycopg.Connection, screen_id: str, *, owner_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM screen_run WHERE id=%s AND owner_id=%s",
                       (screen_id, owner_id)).fetchone()
    if row is None:
        return None
    out = dict(row)
    for k in ("created_at", "finished_at"):
        out[k] = out[k].isoformat()
    out.pop("owner_id")
    return out


def events_for(conn: psycopg.Connection, subject_id: str, *, owner_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT at, kind, data, app_version FROM event "
        "WHERE subject_id=%s AND owner_id=%s ORDER BY at, id",
        (subject_id, owner_id)).fetchall()
    return [{**r, "at": r["at"].isoformat()} for r in rows]


def _cursor(at: datetime, item_id: str) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{item_id}".encode()).decode()


def _parse_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if not cursor:
        return None, None
    try:
        at, item_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(at), item_id
    except Exception as e:
        raise ValueError("bad cursor") from e


def _screen_summaries(conn: psycopg.Connection, ids: list[str], owner_id: int) -> dict[str, dict]:
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT id, created_at, finished_at, status, excipient, protein, route, worst_verdict, "
        "needs_testing, smiles, error, request->>'prompt' AS prompt, app_version "
        "FROM screen_run WHERE id = ANY(%s) AND owner_id=%s", (ids, owner_id)).fetchall()
    return {r["id"]: {
        "kind": "screen", "id": r["id"], "at": r["created_at"].isoformat(),
        "status": r["status"], "excipient": r["excipient"], "protein": r["protein"],
        "route": r["route"], "worst_verdict": r["worst_verdict"],
        "needs_testing": r["needs_testing"], "smiles": r["smiles"], "error": r["error"],
        "prompt": r["prompt"], "app_version": r["app_version"],
        "duration_s": round((r["finished_at"] - r["created_at"]).total_seconds(), 1),
    } for r in rows}


def _campaign_summaries(conn: psycopg.Connection, ids: list[str], owner_id: int) -> dict[str, dict]:
    if not ids:
        return {}
    rows = conn.execute(
        """
        SELECT c.id, c.goal, c.prompt, c.created_at, c.ended_at, c.app_version,
               COUNT(k.id)                                       AS n_candidates,
               COUNT(DISTINCT k.iteration)                       AS n_iterations,
               COUNT(k.id) FILTER (WHERE k.status <> 'benchmarked') AS n_queued,
               MAX(k.created_at)                                 AS last_activity,
               (SELECT payload FROM candidate t WHERE t.campaign_id = c.id
                 ORDER BY t.score DESC LIMIT 1)                  AS top_payload
        FROM campaign c LEFT JOIN candidate k ON k.campaign_id = c.id
        WHERE c.id = ANY(%s) AND c.owner_id = %s
        GROUP BY c.id
        """, (ids, owner_id)).fetchall()
    out = {}
    for r in rows:
        top = json.loads(r["top_payload"]) if r["top_payload"] else None
        units = (top or {}).get("composition") or []
        out[r["id"]] = {
            "kind": "campaign", "id": r["id"], "at": r["created_at"],
            "goal": json.loads(r["goal"]), "prompt": r["prompt"] or "",
            "ended_at": r["ended_at"], "last_activity": r["last_activity"],
            "n_candidates": r["n_candidates"], "n_iterations": r["n_iterations"],
            "n_queued": r["n_queued"], "app_version": r["app_version"],
            "top": top and {
                "name": top.get("name"), "score": top.get("score"),
                "smiles": units[0]["repeat_unit_smiles"] if units
                else (top.get("repeat_unit_smiles") or "").split(" ; ")[0],
            },
        }
    return out


def list_history(conn: psycopg.Connection, *, owner_id: int, kind: str = "all",
                 before: str | None = None, limit: int = 20) -> dict:
    """Screens and campaigns in one newest-first list, a page at a time.

    Two steps: first the ordered ids for the page (a cheap union), then the
    summaries for just those -- so the per-campaign counts are computed for
    twenty rows, not for every campaign the user ever ran.
    """
    at, item_id = _parse_cursor(before)
    rows = conn.execute(
        """
        SELECT kind, id, at FROM (
            SELECT 'screen' AS kind, id, created_at AS at FROM screen_run WHERE owner_id = %(o)s
            UNION ALL
            SELECT 'campaign', id, created_at::timestamptz FROM campaign WHERE owner_id = %(o)s
        ) h
        WHERE (%(k)s = 'all' OR kind = %(k)s)
          AND (%(at)s::timestamptz IS NULL OR (at, id) < (%(at)s::timestamptz, %(id)s))
        ORDER BY at DESC, id DESC
        LIMIT %(n)s
        """, {"o": owner_id, "k": kind, "at": at, "id": item_id, "n": limit + 1}).fetchall()
    page, more = rows[:limit], len(rows) > limit
    screens = _screen_summaries(conn, [r["id"] for r in page if r["kind"] == "screen"], owner_id)
    camps = _campaign_summaries(conn, [r["id"] for r in page if r["kind"] == "campaign"], owner_id)
    items = [(screens if r["kind"] == "screen" else camps)[r["id"]] for r in page]
    return {"items": items,
            "next": _cursor(page[-1]["at"], page[-1]["id"]) if more and page else None}
