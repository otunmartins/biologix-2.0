'use client';

import { useState } from 'react';
import {
  PRESETS,
  ROUTES,
  SAMPLE_SEQ,
  STRUCTURE_PRESETS,
  TEMPS,
  TRACKED,
  cleanSequence,
  sequenceStats,
  structureKind,
  type ScreenForm as Form,
} from '@/lib/screen';
import { Spinner } from './icons';

export type Mode = 'form' | 'text';

interface Props {
  mode: Mode;
  onMode: (m: Mode) => void;
  form: Form;
  onChange: (patch: Partial<Form>) => void;
  freeText: string;
  onFreeText: (t: string) => void;
  loading: boolean;
  elapsed: number;
  onRun: () => void;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-4 border-b border-slate-200 px-6 py-6 last:border-b-0">
      <h2 className="text-[15px] font-semibold text-slate-900">{title}</h2>
      {children}
    </section>
  );
}

function Tabs<T extends string>({
  value,
  onChange,
  items,
}: {
  value: T;
  onChange: (v: T) => void;
  items: { id: T; label: string; filled?: boolean }[];
}) {
  return (
    <div role="tablist" className="flex border-b border-slate-200">
      {items.map((it) => (
        <button
          key={it.id}
          role="tab"
          type="button"
          aria-selected={value === it.id}
          onClick={() => onChange(it.id)}
          className={`relative -mb-px flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-sm font-medium transition ${
            value === it.id
              ? 'border-slate-900 text-slate-900'
              : 'border-transparent text-slate-500 hover:text-slate-800'
          }`}
        >
          {it.label}
          {it.filled && (
            <span className="h-1.5 w-1.5 rounded-full bg-supported" aria-label="filled in" />
          )}
        </button>
      ))}
    </div>
  );
}

export default function ScreenForm(p: Props) {
  const { form, onChange } = p;
  const [proteinTab, setProteinTab] = useState<'structure' | 'sequence'>('structure');

  const seq = cleanSequence(form.sequence);
  const { counts, invalid } = sequenceStats(seq);
  const kind = structureKind(form.structureId);

  return (
    <div className="flex h-full flex-col">
      <div className="px-6 pt-5">
        <div className="grid grid-cols-2 rounded-lg bg-slate-100 p-1 text-sm font-medium">
          {(
            [
              ['form', 'Structured'],
              ['text', 'Plain English'],
            ] as const
          ).map(([m, label]) => (
            <button
              key={m}
              type="button"
              onClick={() => p.onMode(m)}
              className={`rounded-md py-1.5 transition ${
                p.mode === m ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {p.mode === 'text' ? (
          <Section title="Describe the screen">
            <label className="block">
              <span className="label">
                Question <span className="hint">— plain English; paste a sequence inline</span>
              </span>
              <textarea
                rows={9}
                className="input resize-y leading-relaxed"
                value={p.freeText}
                onChange={(e) => p.onFreeText(e.target.value)}
              />
            </label>
            <p className="hint leading-relaxed">
              Sent to the agent as written. Name the excipient, the route, and a UniProt accession or
              PDB ID if you want accessibility weighting.
            </p>
          </Section>
        ) : (
          <>
            <Section title="Excipient">
              <label className="block">
                <span className="label">
                  Name, CAS or SMILES
                </span>
                <input
                  className="input"
                  value={form.excipient}
                  onChange={(e) => onChange({ excipient: e.target.value })}
                />
              </label>
              <div className="flex flex-wrap gap-1.5">
                {PRESETS.map((name) => (
                  <button
                    key={name}
                    type="button"
                    className="chip"
                    data-on={form.excipient === name}
                    onClick={() => onChange({ excipient: name })}
                  >
                    {name}
                  </button>
                ))}
              </div>
              <label className="block">
                <span className="label">
                  Concentration <span className="hint">— optional</span>
                </span>
                <input
                  className="input"
                  value={form.concentration}
                  placeholder="0.02% w/v · 10 mg/mL · 5 mg per dose"
                  onChange={(e) => onChange({ concentration: e.target.value })}
                />
                <span className="hint mt-1.5 block">
                  Checked against the highest level FDA records at this route.
                </span>
              </label>
            </Section>

            <Section title="Protein">
              <Tabs
                value={proteinTab}
                onChange={setProteinTab}
                items={[
                  { id: 'structure', label: 'Structure ID', filled: !!kind },
                  { id: 'sequence', label: 'Sequence', filled: seq.length > 0 },
                ]}
              />

              {proteinTab === 'structure' ? (
                <div className="space-y-3">
                  <label className="block">
                    <span className="label">PDB ID or UniProt accession</span>
                    <input
                      className="input font-mono tracking-wide"
                      value={form.structureId}
                      placeholder="1IGT or P01857"
                      onChange={(e) => onChange({ structureId: e.target.value.toUpperCase() })}
                    />
                    <span className="hint mt-1.5 block">
                      {kind === 'pdb'
                        ? 'PDB ID → experimental structure from RCSB.'
                        : kind === 'uniprot'
                          ? 'UniProt accession → predicted AlphaFold model.'
                          : 'Optional. Without one, liabilities are raw sequence counts.'}
                    </span>
                  </label>
                  <div className="flex flex-wrap gap-1.5">
                    {STRUCTURE_PRESETS.map(([id, label]) => {
                      const on = form.structureId.trim().toUpperCase() === id;
                      return (
                        <button
                          key={id}
                          type="button"
                          className="chip"
                          data-on={on}
                          onClick={() => onChange({ structureId: on ? '' : id })}
                        >
                          <span className="font-mono">{id}</span>
                          <span className="text-slate-400"> · </span>
                          {label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <div className="space-y-3">
                  <label className="block">
                    <span className="label flex items-center justify-between">
                      <span>
                        One-letter code{' '}
                        <span className="hint">
                          — {seq.length ? `${seq.length} residues` : 'optional'}
                        </span>
                      </span>
                      <button
                        type="button"
                        className="text-xs font-medium text-supported hover:underline"
                        onClick={() => onChange({ sequence: form.sequence ? '' : SAMPLE_SEQ })}
                      >
                        {form.sequence ? 'Clear' : 'Use sample'}
                      </button>
                    </span>
                    <textarea
                      rows={5}
                      className="input resize-y break-all font-mono text-[13px] leading-relaxed"
                      value={form.sequence}
                      onChange={(e) => onChange({ sequence: e.target.value })}
                    />
                  </label>
                  {seq.length > 0 && (
                    <div className="grid grid-cols-3 gap-1.5">
                      {TRACKED.map(([one, three, why]) => (
                        <div
                          key={one}
                          title={`${three} — ${why}`}
                          className={`rounded-md border px-2 py-1.5 text-center text-xs ${
                            counts[one] > 0
                              ? 'border-gap-line bg-gap-soft text-gap'
                              : 'border-slate-200 bg-white text-slate-400'
                          }`}
                        >
                          {three} <b className="tabular-nums">{counts[one]}</b>
                        </div>
                      ))}
                    </div>
                  )}
                  {invalid.length > 0 && (
                    <p className="text-xs text-alert">Not valid one-letter codes: {invalid.join(', ')}</p>
                  )}
                </div>
              )}

              {kind && seq.length > 0 && (
                <p className="rounded-lg bg-slate-100 px-3 py-2 text-xs leading-relaxed text-slate-600">
                  Both given. The structure is not aligned to the sequence; accessibility is reported for
                  the structure.
                </p>
              )}
            </Section>

            <Section title="Context">
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="label">Route</span>
                  <select
                    className="input"
                    value={form.route}
                    onChange={(e) => onChange({ route: e.target.value })}
                  >
                    {ROUTES.map((r) => (
                      <option key={r}>{r}</option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="label">Protein dose</span>
                  <div className="relative">
                    <input
                      type="number"
                      min={0}
                      className="input pr-10"
                      value={form.dose}
                      onChange={(e) => onChange({ dose: e.target.value })}
                    />
                    <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-slate-400">
                      mg
                    </span>
                  </div>
                </label>
              </div>
              <div>
                <span className="label">Storage</span>
                <div className="grid grid-cols-3 gap-1.5">
                  {TEMPS.map((t) => {
                    const [deg, note] = t.split(' (');
                    return (
                      <button
                        key={t}
                        type="button"
                        onClick={() => onChange({ temp: t })}
                        className={`rounded-lg border px-2 py-2 text-center transition ${
                          form.temp === t
                            ? 'border-supported bg-supported-soft text-supported'
                            : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
                        }`}
                      >
                        <div className="text-sm font-semibold">{deg}</div>
                        <div className="text-[11px] opacity-80">{note.replace(')', '')}</div>
                      </button>
                    );
                  })}
                </div>
              </div>
            </Section>
          </>
        )}
      </div>

      <div className="border-t border-slate-200 bg-white px-6 py-4">
        <button type="button" className="btn-primary w-full py-2.5" onClick={p.onRun} disabled={p.loading}>
          {p.loading && <Spinner className="h-4 w-4" />}
          {p.loading ? `Screening… ${p.elapsed}s` : 'Run screen'}
        </button>
        <p className="mt-2 text-center text-xs text-slate-500">
          <span className="kbd">Ctrl</span> + <span className="kbd">Enter</span> from anywhere
        </p>
      </div>
    </div>
  );
}
