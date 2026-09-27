'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { SimulationPanel } from '@/components/DesignResults';
import { EventTimeline } from '@/components/history/HistoryDetail';
import { Spinner, Triangle } from '@/components/icons';
import { getMySimulation, getMySimulations, type HistoryEvent, type MySimulation } from '@/lib/api';
import { ago, fullStamp } from '@/lib/when';

// Every simulation the signed-in user has sent off, across all their campaigns,
// and one at a time in full. The list refreshes itself while anything is
// waiting or running; an open simulation refreshes itself while it runs.

type Filter = 'all' | 'active' | 'done' | 'failed';

const isActive = (s: MySimulation) => s.status === 'queued' || s.status === 'simulating';

// One short phrase for where a run stands, and the dot colour that goes with it.
function stateOf(s: MySimulation): { label: string; dot: string } {
  const sim = s.simulation;
  if (s.status === 'simulating') return { label: sim?.progress || 'Running', dot: 'bg-supported animate-pulse' };
  if (s.status === 'queued') {
    if (!sim?.structure_id) return { label: 'Needs a structure', dot: 'bg-gap' };
    if (!sim.approved_at) return { label: 'Awaiting approval', dot: 'bg-gap' };
    return { label: `Approved as ${sim.tier === 'cpu' ? 'CPU preview' : 'GPU run'}, waiting for a worker`, dot: 'bg-supported' };
  }
  if (s.status === 'simulated' && sim?.result) {
    const r = sim.result;
    const g = `Γ23 ${r.gamma23 > 0 ? '+' : ''}${r.gamma23}${r.gamma23_se !== null ? ` ± ${r.gamma23_se}` : ''}`;
    return { label: r.preview || sim.tier === 'cpu' ? `${g} (CPU preview)` : g, dot: 'bg-precedented' };
  }
  return { label: sim?.error ? `Failed: ${sim.error}` : 'Failed', dot: 'bg-alert' };
}

function TierChip({ tier }: { tier: 'gpu' | 'cpu' | null | undefined }) {
  if (!tier) return null;
  return (
    <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
      {tier}
    </span>
  );
}

function Detail({
  id,
  onClose,
  onOpenCampaign,
}: {
  id: string;
  onClose: () => void;
  onOpenCampaign: (campaignId: string) => void;
}) {
  const [data, setData] = useState<{ simulation: MySimulation; events: HistoryEvent[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const d = await getMySimulation(id, ctl.signal);
        setData(d);
        setError(null);
        // Keep an open, running simulation current; stop once it has finished.
        if (isActive(d.simulation)) timer = setTimeout(load, 15000);
      } catch (e) {
        if ((e as Error).name !== 'AbortError') setError((e as Error).message);
      }
    };
    load();
    return () => {
      ctl.abort();
      clearTimeout(timer);
    };
  }, [id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const s = data?.simulation;
  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center bg-slate-900/40 sm:items-center sm:p-6"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Simulation"
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[92vh] w-full max-w-2xl flex-col overflow-hidden rounded-t-2xl bg-white shadow-xl sm:rounded-2xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <div className="eyebrow">Simulation</div>
            <h2 className="mt-1 truncate text-lg font-semibold text-slate-900">{s?.name ?? 'Loading…'}</h2>
            {s && (
              <p className="mt-0.5 text-sm text-slate-500">
                {s.campaign.protein || 'Unnamed biologic'} · {s.campaign.format || 'format not given'}
                {s.campaign.target_temp_c !== null && ` · ${s.campaign.target_temp_c} °C`}
              </p>
            )}
          </div>
          <button type="button" onClick={onClose} className="btn-ghost shrink-0" aria-label="Close">
            Close
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5">
          {error && (
            <p className="flex gap-2 rounded-lg border border-alert-line bg-alert-soft px-4 py-3 text-sm text-slate-800">
              <Triangle className="mt-0.5 h-4 w-4 shrink-0 text-alert" /> {error}
            </p>
          )}
          {!data && !error && (
            <div className="space-y-3" aria-busy="true">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-16 animate-pulse rounded-lg bg-slate-100" />
              ))}
            </div>
          )}
          {s && (
            <>
              {s.simulation ? (
                <SimulationPanel sim={s.simulation} status={s.status} />
              ) : (
                <p className="text-sm text-slate-600">
                  Queued before structures were asked for. Queue it again from its campaign with the biologic’s PDB ID
                  or UniProt accession to run it.
                </p>
              )}
              <div>
                <div className="eyebrow mb-3">What happened</div>
                {data.events.length ? (
                  <EventTimeline events={data.events} />
                ) : (
                  <p className="text-sm text-slate-500">No events recorded for this run.</p>
                )}
              </div>
            </>
          )}
        </div>

        {s && (
          <div className="flex items-center justify-between gap-3 border-t border-slate-200 px-5 py-3">
            <span className="text-xs text-slate-400" title={fullStamp(s.updated_at)}>
              Triage score {s.score.toFixed(1)}
            </span>
            <button type="button" className="btn-primary" onClick={() => onOpenCampaign(s.campaign_id)}>
              Open campaign
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function SimulationsView({ onOpenCampaign }: { onOpenCampaign: (campaignId: string) => void }) {
  const [sims, setSims] = useState<MySimulation[] | null>(null);
  const [previewNs, setPreviewNs] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>('all');
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const r = await getMySimulations(signal);
      setSims(r.simulations);
      setPreviewNs(r.cpu_preview_ns);
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

  // Refresh while anything is waiting or running, so progress appears on its own.
  const anyActive = sims?.some(isActive) ?? false;
  useEffect(() => {
    if (!anyActive) return;
    const t = setInterval(() => load(), 20000);
    return () => clearInterval(t);
  }, [anyActive, load]);

  const counts = useMemo(() => {
    const c = { all: 0, active: 0, done: 0, failed: 0 };
    for (const s of sims ?? []) {
      c.all++;
      if (isActive(s)) c.active++;
      else if (s.status === 'simulated') c.done++;
      else c.failed++;
    }
    return c;
  }, [sims]);

  const shown = (sims ?? []).filter((s) =>
    filter === 'all'
      ? true
      : filter === 'active'
        ? isActive(s)
        : filter === 'done'
          ? s.status === 'simulated'
          : s.status === 'failed',
  );

  return (
    <div className="mx-auto w-full max-w-4xl space-y-5 px-4 py-7 sm:px-6 lg:px-10">
      <div>
        <div className="eyebrow">OpenMM</div>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-900">Simulations</h1>
        <p className="mt-2 text-sm leading-relaxed text-slate-600">
          Every candidate you have sent for simulation. An admin approves each one, as a full GPU run or, when no GPU
          is available, a short CPU preview{previewNs !== null && ` (${previewNs} ns)`} that is not converged and never
          counts as a measurement. Results are grade D either way until an experiment agrees.
        </p>
      </div>

      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Filter simulations">
        {(
          [
            ['all', 'All'],
            ['active', 'Waiting or running'],
            ['done', 'Done'],
            ['failed', 'Failed'],
          ] as const
        ).map(([f, label]) => (
          <button
            key={f}
            type="button"
            role="tab"
            aria-selected={filter === f}
            onClick={() => setFilter(f)}
            className={`rounded-full border px-3 py-1 text-sm font-medium transition ${
              filter === f
                ? 'border-slate-900 bg-slate-900 text-white'
                : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
            }`}
          >
            {label} <span className="tabular-nums opacity-70">{counts[f]}</span>
          </button>
        ))}
      </div>

      {error && (
        <p className="flex gap-2 rounded-lg border border-alert-line bg-alert-soft px-4 py-3 text-sm text-slate-800">
          <Triangle className="mt-0.5 h-4 w-4 shrink-0 text-alert" /> {error}
        </p>
      )}

      {sims === null && !error ? (
        <div className="space-y-3" aria-busy="true">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Spinner className="h-4 w-4" /> Loading your simulations
          </div>
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-[72px] animate-pulse rounded-xl border border-slate-200 bg-white" />
          ))}
        </div>
      ) : sims && sims.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white px-5 py-8 text-center">
          <p className="font-medium text-slate-800">No simulations yet</p>
          <p className="mt-1 text-sm text-slate-500">
            On the Design tab, queue a promising candidate for simulation. It shows up here while it waits for approval,
            runs, and once it is done.
          </p>
        </div>
      ) : shown.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing in this filter.</p>
      ) : (
        <ul className="space-y-3">
          {shown.map((s) => {
            const st = stateOf(s);
            return (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => setOpen(s.id)}
                  className="w-full rounded-xl border border-slate-200 bg-white p-4 text-left shadow-card transition hover:border-slate-300"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-semibold text-slate-900">{s.name}</span>
                        <TierChip tier={s.simulation?.tier} />
                      </div>
                      <div className="mt-0.5 text-sm text-slate-500">
                        {s.campaign.protein || 'Unnamed biologic'}
                        {s.simulation?.structure_id && ` · ${s.simulation.structure_id}`}
                      </div>
                    </div>
                    <span className="text-xs text-slate-400" title={fullStamp(s.updated_at)}>
                      {ago(s.updated_at)}
                    </span>
                  </div>
                  <div className="mt-2 flex items-center gap-2 text-sm text-slate-700">
                    <span className={`h-2 w-2 shrink-0 rounded-full ${st.dot}`} aria-hidden="true" />
                    <span className="truncate">{st.label}</span>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {open && (
        <Detail
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
