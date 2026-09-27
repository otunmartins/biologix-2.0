"""Create every table the app needs in a Postgres database, then list them.

    python api/init_db.py                          # uses DATABASE_URL
    python api/init_db.py NEON_PROD_DATABASE_URL   # or any env var / .env key

The API does the same thing at startup, so this is only for setting up a
database before anything has run against it (a fresh Neon branch, a new
environment). Idempotent: CREATE TABLE IF NOT EXISTS and ADD COLUMN IF NOT
EXISTS only, so it never changes or drops what is already there. The
connection string is read from the environment or the root .env and never
printed.
"""

import os
import sys
from pathlib import Path

import psycopg

import history
import measurements


def _url(var: str) -> str:
    if os.environ.get(var):
        return os.environ[var]
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{var}="):
                return line.split("=", 1)[1].strip()
    sys.exit(f"{var} is not set in the environment or .env")


def main() -> None:
    var = sys.argv[1] if len(sys.argv) > 1 else "DATABASE_URL"
    url = _url(var)
    host = url.split("@", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    print(f"creating tables on {host} (from {var})")
    history.connect(url).close()       # sign-in tables, then campaigns, then history
    measurements.connect(url).close()  # biologics and their measurements
    with psycopg.connect(url, connect_timeout=15) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY 1")]
    print(f"{len(tables)} tables: {', '.join(tables)}")


if __name__ == "__main__":
    main()
