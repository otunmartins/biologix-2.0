'use client';

import { useCallback, useEffect, useState } from 'react';
import { Spinner, Triangle } from '@/components/icons';
import {
  approveSimulation,
  declineSimulation,
  getSimulations,
  type AdminJob,
  type AdminSimulations,
  type SimTier,
} from '@/lib/api';
import { ago } from '@/lib/when';

// Every user's simulations that are waiting, approved or running, and what the
// owner can do to each: approve (as a full GPU run or a short CPU preview),
// switch an approved job's kind before a worker takes it, deny it, or stop it
// while it runs. Approving a GPU run also starts the RunPod pod when the API
// has RUNPOD_API_KEY and RUNPOD_POD_ID; the pod stops itself once idle.

const TIER = { gpu: 'GPU run', cpu: 'CPU preview' } as const;

function stateOf(j: AdminJob): { label: string; detail: string; dot: string } {
  if (j.status === 'simulating')
    return { label: `Running · ${TIER[j.simulation?.tier ?? 'gpu']}`, detail: j.simulation?.progress || 'starting', dot: 'bg-supported animate-pulse' };
  if (!j.simulation?.structure_id) return { label: 'No structure', detail: 'the user has not named the biologic yet', dot: 'bg-slate-300' };
  if (!j.simulation.approved_at) return { label: 'Awaiting your approval', detail: '', dot: 'bg-gap' };
  return { label: `Approved · ${TIER[j.simulation.tier ?? 'gpu']}`, detail: `waiting for a ${j.simulation.tier === 'cpu' ? 'CPU' : 'GPU'} worker`, dot: 'bg-supported' };
}

function Dialog({ label, onClose, children }: { label: string; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center bg-slate-900/40 sm:items-center sm:p-6" onClick={onClose} role="presentation">
      <div role="dialog" aria-modal="true" aria-label={label} onClick={(e) => e.stopPropagation()}
        className="w-full max-w-lg rounded-t-2xl bg-white p-5 shadow-xl sm:rounded-2xl">
        {children}
      </div>
    </div>
  );
}

function TierChoice({ job, previewNs, onPick, onCancel }: {
  job: AdminJob;
  previewNs: number;
  onPick: (tier: SimTier) => void;
  onCancel: () => void;
}) {
  const current = job.simulation?.approved_at ? job.simulation.tier : null;
  const options: [SimTier, string, string][] = [
    ['gpu', 'Full GPU run', '20 ns on the RunPod GPU, about 3 to 6 hours and roughly $1 to $4. Starts the pod. Recorded as a measurement (grade D).'],
    ['cpu', 'CPU preview', `${previewNs} ns on a CPU worker, no GPU cost, hours on a CPU. Not converged: shown on the candidate, never recorded as a measurement.`],
  ];
  return (
    <Dialog label="Approve simulation" onClose={onCancel}>
      <div className="eyebrow">{current ? 'Switch' : 'Approve'}</div>
      <h2 className="mt-1 text-lg font-semibold text-slate-900">{job.name}</h2>
      <p className="mt-0.5 text-sm text-slate-500">{job.owner_email ?? 'unknown user'} · against {job.simulation?.structure_id}</p>
      <div className="mt-4 space-y-2.5">
        {options.map(([tier, title, body]) => (
          <button key={tier} type="button" disabled={tier === current} onClick={() => onPick(tier)}
            className="w-full rounded-xl border border-slate-200 p-4 text-left transition hover:border-slate-900 disabled:cursor-default disabled:opacity-50 disabled:hover:border-slate-200">
            <div className="font-semibold text-slate-900">{title}{tier === current && ' (current)'}</div>
            <p className="mt-1 text-sm leading-relaxed text-slate-600">{body}</p>
          </button>
        ))}
      </div>
      <div className="mt-4 text-right">
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
      </div>
    </Dialog>
  );
}

function ReasonDialog({ job, onSubmit, onCancel }: { job: AdminJob; onSubmit: (reason: string) => void; onCancel: () => void }) {
  const running = job.status === 'simulating';
  const [reason, setReason] = useState(running ? 'Stopped to save compute' : 'Not approved for compute right now');
  return (
    <Dialog label={running ? 'Stop simulation' : 'Deny simulation'} onClose={onCancel}>
      <div className="eyebrow">{running ? 'Stop' : 'Deny'}</div>
      <h2 className="mt-1 text-lg font-semibold text-slate-900">{job.name}</h2>
      <p className="mt-1 text-sm leading-relaxed text-slate-600">
        {running
          ? 'The worker drops the run at its next check-in, within about a minute. The partial run is discarded.'
          : 'It leaves the queue. The user can queue it again.'}{' '}
        They see the reason below.
      </p>
      <form className="mt-4 space-y-4" onSubmit={(e) => { e.preventDefault(); onSubmit(reason.trim() || 'declined by an admin'); }}>
        <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} autoFocus aria-label="Reason"
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-slate-900 focus:outline-none" />
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="rounded-lg bg-alert px-3 py-1.5 text-sm font-medium text-white hover:opacity-90">
            {running ? 'Stop run' : 'Deny'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

const btn = 'rounded-lg px-3 py-1.5 text-sm font-medium disabled:opacity-50';

export default function Approvals({ onWaiting }: { onWaiting?: (n: number) => void }) {
  const [data, setData] = useState<AdminSimulations | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [choosing, setChoosing] = useState<AdminJob | null>(null);
  const [denying, setDenying] = useState<AdminJob | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const r = await getSimulations(signal);
      setData(r);
      setError(null);
      onWaiting?.(r.jobs.filter((j) => j.status === 'queued' && j.simulation?.structure_id && !j.simulation.approved_at).length);
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
    }
  }, [onWaiting]);

  useEffect(() => {
    const ctl = new AbortController();
    load(ctl.signal);
    const t = setInterval(() => load(ctl.signal), 20000);
    return () => {
      clearInterval(t);
      ctl.abort();
    };
  }, [load]);

  const run = async (j: AdminJob, fn: () => Promise<string>) => {
    setBusy(j.id);
    setError(null);
    setNotice(null);
    try {
      setNotice(await fn());
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const online = (iso: string) => Date.now() - new Date(iso).getTime() < 120000;

  return (
    <div className="space-y-6">
      <p className="text-sm leading-relaxed text-slate-600">
        Nothing reaches a worker until you approve it, and each user can have one simulation waiting or running at a
        time. Approve as a full GPU run or, when no GPU is available, a CPU preview; switch or deny before a worker
        takes it; stop it while it runs.
        {data && !data.pod_autostart && ' The API has no RunPod key, so start the GPU pod by hand after approving a GPU run.'}
      </p>

      {/* Which workers are actually there to take work. */}
      <div className="flex flex-wrap gap-2">
        {data?.workers.length ? data.workers.map((w) => (
          <span key={w.name} className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600">
            <span className={`h-2 w-2 rounded-full ${online(w.last_seen) ? 'bg-precedented' : 'bg-slate-300'}`} aria-hidden="true" />
            <b className="font-semibold text-slate-800">{w.name}</b>
            {w.platform}{w.tier ? ` · takes ${w.tier === 'cpu' ? 'CPU previews' : 'GPU runs'}` : ''}
            <span className="text-slate-400">{online(w.last_seen) ? 'online' : `seen ${ago(w.last_seen)}`}</span>
          </span>
        )) : data && (
          <span className="text-xs text-slate-500">No worker has checked in since the API started.</span>
        )}
      </div>

      {error && (
        <p className="flex gap-2 rounded-lg border border-alert-line bg-alert-soft px-4 py-3 text-sm text-slate-800">
          <Triangle className="mt-0.5 h-4 w-4 shrink-0 text-alert" /> {error}
        </p>
      )}
      {notice && <p className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">{notice}</p>}

      {choosing && data && (
        <TierChoice job={choosing} previewNs={data.cpu_preview_ns} onCancel={() => setChoosing(null)}
          onPick={(tier) => {
            const j = choosing;
            setChoosing(null);
            run(j, async () => `${j.name}: ${j.simulation?.approved_at ? 'switched to' : 'approved as'} a ${TIER[tier]}. Worker: ${await approveSimulation(j.id, tier)}.`);
          }} />
      )}
      {denying && (
        <ReasonDialog job={denying} onCancel={() => setDenying(null)}
          onSubmit={(reason) => {
            const j = denying;
            setDenying(null);
            run(j, async () => `${j.name}: ${(await declineSimulation(j.id, reason)) ? 'stopped' : 'denied'}.`);
          }} />
      )}

      {data === null && !error ? (
        <div className="flex items-center gap-2 text-sm text-slate-500"><Spinner className="h-4 w-4" /> Loading</div>
      ) : data && data.jobs.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white px-5 py-8 text-center text-sm text-slate-500">
          Nothing waiting or running.
        </div>
      ) : (
        <ul className="space-y-3">
          {data?.jobs.map((j) => {
            const st = stateOf(j);
            const waiting = j.status === 'queued' && !!j.simulation?.structure_id;
            return (
              <li key={j.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-card">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-semibold text-slate-900">{j.name}</div>
                    <div className="mt-0.5 break-words text-sm text-slate-500">
                      {j.owner_email ?? 'unknown user'} · against {j.simulation?.structure_id || '—'} · score {j.score.toFixed(1)}
                    </div>
                    <div className="mt-1.5 flex items-center gap-2 text-sm text-slate-700">
                      <span className={`h-2 w-2 shrink-0 rounded-full ${st.dot}`} aria-hidden="true" />
                      <b className="font-medium">{st.label}</b>
                      {st.detail && <span className="truncate text-slate-500">{st.detail}</span>}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2">
                    {/* Always there for a queued job, so every row reads Approve / Deny. A job
                        with no structure cannot run, so its Approve is shown but disabled. */}
                    {j.status === 'queued' && (
                      <button type="button" disabled={busy === j.id || !waiting} onClick={() => setChoosing(j)}
                        title={waiting ? undefined : 'Cannot run until the user names the biologic’s PDB ID or UniProt accession'}
                        className={`${btn} disabled:cursor-not-allowed${j.simulation?.approved_at ? 'border border-slate-300 text-slate-700 hover:bg-slate-50' : 'bg-slate-900 text-white hover:bg-slate-700'}`}>
                        {j.simulation?.approved_at ? 'Switch' : 'Approve'}
                      </button>
                    )}
                    <button type="button" disabled={busy === j.id} onClick={() => setDenying(j)}
                      className={`${btn} border border-alert-line text-alert hover:bg-alert-soft`}>
                      {j.status === 'simulating' ? 'Stop' : 'Deny'}
                    </button>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {data && data.recent.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-slate-400">Recently finished or denied</h3>
          <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
            {data.recent.map((j) => (
              <li key={j.id} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 px-4 py-2.5 text-sm">
                <span className="min-w-0 truncate">
                  <b className="font-medium text-slate-800">{j.name}</b>
                  <span className="text-slate-500"> · {j.owner_email ?? 'unknown user'}</span>
                </span>
                <span className="flex items-center gap-3 text-xs">
                  {j.status === 'simulated' && j.simulation?.result ? (
                    <span className="font-medium text-precedented">
                      Γ23 {j.simulation.result.gamma23 > 0 ? '+' : ''}{j.simulation.result.gamma23}
                      {(j.simulation.result.preview || j.simulation.tier === 'cpu') && ' · preview'}
                    </span>
                  ) : (
                    <span className="max-w-[18rem] truncate text-alert">{j.simulation?.error ?? 'failed'}</span>
                  )}
                  {j.updated_at && <span className="text-slate-400">{ago(j.updated_at)}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
