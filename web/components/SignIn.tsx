import { signInWithGoogle } from '@/app/actions';
import { Molecule, Triangle } from '@/components/icons';

// Auth.js reports why a sign-in failed as ?error=<code>. Only the ones a user can
// act on get their own wording; anything else is a configuration problem.
const ERRORS: Record<string, string> = {
  OAuthAccountNotLinked: 'That email is already signed in with a different method.',
  AccessDenied: 'Sign-in was cancelled or refused.',
  Verification: 'That sign-in link has expired. Try again.',
};

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

export default function SignIn({ error }: { error?: string }) {
  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 px-6">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-slate-900 text-white">
            <Molecule className="h-[18px] w-[18px]" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-slate-900">Excipient Screen</span>
        </div>

        <h1 className="mt-8 text-2xl font-semibold tracking-tight text-slate-900">Sign in</h1>
        <p className="mt-2 text-[15px] leading-relaxed text-slate-600">
          Screen excipients and run polymer design campaigns. Your campaigns are visible only to you.
        </p>

        {error && (
          <div className="mt-6 flex gap-3 rounded-xl border border-alert-line bg-alert-soft px-4 py-3 text-sm leading-relaxed text-slate-800">
            <Triangle className="mt-0.5 h-5 w-5 shrink-0 text-alert" />
            <p>{ERRORS[error] ?? 'Sign-in is not working right now. Try again in a minute.'}</p>
          </div>
        )}

        <form action={signInWithGoogle} className="mt-6">
          <button
            type="submit"
            className="flex w-full items-center justify-center gap-3 rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-800 shadow-card transition hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          >
            <GoogleMark />
            Continue with Google
          </button>
        </form>
      </div>
    </main>
  );
}
