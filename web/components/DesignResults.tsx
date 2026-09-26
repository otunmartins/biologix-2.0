'use client';

import { useState } from 'react';
import type { CandidateStatus, DesignGoal, StoredCandidate } from '@/lib/api';
import { GradeBox } from './badges';
import { CheckCircle, Chevron, Flask, Info, Triangle } from './icons';
import Structure from './Structure';

interface Props {
  goal: DesignGoal;
  candidates: StoredCandidate[];
  limits: string;
  verdict: string;
  // Hands the candidate to the screening form, which is what actually judges it.
  onScreen: (c: StoredCandidate) => void;
  // Sends the candidate to the OpenMM simulation backlog.
  onQueue: (c: StoredCandidate) => void;
  queueing: string | null;
}

function GoalChips({ goal: g }: { goal: DesignGoal }) {
  const chips = [
    g.protein,
    g.route,
    g.target_temp_c !== null ? `${g.target_temp_c} °C` : 'temperature not stated',
    g.duration_months !== null ? `${g.duration_months} months` : null,
    g.format,
    g.exposed_residues.length ? `exposed ${g.exposed_residues.join(', ')}` : null,
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

function CandidateStructures({ c }: { c: StoredCandidate }) {
  const units = repeatUnits(c);
  return (
    <div className="space-y-3">
      <div>
        <div className="eyebrow mb-2">{units.length > 1 ? 'Repeat units' : 'Repeat unit'}</div>
        <div className="flex flex-wrap gap-3">
          {units.map((u) => (
            <figure key={u.smiles} className="space-y-1.5">
              <Structure smiles={u.smiles} width={220} height={160} label={`Repeat unit of ${c.name}`} />
              {u.label && <figcaption className="text-center text-xs text-slate-500">{u.label}</figcaption>}
            </figure>
          ))}
        </div>
      </div>
      {c.screened_oligomer_smiles && (
        <div>
          <div className="eyebrow mb-2">Screened as</div>
          <Structure
            smiles={c.screened_oligomer_smiles}
            width={560}
            height={200}
            label={`The ${c.screened_units}-unit chain the alerts were run on`}
          />
          <p className="mt-1.5 text-xs leading-relaxed text-slate-500">
            The {c.screened_units}-unit chain the structural alerts were actually run on, end groups included.
          </p>
        </div>
      )}
    </div>
  );
}

function CandidateCard({
  c,
  defaultOpen,
  onScreen,
  onQueue,
  queueing,
}: {
  c: StoredCandidate;
  defaultOpen: boolean;
  onScreen: () => void;
  onQueue: () => void;
  queueing: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const clean = c.alerts_fired.length === 0;
  const queued = c.status !== 'benchmarked';

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
          </span>
        </span>
        <GradeBox grade="D" />
      </button>

      {open && (
        <div className="space-y-4 border-t border-slate-200 px-5 py-4 md:pl-[3.25rem]">
          <p className="text-[15px] leading-relaxed text-slate-700">{c.mechanism}</p>

          <CandidateStructures c={c} />

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

          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-ghost" onClick={onScreen}>
              Screen this candidate →
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={onQueue}
              disabled={queued || queueing}
            >
              {queued ? 'Queued for simulation ✓' : queueing ? 'Queuing…' : 'Queue for simulation'}
            </button>
          </div>
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
  queueing,
}: Props) {
  // Newest iteration first; within an iteration, highest score first.
  const iterations = Array.from(new Set(candidates.map((c) => c.iteration))).sort((a, b) => b - a);
  const topId = candidates.find((c) => c.iteration === iterations[0])?.id;

  return (
    <div className="space-y-6 px-6 py-7 lg:px-10">
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
                  onScreen={() => onScreen(c)}
                  onQueue={() => onQueue(c)}
                  queueing={queueing === c.id}
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
