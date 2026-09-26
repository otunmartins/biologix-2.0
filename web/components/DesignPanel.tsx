'use client';

import type { DesignGoal } from '@/lib/api';
import { ago } from '@/lib/when';
import { GoalChips } from './DesignResults';
import { Refresh, Spinner } from './icons';

const EXAMPLES = [
  'A 150 mg/mL IgG1 monoclonal antibody, subcutaneous, that has to survive 25 °C for 6 months. Met and Lys are surface-exposed.',
  'Lyophilised enzyme for tropical distribution — needs to hold at 30 °C for a year without a cold chain.',
  'Freeze-dried vaccine antigen, 40 °C excursion tolerance, no polysorbate.',
];

// The campaign on screen, when there is one. While it is open the panel shows it
// instead of the prompt editor, so there is one obvious way to start over.
export interface CurrentExperiment {
  prompt: string;
  goal: DesignGoal;
  startedAt: string;
  endedAt: string | null;
  iteration: number;
  nCandidates: number;
}

interface Props {
  prompt: string;
  onPrompt: (v: string) => void;
  loading: boolean;
  elapsed: number;
  onRun: () => void;
  current: CurrentExperiment | null;
  onNew: () => void;
}

function CurrentCard({ current, loading, onNew }: { current: CurrentExperiment; loading: boolean; onNew: () => void }) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto px-6 py-6">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-[15px] font-semibold text-slate-900">Current experiment</h2>
          {current.endedAt ? (
            <span className="rounded-full border border-slate-200 bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-500">
              ended · next iteration reopens it
            </span>
          ) : (
            <span className="rounded-full border border-supported-line bg-supported-soft px-2 py-0.5 text-[11px] font-semibold text-supported">
              active
            </span>
          )}
        </div>

        {current.prompt && (
          <blockquote className="border-l-2 border-slate-300 pl-3 text-sm leading-relaxed text-slate-700">
            {current.prompt}
          </blockquote>
        )}
        <GoalChips goal={current.goal} />

        <dl className="grid grid-cols-3 gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-card">
          <div>
            <dt className="text-xs text-slate-500">Iterations</dt>
            <dd className="text-lg font-semibold tabular-nums text-slate-900">{current.iteration}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Candidates</dt>
            <dd className="text-lg font-semibold tabular-nums text-slate-900">{current.nCandidates}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Started</dt>
            <dd className="text-sm font-medium text-slate-900">{ago(current.startedAt)}</dd>
          </div>
        </dl>

        <p className="text-xs leading-relaxed text-slate-500">
          Each iteration adds a batch the loop learns from. Start a new experiment whenever you want a clean slate.
          This one stays in your History, where you can reopen and continue it.
        </p>
      </div>

      <div className="border-t border-slate-200 bg-white px-6 py-4">
        <button type="button" className="btn-ghost w-full py-2.5" onClick={onNew} disabled={loading}>
          <Refresh className="h-4 w-4" /> New experiment
        </button>
      </div>
    </div>
  );
}

export default function DesignPanel({ prompt, onPrompt, loading, elapsed, onRun, current, onNew }: Props) {
  if (current) return <CurrentCard current={current} loading={loading} onNew={onNew} />;
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto px-6 py-6">
        <div>
          <h2 className="text-[15px] font-semibold text-slate-900">The formulation problem</h2>
          <p className="hint mt-1 leading-relaxed">
            Describe the biologic and the temperature it has to survive, in your own words.
          </p>
        </div>

        <label className="block">
          <textarea
            rows={7}
            className="input resize-y leading-relaxed"
            value={prompt}
            placeholder="e.g. a 150 mg/mL IgG1 mAb, subcutaneous, stable at 25 °C for 6 months…"
            onChange={(e) => onPrompt(e.target.value)}
          />
        </label>

        <div>
          <span className="label">Start from an example</span>
          <div className="space-y-1.5">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                onClick={() => onPrompt(ex)}
                className="block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left text-[13px] leading-relaxed text-slate-600 transition hover:border-slate-300 hover:text-slate-900"
              >
                {ex}
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-100/70 px-4 py-3 text-xs leading-relaxed text-slate-600">
          <p>
            <b className="font-semibold text-slate-800">Candidates are enumerated, not invented.</b>{' '}
            Backbones and pendant groups come from a curated table of motifs whose stabilisation
            mechanisms are described in the literature. Each pairing is built as a real structure and
            put through the same structural alerts and rule table as any other excipient here.
          </p>
          <p>
            <b className="font-semibold text-slate-800">No simulation is run.</b> There is no
            molecular dynamics, no free-energy calculation and no predicted melting temperature. The
            ranking orders candidates for laboratory work — it does not predict that any of them will
            stabilise your protein.
          </p>
        </div>
      </div>

      <div className="border-t border-slate-200 bg-white px-6 py-4">
        <button
          type="button"
          className="btn-primary w-full py-2.5"
          onClick={onRun}
          disabled={loading || !prompt.trim()}
        >
          {loading && <Spinner className="h-4 w-4" />}
          {loading ? `Designing… ${elapsed}s` : 'Propose candidates'}
        </button>
        <p className="mt-2 text-center text-xs text-slate-500">
          <span className="kbd">Ctrl</span> + <span className="kbd">Enter</span> from anywhere
        </p>
      </div>
    </div>
  );
}
