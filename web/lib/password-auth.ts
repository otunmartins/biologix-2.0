import 'server-only';
import { randomBytes } from 'node:crypto';
import { cookies } from 'next/headers';
import { pool } from '@/lib/db';
import { dummyHash, hashPassword, passwordProblem, verifyPassword } from '@/lib/passwords';

// Email-and-password sign-in, alongside Google.
//
// WHY NOT AUTH.JS'S CREDENTIALS PROVIDER. It always issues a JWT cookie, even
// with database sessions configured, and the API recognises a user only by a
// row in `sessions` (api/users.py). So this does what Auth.js does after a
// Google sign-in -- insert a `sessions` row, set the same cookie -- and from
// then on auth(), sign-out and the API cannot tell the two apart.
//
// NO EMAIL VERIFICATION YET: there is no mail service. So an address typed
// here proves nothing, and two rules follow:
//   - Registering an email that already has an account is refused. A Google
//     account cannot be given a password by someone who merely knows its email.
//   - A Google sign-in to an email that has a password links to that account
//     and REMOVES the password (auth.ts, events.linkAccount). Google has proved
//     who owns the address; whoever set the password may not have been them.

// Auth.js's defaults, so a password session behaves exactly like a Google one:
// 30 days, extended on use (Auth.js's updateAge does that for both).
const SESSION_DAYS = 30;

// Same rule Auth.js applies: the __Secure- cookie over HTTPS, a plain one on
// http://localhost. AUTH_URL is the public address in production (set from
// WEB_ORIGIN in docker-compose) and unset in local dev.
function sessionCookie() {
  const secure = (process.env.AUTH_URL ?? '').startsWith('https://');
  return { name: `${secure ? '__Secure-' : ''}authjs.session-token`, secure };
}

export function normaliseEmail(raw: string): string {
  return raw.trim().toLowerCase();
}

function looksLikeEmail(email: string): boolean {
  // Deliberately loose: the only real check of an address is mail reaching it.
  return email.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

// ---- rate limiting ------------------------------------------------------------
// In memory, so per process: right for the one box this runs on, and it resets
// on deploy, which is fine for a limit measured in minutes.

const WINDOW_MS = 15 * 60 * 1000;
const hits = new Map<string, number[]>();

function recent(key: string): number[] {
  const now = Date.now();
  const kept = (hits.get(key) ?? []).filter((t) => now - t < WINDOW_MS);
  if (kept.length) hits.set(key, kept);
  else hits.delete(key);
  return kept;
}

function record(key: string) {
  hits.set(key, [...recent(key), Date.now()]);
}

// Ten wrong passwords for one email per 15 minutes, and thirty from one
// address across all emails: slow enough to make guessing pointless, loose
// enough that a person who forgot which password they used is not locked out.
const LIMITS = { email: 10, ip: 30, register: 10 } as const;

export function tooManyAttempts(email: string, ip: string): boolean {
  return recent(`fail:email:${email}`).length >= LIMITS.email || recent(`fail:ip:${ip}`).length >= LIMITS.ip;
}

// ---- the three operations -------------------------------------------------------

export type Result = { ok: true; userId: number } | { ok: false; error: string };

export async function register(rawEmail: string, password: string, name: string, ip: string): Promise<Result> {
  const email = normaliseEmail(rawEmail);
  if (!looksLikeEmail(email)) return { ok: false, error: 'Enter a valid email address.' };
  const problem = passwordProblem(password);
  if (problem) return { ok: false, error: problem };
  if (recent(`register:ip:${ip}`).length >= LIMITS.register) {
    return { ok: false, error: 'Too many new accounts from here. Try again in 15 minutes.' };
  }
  record(`register:ip:${ip}`);

  const hash = await hashPassword(password);
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    // ON CONFLICT on the unique email index: two simultaneous sign-ups for one
    // address cannot both win, and an existing Google account is never touched.
    const user = await client.query<{ id: number }>(
      'INSERT INTO users (name, email) VALUES ($1, $2) ON CONFLICT (email) DO NOTHING RETURNING id',
      [name.trim().slice(0, 255) || null, email],
    );
    if (user.rowCount === 0) {
      await client.query('ROLLBACK');
      return {
        ok: false,
        error: 'An account with this email already exists. Sign in instead, or continue with Google.',
      };
    }
    const userId = user.rows[0]!.id;
    await client.query('INSERT INTO passwords (user_id, hash) VALUES ($1, $2)', [userId, hash]);
    await client.query('COMMIT');
    return { ok: true, userId };
  } catch (e) {
    await client.query('ROLLBACK').catch(() => {});
    throw e;
  } finally {
    client.release();
  }
}

export async function checkPassword(rawEmail: string, password: string, ip: string): Promise<Result> {
  const email = normaliseEmail(rawEmail);
  if (tooManyAttempts(email, ip)) {
    return { ok: false, error: 'Too many attempts. Try again in 15 minutes.' };
  }
  const row = await pool.query<{ id: number; hash: string }>(
    'SELECT u.id, p.hash FROM users u JOIN passwords p ON p.user_id = u.id WHERE u.email = $1',
    [email],
  );
  // Always run a full hash check, against a dummy when there is no such
  // account, so the response time does not say whether the email exists.
  const found = row.rows[0];
  const ok = await verifyPassword(password.slice(0, 256), found?.hash ?? (await dummyHash()));
  if (!found || !ok) {
    record(`fail:email:${email}`);
    record(`fail:ip:${ip}`);
    // One message for both cases, for the same reason.
    return { ok: false, error: 'Email or password is incorrect.' };
  }
  return { ok: true, userId: found.id };
}

export async function startSession(userId: number): Promise<void> {
  // 32 random bytes; Auth.js uses a random UUID. Either is unguessable, and the
  // adapter looks sessions up by the string as stored, whatever its shape.
  const token = randomBytes(32).toString('hex');
  const expires = new Date(Date.now() + SESSION_DAYS * 24 * 60 * 60 * 1000);
  await pool.query('INSERT INTO sessions ("userId", expires, "sessionToken") VALUES ($1, $2, $3)', [
    userId,
    expires,
    token,
  ]);
  const { name, secure } = sessionCookie();
  // The same attributes Auth.js gives its own session cookie.
  (await cookies()).set(name, token, { httpOnly: true, sameSite: 'lax', path: '/', secure, expires });
}
