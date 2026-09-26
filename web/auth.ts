// Sign-in. Google is the only way in; there are no passwords here to lose.
//
// DATABASE SESSIONS, ON PURPOSE. With the Postgres adapter, a signed-in browser
// holds only a random token and the session itself is a row in `sessions`. The
// FastAPI side checks that same row (api/users.py), so the two halves agree on
// who is signed in without sharing a JWT secret, and signing out takes effect on
// the API immediately.
//
// THE TABLES ARE CREATED BY THE API, not here: api/users.py owns their DDL
// because the campaign store's foreign keys point at `users`. The API creates
// them on startup, which is why docker-compose starts it first.
import PostgresAdapter from '@auth/pg-adapter';
import NextAuth from 'next-auth';
import Google from 'next-auth/providers/google';
import { Pool } from 'pg';

// Neon's POOLED string, same as the API's. A handful of connections is plenty:
// the adapter only runs on sign-in and on page loads.
const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 5 });

export const { handlers, auth, signIn, signOut } = NextAuth({
  adapter: PostgresAdapter(pool),
  // AUTH_GOOGLE_ID and AUTH_GOOGLE_SECRET are read from the environment.
  providers: [Google],
  session: { strategy: 'database' },
  // One page does both: app/page.tsx shows the sign-in screen (with any error
  // Auth.js reports) when there is no session, and the app when there is.
  pages: { signIn: '/', error: '/' },
  // Behind Caddy, the public host and https arrive in X-Forwarded-* headers.
  // Trusting them is what makes the Google callback URL and the __Secure-
  // cookie come out right without hard-coding the domain here.
  trustHost: true,
});
