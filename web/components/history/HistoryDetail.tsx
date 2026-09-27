'use client';

import { useEffect, useState } from 'react';
import {
  getCampaign,
  getScreenRecord,
  type CampaignState,
  type HistoryEvent,
  type HistoryItem,
  type ScreenRecord,
} from '@/lib/api';
import { duration, fullStamp, timeOfDay } from '@/lib/when';
import DesignResults, { GoalChips } from '../DesignResults';
import { CheckCircle, Flask, Refresh, Spinner, Triangle } from '../icons';
import Results from '../Results';
import ScreenProvenance from './ScreenProvenance';
import Sparkline from './Sparkline';

// One experiment from the history, in full: what was asked, what came back, and
// how it was produced. Opening it in the workspace is the only way to act on
// it, so nothing here changes the record.

const FORM_LABELS: Record<string, string> = {
  excipient: 'Excipient',
  route: 'Route',
  dose: 'Protein dose (mg)',
  concentration: 'Excipient concentration',
  temp: 'Storage',
  structureId: 'Structure (PDB)',
  doseVolume: 'Dose volume (mL)',
  intervalDays: 'Dosing interval (days)',
  durationDays: 'Treatment duration (days)',
};

function Asked({ record }: { record: ScreenRecord }) {
  const form = record.request.form;
  const values = form?.mode === 'form' ? form.values : null;
  const rows = values
    ? Object.entries(FORM_LABELS)
        .map(([k, label]) => [label, values[k]] as const)
        .filter(([, v]) => typeof v === 'string' && v.trim())
    : [];
  const sequence = values && typeof values.sequence === 'string' ? values.sequence : '';
  const polymer = values?.polymer as { enabled?: boolean; repeatUnit?: string } | undefined;

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
      <h3 className="eyebrow mb-3">What was asked</h3>
      <blockquote className="border-l-2 border-slate-300 pl-4 text-[15px] leading-relaxed text-slate-700">
        {record.request.prompt}
      </blockquote>
      {rows.length > 0 && (
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
          {rows.map(([label, v]) => (
            <div key={label} className="min-w-0">
              <dt className="text-xs font-medium text-slate-500">{label}</dt>
              <dd className="mt-0.5 truncate text-sm text-slate-900">{String(v)}</dd>
            </div>
          ))}
          {sequence && (
            <div className="min-w-0">
              <dt className="text-xs font-medium text-slate-500">Sequence</dt>
              <dd className="mt-0.5 truncate font-mono text-xs text-slate-900" title={sequence}>
                {sequence.length} residues
              </dd>
            </div>
          )}
          {polymer?.enabled && (
            <div className="min-w-0">
              <dt className="text-xs font-medium text-slate-500">Described polymer</dt>
              <dd className="mt-0.5 truncate font-mono text-xs text-slate-900">{polymer.repeatUnit}</dd>
            </div>
          )}
        </dl>
      )}
    </section>
  );
}

function ScreenDetail({
  record,
  onOpen,
  onRerun,
}: {
  record: ScreenRecord;
  onOpen: (r: ScreenRecord) => void;
  onRerun: (r: ScreenRecord) => void;
}) {
  const d = record.dossier;
  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="eyebrow">Screen · {fullStamp(record.created_at)}</div>
          <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-slate-900">
            {!d ? (
              'Screen that failed'
            ) : d.protein.length <= 32 ? (
              <>
                {d.excipient} <span className="font-normal text-slate-400">×</span> {d.protein}
              </>
            ) : (
              d.excipient
            )}
          </h1>
          {/* Same rule as the results page: a long protein reads better below. */}
          {d && d.protein.length > 32 && (
            <p className="mt-1 text-[15px] text-slate-600">
              <span className="text-slate-400">×</span> {d.protein}
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn-ghost" onClick={() => onOpen(record)}>
            Open in workspace
          </button>
          <button type="button" className="btn-primary" onClick={() => onRerun(record)}>
            <Refresh className="h-4 w-4" /> Run again
          </button>
        </div>
      </header>

      {record.status === 'failed' && (
        <div className="flex gap-3 rounded-xl border border-alert-line bg-alert-soft px-4 py-3 text-sm text-slate-800">
          <Triangle className="mt-0.5 h-5 w-5 shrink-0 text-alert" />
          <p>
            <b className="font-semibold">This run failed</b> after {duration(record.provenance.duration_s ?? 0)}.{' '}
            {record.error}
          </p>
        </div>
      )}

      <Asked record={record} />
      <ScreenProvenance record={record} />

      {d && (
        <section className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50 shadow-card">
          <h3 className="eyebrow border-b border-slate-200 bg-white px-5 py-3.5">The result, as it was returned</h3>
          <Results dossier={d} context={null} onRerun={() => onRerun(record)} loading={false} />
        </section>
      )}
    </div>
  );
}

// What each kind of event means, in words, from the data it was logged with.
function describe(e: HistoryEvent): { title: string; body: string | null; tone: 'neutral' | 'good' | 'info' | 'bad' } {
  const d = e.data ?? {};
  switch (e.kind) {
    case 'campaign.started':
      return {
        title: 'Started',
        body: d.goal_parsed_by
          ? `Goal read from your words by ${String(d.goal_parsed_by).replace(/^anthropic:/, '')}.`
          : 'Goal given directly.',
        tone: 'info',
      };
    case 'campaign.iterated': {
      const m = d.metrics ?? {};
      const parts = [
        `${d.n_candidates} candidates`,
        m.best_score_so_far != null && `best so far ${m.best_score_so_far}`,
        m.batch_mean_score != null && `batch mean ${m.batch_mean_score}`,
        m.surrogate_cv_mae != null && `surrogate error ${m.surrogate_cv_mae}`,
        m.seeded && 'space-filling seed',
      ].filter(Boolean);
      return { title: `Iteration ${d.iteration}`, body: parts.join(' · '), tone: 'neutral' };
    }
    case 'candidate.queued': {
      const c: { name: string; score: number }[] = d.candidates ?? [];
      return {
        title: `Queued ${c.length} for simulation`,
        body: c.map((x) => `${x.name} (${x.score})`).join('; '),
        tone: 'good',
      };
    }
    case 'candidate.simulating':
      return {
        title: `Simulating ${d.candidate?.name ?? 'a candidate'}`,
        body: `Against ${d.structure_id}, on ${d.worker}${d.attempt > 1 ? ` (attempt ${d.attempt})` : ''}.`,
        tone: 'info',
      };
    case 'candidate.simulated':
      return {
        title: `Simulated ${d.candidate?.name ?? 'a candidate'}`,
        body:
          `Γ23 = ${d.gamma23}${d.gamma23_se != null ? ` ± ${d.gamma23_se}` : ''} over ${d.production_ns} ns` +
          (d.smoke ? ' (pipeline smoke test, not a measurement)' : ''),
        tone: 'good',
      };
    case 'candidate.simulation_failed':
      return { title: `Simulation failed: ${d.candidate?.name ?? 'a candidate'}`, body: d.error ?? '', tone: 'bad' };
    case 'candidate.requeued':
      return { title: 'Simulation requeued', body: d.error ?? 'The worker stopped responding.', tone: 'neutral' };
    case 'campaign.ended':
      return { title: 'Ended', body: 'Closed with New experiment. Still here, and can be continued.', tone: 'neutral' };
    case 'campaign.reopened':
      return { title: 'Reopened', body: 'Continued from history.', tone: 'info' };
    case 'screen.completed':
      return { title: 'Completed', body: null, tone: 'good' };
    case 'screen.failed':
      return { title: 'Failed', body: d.error ?? null, tone: 'neutral' };
  }
}

const TONE_DOT = { neutral: 'bg-slate-400', good: 'bg-precedented', info: 'bg-supported', bad: 'bg-alert' } as const;

function EventTimeline({ events }: { events: HistoryEvent[] }) {
  return (
    <ol className="relative space-y-4 border-l border-slate-200 pl-5">
      {events.map((e, i) => {
        const { title, body, tone } = describe(e);
        return (
          <li key={i} className="relative">
            <span
              className={`absolute -left-[25px] top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-white ${TONE_DOT[tone]}`}
              aria-hidden="true"
            />
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="text-sm font-semibold text-slate-900">{title}</span>
              <span className="text-xs tabular-nums text-slate-400" title={fullStamp(e.at)}>
                {timeOfDay(e.at)} · <span className="font-mono">{e.app_version}</span>
              </span>
            </div>
            {body && <p className="mt-0.5 text-[13px] leading-relaxed text-slate-600">{body}</p>}
          </li>
        );
      })}
    </ol>
  );
}

function CampaignDetail({ state, onOpen }: { state: CampaignState; onOpen: (s: CampaignState) => void }) {
  const candidates = Object.values(state.candidates_by_iteration).flat();
  const best = state.metrics_history.map((m) => m.best_score_so_far);
  const bestNow = best.filter((v): v is number => v !== null).at(-1);
  const stats = [
    ['Iterations', state.n_iterations],
    ['Candidates', state.n_candidates],
    ['Queued', state.queue_summary.n_queued],
    ['Best score', bestNow ?? '—'],
  ] as const;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="eyebrow">Design campaign · {fullStamp(state.created_at)}</div>
          <h1 className="mt-1.5 flex flex-wrap items-center gap-3 text-2xl font-semibold tracking-tight text-slate-900">
            {state.goal.protein || 'Design campaign'}
            {state.ended_at ? (
              <span className="rounded-full border border-slate-200 bg-slate-100 px-2.5 py-0.5 text-xs font-semibold text-slate-500">
                ended
              </span>
            ) : (
              <span className="rounded-full border border-supported-line bg-supported-soft px-2.5 py-0.5 text-xs font-semibold text-supported">
                active
              </span>
            )}
          </h1>
          <div className="mt-3">
            <GoalChips goal={state.goal} />
          </div>
        </div>
        <button type="button" className="btn-primary" onClick={() => onOpen(state)}>
          <Flask className="h-4 w-4" /> {state.ended_at ? 'Continue this campaign' : 'Open in workspace'}
        </button>
      </header>

      {state.prompt && (
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
          <h3 className="eyebrow mb-3">What was asked</h3>
          <blockquote className="border-l-2 border-slate-300 pl-4 text-[15px] leading-relaxed text-slate-700">
            {state.prompt}
          </blockquote>
        </section>
      )}

      <section className="grid gap-5 rounded-xl border border-slate-200 bg-white p-5 shadow-card md:grid-cols-[1fr_auto]">
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {stats.map(([label, v]) => (
            <div key={label}>
              <dt className="text-xs font-medium text-slate-500">{label}</dt>
              <dd className="mt-0.5 text-xl font-semibold tabular-nums text-slate-900">{v}</dd>
            </div>
          ))}
        </dl>
        {best.length > 1 && (
          <div className="flex flex-col items-start justify-center gap-1 md:items-end">
            <Sparkline values={best} width={180} height={44} />
            <span className="text-xs text-slate-500">best score by iteration</span>
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
        <h3 className="eyebrow mb-4">What happened, in order</h3>
        <EventTimeline events={state.events} />
      </section>

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50 shadow-card">
        <h3 className="eyebrow border-b border-slate-200 bg-white px-5 py-3.5">Every candidate, as proposed</h3>
        <DesignResults
          goal={state.goal}
          candidates={candidates}
          limits={state.limits}
          verdict="Data gap: test"
          readOnly
        />
      </section>
    </div>
  );
}

export default function HistoryDetail({
  item,
  onOpenScreen,
  onRerunScreen,
  onOpenCampaign,
}: {
  item: HistoryItem | null;
  onOpenScreen: (r: ScreenRecord) => void;
  onRerunScreen: (r: ScreenRecord) => void;
  onOpenCampaign: (s: CampaignState) => void;
}) {
  const [data, setData] = useState<
    { kind: 'screen'; record: ScreenRecord } | { kind: 'campaign'; state: CampaignState } | null
  >(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!item) return;
    const ctrl = new AbortController();
    setData(null);
    setError(null);
    (item.kind === 'screen'
      ? getScreenRecord(item.id, ctrl.signal).then((record) => ({ kind: 'screen' as const, record }))
      : getCampaign(item.id, ctrl.signal).then((state) => ({ kind: 'campaign' as const, state }))
    )
      .then(setData)
      .catch((e) => {
        if (e.name !== 'AbortError') setError((e as Error).message);
      });
    return () => ctrl.abort();
  }, [item]);

  if (!item) {
    return (
      <div className="grid h-full place-items-center px-6 py-16 text-center">
        <div className="max-w-sm">
          <CheckCircle className="mx-auto h-8 w-8 text-slate-300" />
          <p className="mt-3 font-medium text-slate-700">Pick an experiment</p>
          <p className="mt-1 text-sm leading-relaxed text-slate-500">
            Each one shows what was asked, what came back, and exactly how it was produced: the model, the build,
            and what every data source returned at the time.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-7 lg:px-10">
      {error && (
        <p className="rounded-lg border border-alert-line bg-alert-soft px-4 py-3 text-sm text-slate-800">{error}</p>
      )}
      {!data && !error && (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <Spinner className="h-4 w-4" /> Loading the record
        </div>
      )}
      {data?.kind === 'screen' && <ScreenDetail record={data.record} onOpen={onOpenScreen} onRerun={onRerunScreen} />}
      {data?.kind === 'campaign' && <CampaignDetail state={data.state} onOpen={onOpenCampaign} />}
    </div>
  );
}
