'use client';

import { useCallback, useEffect, useState } from 'react';
import { Spinner } from '@/components/icons';
import { approveSimulation, declineSimulation, getSimulations, type AdminJob } from '@/lib/api';

// The admin's queue: every user's simulations that are waiting or running. Each
// run is hours of paid GPU time, so nothing reaches the worker until it is
// approved here. Approving also starts the worker's RunPod pod when the API has
// RUNPOD_API_KEY and RUNPOD_POD_ID; the pod stops itself once the queue is empty.

function stateOf(j: AdminJob): [string, string] {
  if (j.status === 'simulating')
    return ['Running', j.simulation?.progress || 'starting'];
  if (!j.simulation?.structure_id) return ['No structure', 'The user has not named the biologic yet'];
  if (!j.simulation?.approved_at) return ['Awaiting approval', ''];
  return ['Approved', 'Waiting for the worker'];
}

export default function Approvals() {
  const [jobs, setJobs] = useState<AdminJob[] | null>(null);
  const [autostart, setAutostart] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

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

  const act = async (j: AdminJob, kind: 'approve' | 'decline') => {
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
      if (kind === 'approve') setNotice(`Approved ${j.name}. Worker pod: ${await approveSimulation(j.id)}.`);
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
          Each run takes about 3 to 6 GPU hours. Nothing reaches the worker until you approve it, and each user
          can have one simulation waiting or running at a time.
          {!autostart && ' The API has no RunPod key, so start the worker’s pod by hand after approving.'}
        </p>
      </div>

      {error && <p className="rounded-lg border border-alert-line bg-alert-soft px-4 py-3 text-sm text-slate-800">{error}</p>}
      {notice && <p className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">{notice}</p>}

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
                          onClick={() => act(j, 'approve')}
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
