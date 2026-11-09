'use server';

import { headers } from 'next/headers';
import { redirect } from 'next/navigation';
import { signIn, signOut } from '@/auth';
import { posthogLogsLogger } from '@/instrumentation';
import { checkPassword, register, startSession } from '@/lib/password-auth';

export async function signInWithGoogle() {
  await signIn('google', { redirectTo: '/' });
}

export async function signOutAndReturn() {
  // Deletes the session row too, so the API stops accepting the cookie at once.
  // Works the same for password sessions: they are the same kind of row.
  await signOut({ redirectTo: '/' });
}

// What the email form shows after a failed attempt. `email` is sent back so the
// field is not emptied; the password never is.
export type PasswordState = { error: string | null; email: string };

async function clientIp(): Promise<string> {
  // Caddy sets X-Forwarded-For to the real client address and does not trust
  // one sent by the client, so the first entry is who is actually asking.
  // Absent in local dev, where every request is from this machine anyway.
  return (await headers()).get('x-forwarded-for')?.split(',')[0]?.trim() || 'local';
}

function field(form: FormData, name: string): string {
  const v = form.get(name);
  return typeof v === 'string' ? v : '';
}

function logPasswordAuth(flow: 'sign_in' | 'registration', outcome: 'succeeded' | 'rejected') {
  posthogLogsLogger?.emit({
    body: 'Password authentication completed',
    severityText: outcome === 'succeeded' ? 'INFO' : 'WARN',
    attributes: { event: 'password_authentication_completed', flow, outcome },
  });
}

export async function signInWithPassword(_prev: PasswordState, form: FormData): Promise<PasswordState> {
  const email = field(form, 'email');
  const result = await checkPassword(email, field(form, 'password'), await clientIp());
  if (!result.ok) {
    logPasswordAuth('sign_in', 'rejected');
    return { error: result.error, email };
  }
  await startSession(result.userId);
  logPasswordAuth('sign_in', 'succeeded');
  // Outside any try/catch: redirect() works by throwing.
  redirect('/');
}

export async function registerWithPassword(_prev: PasswordState, form: FormData): Promise<PasswordState> {
  const email = field(form, 'email');
  const result = await register(email, field(form, 'password'), field(form, 'name'), await clientIp());
  if (!result.ok) {
    logPasswordAuth('registration', 'rejected');
    return { error: result.error, email };
  }
  await startSession(result.userId);
  logPasswordAuth('registration', 'succeeded');
  redirect('/');
}
