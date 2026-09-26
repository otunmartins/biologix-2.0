"""Who is asking. Sign-in happens in the web app (Auth.js, Google); this module
only answers "which user does this request belong to?" for the API.

HOW THE TWO HALVES MEET. Auth.js runs with database sessions through
@auth/pg-adapter, so a signed-in browser carries a cookie holding a random session
token, and the matching row sits in the `sessions` table of the same Postgres the
API uses. The API reads that cookie and looks the token up. No shared JWT secret,
no call back to the web app, and signing out (which deletes the row) takes effect
on the API at once.

The cookie reaches the API because both are served from one origin: Caddy sends
/screen and /design to the API and everything else to Next.js. Locally they are
on different ports of localhost, which still counts as the same site, so the
cookie travels as long as the browser sends credentials (lib/api.ts does).

THE SCHEMA BELONGS HERE, NOT TO THE WEB APP. campaign.owner_id references
users(id), so the users table has to exist before the campaign store's DDL runs,
and one side has to own every CREATE TABLE. Column names and types are exactly
what @auth/pg-adapter reads and writes -- camelCase and quoted, SERIAL ids -- so
the adapter needs nothing of its own. The unique indexes are additions: the
adapter looks rows up by these columns and assumes each finds at most one.
"""

import psycopg
from fastapi import HTTPException, Request

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255),
    email           VARCHAR(255),
    "emailVerified" TIMESTAMPTZ,
    image           TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users(email);

CREATE TABLE IF NOT EXISTS accounts (
    id                  SERIAL PRIMARY KEY,
    "userId"            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type                VARCHAR(255) NOT NULL,
    provider            VARCHAR(255) NOT NULL,
    "providerAccountId" VARCHAR(255) NOT NULL,
    refresh_token       TEXT,
    access_token        TEXT,
    expires_at          BIGINT,
    id_token            TEXT,
    scope               TEXT,
    session_state       TEXT,
    token_type          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS accounts_provider
    ON accounts(provider, "providerAccountId");

CREATE TABLE IF NOT EXISTS sessions (
    id             SERIAL PRIMARY KEY,
    "userId"       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires        TIMESTAMPTZ NOT NULL,
    "sessionToken" VARCHAR(255) NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS sessions_token ON sessions("sessionToken");

CREATE TABLE IF NOT EXISTS verification_token (
    identifier TEXT NOT NULL,
    expires    TIMESTAMPTZ NOT NULL,
    token      TEXT NOT NULL,
    PRIMARY KEY (identifier, token)
);
"""

# Auth.js names the cookie by whether the site is served over HTTPS: the
# __Secure- prefix in production behind Caddy's certificate, the bare name on
# http://localhost. Checked in this order so a stale plain cookie left over from
# local testing never shadows the real one.
SESSION_COOKIES = ("__Secure-authjs.session-token", "authjs.session-token")


def ensure_schema(conn: psycopg.Connection) -> None:
    db.init_schema(conn, SCHEMA)


def session_token(request: Request) -> str | None:
    for name in SESSION_COOKIES:
        if value := request.cookies.get(name):
            return value
    return None


def user_for_token(conn: psycopg.Connection, token: str) -> int | None:
    """The user id behind a live session token, or None if it is unknown or expired."""
    ensure_schema(conn)
    row = conn.execute(
        'SELECT "userId" AS user_id FROM sessions '
        'WHERE "sessionToken" = %s AND expires > now()', (token,)).fetchone()
    return None if row is None else row["user_id"]


def current_user(request: Request) -> int:
    """FastAPI dependency: the signed-in user's id, or 401.

    401 rather than 403 in both cases -- no cookie and a dead one -- because the
    fix is the same for the browser: sign in again.
    """
    token = session_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="sign in to use this")
    try:
        conn = db.connect()
    except Exception as e:
        # Unreachable database: nothing can be checked, so nothing is allowed.
        raise HTTPException(status_code=503, detail="cannot check your session right now") from e
    try:
        user_id = user_for_token(conn, token)
    finally:
        conn.close()
    if user_id is None:
        raise HTTPException(status_code=401, detail="your session has expired; sign in again")
    return user_id
