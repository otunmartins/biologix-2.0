'use client';

import { useMemo, useState } from 'react';
import type { Dossier, Verdict } from '@/lib/api';
import { GRADE_MEANING, VERDICTS, overview, sortLiabilities } from '@/lib/screen';
import ExposureTable from './ExposureTable';
import { GradeBox, SeverityTag, VERDICT_STYLE, VerdictPill } from './badges';
import { CheckCircle, Chevron, Download, Refresh, Triangle } from './icons';
import Structure, { Smiles } from './Structure';

// What the backend actually screened, from its own record of the run.
const BASIS: Record<string, { label: string; ok: boolean }> = {
  pubchem: { label: 'PubChem structure', ok: true },
  polymer_description: { label: 'Described polymer · structural grades ≤ C', ok: true },
  surrogate: { label: 'Stand-in structure · structural grades ≤ D', ok: false },
  unresolved: { label: 'Not resolved · structural grades E', ok: false },
};

interface Props {
  dossier: Dossier;
  // What was sent, so the header can say it; null for a plain-English run.
  context: string[] | null;
  onRerun: () => void;
  loading: boolean;
}

function Tile({
  step,
  title,
  value,
  sub,
  ok,
}: {
  step: number;
  title: string;
  value: string;
  sub: string;
  ok: boolean;
}) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-card">
      {ok ? (
        <CheckCircle className="mt-0.5 h-6 w-6 shrink-0 text-precedented" />
      ) : (
        <Triangle className="mt-0.5 h-6 w-6 shrink-0 text-gap" />
      )}
      <div className="min-w-0">
        <div className="text-[13px] font-semibold text-slate-500">
          {step}. {title}
        </div>
        <div className="truncate text-[15px] font-semibold text-slate-900" title={value}>
          {value}
        </div>
        <div className="truncate text-[13px] text-slate-500" title={sub}>
          {sub}
        </div>
      </div>
    </div>
  );
}

export default function Results({ dossier: d, context, onRerun, loading }: Props) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const [filter, setFilter] = useState<Verdict | null>(null);
  const ov = useMemo(() => overview(d), [d]);
  const liabilities = useMemo(() => sortLiabilities(d.liabilities), [d]);

  const rows = d.endpoints
    .map((e, i) => ({ e, i }))
    .filter(({ e }) => !filter || e.verdict === filter);

  const precedentBest = ov.precedent.reduce<(typeof ov.precedent)[number] | null>(
    (best, e) => (!best || e.evidence_grade < best.evidence_grade ? e : best),
    null,
  );
  const precedentSources = new Set(ov.precedent.flatMap((e) => e.sources)).size;
  const actionable = ov.counts['Data gap: test'] + ov.counts['Alert: avoid'];

  function downloadJson() {
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `dossier-${(d.excipient || 'screen').replace(/\W+/g, '-').toLowerCase()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="flex min-h-full flex-col">
      <div className="flex-1 space-y-6 px-6 py-7 lg:px-10">
        <header className="flex items-start justify-between gap-6">
          <div className="min-w-0">
            <div className="eyebrow">Single screen</div>
            {/* The agent writes `protein` freely; a long one reads better below the title. */}
            {d.protein.length <= 32 ? (
              <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-slate-900 sm:text-[28px]">
                {d.excipient} <span className="font-normal text-slate-400">×</span> {d.protein}
              </h1>
            ) : (
              <>
                <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-slate-900 sm:text-[28px]">
                  {d.excipient}
                </h1>
                <p className="mt-1 text-[17px] font-medium text-slate-700">
                  <span className="text-slate-400">×</span> {d.protein}
                </p>
              </>
            )}
            <p className="mt-1.5 text-[15px] text-slate-500">
              {(context ?? [d.route]).join(' · ')}
            </p>
          </div>
          {d.structure_smiles && (
            <div className="hidden w-[200px] shrink-0 space-y-1.5 sm:block">
              <Structure
                smiles={d.structure_smiles}
                width={200}
                height={140}
                label={`Structure screened for ${d.excipient}`}
              />
              <Smiles smiles={d.structure_smiles} width={200} />
            </div>
          )}
        </header>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Tile
            step={1}
            title="Identity"
            value={d.excipient}
            sub={BASIS[d.structure_basis]?.label ?? `against ${d.protein}`}
            ok={BASIS[d.structure_basis]?.ok ?? true}
          />
          <Tile
            step={2}
            title="Precedent"
            value={
              precedentBest
                ? `Grade ${precedentBest.evidence_grade} · ${precedentBest.verdict}`
                : 'Not reported'
            }
            sub={
              precedentBest
                ? precedentSources
                  ? `${precedentSources} source${precedentSources === 1 ? '' : 's'} cited · ${d.route}`
                  : `at ${d.route}`
                : 'No precedent endpoint in this dossier'
            }
            ok={precedentBest?.verdict === 'Precedented'}
          />
          <Tile
            step={3}
            title="Hazard"
            value={`${d.endpoints.length} endpoint${d.endpoints.length === 1 ? '' : 's'}`}
            sub={actionable ? `${actionable} need${actionable === 1 ? 's' : ''} action` : 'none flagged'}
            ok={actionable === 0}
          />
          <Tile
            step={4}
            title="Liability map"
            value={`${d.liabilities.length} flag${d.liabilities.length === 1 ? '' : 's'}`}
            sub={
              d.liabilities.length === 0
                ? 'no alerts triggered'
                : `${ov.high} high · ${ov.modelled ? 'accessibility-weighted' : 'sequence counts only'}`
            }
            ok={ov.high === 0}
          />
        </div>

        <div
          className={`flex gap-3 rounded-xl border px-4 py-3.5 text-[15px] leading-relaxed ${
            d.needs_testing
              ? 'border-gap-line bg-gap-soft text-slate-800'
              : 'border-precedented-line bg-precedented-soft text-slate-800'
          }`}
        >
          {d.needs_testing ? (
            <Triangle className="mt-0.5 h-5 w-5 shrink-0 text-gap" />
          ) : (
            <CheckCircle className="mt-0.5 h-5 w-5 shrink-0 text-precedented" />
          )}
          <div>
            <b className="font-semibold">
              {d.needs_testing ? 'Needs testing. ' : 'No testing demanded by these results alone. '}
            </b>
            {d.needs_testing
              ? 'At least one endpoint is below grade B, or a high-severity liability was flagged.'
              : 'That is not a safety conclusion.'}
            <p className="mt-2 text-slate-600">{d.summary}</p>
          </div>
        </div>

        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-3 border-b border-slate-200 px-5 py-4">
            <div className="mr-auto">
              <h2 className="text-lg font-semibold text-slate-900">Verdict matrix</h2>
              <p className="text-sm text-slate-500">No overall score — each endpoint stands alone</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {VERDICTS.filter((v) => ov.counts[v] > 0).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setFilter(filter === v ? null : v)}
                  aria-pressed={filter === v}
                  className={`rounded-full transition ${
                    filter && filter !== v ? 'opacity-40 hover:opacity-70' : ''
                  }`}
                  title={filter === v ? 'Show all' : `Show only ${v}`}
                >
                  <VerdictPill verdict={v} count={ov.counts[v]} />
                </button>
              ))}
            </div>
          </div>

          <div className="hidden grid-cols-[2.5rem_minmax(0,1.3fr)_minmax(0,1fr)_3.5rem_minmax(0,2.2fr)] gap-x-4 border-b border-slate-200 bg-slate-50 px-5 py-2.5 md:grid">
            <span />
            <span className="eyebrow">Endpoint</span>
            <span className="eyebrow">Verdict</span>
            <span className="eyebrow">Grade</span>
            <span className="eyebrow">Basis</span>
          </div>

          <ul className="divide-y divide-slate-200">
            {rows.map(({ e, i }) => {
              const isOpen = open[i] ?? false;
              const toggle = () => setOpen((o) => ({ ...o, [i]: !isOpen }));
              return (
                <li key={i} className={isOpen ? 'bg-slate-50/60' : ''}>
                  <button
                    type="button"
                    onClick={toggle}
                    aria-expanded={isOpen}
                    className="grid w-full grid-cols-[2.5rem_minmax(0,1fr)_auto] items-start gap-x-4 gap-y-2 px-5 py-4 text-left transition hover:bg-slate-50 md:grid-cols-[2.5rem_minmax(0,1.3fr)_minmax(0,1fr)_3.5rem_minmax(0,2.2fr)]"
                  >
                    <Chevron
                      className={`mt-1 h-5 w-5 text-slate-400 transition-transform ${isOpen ? 'rotate-90' : ''}`}
                    />
                    <div>
                      <div className="font-semibold text-slate-900">{e.endpoint}</div>
                      <div className="text-sm text-slate-500">
                        {e.sources.length
                          ? `${e.sources.length} source${e.sources.length === 1 ? '' : 's'}`
                          : 'no sources cited'}
                      </div>
                    </div>
                    <div className="col-start-2 md:col-start-auto">
                      <VerdictPill verdict={e.verdict} />
                    </div>
                    <div className="col-start-3 row-start-1 md:col-start-auto md:row-start-auto">
                      <GradeBox grade={e.evidence_grade} />
                    </div>
                    <p
                      className={`col-span-2 col-start-2 text-[15px] leading-relaxed text-slate-700 md:col-span-1 md:col-start-auto ${
                        isOpen ? '' : 'line-clamp-2'
                      }`}
                    >
                      {e.rationale}
                    </p>
                  </button>
                  {isOpen && (
                    <div className="space-y-3 pb-5 pl-5 pr-5 md:pl-[4.5rem]">
                      <div className="flex items-center gap-2 text-sm text-slate-600">
                        <span className={`font-semibold ${VERDICT_STYLE[e.verdict].text}`}>
                          Grade {e.evidence_grade}
                        </span>
                        <span className="text-slate-300">—</span>
                        {GRADE_MEANING[e.evidence_grade]}
                      </div>
                      {e.sources.length > 0 && (
                        <div>
                          <div className="eyebrow mb-1.5">Sources</div>
                          <ul className="space-y-1">
                            {e.sources.map((s, j) => (
                              <li
                                key={j}
                                className="rounded-md border border-slate-200 bg-white px-3 py-1.5 font-mono text-[13px] text-slate-700"
                              >
                                {s}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </section>

        {d.exposure && <ExposureTable exposure={d.exposure} />}

        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
          <div className="border-b border-slate-200 px-5 py-4">
            <h2 className="text-lg font-semibold text-slate-900">Protein liabilities</h2>
            <p className="text-sm text-slate-500">
              Residues the excipient&rsquo;s reactive groups can reach, most severe first
            </p>
          </div>
          {liabilities.length ? (
            <ul className="divide-y divide-slate-200">
              {liabilities.map((l, i) => {
                const modelled = !l.accessibility.startsWith('not modelled');
                return (
                  <li key={i} className="grid gap-x-6 gap-y-2 px-5 py-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-[15px] font-semibold text-slate-900">{l.residue}</span>
                        <SeverityTag severity={l.severity} />
                      </div>
                      {l.source && l.source !== 'excipient' && (
                        <div className="mt-1.5 inline-block rounded-md border border-gap-line bg-gap-soft px-2 py-0.5 text-xs font-medium text-gap">
                          from {l.source}
                        </div>
                      )}
                      <div className="mt-1 text-sm text-slate-600">{l.reaction_class}</div>
                      <div
                        className={`mt-2 inline-block rounded-md px-2 py-1 text-xs ${
                          modelled ? 'bg-supported-soft text-supported' : 'bg-slate-100 text-slate-500'
                        }`}
                      >
                        {l.accessibility}
                      </div>
                    </div>
                    <div>
                      <div className="eyebrow mb-1">Mitigation</div>
                      <p className="text-[15px] leading-relaxed text-slate-700">{l.mitigation}</p>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="px-5 py-6 text-[15px] text-slate-600">
              No liabilities triggered by the structural alerts found. Absence of a flag is not evidence of
              compatibility — the rule table is small.
            </p>
          )}
        </section>

        <p className="pb-2 text-center text-xs text-slate-500">
          Not a safety certification. Verdicts are limited to Precedented, Supported without precedent,
          Data gap: test, and Alert: avoid. A human checkpoint is required before any of this reaches a
          dossier.
        </p>
      </div>

      <div className="sticky bottom-0 flex flex-wrap items-center justify-end gap-3 border-t border-slate-200 bg-white/90 px-6 py-3 backdrop-blur lg:px-10">
        <button type="button" className="btn-ghost" onClick={onRerun} disabled={loading}>
          <Refresh className="h-4 w-4" />
          Re-run
        </button>
        <button type="button" className="btn-ghost" onClick={downloadJson}>
          <Download className="h-4 w-4" />
          Download JSON
        </button>
      </div>
    </div>
  );
}
