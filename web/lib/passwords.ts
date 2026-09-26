// Password hashing with scrypt, from Node's own crypto: no native module to
// build in the Docker image, and nothing to keep patched.
//
// The stored string carries its own parameters -- scrypt$N$r$p$salt$key -- so
// the cost can be raised later without invalidating existing hashes: verify()
// reads the parameters from the hash, not from the constants below.
//
// No imports from the app on purpose, so it can be tested on its own:
//   node --experimental-strip-types lib/passwords.test.ts
import { randomBytes, scrypt as scryptCb, timingSafeEqual, type ScryptOptions } from 'node:crypto';

// N = 2^15, r = 8: 32 MiB and roughly 50-100 ms per hash on the server. Enough
// to make guessing expensive without letting a burst of sign-ins exhaust memory.
const N = 2 ** 15;
const R = 8;
const P = 1;
const KEY_BYTES = 32;

export const MIN_LENGTH = 8;
// scrypt takes any length, but an unbounded one lets a single request burn CPU.
export const MAX_LENGTH = 256;

function scrypt(password: string, salt: Buffer, n: number, r: number, p: number): Promise<Buffer> {
  // maxmem must exceed 128 * N * r bytes, or Node refuses the parameters.
  const options: ScryptOptions = { N: n, r, p, maxmem: 256 * n * r };
  return new Promise((resolve, reject) =>
    scryptCb(password.normalize('NFKC'), salt, KEY_BYTES, options, (err, key) =>
      err ? reject(err) : resolve(key),
    ),
  );
}

export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16);
  const key = await scrypt(password, salt, N, R, P);
  return ['scrypt', N, R, P, salt.toString('base64'), key.toString('base64')].join('$');
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const [scheme, n, r, p, salt, key] = stored.split('$');
  if (scheme !== 'scrypt' || !salt || !key) return false;
  const expected = Buffer.from(key, 'base64');
  const actual = await scrypt(password, Buffer.from(salt, 'base64'), Number(n), Number(r), Number(p));
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

// A real hash of a throwaway password, computed once. Checked against when the
// email is unknown, so "no such account" takes as long as "wrong password" and
// response times do not reveal which emails have accounts.
let dummy: Promise<string> | null = null;
export function dummyHash(): Promise<string> {
  dummy ??= hashPassword(randomBytes(16).toString('hex'));
  return dummy;
}

// Length only. Composition rules (a digit, a symbol...) push people toward
// predictable passwords; length is what makes one hard to guess (NIST 800-63B).
export function passwordProblem(password: string): string | null {
  if (password.length < MIN_LENGTH) return `Use at least ${MIN_LENGTH} characters.`;
  if (password.length > MAX_LENGTH) return `Use at most ${MAX_LENGTH} characters.`;
  return null;
}
