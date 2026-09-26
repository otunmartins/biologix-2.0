'use client';

import type { ScreenRecord } from '@/lib/api';
import { duration, fullStamp } from '@/lib/when';
import { CheckCircle, Triangle } from '../icons';
import Structure from '../Structure';

// "How this was produced": the part of a screen's record that makes it
// provenance rather than just a saved answer. Everything here comes from what
// the tools returned during the run (api/history.py), not from the dossier.

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-slate-500">{label}</dt>
      <dd className="mt-0.5 truncate text-sm text-slate-900">{children}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
      <h3 className="eyebrow mb-4">{title}</h3>
      {children}
    </section>
  );
}

const TOOL_NAMES: Record<string, string> = {
  resolve_identity: 'Identity',
  structural_alerts: 'Structural alerts',
  published_alert_screen: 'Published alerts',
  regulatory_precedent: 'Regulatory precedent',
  protein_liability_scan: 'Liability scan',
  final_result: 'Dossier',
};

// The tool sequence, as the model actually called it.
function Trace({ trace }: { trace: ScreenRecord['provenance']['tool_trace'] }) {
  return (
    <ol className="flex flex-wrap items-center gap-1.5 text-[13px]">
      {trace.map((t, i) => (
        <li key={i} className="flex items-center gap-1.5">
          {i > 0 && <span className="text-slate-300">→</span>}
          <span
            className={`rounded-full border px-2.5 py-0.5 ${
              t.tool === 'final_result'
                ? 'border-slate-900 bg-slate-900 text-white'
                : 'border-slate-200 bg-slate-50 text-slate-700'
            }`}
            title={JSON.stringify(t.args).slice(0, 400)}
          >
            {TOOL_NAMES[t.tool] ?? t.tool}
          </span>
        </li>
      ))}
    </ol>
  );
}

function Identity({ call }: { call: Record<string, any> }) {
  const surrogate = call.surrogate || call.structure_basis === 'surrogate';
  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-4 sm:flex-row">
        {call.smiles && <Structure smiles={call.smiles} width={180} height={130} label="Structure screened" />}
        <dl className="grid min-w-0 flex-1 grid-cols-2 gap-x-4 gap-y-3">
          <Fact label="Source">
            <span title={call.source}>{call.source ?? '—'}</span>
          </Fact>
          <Fact label="Basis">
            <span className={surrogate ? 'text-gap' : ''}>
              {call.structure_basis ?? '—'}
              {surrogate && ' (stand-in structure)'}
            </span>
          </Fact>
          {call.cid && (
            <Fact label="PubChem CID">
              <a
                className="text-supported underline decoration-supported/30 underline-offset-2 hover:decoration-supported"
                href={`https://pubchem.ncbi.nlm.nih.gov/compound/${call.cid}`}
                target="_blank"
                rel="noreferrer"
              >
                {call.cid}
              </a>
            </Fact>
          )}
          {call.inchikey && <Fact label="InChIKey"><span className="font-mono text-xs">{call.inchikey}</span></Fact>}
          {call.max_structure_grade && <Fact label="Structural grade ceiling">{call.max_structure_grade}</Fact>}
          <Fact label="SMILES">
            <span className="font-mono text-xs" title={call.smiles}>{call.smiles ?? '—'}</span>
          </Fact>
        </dl>
      </div>
      {call.note && <p className="text-xs leading-relaxed text-slate-500">{call.note}</p>}
    </div>
  );
}

function Precedent({ call }: { call: Record<string, any> }) {
  const labels = call.approved_products_at_this_route ?? {};
  const iid: Record<string, any>[] = call.approved_at_this_route ?? [];
  const errors: string[] = call.lookup_errors ?? [];
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
        <Fact label="Asked">
          {call.excipient_as_asked ?? '—'} · {call.route_requested ?? '—'}
        </Fact>
        <Fact label="Registry name">{call.registry_name ?? '—'}</Fact>
        <Fact label="UNII / CAS">
          {call.unii ?? '—'}
          {call.cas && <span className="text-slate-500"> · {call.cas}</span>}
        </Fact>
        <Fact label="Finding">
          <span className={call.precedent_level === 'route_match' ? 'text-precedented' : 'text-gap'}>
            {String(call.precedent_level ?? 'none').replace(/_/g, ' ')}
            {call.route_matched ? ` (${call.route_matched.toLowerCase()})` : ''}
          </span>
        </Fact>
        <Fact label="Carried by">{call.precedent_basis ?? '—'}</Fact>
        <Fact label="Grade allowed">{call.max_grade ?? '—'}</Fact>
      </dl>

      {iid.length > 0 && (
        <div>
          <div className="mb-1.5 text-xs font-medium text-slate-500">FDA Inactive Ingredient Database at this route</div>
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 text-[13px]">
            {iid.slice(0, 6).map((r, i) => (
              <li key={i} className="flex justify-between gap-3 px-3 py-1.5">
                <span className="text-slate-700">{r.dosage_form}</span>
                <span className="tabular-nums text-slate-500">{r.max_potency}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {labels.checked && (
        <div>
          <div className="mb-1.5 text-xs font-medium text-slate-500">Approved product labels (openFDA)</div>
          <p className="text-[13px] text-slate-700">
            {labels.n_approved_applications ?? 0} applications mention it at this route;{' '}
            {labels.n_verified_as_ingredient ?? 0} verified as an ingredient from {labels.labels_read ?? 0} labels read.
          </p>
          {Array.isArray(labels.examples) && labels.examples.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {labels.examples.map((e: string) => (
                <span key={e} className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-600">
                  {e}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {errors.length > 0 ? (
        <div className="flex gap-2 rounded-lg border border-gap-line bg-gap-soft px-3 py-2 text-[13px] text-slate-800">
          <Triangle className="mt-0.5 h-4 w-4 shrink-0 text-gap" />
          <div>
            <b className="font-semibold">Part of the lookup failed.</b> A lower grade may reflect the outage.
            <ul className="mt-1 list-disc pl-4 text-xs text-slate-600">
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : (
        <p className="flex items-center gap-1.5 text-xs text-slate-500">
          <CheckCircle className="h-4 w-4 text-precedented" /> Every source answered.
        </p>
      )}
    </div>
  );
}

// Each time the evidence gate refused a dossier, in the gate's own words. On a
// failed run this is the reason; on a successful one, what was corrected first.
function Rejections({ reasons, failed }: { reasons: string[]; failed: boolean }) {
  const times = reasons.length === 1 ? 'once' : `${reasons.length} times`;
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-700">
        {failed
          ? `The model's dossier was refused ${times} and the run stopped, rather than return grades its tools did not support.`
          : `The model's dossier was refused ${times} before one passed. The result above is the corrected one.`}
      </p>
      <ol className="space-y-2">
        {reasons.map((r, i) => (
          <li key={i} className="flex gap-2 rounded-lg border border-gap-line bg-gap-soft px-3 py-2 text-[13px] text-slate-800">
            <span className="mt-px shrink-0 font-mono text-xs text-gap">{i + 1}</span>
            <span>{r}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export default function ScreenProvenance({ record }: { record: ScreenRecord }) {
  const p = record.provenance;
  const index = p.precedent_index ?? {};
  return (
    <div className="space-y-4">
      <Section title="How this was produced">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
          <Fact label="Started">{fullStamp(record.created_at)}</Fact>
          <Fact label="Took">{duration(p.duration_s ?? 0)}</Fact>
          <Fact label="Model">{p.model.replace(/^anthropic:/, '')}</Fact>
          <Fact label="Build">
            <span className="font-mono text-xs">{p.app_version}</span>
          </Fact>
          <Fact label="Model calls · tokens">
            {p.usage
              ? `${p.usage.requests} · ${(p.usage.input_tokens ?? 0).toLocaleString()} in / ${(p.usage.output_tokens ?? 0).toLocaleString()} out`
              : '—'}
          </Fact>
          <Fact label="FDA index">
            {index.loaded ? `${(index.rows ?? 0).toLocaleString()} rows loaded` : 'not loaded'}
          </Fact>
        </dl>
        {p.tool_trace.length > 0 && (
          <div className="mt-5">
            <div className="mb-2 text-xs font-medium text-slate-500">Steps, in the order they ran</div>
            <Trace trace={p.tool_trace} />
          </div>
        )}
      </Section>

      {(p.gate_rejections ?? []).length > 0 && (
        <Section title="Sent back by the evidence gate">
          <Rejections reasons={p.gate_rejections!} failed={record.status === 'failed'} />
        </Section>
      )}

      {p.identity_calls.map((call, i) => (
        <Section key={`id-${i}`} title={p.identity_calls.length > 1 ? `Identity · lookup ${i + 1}` : 'Identity'}>
          <Identity call={call} />
        </Section>
      ))}

      {p.precedent_calls.map((call, i) => (
        <Section key={`pr-${i}`} title={p.precedent_calls.length > 1 ? `Regulatory precedent · lookup ${i + 1}` : 'Regulatory precedent'}>
          <Precedent call={call} />
        </Section>
      ))}

      <details className="group rounded-xl border border-slate-200 bg-white shadow-card">
        <summary className="cursor-pointer select-none px-5 py-3.5 text-sm font-medium text-slate-700 hover:text-slate-900">
          The complete record, as stored
        </summary>
        <pre className="max-h-[480px] overflow-auto border-t border-slate-200 bg-slate-50 px-5 py-4 font-mono text-[11px] leading-relaxed text-slate-700">
          {JSON.stringify({ request: record.request, provenance: p, events: record.events }, null, 2)}
        </pre>
      </details>
    </div>
  );
}
