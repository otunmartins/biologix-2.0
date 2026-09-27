'use client';

import { useState } from 'react';
import { signInWithGoogle } from '@/app/actions';
import PasswordForm, { type Mode } from '@/components/PasswordForm';

function GoogleMark() {
  return (
    <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" aria-hidden="true">
      <path fill="#4285F4" d="M23.5 12.27c0-.85-.08-1.67-.22-2.45H12v4.64h6.45a5.52 5.52 0 0 1-2.4 3.62v3h3.88c2.27-2.09 3.57-5.17 3.57-8.81z" />
      <path fill="#34A853" d="M12 24c3.24 0 5.96-1.07 7.94-2.91l-3.88-3.01c-1.07.72-2.45 1.15-4.06 1.15-3.12 0-5.77-2.11-6.71-4.95H1.28v3.1A12 12 0 0 0 12 24z" />
      <path fill="#FBBC05" d="M5.29 14.28a7.2 7.2 0 0 1 0-4.56v-3.1H1.28a12 12 0 0 0 0 10.76l4.01-3.1z" />
      <path fill="#EA4335" d="M12 4.77c1.76 0 3.34.61 4.59 1.8l3.44-3.44A11.94 11.94 0 0 0 12 0 12 12 0 0 0 1.28 6.62l4.01 3.1C6.23 6.88 8.88 4.77 12 4.77z" />
    </svg>
  );
}

// Sign in and sign up are one page with one switch: the heading, the Google
// button and the email form all follow it. Google itself makes no distinction
// -- the first sign-in creates the account -- so both labels run one action.
export default function AuthCard() {
  const [mode, setMode] = useState<Mode>('signin');
  const signingUp = mode === 'register';

  return (
    <>
      <h1 className="mt-8 text-2xl font-semibold tracking-tight text-slate-900">
        {signingUp ? 'Create your account' : 'Sign in'}
      </h1>
      <p className="mt-2 text-[15px] leading-relaxed text-slate-600">
        {signingUp
          ? 'Every screen and design campaign you run is kept in your history, visible only to you.'
          : 'Screen excipients and run polymer design campaigns. Your work is visible only to you.'}
      </p>

      <form action={signInWithGoogle} className="mt-6">
        <button
          type="submit"
          className="flex w-full items-center justify-center gap-3 rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-800 shadow-card transition hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
        >
          <GoogleMark />
          {signingUp ? 'Sign up with Google' : 'Sign in with Google'}
        </button>
      </form>

      <div className="my-6 flex items-center gap-3 text-xs font-medium uppercase tracking-[0.12em] text-slate-400">
        <span className="h-px flex-1 bg-slate-200" />
        {signingUp ? 'or sign up with email' : 'or'}
        <span className="h-px flex-1 bg-slate-200" />
      </div>

      <PasswordForm mode={mode} onMode={setMode} />
    </>
  );
}
