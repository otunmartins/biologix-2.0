'use client';

import { useEffect, useState } from 'react';
import {
  resolveStructure,
  type CandidateStatus,
  type DesignGoal,
  type ResolvedStructure,
  type SimulationJob,
  type StoredCandidate,
} from '@/lib/api';
import { GradeBox } from './badges';
import { CheckCircle, Chevron, Flask, Info, Triangle } from './icons';
import MolViewer from '@/components/MolViewer';
import Structure, { Smiles } from './Structure';
import { formatCondition, planned } from '@/lib/conditions';
import type { ConditionKey } from '@/lib/api';

interface Props {
  goal: DesignGoal;
  candidates: StoredCandidate[];
  limits: string;
  verdict: string;
  // Hands the candidate to the screening form, which is what actually judges it.
  onScreen?: (c: StoredCandidate) => void;
  // Sends the candidate to the OpenMM simulation backlog, against the given
  // structure ('' when the campaign's goal already names one).
  onQueue?: (c: StoredCandidate, structureId: string) => void;
  queueing?: string | null;
  // What the user asked, for finding the biologic when the goal names none.
  prompt?: string;
  // Why the last queue attempt failed, shown on that candidate's card.
  queueError?: { id: string; message: string } | null;
  // History shows a campaign as it stood: no screening from there, though a
  // candidate can still be queued when onQueue is given.
  readOnly?: boolean;
}

export function GoalChips({ goal: g }: { goal: DesignGoal }) {
  const chips = [
    g.protein,
    g.route,
    g.target_temp_c !== null ? `${g.target_temp_c} °C` : 'temperature not stated',
    g.duration_months !== null ? `${g.duration_months} months` : null,
    g.format,
    g.exposed_residues.length ? `exposed ${g.exposed_residues.join(', ')}` : null,
    g.ph != null ? `pH ${g.ph}` : null,
    g.salt_mm != null ? `${g.salt_mm} mM salt` : null,
    g.polymer_wv_percent != null ? `${g.polymer_wv_percent}% w/v polymer` : null,
    g.structure_id ? `structure ${g.structure_id}` : null,
  ].filter(Boolean) as string[];
  return (
    <div className="flex flex-wrap gap-1.5">
      {chips.map((c) => (
        <span
          key={c}
          className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[13px] text-slate-600"
        >
          {c}
        </span>
      ))}
    </div>
  );
}

const STATUS_STYLE: Record<CandidateStatus, [string, string]> = {
  proposed: ['border-slate-200 bg-slate-100 text-slate-500', 'proposed'],
  benchmarked: ['border-slate-200 bg-slate-100 text-slate-600', 'benchmarked'],
  queued: ['border-supported-line bg-supported-soft text-supported', 'queued'],
  simulating: ['border-supported-line bg-supported-soft text-supported', 'simulating'],
  simulated: ['border-precedented-line bg-precedented-soft text-precedented', 'simulated'],
  failed: ['border-alert-line bg-alert-soft text-alert', 'failed'],
};

function StatusBadge({ status }: { status: CandidateStatus }) {
  const [cls, label] = STATUS_STYLE[status];
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${cls}`}>
      {label}
    </span>
  );
}

// Copolymers carry one repeat unit per pendant; a homopolymer, or a candidate
// stored before composition existed, has the single joined SMILES only.
function repeatUnits(c: StoredCandidate): { smiles: string; label: string | null }[] {
  if (c.composition.length > 0) {
    return c.composition.map((p) => ({
      smiles: p.repeat_unit_smiles,
      label: c.composition.length > 1 ? `${p.pendant.split(' (')[0]} · ${Math.round(p.fraction * 100)}%` : null,
    }));
  }
  return c.repeat_unit_smiles.split(' ; ').map((smiles) => ({ smiles, label: null }));
}

function CandidateStructures({ c, structureId }: { c: StoredCandidate; structureId?: string }) {
  const units = repeatUnits(c);
  const [show3d, setShow3d] = useState(false);
  // Once a run has left its last frame, the simulation panel shows the real thing.
  const simulated = c.status === 'simulated' && c.simulation?.result?.has_snapshot;
  return (
    <div className="space-y-3">
      {c.screened_oligomer_smiles && !simulated && (
        <div>
          <button type="button" className="btn-ghost" onClick={() => setShow3d(!show3d)} aria-expanded={show3d}>
            {show3d ? 'Hide 3D view' : structureId ? `View the polymer and ${structureId} in 3D` : 'View the polymer in 3D'}
          </button>
          {show3d && (
            <div className="mt-3">
              <MolViewer structureId={structureId || undefined} polymerSmiles={c.screened_oligomer_smiles} />
            </div>
          )}
        </div>
      )}
      <div>
        <div className="eyebrow mb-2">{units.length > 1 ? 'Repeat units' : 'Repeat unit'}</div>
        <div className="flex flex-wrap gap-3">
          {units.map((u) => (
            <figure key={u.smiles} className="space-y-1.5">
              <Structure smiles={u.smiles} width={220} height={160} label={`Repeat unit of ${c.name}`} />
              {u.label && <figcaption className="text-center text-xs text-slate-500">{u.label}</figcaption>}
              <Smiles smiles={u.smiles} width={220} />
            </figure>
          ))}
        </div>
      </div>
      {c.screened_oligomer_smiles && (
        <details className="group">
          <summary className="eyebrow cursor-pointer select-none list-none">
            <span className="group-open:hidden">Show the full {c.screened_units}-unit chain it was screened as ▸</span>
            <span className="hidden group-open:inline">Screened as ▾</span>
          </summary>
          <div className="mt-2">
          <Structure
            smiles={c.screened_oligomer_smiles}
            width={560}
            height={200}
            label={`The ${c.screened_units}-unit chain the alerts were run on`}
          />
          <div className="mt-1.5">
            <Smiles smiles={c.screened_oligomer_smiles} width={560} />
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-slate-500">
            The {c.screened_units}-unit chain the structural alerts were actually run on, end groups included.
          </p>
          </div>
        </details>
      )}
    </div>
  );
}

// What a Gamma23 means, in words. Within two standard errors of zero is no
// preference either way: the run cannot tell the polymer from water.
function readGamma(g: number, se: number | null): { label: string; body: string; tone: string } {
  if (se !== null && Math.abs(g) < 2 * se) {
    return {
      label: 'No clear preference',
      body: 'Within two standard errors of zero: at this length of run the polymer is indistinguishable from water at the protein surface.',
      tone: 'text-slate-700',
    };
  }
  if (g < 0) {
    return {
      label: 'Excluded from the surface',
      body: 'The protein is preferentially hydrated: the polymer stays away from it. This is the signature of classic stabilisers such as sucrose and trehalose. It is consistent with stabilisation, not proof of it: that needs the unfolded state too, or an experiment.',
      tone: 'text-precedented',
    };
  }
  return {
    label: 'Accumulates at the surface',
    body: 'The polymer gathers at the protein more than water does. That is binding, which often destabilises, though a polymer that covers an aggregation-prone patch can still help. The residues it touches most are listed below.',
    tone: 'text-gap',
  };
}

const TIER_LABEL = { gpu: 'GPU run', cpu: 'CPU preview' } as const;

export function SimulationPanel({
  sim,
  status,
  candidateId,
}: {
  sim: SimulationJob;
  status: CandidateStatus;
  candidateId?: string;
}) {
  const r = sim.result;
  const [show3d, setShow3d] = useState(false);
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
      <div className="eyebrow mb-1.5">
        OpenMM simulation · against {sim.structure_id || 'no structure yet'}
        {sim.tier && ` · ${TIER_LABEL[sim.tier]}`}
      </div>
      {status === 'queued' && (
        <p className="text-sm leading-relaxed text-slate-600">
          {sim.structure_id && !sim.approved_at
            ? 'Waiting for approval. Each simulation takes hours of compute, so an admin approves it, as a full GPU run or a short CPU preview, before it runs.'
            : sim.structure_id && sim.tier === 'cpu'
            ? 'Approved as a CPU preview. Waiting for a CPU worker; approved runs go highest triage score first.'
            : sim.structure_id
            ? 'Approved as a GPU run. Waiting for the GPU worker; approved runs go highest triage score first.'
            : 'Queued before structures were asked for. Queue it again with the biologic’s PDB ID or UniProt accession to run it.'}
          {sim.error && <span className="mt-1 block text-slate-500">{sim.error}</span>}
        </p>
      )}
      {status === 'simulating' && (
        <p className="text-sm leading-relaxed text-slate-600">
          Running{sim.attempts > 1 ? ` (attempt ${sim.attempts})` : ''}: {sim.progress || 'starting'}.
        </p>
      )}
      {status === 'failed' && (
        <p className="text-sm leading-relaxed text-alert">
          Failed{sim.attempts > 1 ? ` after ${sim.attempts} attempts` : ''}: {sim.error}
        </p>
      )}
      {status === 'simulated' && r && (() => {
        const read = readGamma(r.gamma23, r.gamma23_se);
        return (
          <div className="space-y-2.5">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="font-mono text-xl font-semibold tabular-nums text-slate-900">
                Γ23 = {r.gamma23 > 0 ? '+' : ''}
                {r.gamma23}
                {r.gamma23_se !== null && <span className="text-base font-normal text-slate-500"> ± {r.gamma23_se}</span>}
              </span>
              <span className={`text-sm font-semibold ${read.tone}`}>{read.label}</span>
              {r.smoke && (
                <span className="rounded-full border border-gap-line bg-gap-soft px-2 py-0.5 text-[11px] font-semibold text-gap">
                  smoke test, not a measurement
                </span>
              )}
              {(r.preview || sim.tier === 'cpu') && !r.smoke && (
                <span className="rounded-full border border-gap-line bg-gap-soft px-2 py-0.5 text-[11px] font-semibold text-gap">
                  CPU preview, not converged
                </span>
              )}
            </div>
            <p className="text-sm leading-relaxed text-slate-600">{read.body}</p>
            {r.contacts.length > 0 && (
              <p className="text-sm leading-relaxed text-slate-600">
                <span className="font-medium text-slate-700">Most-contacted residues: </span>
                {r.contacts
                  .slice(0, 6)
                  .map((c) => `${c.residue} (${Math.round(c.fraction * 100)}%)`)
                  .join(', ')}
              </p>
            )}
            {Object.keys(r.gamma23_profile).length > 0 && (
              <p className="text-xs leading-relaxed text-slate-500">
                Γ23 by local-domain cutoff:{' '}
                {Object.entries(r.gamma23_profile)
                  .map(([nm, g]) => `${nm} nm ${g > 0 ? '+' : ''}${g}`)
                  .join(' · ')}
                . A value that has stopped changing with the cutoff has captured every perturbed chain.
              </p>
            )}
            <p className="text-xs leading-relaxed text-slate-500">
              {r.production_ns} ns at{' '}
              {r.conditions
                ? (Object.entries(r.conditions) as [ConditionKey, number][])
                    .map(([k, v]) => formatCondition(k, v))
                    .join(', ')
                : `${Math.round(r.temperature_k - 273.15)} °C`}
              , {r.n_chains} chains,{' '}
              {r.n_atoms.toLocaleString()} atoms, {r.n_frames} frames; {r.engine}; {r.forcefields}.{' '}
              {r.structure_source}. Chains per protein; the protein&rsquo;s backbone held to its native structure.
              {r.notes.length > 0 && ` ${r.notes.join('. ')}.`}
            </p>
          </div>
        );
      })()}
      {sim.structure_id && (
        <div className="mt-3 border-t border-slate-200 pt-3">
          <button type="button" className="btn-ghost" onClick={() => setShow3d(!show3d)} aria-expanded={show3d}>
            {show3d
              ? 'Hide 3D view'
              : r?.has_snapshot
                ? 'View the polymer around the protein in 3D'
                : `View ${sim.structure_id} in 3D`}
          </button>
          {show3d && (
            <div className="mt-3">
              <MolViewer
                structureId={sim.structure_id}
                candidateId={candidateId}
                hasSnapshot={status === 'simulated' && Boolean(r?.has_snapshot)}
                contacts={status === 'simulated' ? r?.contacts : []}
              />
              {status === 'simulated' && r && !r.has_snapshot && (
                <p className="mt-2 text-xs text-slate-500">
                  This run finished before the worker saved its last frame, so the polymer is not drawn; the residues it
                  touched most are.
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// The structure for the biologic the campaign names, looked up by the API
// (api/structures.py) once per name and remembered for the page's life.
const resolved = new Map<string, Promise<ResolvedStructure | null>>();

function useResolvedStructure(query: string): ResolvedStructure | null | undefined {
  const q = query.trim();
  const [hit, setHit] = useState<{ q: string; value: ResolvedStructure | null } | null>(null);
  useEffect(() => {
    if (!q) return;
    let live = true;
    if (!resolved.has(q)) resolved.set(q, resolveStructure(q).catch(() => null));
    resolved.get(q)!.then((value) => live && setHit({ q, value }));
    return () => {
      live = false;
    };
  }, [q]);
  if (!q) return null;
  return hit?.q === q ? hit.value : undefined; // undefined: still looking
}

// What is in the box, and why: the entry found for the named biologic, or that
// nothing was found and an ID is needed.
function StructureNote({
  protein,
  suggested,
  sid,
  onPick,
}: {
  protein: string;
  suggested: ResolvedStructure | null | undefined;
  sid: string;
  onPick: (id: string) => void;
}) {
  const name = protein.trim().slice(0, 80);
  if (!name) {
    return (
      <p className="w-full text-xs text-slate-500">
        The campaign does not name the biologic, so type its PDB ID or UniProt accession.
      </p>
    );
  }
  if (suggested === undefined) {
    return <p className="w-full text-xs text-slate-500">Looking up the structure of {name}…</p>;
  }
  if (suggested === null) {
    return (
      <p className="w-full text-xs text-slate-500">
        No structure of &ldquo;{name}&rdquo; alone was found in the PDB or UniProt. Type its PDB ID or UniProt
        accession.
      </p>
    );
  }
  if (sid.trim().toUpperCase() !== suggested.structure_id.toUpperCase()) return null;
  return (
    <div className="w-full space-y-1 text-xs leading-relaxed text-slate-500">
      <p>
        <b className="font-semibold text-slate-700">{suggested.structure_id}</b>
        {suggested.title && <> &middot; {suggested.title}</>} &middot; {suggested.source}. Found for &ldquo;{name}
        &rdquo;: {suggested.why}.
      </p>
      {suggested.alternatives.length > 0 && (
        <p>
          Others:{' '}
          {suggested.alternatives.map((a, i) => (
            <span key={a.structure_id}>
              {i > 0 && ', '}
              <button
                type="button"
                className="font-mono text-slate-700 underline decoration-slate-300 underline-offset-2 hover:decoration-slate-500"
                title={a.title}
                onClick={() => onPick(a.structure_id)}
              >
                {a.structure_id}
              </button>
            </span>
          ))}
          . Or type your own.
        </p>
      )}
    </div>
  );
}

// The conditions a simulation of this candidate would run at: the request's
// where it stated them, the default where it did not. Change them by saying so
// in the request.
function PlannedConditions({ goal }: { goal: DesignGoal }) {
  const conds = planned(goal);
  const unstated = conds.filter((x) => !x.fromRequest).length;
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-xs leading-relaxed text-slate-600">
      <span className="font-semibold text-slate-700">Simulated at </span>
      {conds.map((x, i) => (
        <span key={x.key}>
          {i > 0 && ', '}
          {x.fromRequest ? (
            <b className="font-semibold text-slate-800">{x.text}</b>
          ) : (
            <span>
              {x.text} <span className="text-slate-400">(default)</span>
            </span>
          )}
        </span>
      ))}
      .{' '}
      {unstated > 0
        ? 'Bold values come from your request; say a different temperature, pH, salt or polymer concentration in it to change the rest.'
        : 'All from your request.'}{' '}
      Shelf life, route and a freeze-dried form are not simulated: the run is nanoseconds, in solution.
    </div>
  );
}

// Queue (or re-queue) for simulation. Asks for the biologic's structure when the
// campaign's goal does not name one: the run is against that protein.
function QueueControl({
  status,
  needsStructure,
  protein,
  onQueue,
  queueing,
  error,
}: {
  status: CandidateStatus;
  needsStructure: boolean;
  protein: string;
  onQueue: (structureId: string) => void;
  queueing: boolean;
  error?: string;
}) {
  const suggested = useResolvedStructure(needsStructure ? protein : '');
  const [sid, setSid] = useState('');
  const [edited, setEdited] = useState(false);
  const [needsId, setNeedsId] = useState(false);
  // Fill the box with what was found, unless the user has typed their own.
  useEffect(() => {
    if (suggested && !edited) setSid(suggested.structure_id);
  }, [suggested, edited]);
  const canQueue = status === 'benchmarked' || status === 'failed';
  const refused = error && (
    <p className="w-full text-xs text-alert" role="alert">
      {error}
    </p>
  );
  if (!canQueue) {
    return (
      <>
        <button type="button" className="btn-ghost" disabled>
          {status === 'simulated' ? 'Simulated ✓' : status === 'simulating' ? 'Simulating…' : 'Queued for simulation ✓'}
        </button>
        {refused}
      </>
    );
  }
  const label = queueing ? 'Queuing…' : status === 'failed' ? 'Queue again' : 'Queue for simulation';
  if (!needsStructure) {
    return (
      <>
        <button type="button" className="btn-ghost" onClick={() => onQueue('')} disabled={queueing}>
          {label}
        </button>
        {refused}
      </>
    );
  }
  return (
    <form
      className="flex w-full flex-wrap items-end gap-2 sm:w-auto"
      onSubmit={(e) => {
        e.preventDefault();
        // Never a silently greyed-out button: say what is missing.
        if (sid.trim()) onQueue(sid.trim());
        else setNeedsId(true);
      }}
    >
      <label className="block text-xs font-medium text-slate-600">
        Your biologic&rsquo;s PDB ID or UniProt accession
        <input
          value={sid}
          onChange={(e) => {
            setSid(e.target.value);
            setEdited(true);
            setNeedsId(false);
          }}
          aria-invalid={needsId}
          placeholder="1IGT or P01857"
          maxLength={40}
          className="mt-1 block w-40 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 font-mono text-sm text-slate-900 placeholder:text-slate-400 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200"
        />
      </label>
      <button type="submit" className="btn-ghost" disabled={queueing}>
        {label}
      </button>
      <StructureNote protein={protein} suggested={suggested} sid={sid} onPick={(id) => { setSid(id); setEdited(true); }} />
      {needsId && (
        <p className="w-full text-xs text-alert" role="alert">
          A simulation runs the polymer against your protein, so it needs the protein&rsquo;s structure: type its PDB ID
          (e.g. 1IGT) or UniProt accession (e.g. P01857), then queue.
        </p>
      )}
      {refused}
    </form>
  );
}

function CandidateCard({
  c,
  defaultOpen,
  needsStructure,
  onScreen,
  onQueue,
  queueing,
  queueError,
  readOnly,
  structureId,
  protein,
  goal,
}: {
  c: StoredCandidate;
  defaultOpen: boolean;
  needsStructure: boolean;
  // The campaign's biologic, for the 3D view; a queued job's own structure wins.
  structureId?: string;
  onScreen: () => void;
  onQueue?: (structureId: string) => void;
  queueing: boolean;
  queueError?: string;
  readOnly?: boolean;
  protein: string;
  goal: DesignGoal;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const clean = c.alerts_fired.length === 0;
  const sim = c.simulation?.result;

  return (
    <li className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 px-5 py-4 text-left transition hover:bg-slate-50"
      >
        <Chevron className={`mt-1 h-5 w-5 shrink-0 text-slate-400 transition-transform ${open ? 'rotate-90' : ''}`} />
        <Structure
          smiles={repeatUnits(c)[0]?.smiles ?? ''}
          width={72}
          height={56}
          label={`Repeat unit of ${c.name}`}
          className="hidden sm:grid"
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-semibold text-slate-900">{c.name}</span>
            <StatusBadge status={c.status} />
          </span>
          <span className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-slate-500">
            <span>triage score {c.score}</span>
            {c.tg_c !== null && (
              <span title={c.tg_note}>
                Tg ≈ {c.tg_c} °C
                {c.tg_in_model_domain === false && ' (extrapolated)'}
              </span>
            )}
            {c.charge !== 'neutral' && <span>{c.charge}</span>}
            <span className={clean ? 'text-precedented' : 'text-alert'}>
              {clean ? 'no alerts' : `${c.alerts_fired.length} alert${c.alerts_fired.length === 1 ? '' : 's'}`}
            </span>
            {c.status === 'simulated' && sim && (
              <span className="font-mono tabular-nums text-slate-700">
                Γ23 {sim.gamma23 > 0 ? '+' : ''}
                {sim.gamma23}
                {sim.gamma23_se !== null && ` ± ${sim.gamma23_se}`}
              </span>
            )}
          </span>
        </span>
        <GradeBox grade="D" />
      </button>

      {open && (
        <div className="space-y-4 border-t border-slate-200 px-5 py-4 md:pl-[3.25rem]">
          <p className="text-[15px] leading-relaxed text-slate-700">{c.mechanism}</p>

          <CandidateStructures c={c} structureId={c.simulation?.structure_id || structureId} />

          {c.supports.length > 0 && (
            <ul className="space-y-1.5">
              {c.supports.map((s, i) => (
                <li key={i} className="flex gap-2 text-sm leading-relaxed text-slate-700">
                  <CheckCircle className="mt-0.5 h-4 w-4 shrink-0 text-precedented" />
                  {s}
                </li>
              ))}
            </ul>
          )}

          {c.risks.length > 0 && (
            <ul className="space-y-1.5">
              {c.risks.map((s, i) => (
                <li key={i} className="flex gap-2 text-sm leading-relaxed text-slate-700">
                  <Triangle className="mt-0.5 h-4 w-4 shrink-0 text-gap" />
                  {s}
                </li>
              ))}
            </ul>
          )}

          {c.tg_note && (
            <div>
              <div className="eyebrow mb-1.5">Glass transition</div>
              <p className="text-sm leading-relaxed text-slate-600">{c.tg_note}</p>
            </div>
          )}

          <div>
            <div className="eyebrow mb-1.5 flex items-center gap-1.5">
              <Flask className="h-4 w-4" /> Suggested experiments
            </div>
            <ul className="space-y-1.5">
              {c.suggested_experiments.map((e, i) => (
                <li key={i} className="flex gap-2 text-sm leading-relaxed text-slate-700">
                  <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-slate-400" />
                  {e}
                </li>
              ))}
            </ul>
          </div>

          {c.simulation && c.status !== 'benchmarked' && <SimulationPanel sim={c.simulation} status={c.status} candidateId={c.id} />}

          {onQueue && (c.status === 'benchmarked' || c.status === 'failed') && <PlannedConditions goal={goal} />}

          {(!readOnly || onQueue) && (
            <div className="flex flex-wrap items-end gap-2">
              {!readOnly && (
                <button type="button" className="btn-ghost" onClick={onScreen}>
                  Screen this candidate →
                </button>
              )}
              {onQueue && (
                <QueueControl
                  status={c.status}
                  needsStructure={needsStructure}
                  protein={protein}
                  onQueue={onQueue}
                  queueing={queueing}
                  error={queueError}
                />
              )}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export default function DesignResults({
  goal,
  candidates,
  limits,
  verdict,
  onScreen,
  onQueue,
  queueing = null,
  queueError = null,
  readOnly = false,
  prompt = '',
}: Props) {
  // Newest iteration first; within an iteration, highest score first.
  const iterations = Array.from(new Set(candidates.map((c) => c.iteration))).sort((a, b) => b - a);
  const topId = candidates.find((c) => c.iteration === iterations[0])?.id;

  return (
    <div className="space-y-6 px-6 py-7 lg:px-10">
      {/* In history the campaign's own header already says all this. */}
      {!readOnly && (
        <header>
          <div className="eyebrow">Candidate polymers</div>
          <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-slate-900 sm:text-[28px]">
            {candidates.length} candidates screened
          </h1>
          <p className="mt-2 text-[15px] text-slate-500">
            Copolymers proposed and screened over {iterations.length} iteration
            {iterations.length === 1 ? '' : 's'}. Every candidate is grade D, verdict &ldquo;{verdict}&rdquo;.
          </p>
          <div className="mt-3">
            <GoalChips goal={goal} />
          </div>
        </header>
      )}

      <div className="flex gap-3 rounded-xl border border-gap-line bg-gap-soft px-4 py-3.5 text-[15px] leading-relaxed text-slate-800">
        <Triangle className="mt-0.5 h-5 w-5 shrink-0 text-gap" />
        <p>
          <b className="font-semibold">A ranking, not a prediction.</b> The loop optimises a
          transparent triage score; the surrogate models that proxy, not stabilisation. None of these
          has been shown to stabilise anything.
        </p>
      </div>

      {iterations.map((it) => {
        const rows = candidates
          .filter((c) => c.iteration === it)
          .sort((a, b) => b.score - a.score);
        return (
          <section key={it} className="space-y-3">
            <div className="eyebrow">
              Iteration {it}
              {it === iterations[0] && <span className="ml-2 text-supported">newest</span>}
            </div>
            <ul className="space-y-3">
              {rows.map((c) => (
                <CandidateCard
                  key={c.id}
                  c={c}
                  defaultOpen={c.id === topId}
                  needsStructure={!goal.structure_id}
                  structureId={goal.structure_id}
                  protein={goal.protein || prompt}
                  goal={goal}
                  onScreen={() => onScreen?.(c)}
                  onQueue={onQueue && ((sid) => onQueue(c, sid))}
                  queueing={queueing === c.id}
                  queueError={queueError?.id === c.id ? queueError.message : undefined}
                  readOnly={readOnly}
                />
              ))}
            </ul>
          </section>
        );
      })}

      <div className="flex gap-2.5 rounded-xl border border-slate-200 bg-white px-5 py-4 text-xs leading-relaxed text-slate-500 shadow-card">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p>{limits}</p>
      </div>
    </div>
  );
}
