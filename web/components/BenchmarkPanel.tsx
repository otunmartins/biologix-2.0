'use client';

import type { IterationMetrics, QueueSummary, Recommendation } from '@/lib/api';
import { Flask, Info, Spinner } from './icons';

interface Props {
  iteration: number;
  history: IterationMetrics[];
  recommendation: Recommendation | null;
  queue: QueueSummary | null;
  loading: boolean;
  onIterate: () => void;
}

// A single-series trend over iterations: 2px line, endpoint dot, recessive
// baseline. One metric per sparkline, so there is no legend and no palette to
// validate — the colour just carries "which metric", text stays in ink tokens.
function Sparkline({
  values,
  color = 'text-supported',
  width = 104,
  height = 30,
}: {
  values: (number | null)[];
  color?: string;
  width?: number;
  height?: number;
}) {
  const pts = values
    .map((v, i) => ({ v, i }))
    .filter((p): p is { v: number; i: number } => p.v !== null && Number.isFinite(p.v));
  if (pts.length < 2) return <div style={{ width, height }} aria-hidden />;

  const n = values.length;
  const vs = pts.map((p) => p.v);
  const min = Math.min(...vs);
  const span = Math.max(...vs) - min || 1;
  const pad = 4;
  const x = (i: number) => pad + (i / (n - 1)) * (width - 2 * pad);
  const y = (v: number) => pad + (1 - (v - min) / span) * (height - 2 * pad);
  const d = pts.map((p, k) => `${k ? 'L' : 'M'}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
  const last = pts[pts.length - 1];

  return (
    <svg width={width} height={height} className={color} aria-hidden>
      <line
        x1={pad}
        y1={height - pad}
        x2={width - pad}
        y2={height - pad}
        className="stroke-slate-200"
        strokeWidth={1}
      />
      <path
        d={d}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx={x(last.i)} cy={y(last.v)} r={2.5} fill="currentColor" />
    </svg>
  );
}

function fmt(v: number | null, digits = 2): string {
  return v === null || v === undefined ? '—' : v.toFixed(digits);
}

function MetricRow({
  label,
  hint,
  value,
  values,
  color,
}: {
  label: string;
  hint?: string;
  value: string;
  values: (number | null)[];
  color?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-slate-700">{label}</div>
        {hint && <div className="text-[11px] leading-tight text-slate-400">{hint}</div>}
      </div>
      <div className="flex items-center gap-2.5">
        <Sparkline values={values} color={color} />
        <span className="w-10 text-right text-sm font-semibold tabular-nums text-slate-900">
          {value}
        </span>
      </div>
    </div>
  );
}

const PHASE_STYLE: Record<Recommendation['phase'], string> = {
  seed: 'border-slate-200 bg-slate-100 text-slate-600',
  explore: 'border-supported-line bg-supported-soft text-supported',
  exploit: 'border-precedented-line bg-precedented-soft text-precedented',
  converged: 'border-gap-line bg-gap-soft text-gap',
};

function RecommendationCard({ rec }: { rec: Recommendation }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-card">
      <div className="flex items-center gap-2">
        <span className="eyebrow">Controller</span>
        <span
          className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold capitalize ${PHASE_STYLE[rec.phase]}`}
        >
          {rec.phase}
        </span>
      </div>
      <p className="mt-2 text-[13px] leading-relaxed text-slate-700">{rec.reason}</p>
    </div>
  );
}

export default function BenchmarkPanel({
  iteration,
  history,
  recommendation,
  queue,
  loading,
  onIterate,
}: Props) {
  const best = history.map((m) => m.best_score_so_far);
  const mean = history.map((m) => m.batch_mean_score);
  const cv = history.map((m) => m.surrogate_cv_mae);
  const diversity = history.map((m) => m.batch_diversity);
  const alertFree = history.map((m) => (m.frac_alert_free === null ? null : m.frac_alert_free * 100));
  const latest = history[history.length - 1];

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
        <header>
          <div className="eyebrow">Benchmarking</div>
          <h2 className="mt-1 text-[15px] font-semibold text-slate-900">
            Iteration {iteration} · {latest?.n_total ?? 0} screened
          </h2>
          <p className="hint mt-1 leading-relaxed">
            The loop optimises the <b className="font-semibold text-slate-600">triage proxy</b> — a
            surrogate models that score to steer the search, not stabilisation.
          </p>
        </header>

        {recommendation && <RecommendationCard rec={recommendation} />}

        <button
          type="button"
          className="btn-primary w-full py-2.5"
          onClick={onIterate}
          disabled={loading}
        >
          {loading && <Spinner className="h-4 w-4" />}
          {loading ? 'Generating…' : 'Generate next iteration'}
        </button>

        <div className="rounded-xl border border-slate-200 bg-white px-4 py-2 shadow-card">
          <MetricRow
            label="Best triage score"
            hint="running maximum"
            value={fmt(latest?.best_score_so_far ?? null)}
            values={best}
            color="text-precedented"
          />
          <div className="border-t border-slate-100" />
          <MetricRow
            label="Batch mean score"
            value={fmt(latest?.batch_mean_score ?? null)}
            values={mean}
            color="text-supported"
          />
          <div className="border-t border-slate-100" />
          <MetricRow
            label="Surrogate CV-MAE"
            hint="lower = predicts the proxy better"
            value={fmt(latest?.surrogate_cv_mae ?? null)}
            values={cv}
            color="text-gap"
          />
          <div className="border-t border-slate-100" />
          <MetricRow
            label="Batch diversity"
            value={fmt(latest?.batch_diversity ?? null)}
            values={diversity}
            color="text-supported"
          />
          <div className="border-t border-slate-100" />
          <MetricRow
            label="Alert-free"
            value={latest?.frac_alert_free === null ? '—' : `${Math.round((latest?.frac_alert_free ?? 0) * 100)}%`}
            values={alertFree}
            color="text-precedented"
          />
        </div>

        {queue && (
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-card">
            <div className="flex items-center gap-1.5">
              <Flask className="h-4 w-4 text-slate-500" />
              <span className="eyebrow">Simulation queue</span>
            </div>
            <p className="mt-2 text-sm text-slate-700">
              <b className="text-lg font-semibold text-slate-900">{queue.n_queued}</b> queued for
              OpenMM
              {(queue.n_simulating ?? 0) > 0 && <>, {queue.n_simulating} running</>}
              {queue.n_simulated > 0 && <>, {queue.n_simulated} done</>}
              {' '}
              <span className="text-slate-500">— runs whenever a simulation worker is on</span>
            </p>
            {queue.top.length > 0 && (
              <ul className="mt-2 space-y-1">
                {queue.top.map((c) => (
                  <li key={c.id} className="flex justify-between gap-2 text-xs text-slate-500">
                    <span className="truncate">{c.name}</span>
                    <span className="tabular-nums text-slate-400">{c.score.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      <div className="border-t border-slate-200 bg-white px-5 py-3">
        <div className="flex gap-2 text-[11px] leading-relaxed text-slate-500">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <p>
            A queue position is a triage decision, not evidence. Every candidate is grade D until a
            measurement or an OpenMM run exists.
          </p>
        </div>
      </div>
    </div>
  );
}
