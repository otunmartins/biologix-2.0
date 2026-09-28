'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import DesignPanel, { type CurrentExperiment } from '@/components/DesignPanel';
import DesignResults from '@/components/DesignResults';
import Results from '@/components/Results';
import ScreenForm, { type Mode } from '@/components/ScreenForm';
import { GradeBox } from '@/components/badges';
import { Info, Spinner, Triangle } from '@/components/icons';
import BenchmarkPanel from '@/components/BenchmarkPanel';
import { BiologixLockup } from '@/components/brand';
import UserMenu, { type SessionUser } from '@/components/UserMenu';
import HistoryView from '@/components/history/HistoryView';
import ResultsView from '@/components/ResultsView';
import {
  API_URL,
  endCampaign,
  getCampaign,
  getHealth,
  getScreenRecord,
  iterateDesign,
  queueCandidates,
  runScreen,
  type CampaignState,
  type DesignGoal,
  type Dossier,
  type Grade,
  type Health,
  type IterationMetrics,
  type QueueSummary,
  type Recommendation,
  type SavedForm,
  type ScreenRecord,
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
    // On a phone only the dot shows; the label stays as a tooltip and for screen readers.
    <span
      title={label}
      className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-2 py-2 text-xs font-medium text-slate-600 sm:px-3 sm:py-1"
    >
      <span className={`h-2 w-2 rounded-full ${dot}`} />
      <span className="sr-only sm:not-sr-only">{label}</span>
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

type Workflow = 'screen' | 'design' | 'results' | 'history';

interface CampaignView {
  campaignId: string;
  iteration: number;
  goal: DesignGoal;
  candidates: StoredCandidate[];
  history: IterationMetrics[];
  recommendation: Recommendation;
  limits: string;
  verdict: string;
  // Provenance the panel shows while the campaign is open.
  prompt: string;
  startedAt: string;
  // Set when it was reopened from history after being ended; the next
  // iteration reopens it.
  endedAt: string | null;
}

// A campaign as the store returns it, in the shape the workspace shows.
function campaignFromState(s: CampaignState): CampaignView {
  const candidates = Object.values(s.candidates_by_iteration).flat();
  return {
    campaignId: s.campaign_id,
    iteration: s.n_iterations,
    goal: s.goal,
    candidates,
    history: s.metrics_history,
    recommendation: s.recommendation,
    limits: s.limits,
    verdict: 'Data gap: test',
    prompt: s.prompt,
    startedAt: s.created_at,
    endedAt: s.ended_at,
  };
}

// The signed-in app. app/page.tsx renders it only once there is a session.
export default function Workbench({ user, isAdmin = false }: { user: SessionUser; isAdmin?: boolean }) {
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
  // The last screen came back but could not be written to history.
  const [unsaved, setUnsaved] = useState(false);
  // A designed candidate handed over with "Screen this candidate". The screen is
  // tagged with it only while the form still describes that candidate: change the
  // excipient or its repeat unit and it is an excipient screen of your own.
  const [handoff, setHandoff] = useState<{ candidateId: string; excipient: string; repeatUnit: string } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // "Run again" from history refills the form, then runs once it has rendered.
  const rerunPending = useRef(false);

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
    setUnsaved(false);

    try {
      // Only the structured form carries a polymer description.
      const polymer = mode === 'form' ? toPolymerSpec(form.polymer) : null;
      // Kept with the run in history, so it can be reopened exactly as filled in.
      const saved: SavedForm =
        mode === 'form' ? { mode: 'form', values: { ...form } } : { mode: 'text', text: freeText };
      const fromDesign =
        handoff && mode === 'form' && form.excipient === handoff.excipient && form.polymer.repeatUnit === handoff.repeatUnit
          ? handoff.candidateId
          : undefined;
      const result = await runScreen(
        prompt,
        polymer,
        mode === 'form' ? toExposureInputs(form) : null,
        saved,
        ctrl.signal,
        fromDesign,
      );
      setDossier(result);
      setUnsaved(!result.history_id);
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
  }, [mode, form, freeText, handoff]);

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
        prompt: designPrompt,
        startedAt: new Date().toISOString(),
        endedAt: null,
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
          // Continuing an ended campaign reopens it (the API logs that).
          endedAt: null,
        },
      );
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
    } finally {
      if (abortRef.current === ctrl) setLoading(false);
    }
  }, [campaign]);

  // Close the campaign on screen and clear the workspace. Nothing is lost: it
  // stays in History, where it can be reopened. The workspace clears first so
  // the button feels instant; a failure to record the end is shown, not hidden.
  const newExperiment = useCallback(async () => {
    if (!campaign) return;
    abortRef.current?.abort();
    const id = campaign.campaignId;
    setCampaign(null);
    setDesignPrompt('');
    setError(null);
    setLoading(false);
    try {
      await endCampaign(id);
    } catch (e) {
      setError(`The workspace is clear, but the old campaign could not be marked as ended: ${(e as Error).message}`);
    }
  }, [campaign]);

  // Put a past screen back in the workspace: the form as it was filled in, and
  // the dossier it produced.
  const openScreen = useCallback((r: ScreenRecord) => {
    abortRef.current?.abort();
    const saved = r.request.form;
    // Reopening a designed candidate's screen keeps it one, so a rerun is too.
    const sv = saved?.mode === 'form' ? (saved.values as Partial<Form>) : null;
    setHandoff(
      r.request.candidate_id && sv?.excipient && sv.polymer?.repeatUnit
        ? { candidateId: r.request.candidate_id, excipient: sv.excipient, repeatUnit: sv.polymer.repeatUnit }
        : null,
    );
    if (saved?.mode === 'form') {
      setMode('form');
      // Defaults first, so a record saved by an older form still opens.
      setForm({ ...DEFAULT_FORM, ...(saved.values as Partial<Form>) });
    } else {
      setMode('text');
      setFreeText(saved?.mode === 'text' ? saved.text : r.request.prompt);
    }
    setDossier(r.dossier);
    setContext(null);
    setUnsaved(false);
    setLoading(false);
    setError(r.status === 'failed' ? r.error : null);
    setWorkflow('screen');
  }, []);

  const rerunScreen = useCallback(
    (r: ScreenRecord) => {
      openScreen(r);
      rerunPending.current = true;
    },
    [openScreen],
  );

  const openCampaign = useCallback((s: CampaignState) => {
    abortRef.current?.abort();
    setCampaign(campaignFromState(s));
    setDesignPrompt(s.prompt);
    setLoading(false);
    setError(null);
    setWorkflow('design');
  }, []);

  const queueCandidate = useCallback(async (c: StoredCandidate, structureId: string) => {
    setQueueingId(c.id);
    setError(null);
    try {
      await queueCandidates([c.id], structureId);
      setCampaign((prev) => {
        if (!prev) return prev;
        const sid = structureId.toUpperCase() || prev.goal.structure_id || '';
        return {
          ...prev,
          // The API remembers a structure given here on the campaign's goal.
          goal: prev.goal.structure_id ? prev.goal : { ...prev.goal, structure_id: sid },
          candidates: prev.candidates.map((x) =>
            x.id === c.id
              ? {
                  ...x,
                  status: 'queued',
                  simulation: {
                    structure_id: sid, attempts: 0, started_at: null, heartbeat_at: null,
                    finished_at: null, progress: null, result: null, error: null, approved_at: null,
                    tier: null,
                  },
                }
              : x,
          ),
        };
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setQueueingId(null);
    }
  }, []);

  // While any candidate is waiting on or running a simulation, refresh the
  // campaign now and then so progress and results appear without a reload.
  const activeSims = campaign?.candidates.some((c) => c.status === 'queued' || c.status === 'simulating');
  const campaignId = campaign?.campaignId;
  useEffect(() => {
    if (!activeSims || !campaignId) return;
    const ctl = new AbortController();
    const t = setInterval(async () => {
      try {
        const s = await getCampaign(campaignId, ctl.signal);
        const fresh = Object.values(s.candidates_by_iteration).flat();
        setCampaign((prev) =>
          prev && prev.campaignId === s.campaign_id ? { ...prev, goal: s.goal, candidates: fresh } : prev,
        );
      } catch {
        // A missed refresh is not worth an error banner; the next one will do.
      }
    }, 20000);
    return () => {
      clearInterval(t);
      ctl.abort();
    };
  }, [activeSims, campaignId]);

  useEffect(() => {
    if (rerunPending.current && workflow === 'screen') {
      rerunPending.current = false;
      run();
    }
  }, [run, workflow]);

  // Ctrl/Cmd+Enter runs from anywhere, including inside the textareas.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !loading && (workflow === 'screen' || workflow === 'design')) {
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
    setHandoff({ candidateId: c.id, excipient: c.name, repeatUnit: c.screen_as.repeat_unit });
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

  const currentExperiment: CurrentExperiment | null = campaign && {
    prompt: campaign.prompt,
    goal: campaign.goal,
    startedAt: campaign.startedAt,
    endedAt: campaign.endedAt,
    iteration: campaign.iteration,
    nCandidates: campaign.candidates.length,
  };

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
      {/* On a phone the tabs drop to a second, full-width row; from sm up it is one row. */}
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-y-2 border-b border-slate-200 bg-white px-4 py-2 sm:h-14 sm:flex-nowrap sm:px-5 sm:py-0">
        <BiologixLockup />

        <nav className="order-last flex w-full rounded-lg bg-slate-100 p-1 text-sm font-medium sm:order-none sm:w-auto">
          {(
            [
              ['screen', 'Screen'],
              ['design', 'Design'],
              ['results', 'Results'],
              ['history', 'History'],
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
              className={`flex-1 rounded-md px-4 py-1.5 transition sm:flex-none ${
                workflow === w ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'
              }`}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="flex items-center gap-3">
          <ApiBadge api={api} />
          <UserMenu user={user} isAdmin={isAdmin} />
        </div>
      </header>

      {workflow === 'results' ? (
        <ResultsView
          onOpenCampaign={(id) => getCampaign(id).then(openCampaign).catch((e) => setError((e as Error).message))}
          onOpenScreen={(id) => getScreenRecord(id).then(openScreen).catch((e) => setError((e as Error).message))}
        />
      ) : workflow === 'history' ? (
        <HistoryView onOpenScreen={openScreen} onRerunScreen={rerunScreen} onOpenCampaign={openCampaign} />
      ) : (
        <div className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
          <aside className="shrink-0 border-b border-slate-200 bg-white lg:w-[400px] lg:border-b-0 lg:border-r">
            {workflow === 'design' ? (
              <DesignPanel
                prompt={designPrompt}
                onPrompt={setDesignPrompt}
                loading={loading}
                elapsed={elapsed}
                onRun={design}
                current={currentExperiment}
                onNew={newExperiment}
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
              {(api.kind === 'down' ||
                (api.kind === 'up' && !api.health.model_configured) ||
                error ||
                (unsaved && workflow === 'screen' && dossier)) && (
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
                  {unsaved && workflow === 'screen' && dossier && (
                    <Notice tone="gap" title="Not saved to your history">
                      The screen finished, but its record could not be stored. Download the dossier if you need to
                      keep it.
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
      )}
    </div>
  );
}
