import type { ExposureAssessment, ImpurityExposure } from '@/lib/api';
import { Info } from './icons';

function fmt(ug: number | undefined): string {
  if (ug === undefined) return '—';
  if (ug === 0) return '0';
  if (ug < 0.001) return ug.toExponential(1);
  return ug.toLocaleString(undefined, { maximumSignificantDigits: 3 });
}

function margin(m: number | null | undefined): string {
  if (m === null || m === undefined) return '—';
  return `${m.toLocaleString(undefined, { maximumSignificantDigits: m < 10 ? 2 : 3 })}×`;
}

const STATUS: Record<ImpurityExposure['status'], [string, string]> = {
  within: ['Within benchmark', 'border-precedented-line bg-precedented-soft text-precedented'],
  above: ['Above benchmark', 'border-alert-line bg-alert-soft text-alert'],
  not_computed: ['Not computed', 'border-slate-200 bg-slate-100 text-slate-500'],
};

export default function ExposureTable({ exposure: x }: { exposure: ExposureAssessment }) {
  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-200 px-5 py-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Impurity exposure</h2>
          <p className="text-sm text-slate-500">
            Intake per dosing day against the ICH M7 acceptable intake for one mutagenic impurity
          </p>
        </div>
        <div className="text-right">
          <div className="font-mono text-lg font-semibold text-slate-900">
            {x.acceptable_intake_ug_per_day} µg/day
          </div>
          <div className="text-xs text-slate-500">
            {x.duration_category} · {x.duration_basis}
          </div>
        </div>
      </div>

      <div className="border-b border-slate-200 bg-slate-50 px-5 py-2.5 text-sm text-slate-600">
        Excipient per dose:{' '}
        <span className="font-medium text-slate-800">
          {x.excipient_mg_per_dose !== null ? `${fmt(x.excipient_mg_per_dose)} mg` : 'unknown'}
        </span>{' '}
        <span className="text-slate-400">— {x.excipient_mass_basis}</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200">
              {/* Units stay out of the uppercase eyebrow style: CSS uppercases µ to
                  Greek capital mu, and "µg" then reads as "MG" — a 1000× misreading. */}
              {[
                ['Impurity', ''],
                ['Level', ''],
                ['Per dose', 'µg'],
                ['Per dosing day', 'µg'],
                ['Margin', ''],
                ['', ''],
              ].map(([h, unit], i) => (
                <th key={i} className="px-5 py-2.5">
                  <span className="eyebrow font-semibold">{h}</span>
                  {unit && <span className="ml-1 text-xs font-medium text-slate-500">({unit})</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {x.impurities.map((row) => {
              const [label, tone] = STATUS[row.status];
              return (
                <tr key={row.name} className="align-top">
                  <td className="px-5 py-3 font-medium text-slate-900">{row.name}</td>
                  <td className="px-5 py-3 font-mono text-slate-700">{row.level || '—'}</td>
                  {row.status === 'not_computed' ? (
                    <td colSpan={3} className="px-5 py-3 text-slate-500">
                      {row.reason}
                    </td>
                  ) : (
                    <>
                      <td className="px-5 py-3 font-mono text-slate-700">{fmt(row.ug_per_dose)}</td>
                      <td className="px-5 py-3 font-mono text-slate-700">{fmt(row.ug_per_dosing_day)}</td>
                      <td className="px-5 py-3 font-mono font-semibold text-slate-900">{margin(row.margin)}</td>
                    </>
                  )}
                  <td className="px-5 py-3">
                    <span className={`whitespace-nowrap rounded-md border px-2 py-0.5 text-xs font-semibold ${tone}`}>
                      {label}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex gap-2.5 border-t border-slate-200 px-5 py-3 text-xs leading-relaxed text-slate-500">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p>{x.basis}</p>
      </div>
    </section>
  );
}
