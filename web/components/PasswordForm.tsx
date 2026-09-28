'use client';

import { useActionState } from 'react';
import { useFormStatus } from 'react-dom';
import { registerWithPassword, signInWithPassword, type PasswordState } from '@/app/actions';
import { Spinner, Triangle } from '@/components/icons';

export type Mode = 'signin' | 'register';

const INITIAL: PasswordState = { error: null, email: '' };

const INPUT =
  'mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-card placeholder:text-slate-400 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200';

function Submit({ mode }: { mode: Mode }) {
  // Pending state for the form it sits in; disables double submits.
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending}
      className="flex w-full items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:opacity-60"
    >
      {pending && <Spinner className="h-4 w-4" />}
      {mode === 'signin' ? 'Sign in' : 'Create account'}
    </button>
  );
}

function Fields({ mode, state }: { mode: Mode; state: PasswordState }) {
  return (
    <>
      {mode === 'register' && (
        <label className="block text-sm font-medium text-slate-700">
          Name <span className="font-normal text-slate-400">(optional)</span>
          <input name="name" type="text" autoComplete="name" maxLength={255} className={INPUT} />
        </label>
      )}
      <label className="block text-sm font-medium text-slate-700">
        Email
        <input
          name="email"
          type="email"
          required
          autoComplete="email"
          defaultValue={state.email}
          maxLength={254}
          className={INPUT}
        />
      </label>
      <label className="block text-sm font-medium text-slate-700">
        Password
        <input
          name="password"
          type="password"
          required
          // The browser offers to generate one on the sign-up form and to fill
          // the saved one on the sign-in form; these values are what tell it which.
          autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
          minLength={mode === 'register' ? 8 : undefined}
          maxLength={256}
          className={INPUT}
        />
        {mode === 'register' && (
          <span className="mt-1 block text-xs font-normal text-slate-500">At least 8 characters.</span>
        )}
      </label>
      {state.error && (
        <div
          role="alert"
          className="flex gap-2.5 rounded-lg border border-alert-line bg-alert-soft px-3 py-2.5 text-sm text-slate-800"
        >
          <Triangle className="mt-0.5 h-4 w-4 shrink-0 text-alert" />
          <p>{state.error}</p>
        </div>
      )}
    </>
  );
}

// The mode is owned by AuthCard, so the heading and the Google button follow it.
export default function PasswordForm({ mode, onMode }: { mode: Mode; onMode: (m: Mode) => void }) {
  const [signInState, signInAction] = useActionState(signInWithPassword, INITIAL);
  const [registerState, registerAction] = useActionState(registerWithPassword, INITIAL);

  return (
    <div>
      {mode === 'signin' ? (
        // key: remount per mode so an error from one form never shows on the other.
        <form key="signin" action={signInAction} className="space-y-4">
          <Fields mode="signin" state={signInState} />
          <Submit mode="signin" />
        </form>
      ) : (
        <form key="register" action={registerAction} className="space-y-4">
          <Fields mode="register" state={registerState} />
          <Submit mode="register" />
        </form>
      )}

      <p className="mt-4 text-center text-sm text-slate-600">
        {mode === 'signin' ? 'New here?' : 'Already have an account?'}{' '}
        <button
          type="button"
          onClick={() => onMode(mode === 'signin' ? 'register' : 'signin')}
          className="font-medium text-slate-900 underline decoration-slate-300 underline-offset-2 hover:decoration-slate-900"
        >
          {mode === 'signin' ? 'Create an account' : 'Sign in'}
        </button>
      </p>
    </div>
  );
}
