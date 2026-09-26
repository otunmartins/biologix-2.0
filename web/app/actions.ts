'use server';

import { signIn, signOut } from '@/auth';

export async function signInWithGoogle() {
  await signIn('google', { redirectTo: '/' });
}

export async function signOutAndReturn() {
  // Deletes the session row too, so the API stops accepting the cookie at once.
  await signOut({ redirectTo: '/' });
}
