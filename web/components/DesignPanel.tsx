'use client';

import { Spinner } from './icons';

const EXAMPLES = [
  'A 150 mg/mL IgG1 monoclonal antibody, subcutaneous, that has to survive 25 °C for 6 months. Met and Lys are surface-exposed.',
  'Lyophilised enzyme for tropical distribution — needs to hold at 30 °C for a year without a cold chain.',
  'Freeze-dried vaccine antigen, 40 °C excursion tolerance, no polysorbate.',
];

interface Props {
  prompt: string;
  onPrompt: (v: string) => void;
  loading: boolean;
  elapsed: number;
  onRun: () => void;
}

export default function DesignPanel({ prompt, onPrompt, loading, elapsed, onRun }: Props) {
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
