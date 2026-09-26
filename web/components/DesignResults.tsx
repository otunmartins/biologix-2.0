'use client';

import { useState } from 'react';
import type { Candidate, DesignResult } from '@/lib/api';
import { GradeBox } from './badges';
import { CheckCircle, Chevron, Flask, Info, Triangle } from './icons';

interface Props {
  result: DesignResult;
  // Hands the candidate to the screening form, which is what actually judges it.
  onScreen: (c: Candidate) => void;
}

function GoalChips({ result: r }: { result: DesignResult }) {
  const g = r.goal;
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

function CandidateCard({ c, onScreen }: { c: Candidate; onScreen: () => void }) {
  const [open, setOpen] = useState(c.rank === 1);
  const clean = c.alerts_fired.length === 0;

  return (
    <li className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 px-5 py-4 text-left transition hover:bg-slate-50"
      >
        <Chevron className={`mt-1 h-5 w-5 shrink-0 text-slate-400 transition-transform ${open ? 'rotate-90' : ''}`} />
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-slate-900 text-xs font-semibold text-white">
          {c.rank}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-semibold text-slate-900">{c.name}</span>
          <span className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-slate-500">
            <span>triage score {c.score}</span>
            {c.backbone_tg_c !== null && <span>Tg ≈ {c.backbone_tg_c} °C</span>}
            {c.charge !== 'neutral' && <span>{c.charge}</span>}
            <span className={clean ? 'text-precedented' : 'text-alert'}>
              {clean ? 'no alerts' : `${c.alerts_fired.length} alert${c.alerts_fired.length === 1 ? '' : 's'}`}
            </span>
          </span>
        </span>
        <GradeBox grade="D" />
      </button>

      {open && (
        <div className="space-y-4 border-t border-slate-200 px-5 py-4 md:pl-[4.25rem]">
          <p className="text-[15px] leading-relaxed text-slate-700">{c.mechanism}</p>

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

          <div>
            <div className="eyebrow mb-1.5">Structure screened</div>
            <div className="space-y-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
              <div className="break-all font-mono text-[12px] text-slate-700">
                repeat unit: {c.repeat_unit_smiles}
              </div>
              <div className="text-xs text-slate-500">
                screened as a {c.screened_units}-unit chain · MW {c.descriptors.mw} · cLogP{' '}
                {c.descriptors.clogp} · TPSA {c.descriptors.tpsa} · {c.descriptors.hydroxyls} OH
              </div>
            </div>
          </div>

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

          <button type="button" className="btn-ghost" onClick={onScreen}>
            Screen this candidate →
          </button>
        </div>
      )}
    </li>
  );
}

export default function DesignResults({ result, onScreen }: Props) {
  return (
    <div className="space-y-6 px-6 py-7 lg:px-10">
      <header>
        <div className="eyebrow">Candidate polymers</div>
        <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-slate-900 sm:text-[28px]">
          {result.candidates.length} candidates for laboratory triage
        </h1>
        <p className="mt-2 text-[15px] text-slate-500">
          Ranked from {result.n_motif_pairs_considered} motif pairings. Every candidate is grade D,
          verdict &ldquo;{result.verdict}&rdquo;.
        </p>
        <div className="mt-3">
          <GoalChips result={result} />
        </div>
      </header>

      <div className="flex gap-3 rounded-xl border border-gap-line bg-gap-soft px-4 py-3.5 text-[15px] leading-relaxed text-slate-800">
        <Triangle className="mt-0.5 h-5 w-5 shrink-0 text-gap" />
        <p>
          <b className="font-semibold">A ranking, not a prediction.</b> These are hypotheses to test,
          ordered by chemistry the app can see. None of them has been shown to stabilise anything.
        </p>
      </div>

      <ul className="space-y-3">
        {result.candidates.map((c) => (
          <CandidateCard key={c.name} c={c} onScreen={() => onScreen(c)} />
        ))}
      </ul>

      <div className="flex gap-2.5 rounded-xl border border-slate-200 bg-white px-5 py-4 text-xs leading-relaxed text-slate-500 shadow-card">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p>{result.limits}</p>
      </div>
    </div>
  );
}
