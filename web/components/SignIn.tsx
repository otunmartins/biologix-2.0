import AuthCard from '@/components/AuthCard';
import { Molecule, Triangle } from '@/components/icons';

// Auth.js reports why a sign-in failed as ?error=<code>. Only the ones a user can
// act on get their own wording; anything else is a configuration problem.
const ERRORS: Record<string, string> = {
  // Rare now that Google links to a matching password account (auth.ts).
  OAuthAccountNotLinked: 'That email is already signed in with a different method.',
  AccessDenied: 'Sign-in was cancelled or refused.',
  Verification: 'That sign-in link has expired. Try again.',
};

export default function SignIn({ error }: { error?: string }) {
  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 px-6 py-12">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-slate-900 text-white">
            <Molecule className="h-[18px] w-[18px]" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-slate-900">Excipient Screen</span>
        </div>

        {error && (
          <div className="mt-6 flex gap-3 rounded-xl border border-alert-line bg-alert-soft px-4 py-3 text-sm leading-relaxed text-slate-800">
            <Triangle className="mt-0.5 h-5 w-5 shrink-0 text-alert" />
            <p>{ERRORS[error] ?? 'Sign-in is not working right now. Try again in a minute.'}</p>
          </div>
        )}

        <AuthCard />
      </div>
    </main>
  );
}
