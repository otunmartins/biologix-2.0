'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Results from '@/components/Results';
import ScreenForm, { type Mode } from '@/components/ScreenForm';
import { GradeBox } from '@/components/badges';
import { Info, Molecule, Spinner, Triangle } from '@/components/icons';
import { API_URL, getHealth, runScreen, type Dossier, type Grade, type Health } from '@/lib/api';
import { DEFAULT_FORM, GRADE_MEANING, buildPrompt, type ScreenForm as Form } from '@/lib/screen';

type ApiState = { kind: 'checking' } | { kind: 'down' } | { kind: 'up'; health: Health };

const STEPS = [
  ['Identity', 'Resolves the excipient to a structure via PubChem, or a labelled surrogate for polymers.'],
  ['Precedent', 'Looks it up in the FDA Inactive Ingredient Database and approved product labels at your route.'],
  ['Hazard', 'Runs curated structural alerts and grades each endpoint A–E on the evidence behind it.'],
  ['Liability map', 'Maps reactive groups to protein residues, weighted by solvent accessibility when a structure is given.'],
] as const;

function ApiBadge({ api }: { api: ApiState }) {
  const [dot, label] =
    api.kind === 'checking'
      ? ['bg-slate-300', 'Checking API']
      : api.kind === 'down'
        ? ['bg-alert', 'API unreachable']
        : !api.health.model_configured
          ? ['bg-gap', 'No model key']
          : ['bg-precedented', 'API ready'];
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600">
      <span className={`h-2 w-2 rounded-full ${dot}`} />
      {label}
    </span>
  );
}

function Notice({ tone, title, children }: { tone: 'alert' | 'gap'; title: string; children: React.ReactNode }) {
  const cls = tone === 'alert' ? 'border-alert-line bg-alert-soft' : 'border-gap-line bg-gap-soft';
  return (
    <div className={`flex gap-3 rounded-xl border px-4 py-3 text-sm leading-relaxed text-slate-800 ${cls}`}>
      <Triangle className={`mt-0.5 h-5 w-5 shrink-0 ${tone === 'alert' ? 'text-alert' : 'text-gap'}`} />
      <div>
        <b className="block font-semibold">{title}</b>
        {children}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="mx-auto max-w-3xl space-y-8 px-6 py-12 lg:px-10">
      <div>
        <div className="eyebrow">Excipient triage</div>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-900">
          What should you test next?
        </h1>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-slate-600">
          Screen an excipient against a protein, a route and a storage condition. You get a verdict per
          endpoint, graded by the evidence behind it. It never tells you something is safe.
        </p>
      </div>

      <ol className="grid gap-3 sm:grid-cols-2">
        {STEPS.map(([title, body], i) => (
          <li key={title} className="rounded-xl border border-slate-200 bg-white p-4 shadow-card">
            <div className="flex items-center gap-2.5">
              <span className="grid h-6 w-6 place-items-center rounded-full bg-slate-900 text-xs font-semibold text-white">
                {i + 1}
              </span>
              <span className="font-semibold text-slate-900">{title}</span>
            </div>
            <p className="mt-2 text-sm leading-relaxed text-slate-600">{body}</p>
          </li>
        ))}
      </ol>

      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
        <div className="eyebrow mb-3">Evidence grades</div>
        <ul className="space-y-2.5">
          {(Object.keys(GRADE_MEANING) as Grade[]).map((g) => (
            <li key={g} className="flex items-center gap-3 text-sm text-slate-700">
              <GradeBox grade={g} />
              {GRADE_MEANING[g]}
            </li>
          ))}
        </ul>
      </div>

      <div className="flex gap-3 rounded-xl border border-slate-200 bg-slate-100/70 px-4 py-3 text-sm leading-relaxed text-slate-600">
        <Info className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" />
        <p>
          <b className="font-semibold text-slate-800">Triage, not a safety assessment.</b> Precedent means an
          excipient has been in an approved product at a route — not that it is compatible with your protein
          at your concentration. No compatibility simulation is run.
        </p>
      </div>
    </div>
  );
}

function LoadingState({ elapsed }: { elapsed: number }) {
  return (
    <div className="space-y-6 px-6 py-7 lg:px-10" aria-busy="true">
      <div className="flex items-center gap-3 text-[15px] text-slate-600">
        <Spinner className="h-5 w-5 text-supported" />
        <span>
          Screening · <span className="tabular-nums">{elapsed}s</span>
        </span>
      </div>
      <p className="-mt-3 text-sm text-slate-500">
        The agent runs every step in one request, so results arrive together.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {STEPS.map(([title]) => (
          <div key={title} className="rounded-xl border border-slate-200 bg-white p-4 shadow-card">
            <div className="text-[13px] font-semibold text-slate-400">{title}</div>
            <div className="relative mt-2 h-4 w-3/4 overflow-hidden rounded bg-slate-100">
              <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white to-transparent" />
            </div>
          </div>
        ))}
      </div>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="relative h-16 overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-slate-100 to-transparent" />
        </div>
      ))}
    </div>
  );
}

export default function Home() {
  const [mode, setMode] = useState<Mode>('form');
  const [form, setForm] = useState<Form>(DEFAULT_FORM);
  const [freeText, setFreeText] = useState(
    'Is polysorbate 80 a concern for my antibody given subcutaneously, stored at room temperature?',
  );

  const [api, setApi] = useState<ApiState>({ kind: 'checking' });
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [dossier, setDossier] = useState<Dossier | null>(null);
  const [context, setContext] = useState<string[] | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Say the API is unreachable or keyless before anyone waits on a run.
  useEffect(() => {
    const ctrl = new AbortController();
    getHealth(ctrl.signal)
      .then((health) => setApi({ kind: 'up', health }))
      .catch((e) => {
        if (e.name !== 'AbortError') setApi({ kind: 'down' });
      });
    return () => ctrl.abort();
  }, []);

  useEffect(() => {
    if (!loading) return;
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 250);
    return () => clearInterval(id);
  }, [loading]);

  const run = useCallback(async () => {
    const prompt = mode === 'form' ? buildPrompt(form) : freeText;
    if (!prompt.trim()) {
      setError('Nothing to screen — describe the excipient first.');
      return;
    }

    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setLoading(true);
    setElapsed(0);
    setError(null);
    setDossier(null);

    try {
      const result = await runScreen(prompt, ctrl.signal);
      setDossier(result);
      setContext(
        mode === 'form'
          ? [
              form.route,
              `${form.dose} mg protein`,
              form.concentration.trim() ? `${form.concentration.trim()} excipient` : 'concentration not given',
              `stored at ${form.temp.split(' (')[0]}`,
              form.structureId.trim() ? `structure ${form.structureId.trim().toUpperCase()}` : 'no structure',
            ]
          : null,
      );
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
    } finally {
      if (abortRef.current === ctrl) setLoading(false);
    }
  }, [mode, form, freeText]);

  // Ctrl/Cmd+Enter runs from anywhere, including inside the textareas.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !loading) {
        e.preventDefault();
        run();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [run, loading]);

  const updateForm = useCallback((patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch })), []);

  return (
    <div className="flex min-h-screen flex-col lg:h-screen">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-5">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-slate-900 text-white">
            <Molecule className="h-[18px] w-[18px]" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-slate-900">Excipient Screen</span>
          <span className="hidden text-sm text-slate-400 sm:inline">· biologic formulation triage</span>
        </div>
        <ApiBadge api={api} />
      </header>

      <div className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
        <aside className="shrink-0 border-b border-slate-200 bg-white lg:w-[400px] lg:border-b-0 lg:border-r">
          <ScreenForm
            mode={mode}
            onMode={setMode}
            form={form}
            onChange={updateForm}
            freeText={freeText}
            onFreeText={setFreeText}
            loading={loading}
            elapsed={elapsed}
            onRun={run}
          />
        </aside>

        <main className="flex-1 lg:min-h-0 lg:overflow-y-auto">
          {(api.kind === 'down' || (api.kind === 'up' && !api.health.model_configured) || error) && (
            <div className="space-y-3 px-6 pt-6 lg:px-10">
              {api.kind === 'down' && (
                <Notice tone="alert" title={`API unreachable at ${API_URL}`}>
                  Start it with <code className="font-mono">uvicorn main:app --port 8000</code> in{' '}
                  <code className="font-mono">api/</code>.
                </Notice>
              )}
              {api.kind === 'up' && !api.health.model_configured && (
                <Notice tone="gap" title="API is up, but no model key is configured">
                  Set <code className="font-mono">ANTHROPIC_API_KEY</code> in the API environment — screens
                  fail with 503 until you do.
                </Notice>
              )}
              {error && (
                <Notice tone="alert" title="Screen failed">
                  {error}
                </Notice>
              )}
            </div>
          )}

          {loading ? (
            <LoadingState elapsed={elapsed} />
          ) : dossier ? (
            <Results dossier={dossier} context={context} onRerun={run} loading={loading} />
          ) : (
            <EmptyState />
          )}
        </main>
      </div>
    </div>
  );
}
