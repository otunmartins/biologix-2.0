"""One Postgres connection for the whole app, and the transaction helper around it.

WHY THIS EXISTS. measurements.py and candidates.py each opened their own sqlite
file, which was fine while every row lived on one box and belonged to nobody. It
stops being fine the moment a user owns rows: sqlite cannot be shared between the
api container and a worker, it has no SKIP LOCKED for claiming a job, and it
cannot hold a foreign key into a users table that Prisma migrates. Both stores
were written for this move -- portable SQL, TEXT uuid primary keys, explicit
timestamps -- so what changes here is the connection, not the data model.

WHAT CHANGED GOING FROM sqlite3 TO psycopg, AND WHY YOU CARE.

  Placeholders are %s, not ?.

  PRAGMA foreign_keys is gone. Postgres always enforces a foreign key; sqlite had
  to be asked, and forgetting to ask silently accepted orphans.

  A FAILED STATEMENT POISONS THE TRANSACTION. This is the one that bites. In
  sqlite a rejected INSERT left the connection usable, so code (and the smoke
  test) could catch an IntegrityError and carry on. In Postgres every later
  statement on that connection fails with InFailedSqlTransaction until someone
  rolls back. That is why every write below goes through tx(): it commits on
  success and rolls back on failure, so a caller that catches an expected
  integrity error still has a working connection afterwards.

  CONNECTIONS ARE AUTOCOMMIT, AND tx() IS THE ONLY TRANSACTION. psycopg's default
  opens a transaction on the first statement, reads included, and nothing here
  ends one after a read. The connection then sits "idle in transaction" holding
  its table locks until it closes: the smoke test hung for hours on a DROP TABLE
  queued behind a finished SELECT, and in production every request would pin a
  Neon pooler connection for its whole lifetime. With autocommit a read is its
  own statement and releases its locks at once; writes still get BEGIN/COMMIT
  from tx().

CONNECTIONS ARE PER REQUEST, NOT POOLED, for now. Against a local Postgres that
costs nothing. Against Neon's pooled endpoint over the internet it costs a TLS
handshake per request, which is worth replacing with psycopg_pool once there is
traffic to justify it -- but a pool is a lifecycle to get wrong, and nothing here
is latency-bound yet.
"""

import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

# Re-exported so callers and tests can catch a constraint violation without
# importing psycopg themselves, and without caring that the concrete class is
# ForeignKeyViolation or UniqueViolation rather than the base.
IntegrityError = psycopg.IntegrityError

# DATABASE_URL is the app's connection. Neon publishes two strings and they are
# not interchangeable: the POOLED one (host contains "-pooler") belongs here, and
# the direct one belongs to prisma migrate, which needs a real session.
ENV_VAR = "DATABASE_URL"


def url(explicit: str | None = None) -> str:
    u = explicit or os.environ.get(ENV_VAR, "")
    if not u.strip():
        raise RuntimeError(
            f"no database configured: set {ENV_VAR} to a Postgres connection string "
            "(Neon's pooled URL in deployment, or postgresql://…@localhost:5432/… locally)")
    return u


def connect(explicit_url: str | None = None) -> psycopg.Connection:
    """A connection whose rows are plain dicts, so row["col"] and dict(row) both
    work exactly as they did under sqlite3.Row."""
    return psycopg.connect(url(explicit_url), row_factory=dict_row, autocommit=True)


@contextmanager
def tx(conn: psycopg.Connection):
    """Commit on success, roll back on any exception, then re-raise.

    The rollback is the point: without it one rejected write leaves the connection
    unusable for everything after it. See the module docstring.
    """
    with conn.transaction():
        yield conn


# Which schemas this process has already applied, so that opening a connection per
# request does not re-issue the DDL every time. CREATE TABLE IF NOT EXISTS is
# harmless but it is still several round trips, and against Neon those are over
# the internet. Keyed by (url, schema) so a test pointing at a different database
# still gets its tables.
_applied: set[tuple[str, int]] = set()


def reset_schema_cache() -> None:
    """Forget which schemas were applied. A test that DROPs its tables has to call
    this, or the cache above would skip recreating them."""
    _applied.clear()


def init_schema(conn: psycopg.Connection, schema: str, force: bool = False) -> None:
    """Run an idempotent CREATE TABLE IF NOT EXISTS script, once per process.

    Kept as a plain script rather than a migration tool because both stores were
    written to be idempotent and that still holds on Postgres. The moment a column
    has to CHANGE rather than appear, this is where Alembic earns its place.
    """
    key = (conn.info.dsn, hash(schema))
    if not force and key in _applied:
        return
    with tx(conn):
        conn.execute(schema)
    _applied.add(key)
