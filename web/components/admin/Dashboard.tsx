'use client';

import { useCallback, useEffect, useState } from 'react';
import { Spinner, Triangle } from '@/components/icons';
import { getAdminStats, type AdminStats, type Bin } from '@/lib/api';
import { ChartCard, Columns, DailyBars, HBars, LineChart, ShareBar, StatTile, fmt } from './charts';

// The owner's view of the whole platform: how much is being done, never what.
// Every number comes from api/stats.py, which aggregates and selects no user
// content. One colour per metric, the same everywhere on the page.

const C = {
  screens: 'var(--series-screens)',
  polymers: 'var(--series-polymers)',
  sims: 'var(--series-sims)',
  users: 'var(--series-users)',
};

const WINDOWS = [
  [30, '30 days'],
  [90, '90 days'],
  [365, '12 months'],
] as const;

const BACKBONE = {
  methacrylamide: 'Poly(methacrylamide)',
  acrylamide: 'Poly(acrylamide)',
  methacrylate: 'Poly(methacrylate)',
  vinyl: 'Poly(vinyl)',
  oxazoline: 'Poly(2-oxazoline)',
} as Record<string, string>;

// Verdicts carry meaning, so they wear the status colours, each with an icon.
const VERDICT = [
  ['Precedented', 'var(--status-good)', '✓'],
  ['Supported without precedent', 'var(--series-screens)', '◆'],
  ['Data gap: test', 'var(--status-warning)', '?'],
  ['Alert: avoid', 'var(--status-critical)', '!'],
] as const;

const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

// Proper minus signs, so "−1–0" never reads as a double hyphen.
const num = (v: number) => (v < 0 ? `−${Math.abs(v)}` : `${v}`);
const binLabel = (b: Bin, unit = '') =>
  b.lo === null ? `<${num(b.hi!)}${unit}` : b.hi === null ? `≥${num(b.lo)}${unit}` : `${num(b.lo)} to ${num(b.hi)}${unit}`;

function secs(s: number | null) {
  if (s === null) return '—';
  return s < 90 ? `${Math.round(s)} s` : `${(s / 60).toFixed(1)} min`;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-4">
      <h2 className="text-xs font-semibold uppercase tracking-[0.12em] text-[color:var(--ink-muted)]">{title}</h2>
      {children}
    </section>
  );
}

function MiniStat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-2.5">
      <div className="text-xl font-semibold tracking-tight text-[color:var(--ink-1)]">{value}</div>
      <div className="text-xs text-[color:var(--ink-2)]">{label}</div>
      {hint && <div className="text-[11px] text-[color:var(--ink-muted)]">{hint}</div>}
    </div>
  );
}

export default function Dashboard() {
  const [days, setDays] = useState<number>(90);
  const [data, setData] = useState<AdminStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (d: number, signal?: AbortSignal) => {
    setLoading(true);
    try {
      setData(await getAdminStats(d, signal));
      setError(null);
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const ctl = new AbortController();
    load(days, ctl.signal);
    return () => ctl.abort();
  }, [days, load]);

  if (!data) {
    return (
      <div className="viz-root space-y-6" aria-busy="true">
        {error ? (
          <p className="flex gap-2 rounded-xl border border-alert-line bg-alert-soft px-4 py-3 text-sm text-slate-800">
            <Triangle className="mt-0.5 h-4 w-4 shrink-0 text-alert" /> {error}
          </p>
        ) : (
          <>
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Spinner className="h-4 w-4" /> Counting everything
            </div>
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} className="h-40 animate-pulse rounded-2xl bg-slate-100" />
              ))}
            </div>
            <div className="grid gap-4 lg:grid-cols-2">
              {[0, 1].map((i) => (
                <div key={i} className="h-56 animate-pulse rounded-2xl bg-slate-100" />
              ))}
            </div>
          </>
        )}
      </div>
    );
  }

  const { totals: t, series: s, previous: p } = data;
  const tokens = t.tokens_in + t.tokens_out;
  const sp = data.simulations;
  const byStatus = data.polymers.by_status;
  const format = data.polymers.by_format;
  const gammaBins = sp.gamma23;
  // Diverging: excluded (negative, stabiliser-like) in blue, accumulating in red,
  // the two bins nearest zero lighter, and no hue at zero itself.
  const gammaColors = gammaBins.map((b) =>
    b.hi !== null && b.hi <= -1 ? 'var(--div-neg-2)' : b.hi !== null && b.hi <= 0 ? 'var(--div-neg-1)'
      : b.lo !== null && b.lo >= 1 ? 'var(--div-pos-2)' : 'var(--div-pos-1)',
  );

  return (
    <div className={`viz-root space-y-8 transition-opacity ${loading ? 'opacity-60' : ''}`}>
      {/* One filter row, above everything it scopes. */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg bg-slate-100 p-1 text-sm font-medium" role="radiogroup" aria-label="Period">
          {WINDOWS.map(([d, label]) => (
            <button
              key={d}
              type="button"
              role="radio"
              aria-checked={days === d}
              onClick={() => setDays(d)}
              className={`rounded-md px-3 py-1.5 transition ${days === d ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="text-xs text-[color:var(--ink-muted)]">
          Updated {new Date(data.generated_at).toLocaleTimeString()} · totals are all-time; trends cover the period
        </span>
        {loading && <Spinner className="h-4 w-4 text-slate-400" />}
        {error && <span className="text-xs text-alert">{error}</span>}
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Polymers designed" value={t.polymers} color={C.polymers} spark={s.polymers} current={sum(s.polymers)} previous={p.polymers} />
        <StatTile label="Excipient screens" value={t.screens} color={C.screens} spark={s.screens} current={sum(s.screens)} previous={p.screens} />
        <StatTile label="Design campaigns" value={t.campaigns} color={C.polymers} spark={s.campaigns} current={sum(s.campaigns)} previous={p.campaigns} />
        <StatTile label="Simulations done" value={t.simulations_done} color={C.sims} spark={s.simulations} current={sum(s.simulations)} previous={p.simulations} />
        <StatTile label="Users" value={t.users} color={C.users} spark={s.users_cumulative} sub={`${t.active_users_7d} active this week · ${t.active_users_30d} this month`} />
        <StatTile label="Model tokens" value={tokens} color="var(--ramp-2)" sub={`${fmt(t.model_requests)} model calls`} />
      </div>

      <Section title="Activity">
        <div className="grid gap-4 lg:grid-cols-2">
          <ChartCard title="Excipient screens per day" subtitle={`${sum(s.screens)} in the period`}
            table={{ head: ['Day', 'Screens'], rows: s.dates.map((d, i) => [d, s.screens[i]]) }}>
            <DailyBars dates={s.dates} values={s.screens} color={C.screens} label="screens" />
          </ChartCard>
          <ChartCard title="Polymers designed per day" subtitle={`${sum(s.polymers)} candidates built and screened in the period`}
            table={{ head: ['Day', 'Polymers'], rows: s.dates.map((d, i) => [d, s.polymers[i]]) }}>
            <DailyBars dates={s.dates} values={s.polymers} color={C.polymers} label="polymers" />
          </ChartCard>
          <ChartCard title="Simulations finished per day" subtitle={`${sum(s.simulations)} in the period`}
            table={{ head: ['Day', 'Simulations'], rows: s.dates.map((d, i) => [d, s.simulations[i]]) }}>
            <DailyBars dates={s.dates} values={s.simulations} color={C.sims} label="simulations" />
          </ChartCard>
          <ChartCard title="Users" subtitle="Cumulative, dated by each user's first activity"
            table={{ head: ['Day', 'Users', 'Active that day'], rows: s.dates.map((d, i) => [d, s.users_cumulative[i], s.active_users[i]]) }}>
            <LineChart dates={s.dates} values={s.users_cumulative} color={C.users} label="users" />
          </ChartCard>
        </div>
      </Section>

      <Section title="Excipient screens">
        <div className="grid gap-4 lg:grid-cols-3">
          <ChartCard title="Outcomes" subtitle="Each screen's worst verdict across its endpoints" className="lg:col-span-2"
            table={{ head: ['Worst verdict', 'Screens'], rows: VERDICT.map(([v]) => [v, data.screens.worst_verdict[v] ?? 0]) }}>
            <ShareBar parts={VERDICT.map(([label, color, icon]) => ({ label, color, icon, value: data.screens.worst_verdict[label] ?? 0 }))} />
            <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <MiniStat label="completed" value={t.screens_ok.toLocaleString()} hint={t.screens ? `${Math.round((t.screens_ok / t.screens) * 100)}% success` : undefined} />
              <MiniStat label="failed" value={t.screens_failed.toLocaleString()} />
              <MiniStat label="need testing" value={data.screens.needs_testing.toLocaleString()} />
              <MiniStat label="typical run" value={secs(data.screens.duration_median_s)} hint={`90% under ${secs(data.screens.duration_p90_s)}`} />
            </div>
          </ChartCard>
          <ChartCard title="Evidence grades" subtitle="Across every endpoint of every screen; A is strongest"
            table={{ head: ['Grade', 'Endpoints'], rows: Object.entries(data.screens.grades) }}>
            <Columns bins={Object.entries(data.screens.grades).map(([label, value]) => ({ label, value }))}
              colors={['var(--ramp-1)', 'var(--ramp-2)', 'var(--ramp-3)', 'var(--ramp-4)', 'var(--ramp-5)']} />
          </ChartCard>
          <ChartCard title="Routes screened" subtitle="Route of administration" className="lg:col-span-3"
            table={{ head: ['Route', 'Screens'], rows: Object.entries(data.screens.routes) }}>
            <HBars color={C.screens} items={Object.entries(data.screens.routes).slice(0, 8).map(([label, value]) => ({ label, value }))} />
          </ChartCard>
        </div>
      </Section>

      <Section title="Polymer design">
        <div className="grid gap-4 lg:grid-cols-3">
          <ChartCard title="Pipeline" subtitle="Where every designed polymer stands"
            table={{ head: ['Stage', 'Polymers'], rows: Object.entries(byStatus) }}>
            <HBars
              colors={['var(--ramp-5)', 'var(--ramp-4)', 'var(--ramp-3)', 'var(--ramp-2)', 'var(--ramp-1)']}
              items={[
                ['Screened (benchmarked)', byStatus.benchmarked ?? 0],
                ['Queued for simulation', byStatus.queued ?? 0],
                ['Simulating', byStatus.simulating ?? 0],
                ['Simulated', byStatus.simulated ?? 0],
                ['Failed or denied', byStatus.failed ?? 0],
              ].map(([label, value]) => ({ label: label as string, value: value as number }))}
            />
          </ChartCard>
          <ChartCard title="Backbones" subtitle="Which chemistries the designer reaches for"
            table={{ head: ['Backbone', 'Polymers'], rows: Object.entries(data.polymers.by_backbone).map(([k, v]) => [BACKBONE[k] ?? k, v]) }}>
            <HBars color={C.polymers} items={Object.entries(data.polymers.by_backbone).map(([k, value]) => ({ label: BACKBONE[k] ?? k, value }))} />
          </ChartCard>
          <ChartCard title="Campaign goals" subtitle="Formulation format, and the temperature to survive"
            table={{ head: ['Format or °C band', 'Campaigns'], rows: [...Object.entries(format), ...data.polymers.target_temp_c.map((b) => [binLabel(b, ' °C'), b.n])] }}>
            <ShareBar parts={[
              { label: 'Liquid', value: format.liquid ?? 0, color: 'var(--cat-magenta)' },
              { label: 'Lyophilised', value: format.lyophilised ?? 0, color: 'var(--cat-yellow)' },
              ...(format['not stated'] ? [{ label: 'Not stated', value: format['not stated'], color: 'var(--div-mid)' }] : []),
            ]} />
            <div className="mt-4">
              <Columns bins={data.polymers.target_temp_c.map((b) => ({ label: b.lo === null ? `<${num(b.hi!)}` : num(b.lo), tip: binLabel(b), value: b.n }))}
                colors={data.polymers.target_temp_c.map(() => C.polymers)} unit=" °C" height={120} />
            </div>
          </ChartCard>
        </div>
      </Section>

      <Section title="Simulations">
        <div className="grid gap-4 lg:grid-cols-3">
          <ChartCard title="Now" subtitle="Across every user"
            table={{ head: ['State', 'Simulations'], rows: Object.entries(sp.by_status) }}>
            <div className="grid grid-cols-2 gap-3">
              <MiniStat label="awaiting your approval" value={sp.by_status.waiting.toLocaleString()} />
              <MiniStat label="approved, queued" value={sp.by_status.approved.toLocaleString()} />
              <MiniStat label="running" value={sp.by_status.running.toLocaleString()} />
              <MiniStat label="finished" value={sp.by_status.done.toLocaleString()} />
              <MiniStat label="failed or denied" value={sp.by_status.failed.toLocaleString()} />
              <MiniStat label="ever requested" value={t.simulations_requested.toLocaleString()} />
            </div>
          </ChartCard>
          <ChartCard title="GPU runs and CPU previews" subtitle="How approved simulations were run"
            table={{ head: ['Kind', 'Approvals'], rows: Object.entries(sp.by_tier) }}>
            <ShareBar parts={[
              { label: 'Full GPU runs', value: sp.by_tier.gpu ?? 0, color: C.sims },
              { label: 'CPU previews', value: sp.by_tier.cpu ?? 0, color: 'var(--cat-yellow)' },
            ]} />
            <p className="mt-4 text-xs leading-relaxed text-[color:var(--ink-muted)]">
              A CPU preview is short and not converged; only full runs are recorded as measurements.
            </p>
          </ChartCard>
          <ChartCard title="Γ23: where the polymer sits" subtitle="Preferential interaction per finished run, chains per protein"
            table={{ head: ['Γ23 band', 'Runs'], rows: gammaBins.map((b) => [binLabel(b), b.n]) }}>
            <Columns bins={gammaBins.map((b) => ({ label: b.lo === null ? `<${num(b.hi!)}` : num(b.lo), tip: binLabel(b), value: b.n }))}
              colors={gammaColors} height={140} />
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[color:var(--ink-2)]">
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-sm bg-[color:var(--div-neg-2)]" aria-hidden="true" />
                Excluded, stabiliser-like: <b className="tabular-nums">{sp.gamma23_excluded}</b>
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-sm bg-[color:var(--div-pos-2)]" aria-hidden="true" />
                Accumulates at the surface: <b className="tabular-nums">{sp.gamma23_accumulated}</b>
              </span>
            </div>
          </ChartCard>
        </div>
      </Section>

      <Section title="Model usage">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MiniStat label="input tokens" value={fmt(t.tokens_in)} />
          <MiniStat label="output tokens" value={fmt(t.tokens_out)} />
          <MiniStat label="model calls" value={fmt(t.model_requests)} />
          <MiniStat label="measurements stored" value={t.measurements.toLocaleString()} hint={`${t.iterations} design iterations`} />
        </div>
      </Section>
    </div>
  );
}
