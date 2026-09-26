'use client';

import {
  IMPURITY_PRESETS,
  POLYMER_PRESETS,
  attachmentPoints,
  polymerFamily,
  presetPolymer,
  type PolymerForm,
} from '@/lib/screen';

interface Props {
  excipient: string;
  value: PolymerForm;
  onChange: (p: PolymerForm) => void;
}

function SmilesField({
  label,
  value,
  points,
  onChange,
}: {
  label: string;
  value: string;
  points: number;
  onChange: (v: string) => void;
}) {
  const found = attachmentPoints(value);
  const bad = value.trim() !== '' && found !== points;
  return (
    <label className="block">
      <span className="label flex items-baseline justify-between">
        {label}
        <span className={`text-[11px] font-normal ${bad ? 'text-alert' : 'text-slate-400'}`}>
          {points} × [*]{bad ? ` · found ${found}` : ''}
        </span>
      </span>
      <input
        className={`input font-mono text-[13px] ${bad ? 'border-alert focus:border-alert focus:ring-alert/10' : ''}`}
        value={value}
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

export default function PolymerPanel({ excipient, value: p, onChange }: Props) {
  const family = polymerFamily(excipient);
  const set = (patch: Partial<PolymerForm>) => onChange({ ...p, ...patch });

  if (!p.enabled && family === undefined) {
    return (
      <button
        type="button"
        className="text-xs font-medium text-slate-500 hover:text-supported"
        onClick={() => onChange({ ...p, enabled: true })}
      >
        Polymeric excipient? Describe the chain →
      </button>
    );
  }

  if (!p.enabled) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50/60 p-4">
        <p className="text-sm leading-relaxed text-slate-600">
          <b className="font-semibold text-slate-800">{excipient.trim()}</b> is a polymer. Without a
          description it is screened on a short stand-in and every structural finding is capped at
          grade D.
        </p>
        <button
          type="button"
          className="mt-3 text-sm font-semibold text-supported hover:underline"
          onClick={() =>
            onChange({ ...(family ? presetPolymer(family, p) : p), enabled: true })
          }
        >
          Describe the polymer →
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-xl border border-dashed border-slate-300 bg-slate-50/60 p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-slate-800">Polymer description</span>
        <button
          type="button"
          className="text-xs font-medium text-slate-500 hover:text-slate-800"
          onClick={() => set({ enabled: false })}
        >
          Use stand-in instead
        </button>
      </div>

      <div>
        <span className="label">Start from</span>
        <div className="flex flex-wrap gap-1.5">
          {POLYMER_PRESETS.map((x) => (
            <button
              key={x.id}
              type="button"
              className="chip"
              data-on={p.repeatUnit === x.repeatUnit && p.endA === x.endA && p.endB === x.endB}
              onClick={() => onChange(presetPolymer(x.id, p))}
            >
              {x.label}
            </button>
          ))}
        </div>
      </div>

      <SmilesField label="Repeat unit" value={p.repeatUnit} points={2} onChange={(v) => set({ repeatUnit: v })} />
      <div className="grid grid-cols-2 gap-3">
        <SmilesField label="Head end group" value={p.endA} points={1} onChange={(v) => set({ endA: v })} />
        <SmilesField label="Tail end group" value={p.endB} points={1} onChange={(v) => set({ endB: v })} />
      </div>

      <label className="block">
        <span className="label">
          Approx. DP <span className="hint">— number-average repeat units</span>
        </span>
        <input
          type="number"
          min={1}
          step="any"
          className="input"
          value={p.dp}
          placeholder="optional"
          onChange={(e) => set({ dp: e.target.value })}
        />
      </label>

      <div>
        <span className="label">
          Residuals &amp; degradants <span className="hint">— each screened as its own molecule</span>
        </span>
        <div className="space-y-2">
          {p.impurities.map((imp, i) => (
            <div key={i} className="grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto] gap-2">
              <input
                className="input py-1.5 text-sm"
                value={imp.name}
                placeholder="Name or CAS"
                onChange={(e) =>
                  set({ impurities: p.impurities.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })
                }
              />
              <input
                className="input py-1.5 text-sm"
                value={imp.level}
                placeholder="≤ 1 ppm"
                onChange={(e) =>
                  set({ impurities: p.impurities.map((x, j) => (j === i ? { ...x, level: e.target.value } : x)) })
                }
              />
              <button
                type="button"
                aria-label={`Remove ${imp.name || 'impurity'}`}
                className="rounded-lg px-2 text-lg leading-none text-slate-400 hover:bg-slate-200 hover:text-slate-700"
                onClick={() => set({ impurities: p.impurities.filter((_, j) => j !== i) })}
              >
                ×
              </button>
            </div>
          ))}
        </div>
        {p.impurities.length < 10 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {IMPURITY_PRESETS.filter((n) => !p.impurities.some((x) => x.name === n)).map((n) => (
              <button
                key={n}
                type="button"
                className="chip text-xs"
                onClick={() => set({ impurities: [...p.impurities, { name: n, level: '' }] })}
              >
                + {n}
              </button>
            ))}
            <button
              type="button"
              className="chip text-xs"
              onClick={() => set({ impurities: [...p.impurities, { name: '', level: '' }] })}
            >
              + Other
            </button>
          </div>
        )}
      </div>

      <p className="text-xs leading-relaxed text-slate-500">
        Structural findings on a described chain can reach grade C. Polydispersity and branching are not
        modelled. An impurity&rsquo;s level feeds its exposure margin; it never changes a severity.
      </p>
    </div>
  );
}
