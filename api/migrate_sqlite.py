"""One-off: copy the sqlite stores into Postgres, then prove every row arrived.

    python migrate_sqlite.py --measurements measurements.db --campaigns campaigns.db
    python migrate_sqlite.py ... --url postgresql://...   (default: $DATABASE_URL)
    python migrate_sqlite.py ... --dry-run                (copy, verify, roll back)

Run it against COPIES of the box's files (docker cp them out first), never the
live volume. Either file may be left out if that store was never used.

SAFE TO RE-RUN. Everything goes in one transaction: a failure anywhere (an orphan
row, a column Postgres does not have) rolls the whole thing back and Postgres is
exactly as it was. Rows already present are skipped by primary key, so a second
run after a success inserts nothing and still verifies.

VERIFIED, NOT COUNTED. After inserting, every sqlite row is read back from
Postgres by its key and compared column by column, inside the same transaction,
so a mismatch rolls back too. A row that was already in Postgres with different
contents is a mismatch: the script refuses rather than choosing a winner.
"""

import argparse
import os
import sqlite3
import sys

import candidates
import db
import measurements

# Parents before children, so foreign keys hold as each table goes in.
STORES = {
    "measurements": (measurements.SCHEMA, [("biologic", ["id"]),
                                           ("measurement", ["id"])]),
    "campaigns": (candidates.SCHEMA, [("campaign", ["id"]),
                                      ("iteration", ["campaign_id", "iteration"]),
                                      ("candidate", ["id"])]),
}


def pg_columns(conn, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s "
        "ORDER BY ordinal_position", (table,)).fetchall()
    return [r["column_name"] for r in rows]


def copy_table(src: sqlite3.Connection, conn, table: str, key: list[str]) -> tuple[int, int]:
    """Insert every sqlite row missing from Postgres, then compare every row.
    Returns (rows in sqlite, rows newly inserted)."""
    exists = src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                         (table,)).fetchone()
    if not exists:
        return 0, 0
    rows = src.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return 0, 0

    cols = list(rows[0].keys())
    missing = set(cols) - set(pg_columns(conn, table))
    if missing:
        # Dropping a column silently is data loss; stop instead.
        raise SystemExit(f"{table}: sqlite has columns Postgres does not: {sorted(missing)}")

    placeholders = ", ".join(["%s"] * len(cols))
    insert = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
              f"ON CONFLICT ({', '.join(key)}) DO NOTHING")
    inserted = 0
    for r in rows:
        inserted += conn.execute(insert, tuple(r)).rowcount

    where = " AND ".join(f"{k} = %s" for k in key)
    for r in rows:
        got = conn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE {where}",
                           tuple(r[k] for k in key)).fetchone()
        if got is None:
            raise SystemExit(f"{table}: row {[r[k] for k in key]} missing after insert")
        diff = [c for c in cols if got[c] != r[c]]
        if diff:
            raise SystemExit(f"{table}: row {[r[k] for k in key]} already in Postgres "
                             f"with different {diff}; not overwriting")
    return len(rows), inserted


class DryRun(Exception):
    pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--measurements", help="path to a copy of measurements.db")
    ap.add_argument("--campaigns", help="path to a copy of campaigns.db")
    ap.add_argument("--url", help="Postgres URL (default: $DATABASE_URL)")
    ap.add_argument("--dry-run", action="store_true",
                    help="do everything, verify, then roll back")
    args = ap.parse_args()

    sources = {name: getattr(args, name) for name in STORES if getattr(args, name)}
    if not sources:
        ap.error("give --measurements and/or --campaigns")
    for path in sources.values():
        if not os.path.isfile(path):
            ap.error(f"no such file: {path}")

    conn = db.connect(args.url)
    report = []
    try:
        with db.tx(conn):
            for name, path in sources.items():
                schema, tables = STORES[name]
                conn.execute(schema)
                src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                src.row_factory = sqlite3.Row
                try:
                    for table, key in tables:
                        total, new = copy_table(src, conn, table, key)
                        report.append((table, total, new))
                finally:
                    src.close()
            if args.dry_run:
                raise DryRun
    except DryRun:
        pass
    finally:
        conn.close()

    for table, total, new in report:
        print(f"{table:12} {total:6} rows in sqlite, {new:6} inserted, all verified")
    print("DRY RUN: rolled back, Postgres unchanged" if args.dry_run
          else "committed")


if __name__ == "__main__":
    sys.exit(main())
