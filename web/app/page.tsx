'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import DesignPanel from '@/components/DesignPanel';
import DesignResults from '@/components/DesignResults';
import Results from '@/components/Results';
import ScreenForm, { type Mode } from '@/components/ScreenForm';
import { GradeBox } from '@/components/badges';
import { Info, Molecule, Spinner, Triangle } from '@/components/icons';
import BenchmarkPanel from '@/components/BenchmarkPanel';
import {
  API_URL,
  getHealth,
  iterateDesign,
  queueCandidates,
  runScreen,
  type DesignGoal,
  type Dossier,
  type Grade,
  type Health,
  type IterationMetrics,
  type QueueSummary,
  type Recommendation,
  type StoredCandidate,
} from '@/lib/api';
import {
  DEFAULT_FORM,
  GRADE_MEANING,
  buildPrompt,
  toExposureInputs,
  toPolymerSpec,
  type ScreenForm as Form,
} from '@/lib/screen';

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

const DESIGN_STEPS = [
  ['Read the goal', 'A model reads the temperature, duration and format out of your words — and does nothing else.'],
  ['Propose', 'Each iteration proposes a batch of copolymers — a backbone with a weighted mixture of pendants — from a curated motif table, built as real structures.'],
  ['Screen', 'Each chain goes through the same structural alerts and rule table as any other excipient here, and is scored on hydration, glass transition, charge and the alerts that fired.'],
  ['Learn', 'A surrogate learns which compositions score well and steers the next batch — a triage proxy, not a stabilisation prediction. Promising candidates queue for an OpenMM run.'],
] as const;

function DesignEmpty() {
  return (
    <div className="mx-auto max-w-3xl space-y-8 px-6 py-12 lg:px-10">
      <div>
        <div className="eyebrow">Polymer designer</div>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-900">
          Stabilising a biologic out of the fridge
        </h1>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-slate-600">
          Describe the biologic and the temperature it has to survive. You get a ranked shortlist of
          polymer candidates to take into the laboratory, each with the chemistry that supports it,
          the liabilities it carries, and the experiments that would settle it.
        </p>
      </div>

      <ol className="grid gap-3 sm:grid-cols-2">
        {DESIGN_STEPS.map(([title, body], i) => (
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

      <div className="flex gap-3 rounded-xl border border-gap-line bg-gap-soft px-4 py-3.5 text-sm leading-relaxed text-slate-800">
        <Triangle className="mt-0.5 h-5 w-5 shrink-0 text-gap" />
        <p>
          <b className="font-semibold">Hypotheses, not predictions.</b> Nothing here simulates the
          protein. Candidates are enumerated from known stabilising motifs and ranked by chemistry
          the app can see — every one comes back grade D, to be tested.
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

type Workflow = 'screen' | 'design';

interface CampaignView {
  campaignId: string;
  iteration: number;
  goal: DesignGoal;
  candidates: StoredCandidate[];
  history: IterationMetrics[];
  recommendation: Recommendation;
  limits: string;
  verdict: string;
}

export default function Home() {
  const [workflow, setWorkflow] = useState<Workflow>('screen');
  const [mode, setMode] = useState<Mode>('form');
  const [designPrompt, setDesignPrompt] = useState('');
  const [campaign, setCampaign] = useState<CampaignView | null>(null);
  const [queueingId, setQueueingId] = useState<string | null>(null);
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
      // Only the structured form carries a polymer description.
      const polymer = mode === 'form' ? toPolymerSpec(form.polymer) : null;
      const result = await runScreen(
        prompt,
        polymer,
        mode === 'form' ? toExposureInputs(form) : null,
        ctrl.signal,
      );
      setDossier(result);
      setContext(
        mode === 'form'
          ? [
              form.route,
              `${form.dose} mg protein`,
              form.concentration.trim() ? `${form.concentration.trim()} excipient` : 'concentration not given',
              `stored at ${form.temp.split(' (')[0]}`,
              form.structureId.trim() ? `structure ${form.structureId.trim().toUpperCase()}` : 'no structure',
              ...(form.polymer.enabled ? ['described polymer'] : []),
            ]
          : null,
      );
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
    } finally {
      if (abortRef.current === ctrl) setLoading(false);
    }
  }, [mode, form, freeText]);

  // Start a campaign: iteration 1 from the goal in the user's words.
  const design = useCallback(async () => {
    if (!designPrompt.trim()) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setElapsed(0);
    setError(null);
    setCampaign(null);
    try {
      const r = await iterateDesign({ prompt: designPrompt }, ctrl.signal);
      setCampaign({
        campaignId: r.campaign_id,
        iteration: r.iteration,
        goal: r.goal,
        candidates: r.candidates,
        history: r.metrics_history,
        recommendation: r.recommendation,
        limits: r.limits,
        verdict: r.verdict,
      });
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
    } finally {
      if (abortRef.current === ctrl) setLoading(false);
    }
  }, [designPrompt]);

  // The active-learning button: one more batch, appended to what is already shown.
  const iterateNext = useCallback(async () => {
    if (!campaign) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setElapsed(0);
    setError(null);
    try {
      const r = await iterateDesign({ campaignId: campaign.campaignId }, ctrl.signal);
      setCampaign((prev) =>
        prev && {
          ...prev,
          iteration: r.iteration,
          candidates: [...prev.candidates, ...r.candidates],
          history: r.metrics_history,
          recommendation: r.recommendation,
        },
      );
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
    } finally {
      if (abortRef.current === ctrl) setLoading(false);
    }
  }, [campaign]);

  const queueCandidate = useCallback(async (c: StoredCandidate) => {
    setQueueingId(c.id);
    setError(null);
    try {
      await queueCandidates([c.id]);
      setCampaign((prev) =>
        prev && {
          ...prev,
          candidates: prev.candidates.map((x) =>
            x.id === c.id ? { ...x, status: 'queued' } : x,
          ),
        },
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setQueueingId(null);
    }
  }, []);

  // Ctrl/Cmd+Enter runs from anywhere, including inside the textareas.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !loading) {
        e.preventDefault();
        // In a running campaign, Ctrl+Enter runs the next iteration.
        (workflow === 'design' ? (campaign ? iterateNext : design) : run)();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [run, design, iterateNext, campaign, workflow, loading]);

  const updateForm = useCallback((patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch })), []);


  // A candidate is only a hypothesis until the screen judges it, so handing it
  // over switches workflow and fills the polymer description in place.
  const screenCandidate = useCallback((c: StoredCandidate) => {
    setForm((f) => ({
      ...f,
      excipient: c.name,
      polymer: {
        ...f.polymer,
        enabled: true,
        repeatUnit: c.screen_as.repeat_unit,
        endA: c.screen_as.end_group_a,
        endB: c.screen_as.end_group_b,
      },
    }));
    setMode('form');
    setWorkflow('screen');
    setDossier(null);
    setError(null);
  }, []);

  // The queue panel is derived from the candidates on screen, so it always reflects
  // this campaign rather than the global backlog the queue endpoint reports.
  const queueView: QueueSummary | null = campaign
    ? (() => {
        const byStatus: Record<string, number> = {};
        campaign.candidates.forEach((c) => {
          byStatus[c.status] = (byStatus[c.status] || 0) + 1;
        });
        const queued = campaign.candidates
          .filter((c) => c.status === 'queued')
          .sort((a, b) => b.score - a.score);
        return {
          n_queued: queued.length,
          n_benchmarked: byStatus['benchmarked'] || 0,
          n_simulated: byStatus['simulated'] || 0,
          by_status: byStatus,
          top: queued.slice(0, 5).map((c) => ({ id: c.id, name: c.name, score: c.score })),
          note: '',
        };
      })()
    : null;

  return (
    <div className="flex min-h-screen flex-col lg:h-screen">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-5">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-slate-900 text-white">
            <Molecule className="h-[18px] w-[18px]" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-slate-900">Excipient Screen</span>
        </div>

        <nav className="flex rounded-lg bg-slate-100 p-1 text-sm font-medium">
          {(
            [
              ['screen', 'Screen'],
              ['design', 'Design'],
            ] as const
          ).map(([w, label]) => (
            <button
              key={w}
              type="button"
              onClick={() => {
                setWorkflow(w);
                setError(null);
              }}
              aria-current={workflow === w}
              className={`rounded-md px-4 py-1.5 transition ${
                workflow === w ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'
              }`}
            >
              {label}
            </button>
          ))}
        </nav>
        <ApiBadge api={api} />
      </header>

      <div className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
        <aside className="shrink-0 border-b border-slate-200 bg-white lg:w-[400px] lg:border-b-0 lg:border-r">
          {workflow === 'design' ? (
            <DesignPanel
              prompt={designPrompt}
              onPrompt={setDesignPrompt}
              loading={loading}
              elapsed={elapsed}
              onRun={design}
            />
          ) : (
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
          )}
        </aside>

        <main className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
          <div className="flex-1 lg:min-h-0 lg:overflow-y-auto">
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
                  <Notice tone="alert" title={workflow === 'design' ? 'Design failed' : 'Screen failed'}>
                    {error}
                  </Notice>
                )}
              </div>
            )}

            {workflow === 'design' ? (
              // A campaign stays on screen while the next batch loads; only the very
              // first proposal shows the full skeleton.
              !campaign && loading ? (
                <LoadingState elapsed={elapsed} />
              ) : campaign ? (
                <DesignResults
                  goal={campaign.goal}
                  candidates={campaign.candidates}
                  limits={campaign.limits}
                  verdict={campaign.verdict}
                  onScreen={screenCandidate}
                  onQueue={queueCandidate}
                  queueing={queueingId}
                />
              ) : (
                <DesignEmpty />
              )
            ) : loading ? (
              <LoadingState elapsed={elapsed} />
            ) : dossier ? (
              <Results dossier={dossier} context={context} onRerun={run} loading={loading} />
            ) : (
              <EmptyState />
            )}
          </div>

          {workflow === 'design' && campaign && (
            <aside className="shrink-0 border-t border-slate-200 bg-slate-50 lg:min-h-0 lg:w-[360px] lg:border-l lg:border-t-0">
              <BenchmarkPanel
                iteration={campaign.iteration}
                history={campaign.history}
                recommendation={campaign.recommendation}
                queue={queueView}
                loading={loading}
                onIterate={iterateNext}
              />
            </aside>
          )}
        </main>
      </div>
    </div>
  );
}
