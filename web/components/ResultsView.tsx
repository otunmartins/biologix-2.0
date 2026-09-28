'use client';

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { ChartCard, Columns, ForestPlot, ShareBar, type ForestRow } from '@/components/admin/charts';
import SimulationsView, { SimulationDetail } from '@/components/SimulationsView';
import { Spinner, Triangle } from '@/components/icons';
import { getMyResults, type CampaignResult, type GammaCall, type MyResults } from '@/lib/api';
import { ago, fullStamp } from '@/lib/when';

// Everything the signed-in user has run, with what it amounts to: their
// excipient screens, the candidates their design campaigns generated, and the
// simulations those led to. The numbers come from api/results.py, which also
// says what each score means and, as importantly, what it does not.

const VERDICT = [
  ['Precedented', 'var(--status-good)', '✓'],
  ['Supported without precedent', 'var(--series-screens)', '◆'],
  ['Data gap: test', 'var(--status-warning)', '?'],
  ['Alert: avoid', 'var(--status-critical)', '!'],
] as const;
const VERDICT_OF = Object.fromEntries(VERDICT.map(([v, c, i]) => [v, { color: c, icon: i }]));

const GRADE_COLORS = ['var(--ramp-1)', 'var(--ramp-2)', 'var(--ramp-3)', 'var(--ramp-4)', 'var(--ramp-5)'];

const STATUS = [
  ['benchmarked', 'Screened only', 'var(--div-mid)'],
  ['queued', 'Sent for simulation', 'var(--cat-yellow)'],
  ['simulating', 'Simulating', 'var(--series-screens)'],
  ['simulated', 'Simulated', 'var(--series-sims)'],
  ['failed', 'Simulation failed', 'var(--cat-magenta)'],
] as const;

const CALL: Record<GammaCall, { label: string; color: string }> = {
  excluded: { label: 'Excluded from the surface', color: 'var(--div-neg-2)' },
  accumulated: { label: 'Accumulates at the surface', color: 'var(--div-pos-2)' },
  unclear: { label: 'No clear preference', color: 'var(--ink-muted)' },
};

const pct = (x: number | null) => (x === null ? '—' : `${Math.round(x * 100)}%`);
const signed = (v: number) => (v > 0 ? `+${v}` : v < 0 ? `−${Math.abs(v)}` : '0');

function Tile({ label, value, sub, color }: { label: string; value: string; sub: string; color: string }) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-slate-200 bg-[color:var(--surface-1)] p-4 shadow-card">
      <div className="absolute inset-x-0 top-0 h-1" style={{ background: color }} aria-hidden="true" />
      <div className="text-[13px] font-medium text-[color:var(--ink-2)]">{label}</div>
      <div className="mt-1 text-3xl font-semibold tracking-tight tabular-nums text-[color:var(--ink-1)]">{value}</div>
      <div className="mt-0.5 text-xs text-[color:var(--ink-muted)]">{sub}</div>
    </div>
  );
}

function MiniStat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-[color:var(--surface-1)] px-3 py-2.5">
      <div className="text-xl font-semibold tracking-tight tabular-nums text-[color:var(--ink-1)]">{value}</div>
      <div className="text-xs text-[color:var(--ink-2)]">{label}</div>
      {hint && <div className="text-[11px] text-[color:var(--ink-muted)]">{hint}</div>}
    </div>
  );
}

function Caveat({ children }: { children: ReactNode }) {
  return <p className="text-xs leading-relaxed text-[color:var(--ink-muted)]">{children}</p>;
}

function Empty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white px-5 py-8 text-center">
      <p className="font-medium text-slate-800">{title}</p>
      <p className="mt-1 text-sm text-slate-500">{children}</p>
    </div>
  );
}

// A campaign's best score by iteration. Scores can be negative, so it spans
// min..max rather than assuming a zero baseline.
function Curve({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const W = 96;
  const H = 28;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const y = (v: number) => (hi === lo ? H / 2 : H - 3 - ((v - lo) / (hi - lo)) * (H - 6));
  const x = (i: number) => 2 + (i / (values.length - 1)) * (W - 4);
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  return (
    <svg width={W} height={H} role="img" aria-label={`Best score by iteration: ${values.join(', ')}`}>
      <title>{`Best score by iteration: ${values.join(' → ')}`}</title>
      <path d={d} fill="none" stroke="var(--series-polymers)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(values.length - 1)} cy={y(values.at(-1)!)} r={3} fill="var(--series-polymers)" stroke="var(--surface-1)" strokeWidth={2} />
    </svg>
  );
}

// ---------------------------------------------------------------------------

function ScreensSection({ data, onOpenScreen }: { data: MyResults['screens']; onOpenScreen: (id: string) => void }) {
  if (!data.total)
    return (
      <Empty title="No screens yet">
        On the Screen tab, screen an excipient against your protein. Each one lands here with its evidence coverage.
      </Empty>
    );
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Worst verdict per screen"
          subtitle="The most serious verdict any endpoint reached."
          table={{ head: ['Verdict', 'Screens'], rows: VERDICT.map(([v]) => [v, data.worst_verdict[v] ?? 0]) }}
        >
          <ShareBar parts={VERDICT.map(([v, color, icon]) => ({ label: v, value: data.worst_verdict[v] ?? 0, color, icon }))} />
        </ChartCard>
        <ChartCard
          title="Evidence grade of every endpoint"
          subtitle="A is an approved precedent at your route; E is nothing to go on."
          table={{ head: ['Grade', 'Endpoints'], rows: Object.entries(data.grades) }}
        >
          <Columns bins={Object.entries(data.grades).map(([g, n]) => ({ label: g, value: n, tip: `Grade ${g}` }))} colors={GRADE_COLORS} height={130} />
        </ChartCard>
      </div>

      <ChartCard
        title="Excipients, most evidence first"
        subtitle="Each excipient's latest screen. Coverage is the share of endpoints that are Precedented or Supported."
      >
        <ul className="divide-y divide-slate-100">
          {data.excipients.map((x) => {
            const v = x.worst_verdict ? VERDICT_OF[x.worst_verdict] : null;
            return (
              <li key={x.latest_id}>
                <button
                  type="button"
                  onClick={() => onOpenScreen(x.latest_id)}
                  className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 rounded-md px-1 py-2.5 text-left transition hover:bg-slate-50 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto]"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-[color:var(--ink-1)]">{x.excipient}</span>
                    <span className="block truncate text-xs text-[color:var(--ink-muted)]">
                      {[x.protein, x.route].filter(Boolean).join(' · ') || 'no protein or route given'}
                      {x.n_screens > 1 && ` · screened ${x.n_screens}×`} · {ago(x.latest_at)}
                    </span>
                  </span>
                  <span className="order-last col-span-2 flex items-center gap-2 sm:order-none sm:col-span-1">
                    <span className="h-2 flex-1 rounded-full bg-slate-100">
                      <span
                        className="block h-2 rounded-full"
                        style={{ width: `${(x.coverage ?? 0) * 100}%`, background: 'var(--series-screens)', minWidth: x.coverage ? 4 : 0 }}
                      />
                    </span>
                    <span className="w-9 text-right text-xs font-semibold tabular-nums text-[color:var(--ink-1)]">{pct(x.coverage)}</span>
                  </span>
                  <span className="flex items-center justify-end gap-2 text-xs text-[color:var(--ink-2)]">
                    {x.n_alerts > 0 && <span className="tabular-nums">{x.n_alerts} alert{x.n_alerts > 1 ? 's' : ''}</span>}
                    {x.n_gaps > 0 && <span className="tabular-nums">{x.n_gaps} gap{x.n_gaps > 1 ? 's' : ''}</span>}
                    {v && (
                      <span
                        className="grid h-5 w-5 place-items-center rounded-full text-[11px] font-bold text-white"
                        style={{ background: v.color }}
                        title={x.worst_verdict ?? ''}
                        aria-label={x.worst_verdict ?? ''}
                      >
                        {v.icon}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </ChartCard>
      <Caveat>
        Coverage measures how much is already known about an excipient at your route, not whether it is safe or compatible
        with your protein. A screen at 100% has still not tested that.
      </Caveat>
    </div>
  );
}

function CampaignCard({ c, onOpen }: { c: CampaignResult; onOpen: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="w-full rounded-xl border border-slate-200 bg-white p-4 text-left shadow-card transition hover:border-slate-300"
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate font-semibold text-slate-900">
              {c.protein || 'Unnamed biologic'}
              <span className="font-normal text-slate-500">
                {' '}
                · {c.format || 'format not given'}
                {c.target_temp_c !== null && ` · ${c.target_temp_c} °C`}
              </span>
            </div>
            {c.prompt && <div className="mt-0.5 truncate text-sm text-slate-500">&ldquo;{c.prompt}&rdquo;</div>}
          </div>
          <span className="text-xs text-slate-400" title={fullStamp(c.created_at)}>
            {c.ended_at ? 'ended · ' : ''}
            {ago(c.created_at)}
          </span>
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-x-6 gap-y-2">
          {(
            [
              ['candidates', `${c.n_candidates}`],
              ['iterations', `${c.n_iterations}`],
              ['best score', c.best_score === null ? '—' : signed(c.best_score)],
              ['median', c.median_score === null ? '—' : signed(c.median_score)],
              ['alert-free', pct(c.alert_free)],
              ['screened in full', `${c.n_screened}`],
              ['simulated', `${c.n_simulated}/${c.n_sent}`],
            ] as const
          ).map(([label, v]) => (
            <div key={label}>
              <div className="text-base font-semibold tabular-nums text-slate-900">{v}</div>
              <div className="text-[11px] text-slate-500">{label}</div>
            </div>
          ))}
          <div className="ml-auto">
            <Curve values={c.best_by_iteration} />
          </div>
        </div>
        {c.top && (
          <div className="mt-2 truncate text-xs text-slate-500">
            Top candidate: <span className="text-slate-700">{c.top.name}</span>
            {c.top.screen && <span> · screened: {c.top.screen}</span>}
          </div>
        )}
      </button>
    </li>
  );
}

function CandidatesSection({ data, onOpenCampaign }: { data: MyResults['design']; onOpenCampaign: (id: string) => void }) {
  if (!data.candidates)
    return (
      <Empty title="No candidates yet">
        On the Design tab, describe what your biologic has to survive. Every candidate polymer it generates is counted here.
      </Empty>
    );
  const sent = data.candidates - (data.by_status.benchmarked ?? 0);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <MiniStat
          label="Campaigns"
          value={`${data.campaigns}`}
          hint={`${Math.round(data.candidates / Math.max(1, data.iterations))} candidates per iteration`}
        />
        <MiniStat label="Free of structural alerts" value={pct(data.alert_free)} />
        <MiniStat label="Tg within the model's domain" value={pct(data.tg_in_domain)} hint="where it could be judged" />
        <MiniStat label="Sent for simulation" value={`${sent}`} hint={`${data.by_status.simulated ?? 0} simulated`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Where every candidate stands"
          table={{ head: ['Status', 'Candidates'], rows: STATUS.map(([k, label]) => [label, data.by_status[k] ?? 0]) }}
        >
          <ShareBar parts={STATUS.map(([k, label, color]) => ({ label, value: data.by_status[k] ?? 0, color }))} />
        </ChartCard>
        <ChartCard
          title="Candidates screened in full"
          subtitle="Put through the excipient screen with “Screen this candidate”; each one's worst verdict."
          table={{ head: ['Worst verdict', 'Candidates'], rows: VERDICT.map(([v]) => [v, data.screened.worst_verdict[v] ?? 0]) }}
        >
          {data.screened.total ? (
            <ShareBar
              parts={VERDICT.map(([v, color, icon]) => ({ label: v, value: data.screened.worst_verdict[v] ?? 0, color, icon }))}
            />
          ) : (
            <p className="text-sm text-[color:var(--ink-muted)]">
              None yet. Open a candidate on the Design tab and use “Screen this candidate”.
            </p>
          )}
        </ChartCard>
      </div>

      <div>
        <h3 className="mb-2 text-[15px] font-semibold text-[color:var(--ink-1)]">By campaign</h3>
        <ul className="space-y-3">
          {data.per_campaign
            .filter((c) => c.n_candidates > 0)
            .map((c) => (
              <CampaignCard key={c.id} c={c} onOpen={() => onOpenCampaign(c.id)} />
            ))}
        </ul>
      </div>
      <Caveat>
        The triage score ranks candidates within one campaign; its terms depend on that campaign&rsquo;s goal, so scores
        are never compared across campaigns. The line is the best score as the loop iterated. A high score is a reason to
        look closer, not a prediction of stabilisation.
      </Caveat>
    </div>
  );
}

function SimulationsSection({
  data,
  previewNs,
  onOpenCampaign,
}: {
  data: MyResults['simulations'];
  previewNs: number;
  onOpenCampaign: (id: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const rows: ForestRow[] = data.runs.map((r) => ({
    id: r.id,
    label: r.name,
    sub: `${r.protein || 'Unnamed biologic'}${r.preview ? ' · CPU preview' : ''}`,
    value: r.gamma23,
    se: r.gamma23_se,
    color: CALL[r.call].color,
    hollow: r.preview,
    tip: [
      { color: CALL[r.call].color, label: CALL[r.call].label, value: `Γ23 ${signed(r.gamma23)}${r.gamma23_se !== null ? ` ± ${r.gamma23_se}` : ''}` },
      { label: 'triage score', value: signed(r.triage_score) },
      ...(r.production_ns !== null ? [{ label: r.preview ? 'CPU preview' : 'production', value: `${r.production_ns} ns` }] : []),
    ],
  }));
  const active = data.by_state.waiting + data.by_state.running;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <MiniStat label="Excluded from the surface" value={`${data.calls.excluded}`} hint="clear at 2 SE" />
        <MiniStat label="Accumulate at the surface" value={`${data.calls.accumulated}`} hint="clear at 2 SE" />
        <MiniStat label="No clear preference" value={`${data.calls.unclear}`} />
        <MiniStat label="Waiting or running" value={`${active}`} hint={data.by_state.failed ? `${data.by_state.failed} failed` : undefined} />
      </div>

      <ChartCard
        title="Γ23 of every finished run"
        subtitle="Preferential interaction, chains per protein, with a ±2 SE bar. Left of zero the polymer is excluded from the protein, the signature of classic stabilisers; right of zero it binds. Hollow dots are CPU previews."
        table={{
          head: ['Candidate', 'Γ23', '± SE', 'Call', 'Triage'],
          rows: data.runs.map((r) => [
            `${r.name}${r.preview ? ' (CPU preview)' : ''}`,
            signed(r.gamma23),
            r.gamma23_se ?? '—',
            CALL[r.call].label,
            signed(r.triage_score),
          ]),
        }}
      >
        <ForestPlot rows={rows} unit="chains per protein" onPick={setOpen} />
      </ChartCard>

      <div>
        <h3 className="mb-2 text-[15px] font-semibold text-[color:var(--ink-1)]">Every run</h3>
        <SimulationsView onOpenCampaign={onOpenCampaign} />
      </div>
      <Caveat>
        An admin approves each run, as a full GPU run or, when no GPU is available, a short CPU preview ({previewNs} ns) that
        is not converged and never counts as a measurement. Results are grade D either way until an experiment agrees.
      </Caveat>

      {open && (
        <SimulationDetail
          id={open}
          onClose={() => setOpen(null)}
          onOpenCampaign={(cid) => {
            setOpen(null);
            onOpenCampaign(cid);
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

// Two scopes that answer different questions and never share a report:
// - Excipient screening: the Screen tab's own runs -- is this excipient known and
//   safe enough to test at my route?
// - Polymer design: what the designer generated, the candidates it put through
//   the full screen ("Screen this candidate"), and the simulations they led to.
type Scope = 'screening' | 'design';
type DesignSection = 'candidates' | 'simulations';
const SCOPE_KEY = 'results.scope';
const DESIGN_KEY = 'results.design';

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, v: string) {
  try {
    localStorage.setItem(key, v);
  } catch {}
}

function Pills<K extends string>({
  label,
  value,
  items,
  onPick,
}: {
  label: string;
  value: K | null;
  items: readonly (readonly [K, string, number])[];
  onPick: (k: K) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2" role="tablist" aria-label={label}>
      {items.map(([k, text, n]) => (
        <button
          key={k}
          type="button"
          role="tab"
          aria-selected={value === k}
          onClick={() => onPick(k)}
          className={`rounded-full border px-3 py-1 text-sm font-medium transition ${
            value === k ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
          }`}
        >
          {text} <span className="tabular-nums opacity-70">{n}</span>
        </button>
      ))}
    </div>
  );
}

export default function ResultsView({
  onOpenCampaign,
  onOpenScreen,
}: {
  onOpenCampaign: (campaignId: string) => void;
  onOpenScreen: (screenId: string) => void;
}) {
  const [data, setData] = useState<MyResults | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<Scope | null>(null);
  const [designSection, setDesignSection] = useState<DesignSection | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      setData(await getMyResults(signal));
      setError(null);
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    const ctl = new AbortController();
    load(ctl.signal);
    return () => ctl.abort();
  }, [load]);

  // Keep the counts current while a simulation waits or runs.
  const active = data ? data.simulations.by_state.waiting + data.simulations.by_state.running : 0;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => load(), 30000);
    return () => clearInterval(t);
  }, [active, load]);

  // Open where the user last was; otherwise where their newest work is.
  useEffect(() => {
    if (!data || scope) return;
    const sims = data.simulations.runs.length + active + data.simulations.by_state.failed;
    const savedScope = read(SCOPE_KEY);
    const savedSection = read(DESIGN_KEY);
    setScope(
      savedScope === 'screening' || savedScope === 'design'
        ? savedScope
        : data.design.candidates || sims
          ? 'design'
          : 'screening',
    );
    setDesignSection(savedSection === 'candidates' || savedSection === 'simulations' ? savedSection : sims ? 'simulations' : 'candidates');
  }, [data, scope, active]);

  const pickScope = (v: Scope) => {
    setScope(v);
    write(SCOPE_KEY, v);
  };
  const pickSection = (v: DesignSection) => {
    setDesignSection(v);
    write(DESIGN_KEY, v);
  };

  const s = data?.simulations;
  return (
    <div className="viz-root mx-auto w-full max-w-5xl space-y-6 px-4 py-7 sm:px-6 lg:px-10">
      <div>
        <div className="eyebrow">Your work</div>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-900">Results</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate-600">
          Two separate reports: your excipient screening, and your polymer design with the simulations it led to. Everything
          here is triage: it says what to test next, never that something is safe.
        </p>
      </div>

      {error && (
        <p className="flex gap-2 rounded-lg border border-alert-line bg-alert-soft px-4 py-3 text-sm text-slate-800">
          <Triangle className="mt-0.5 h-4 w-4 shrink-0 text-alert" /> {error}
        </p>
      )}

      {!data && !error ? (
        <div className="space-y-3" aria-busy="true">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Spinner className="h-4 w-4" /> Adding up your results
          </div>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-[104px] animate-pulse rounded-2xl border border-slate-200 bg-white" />
            ))}
          </div>
        </div>
      ) : data && s && scope ? (
        <>
          {/* The two reports: a segmented switch, since they are alternatives, not filters. */}
          <div className="inline-flex rounded-lg bg-slate-100 p-1 text-sm font-medium" role="tablist" aria-label="Report">
            {(
              [
                ['screening', 'Excipient screening'],
                ['design', 'Polymer design'],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={scope === k}
                onClick={() => pickScope(k)}
                className={`rounded-md px-4 py-1.5 transition ${
                  scope === k ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {scope === 'screening' ? (
            <>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <Tile
                  label="Excipient screens"
                  value={data.screens.total.toLocaleString()}
                  sub={data.screens.failed ? `${data.screens.failed} failed` : 'from the Screen tab'}
                  color="var(--series-screens)"
                />
                <Tile
                  label="Need testing"
                  value={data.screens.needs_testing.toLocaleString()}
                  sub={`of ${data.screens.ok} finished`}
                  color="var(--status-warning)"
                />
                <Tile
                  label="Evidence coverage"
                  value={pct(data.screens.coverage_mean)}
                  sub="average across your screens"
                  color="var(--ramp-2)"
                />
                <Tile
                  label="High-severity liabilities"
                  value={data.screens.liabilities.high.toLocaleString()}
                  sub={`${data.screens.liabilities.moderate} moderate, ${data.screens.liabilities.low} low`}
                  color="var(--status-critical)"
                />
              </div>
              <ScreensSection data={data.screens} onOpenScreen={onOpenScreen} />
            </>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <Tile
                  label="Candidates generated"
                  value={data.design.candidates.toLocaleString()}
                  sub={`${data.design.campaigns} campaign${data.design.campaigns === 1 ? '' : 's'}, ${data.design.iterations} iterations`}
                  color="var(--series-polymers)"
                />
                <Tile
                  label="Screened in full"
                  value={data.design.screened.total.toLocaleString()}
                  sub={`${data.design.screened.needs_testing} need testing`}
                  color="var(--series-screens)"
                />
                <Tile
                  label="Simulations finished"
                  value={s.by_state.done.toLocaleString()}
                  sub={active ? `${active} waiting or running` : `${s.by_state.failed} failed`}
                  color="var(--series-sims)"
                />
                <Tile
                  label="Excluded from the surface"
                  value={s.calls.excluded.toLocaleString()}
                  sub="clearly, at two standard errors"
                  color="var(--div-neg-2)"
                />
              </div>

              <Pills
                label="Polymer design section"
                value={designSection}
                onPick={pickSection}
                items={[
                  ['candidates', 'Candidates', data.design.candidates],
                  ['simulations', 'Simulations', s.by_state.done + active + s.by_state.failed],
                ] as const}
              />

              {designSection === 'candidates' && <CandidatesSection data={data.design} onOpenCampaign={onOpenCampaign} />}
              {designSection === 'simulations' && (
                <SimulationsSection data={s} previewNs={data.cpu_preview_ns} onOpenCampaign={onOpenCampaign} />
              )}
            </>
          )}
        </>
      ) : null}
    </div>
  );
}
