// Sign-in. Two ways in: Google, and email and password (lib/password-auth.ts,
// which is not an Auth.js provider -- see there for why). Both end in the same
// kind of database session, so everything below treats them alike.
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
import { pool } from '@/lib/db';

export const { handlers, auth, signIn, signOut } = NextAuth({
  adapter: PostgresAdapter(pool),
  providers: [
    // AUTH_GOOGLE_ID and AUTH_GOOGLE_SECRET are read from the environment.
    Google({
      // Someone who registered with a password and later clicks Google lands in
      // the same account instead of an "already signed in another way" error.
      // Safe for Google specifically: it only hands over addresses it has
      // verified, so this proves the person owns the email.
      allowDangerousEmailAccountLinking: true,
    }),
  ],
  session: { strategy: 'database' },
  events: {
    // A password is set without proving the email (there is no verification
    // mail yet). Once Google has proved who owns the address, a password on it
    // could belong to someone else who registered it first -- so it goes, and
    // the account is Google-only from then on. For a brand-new Google user this
    // deletes nothing.
    async linkAccount({ user, account }) {
      if (account.provider === 'google' && user.id) {
        await pool.query('DELETE FROM passwords WHERE user_id = $1', [user.id]);
      }
    },
  },
  // One page does both: app/page.tsx shows the sign-in screen (with any error
  // Auth.js reports) when there is no session, and the app when there is.
  pages: { signIn: '/', error: '/' },
  // Behind Caddy, the public host and https arrive in X-Forwarded-* headers.
  // AUTH_URL (from WEB_ORIGIN) is what actually fixes the callback URL; this
  // stops Auth.js rejecting the forwarded host in the meantime.
  trustHost: true,
});
