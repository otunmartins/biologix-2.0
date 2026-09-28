import AuthCard from '@/components/AuthCard';
import { AlgonixMark, AlgonixWordmark, BRAND, BiologixMark } from '@/components/brand';
import { Triangle } from '@/components/icons';

// Auth.js reports why a sign-in failed as ?error=<code>. Only the ones a user can
// act on get their own wording; anything else is a configuration problem.
const ERRORS: Record<string, string> = {
  // Rare now that Google links to a matching password account (auth.ts).
  OAuthAccountNotLinked: 'That email is already signed in with a different method.',
  AccessDenied: 'Sign-in was cancelled or refused.',
  Verification: 'That sign-in link has expired. Try again.',
};

// What the product does, in the order a formulation question travels through it.
const CAPABILITIES = [
  ['01', 'Excipient triage', 'Evidence-graded verdicts per endpoint, from FDA precedent, structural alerts and your protein’s exposed residues.'],
  ['02', 'Polymer design', 'An active-learning loop proposes copolymers, screens every one, and learns which compositions to try next.'],
  ['03', 'Molecular dynamics', 'OpenMM runs the polymer around your protein and measures where it sits: preferential interaction, Γ23.'],
] as const;

// A copolymer drawn as its repeat unit: a zig-zag backbone whose carbons each
// carry the brand's ring-bond-dot pendant, alternating up and down with the
// zig-zag, in bracket notation with the subscript n. Decorative.
function PolymerChain({ className = '' }: { className?: string }) {
  const TOP = 104;
  const BOTTOM = 136;
  const atoms = Array.from({ length: 8 }, (_, i) => [52 + i * 46, i % 2 ? BOTTOM : TOP] as const);
  const backbone = atoms.map(([x, y], i) => `${i ? 'L' : 'M'}${x},${y}`).join('');
  return (
    <svg viewBox="0 0 440 240" className={className} aria-hidden="true">
      <g fill="none" stroke={BRAND.cream} strokeLinecap="round" strokeLinejoin="round">
        {/* [ ... ]n around the repeat unit */}
        <path d="M28 18h-12v204h12" strokeWidth="3" opacity="0.5" />
        <path d="M400 18h12v204h-12" strokeWidth="3" opacity="0.5" />
        <path d={backbone} strokeWidth="2.4" opacity="0.85" />
        {atoms.map(([x, y], i) => {
          const dir = y === TOP ? -1 : 1; // up from the top carbons, down from the bottom ones
          return (
            <g key={i} opacity="0.75">
              <path d={`M${x},${y}V${y + dir * 22}`} strokeWidth="2" />
              <circle cx={x} cy={y + dir * 30} r="8" strokeWidth="2.4" />
              <path d={`M${x},${y + dir * 38}V${y + dir * 50}`} strokeWidth="2" />
            </g>
          );
        })}
      </g>
      {atoms.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y + (y === TOP ? -1 : 1) * 60} r="9" fill={BRAND.cyan} opacity={0.45 + i * 0.07} />
      ))}
      <text x="418" y="236" fill={BRAND.cyan} fontSize="22" fontFamily="var(--font-mono), monospace">
        n
      </text>
    </svg>
  );
}

export default function SignIn({ error }: { error?: string }) {
  // Stacked on a phone: the brand panel as tall as its content, the form taking the rest.
  return (
    <main className="grid min-h-screen grid-rows-[auto_1fr] lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:grid-rows-1">
      {/* The scientific panel: brand, what it does, whose it is. */}
      <section
        className="relative isolate flex flex-col overflow-hidden px-6 py-8 sm:px-10 lg:px-14 lg:py-12"
        style={{ background: BRAND.ink, color: BRAND.cream }}
      >
        {/* Graph paper, fading out toward the edges. */}
        <div
          className="pointer-events-none absolute inset-0 -z-10 opacity-[0.07]"
          style={{
            backgroundImage: `linear-gradient(${BRAND.cream} 1px, transparent 1px), linear-gradient(90deg, ${BRAND.cream} 1px, transparent 1px)`,
            backgroundSize: '32px 32px',
            maskImage: 'radial-gradient(ellipse at 30% 40%, black 30%, transparent 80%)',
            WebkitMaskImage: 'radial-gradient(ellipse at 30% 40%, black 30%, transparent 80%)',
          }}
          aria-hidden="true"
        />
        <div
          className="pointer-events-none absolute -right-32 -top-32 -z-10 h-96 w-96 rounded-full opacity-20 blur-3xl"
          style={{ background: BRAND.cyan }}
          aria-hidden="true"
        />

        <div className="flex items-center gap-3">
          <AlgonixMark className="h-7 w-auto" />
          <AlgonixWordmark className="text-[13px]" />
        </div>

        <div className="my-10 max-w-xl lg:my-auto lg:py-6">
          <p className="font-mono text-xs uppercase tracking-[0.24em]" style={{ color: BRAND.cyan }}>
            Formulation science, computed
          </p>
          <h1 className="mt-3 text-5xl font-semibold tracking-tight sm:text-6xl">
            Biologix <span style={{ color: BRAND.cyan }}>2.0</span>
          </h1>
          <p className="mt-4 max-w-md text-[17px] leading-relaxed text-[#a8a597]">
            Find the excipient your biologic needs, design the polymer that could stabilise it, and see where it sits
            around the protein.
          </p>

          <PolymerChain className="mt-6 hidden w-full max-w-[360px] sm:block" />

          <ol className="mt-6 hidden space-y-4 lg:block">
            {CAPABILITIES.map(([n, title, body]) => (
              <li key={n} className="flex gap-4">
                <span className="mt-0.5 font-mono text-xs" style={{ color: BRAND.cyan }}>
                  {n}
                </span>
                <span>
                  <span className="block text-[15px] font-semibold">{title}</span>
                  <span className="mt-1 block text-sm leading-relaxed text-[#a8a597]">{body}</span>
                </span>
              </li>
            ))}
          </ol>
        </div>

        <p className="hidden font-mono text-[11px] uppercase tracking-[0.2em] text-[#a8a597] lg:block">
          An Algonix AI product · Triage, not a safety assessment
        </p>
      </section>

      {/* Sign in. */}
      <section className="flex items-center justify-center bg-[#f7f6f2] px-6 py-12 sm:px-10">
        <div className="w-full max-w-sm">
          <div className="flex items-center gap-2.5">
            <BiologixMark className="h-9 w-9" />
            <div className="leading-tight">
              <div className="text-[15px] font-semibold tracking-tight text-slate-900">
                Biologix <span style={{ color: BRAND.cyanInk }}>2.0</span>
              </div>
              <div className="text-xs text-slate-500">by Algonix AI</div>
            </div>
          </div>

          {error && (
            <div className="mt-6 flex gap-3 rounded-xl border border-alert-line bg-alert-soft px-4 py-3 text-sm leading-relaxed text-slate-800">
              <Triangle className="mt-0.5 h-5 w-5 shrink-0 text-alert" />
              <p>{ERRORS[error] ?? 'Sign-in is not working right now. Try again in a minute.'}</p>
            </div>
          )}

          <AuthCard />

          <p className="mt-10 text-center text-xs text-slate-400">© 2026 Algonix AI</p>
        </div>
      </section>
    </main>
  );
}
