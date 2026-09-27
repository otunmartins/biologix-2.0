'use client';

import { useCallback, useEffect, useState } from 'react';
import { Spinner } from '@/components/icons';
import { approveSimulation, declineSimulation, getSimulations, type AdminJob, type SimTier } from '@/lib/api';

// The admin's queue: every user's simulations that are waiting or running. Each
// run is hours of paid GPU time, so nothing reaches the worker until it is
// approved here. Approving also starts the worker's RunPod pod when the API has
// RUNPOD_API_KEY and RUNPOD_POD_ID; the pod stops itself once the queue is empty.

function stateOf(j: AdminJob): [string, string] {
  if (j.status === 'simulating')
    return ['Running', j.simulation?.progress || 'starting'];
  if (!j.simulation?.structure_id) return ['No structure', 'The user has not named the biologic yet'];
  if (!j.simulation?.approved_at) return ['Awaiting approval', ''];
  return [`Approved as ${j.simulation.tier === 'cpu' ? 'a CPU preview' : 'a GPU run'}`,
    `Waiting for a ${j.simulation.tier === 'cpu' ? 'CPU' : 'GPU'} worker`];
}

// Approving is also choosing where it runs: the full run on the GPU, or a short
// preview on a CPU when no GPU is available.
function TierChoice({
  job,
  onPick,
  onCancel,
}: {
  job: AdminJob;
  onPick: (tier: SimTier) => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onCancel();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);
  const options: [SimTier, string, string][] = [
    ['gpu', 'Full GPU run',
      '20 ns on the RunPod GPU, about 3 to 6 hours. Starts the pod; roughly $1 to $4 of GPU time. Recorded as a measurement (grade D).'],
    ['cpu', 'CPU preview',
      'A short run on a CPU worker, no GPU cost. Hours on a CPU. Not converged: shown on the candidate, never recorded as a measurement.'],
  ];
  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center bg-slate-900/40 sm:items-center sm:p-6" onClick={onCancel} role="presentation">
      <div role="dialog" aria-modal="true" aria-label="Approve simulation" onClick={(e) => e.stopPropagation()}
        className="w-full max-w-lg rounded-t-2xl bg-white p-5 shadow-xl sm:rounded-2xl">
        <div className="eyebrow">Approve</div>
        <h2 className="mt-1 text-lg font-semibold text-slate-900">{job.name}</h2>
        <p className="mt-0.5 text-sm text-slate-500">
          {job.owner_email ?? 'unknown user'} · against {job.simulation?.structure_id}
        </p>
        <div className="mt-4 space-y-2.5">
          {options.map(([tier, title, body]) => (
            <button key={tier} type="button" onClick={() => onPick(tier)}
              className="w-full rounded-xl border border-slate-200 p-4 text-left transition hover:border-slate-900">
              <div className="font-semibold text-slate-900">{title}</div>
              <p className="mt-1 text-sm leading-relaxed text-slate-600">{body}</p>
            </button>
          ))}
        </div>
        <div className="mt-4 text-right">
          <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

export default function Approvals() {
  const [jobs, setJobs] = useState<AdminJob[] | null>(null);
  const [autostart, setAutostart] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [choosing, setChoosing] = useState<AdminJob | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const r = await getSimulations(signal);
      setJobs(r.jobs);
      setAutostart(r.pod_autostart);
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    const ctl = new AbortController();
    load(ctl.signal);
    const t = setInterval(() => load(ctl.signal), 30000);
    return () => {
      clearInterval(t);
      ctl.abort();
    };
  }, [load]);

  const act = async (j: AdminJob, kind: 'approve' | 'decline', tier: SimTier = 'gpu') => {
    let reason = '';
    if (kind === 'decline') {
      const r = window.prompt(`Why not run ${j.name}? The user sees this.`, 'Not approved for GPU time right now');
      if (r === null) return;
      reason = r.trim() || 'declined by an admin';
    }
    setBusy(j.id);
    setError(null);
    setNotice(null);
    try {
      if (kind === 'approve')
        setNotice(`Approved ${j.name} as a ${tier === 'cpu' ? 'CPU preview' : 'GPU run'}. Worker: ${await approveSimulation(j.id, tier)}.`);
      else await declineSimulation(j.id, reason);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mx-auto w-full max-w-4xl space-y-5 px-4 py-7 sm:px-6 lg:px-10">
      <div>
        <div className="eyebrow">Admin</div>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-900">Simulation approvals</h1>
        <p className="mt-2 text-sm leading-relaxed text-slate-600">
          Nothing reaches a worker until you approve it, and each user can have one simulation waiting or running
          at a time. Approve each as a full GPU run (about 3 to 6 GPU hours) or, when no GPU is available, a short
          CPU preview.
          {!autostart && ' The API has no RunPod key, so start the worker’s pod by hand after approving a GPU run.'}
        </p>
      </div>

      {error && <p className="rounded-lg border border-alert-line bg-alert-soft px-4 py-3 text-sm text-slate-800">{error}</p>}
      {notice && <p className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">{notice}</p>}

      {choosing && (
        <TierChoice
          job={choosing}
          onCancel={() => setChoosing(null)}
          onPick={(tier) => {
            const j = choosing;
            setChoosing(null);
            act(j, 'approve', tier);
          }}
        />
      )}

      {jobs === null ? (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <Spinner className="h-4 w-4" /> Loading
        </div>
      ) : jobs.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing waiting or running.</p>
      ) : (
        <ul className="space-y-3">
          {jobs.map((j) => {
            const [label, detail] = stateOf(j);
            const waiting = j.status === 'queued';
            return (
              <li key={j.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-card">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-semibold text-slate-900">{j.name}</div>
                    <div className="mt-0.5 break-words text-sm text-slate-500">
                      {j.owner_email ?? 'unknown user'} · against {j.simulation?.structure_id || '—'} · score{' '}
                      {j.score.toFixed(1)}
                    </div>
                    <div className="mt-1.5 text-sm text-slate-700">
                      <b className="font-medium">{label}</b>
                      {detail && <span className="text-slate-500">: {detail}</span>}
                    </div>
                  </div>
                  {waiting && (
                    <div className="flex shrink-0 gap-2">
                      {!j.simulation?.approved_at && j.simulation?.structure_id && (
                        <button
                          type="button"
                          disabled={busy === j.id}
                          onClick={() => setChoosing(j)}
                          className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
                        >
                          Approve
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={busy === j.id}
                        onClick={() => act(j, 'decline')}
                        className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      >
                        Decline
                      </button>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
