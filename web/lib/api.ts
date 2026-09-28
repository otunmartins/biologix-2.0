// Typed mirror of the FastAPI contract in api/main.py. If the Pydantic models
// there change, change these with them — nothing generates one from the other.

export type Verdict =
  | 'Precedented'
  | 'Supported without precedent'
  | 'Data gap: test'
  | 'Alert: avoid';

export type Grade = 'A' | 'B' | 'C' | 'D' | 'E';
export type Severity = 'low' | 'moderate' | 'high';

export interface EndpointResult {
  endpoint: string;
  verdict: Verdict;
  evidence_grade: Grade;
  rationale: string;
  sources: string[];
}

export interface LiabilityFlag {
  residue: string;
  reaction_class: string;
  severity: Severity;
  mitigation: string;
  accessibility: string;
  // "excipient", or the residual impurity that produced the flag.
  source: string;
}

export type StructureBasis = 'pubchem' | 'polymer_description' | 'surrogate' | 'unresolved' | '';

export interface Dossier {
  excipient: string;
  protein: string;
  route: string;
  summary: string;
  endpoints: EndpointResult[];
  liabilities: LiabilityFlag[];
  needs_testing: boolean;
  // Set by the backend from what it actually screened, not by the model.
  structure_basis: StructureBasis;
  // The SMILES the screen actually ran on; empty if nothing resolved. Optional
  // because dossiers saved before this field existed do not have it.
  structure_smiles?: string;
  // Where this run was saved in the user's history; null if saving failed.
  history_id?: string | null;
  // Computed by the backend from the request, before the agent runs.
  exposure: ExposureAssessment | null;
}

// Mirrors api/exposure.py.
export interface ExposureInputs {
  excipient_concentration: string;
  dose_volume_ml: number | null;
  dosing_interval_days: number;
  treatment_duration_days: number | null;
}

export interface ImpurityExposure {
  name: string;
  level: string;
  status: 'within' | 'above' | 'not_computed';
  reason?: string;
  level_ug_per_g?: number;
  ug_per_dose?: number;
  ug_per_dosing_day?: number;
  margin?: number | null;
}

export interface ExposureAssessment {
  acceptable_intake_ug_per_day: number;
  duration_category: string;
  duration_basis: string;
  excipient_mg_per_dose: number | null;
  excipient_mass_basis: string;
  impurities: ImpurityExposure[];
  basis: string;
}

// Mirrors api/polymer.py. SMILES use [*] for attachment points.
export interface PolymerSpec {
  repeat_unit: string;
  end_group_a: string;
  end_group_b: string;
  dp: number | null;
  impurities: { name: string; level: string }[];
}

// ---- polymer designer (mirrors api/design.py) ----------------------------

export interface DesignGoal {
  protein: string;
  route: string;
  target_temp_c: number | null;
  duration_months: number | null;
  format: 'liquid' | 'lyophilised';
  exposed_residues: string[];
  notes: string;
  // The biologic's PDB ID or UniProt accession; what simulations run against.
  // Absent on campaigns started before it existed.
  structure_id?: string;
  // Solution conditions the user stated; a simulation runs at them (lib/conditions.ts).
  ph?: number | null;
  salt_mm?: number | null;
  polymer_wv_percent?: number | null;
}

export interface Candidate {
  rank: number;
  name: string;
  backbone: string;
  pendant: string;
  repeat_unit_smiles: string;
  screened_oligomer_smiles: string;
  screened_units: number;
  // backbone_tg_c is the bare backbone's handbook value, reference only. tg_c is
  // the Tg for this repeat unit, predicted where the model could see it, and
  // tg_judged_c is the lower bound the screen actually scored.
  backbone_tg_c: number | null;
  tg_c: number | null;
  tg_source: string | null;
  tg_judged_c: number | null;
  tg_model_spread_sd: number | null;
  tg_in_model_domain: boolean | null;
  tg_note: string;
  charge: string;
  score: number;
  mechanism: string;
  supports: string[];
  risks: string[];
  alerts_fired: string[];
  descriptors: Record<string, number>;
  suggested_experiments: string[];
  // Feeds straight into the screening form, which is what actually judges it.
  screen_as: { repeat_unit: string; end_group_a: string; end_group_b: string };
}

export interface DesignResult {
  goal: DesignGoal;
  candidates: Candidate[];
  n_motif_pairs_considered: number;
  limits: string;
  verdict: string;
  max_grade: string;
}

// ---- active-learning campaigns (mirrors api/active.py, candidates.py) ------

export type CandidateStatus =
  | 'proposed'
  | 'benchmarked'
  | 'queued'
  | 'simulating'
  | 'simulated'
  | 'failed';

export interface CompositionPart {
  pendant: string;
  fraction: number;
  repeat_unit_smiles: string;
  charge: string;
}

// A Candidate as stored in a campaign: the design fields plus what only the store
// knows. `composition` breaks a copolymer into its weighted motifs.
// A finished OpenMM run (api/main.py SimulationResult, worker/simulate.py).
export interface SimulationResult {
  gamma23: number;
  gamma23_se: number | null;
  gamma23_blocks: number[];
  gamma23_profile: Record<string, number>;
  production_ns: number;
  temperature_k: number;
  n_chains: number;
  n_frames: number;
  r_local_nm: number;
  r_bulk_nm: number;
  bulk_chain_molar: number | null;
  contacts: { residue: string; fraction: number }[];
  engine: string;
  forcefields: string;
  structure_source: string;
  n_atoms: number;
  wall_seconds: number;
  smoke: boolean;
  // A short CPU run: not converged, never recorded as a measurement.
  preview?: boolean;
  // The worker saved the last frame for the 3D view (absent on older runs).
  has_snapshot?: boolean;
  notes: string[];
  // What the worker actually applied (absent on runs from before it reported them).
  conditions?: Partial<Record<ConditionKey, number>>;
  // Tier 1 (worker/metrics.py), absent on runs from before it existed.
  interaction_energy?: {
    total_kj: number;
    total_se: number | null;
    elec_kj: number;
    vdw_kj: number;
    per_chain_kj: number;
    per_residue: { residue: string; kj: number; elec_kj: number; vdw_kj: number }[];
    repulsive: { residue: string; kj: number }[];
    series: number[];
    method: string;
  } | null;
  hbonds?: {
    polymer_protein: number;
    protein_water: number;
    per_residue: { residue: string; per_frame: number }[];
    series_polymer_protein: number[];
    criterion: string;
  } | null;
  residence?: {
    bound_fraction: number;
    chain_mean_ps: number;
    chain_max_ps: number;
    binding_events: number;
    residues: { residue: string; mean_ps: number; max_ps: number; events: number }[];
    frame_ps: number;
  } | null;
  liability_coverage?: {
    class: string;
    why: string;
    n_sites: number;
    coverage: number;
    residues: { residue: string; fraction: number }[];
  }[];
  self_association?: {
    n_chains: number;
    mean_largest_cluster: number;
    max_cluster: number;
    mean_clusters: number;
    free_fraction: number;
  } | null;
  qc?: {
    temperature_k: number;
    temperature_sd: number;
    target_k: number;
    density_g_ml: number;
    density_sd: number;
    potential_drift_kj_per_ns: number;
    ok: boolean;
  } | null;
  // Tier 2: the unrestrained stage at a stress temperature.
  stability?: {
    temperature_c: number;
    ns: number;
    n_frames: number;
    rmsd_nm: { final: number; mean_last_half: number; max: number };
    q: { final: number; mean_last_half: number; min: number; n_contacts: number };
    rg_nm: { start: number; final: number };
    sasa_nm2: { start: number; final: number; hydrophobic_start: number; hydrophobic_final: number };
    secondary: { helix_start: number; strand_start: number; helix_final: number; strand_final: number; retained: number };
    rmsf_top: { residue: string; nm: number }[];
    rmsf: number[];
    rmsf_residues: string[];
    series: {
      t_ns: number[];
      rmsd: number[];
      q: number[];
      rg: number[];
      t_slow_ns: number[];
      sasa: number[];
      hydrophobic_sasa: number[];
      helix: number[];
      strand: number[];
    };
  } | null;
}

export type ConditionKey = 'temperature_c' | 'ph' | 'salt_mm' | 'polymer_wv_percent';

// The simulation job on a candidate, once it has been queued.
export interface SimulationJob {
  structure_id: string;
  attempts: number;
  started_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
  progress: string | null;
  result: SimulationResult | null;
  error: string | null;
  // Set once an admin has released the run to the worker; until then it waits.
  approved_at: string | null;
  // What it was approved as: the full GPU run, or a short CPU preview.
  tier: SimTier | null;
}

export type SimTier = 'gpu' | 'cpu';

export interface StoredCandidate extends Candidate {
  id: string;
  iteration: number;
  status: CandidateStatus;
  composition: CompositionPart[];
  simulation?: SimulationJob;
}

export interface IterationMetrics {
  iteration?: number;
  batch_size: number;
  best_score_so_far: number | null;
  batch_mean_score: number | null;
  batch_diversity: number | null;
  // Cross-validated error of the surrogate against the triage proxy — the loop's
  // own learning curve. Null until there are enough points to fold.
  surrogate_cv_mae: number | null;
  frac_alert_free: number | null;
  n_total: number;
  seeded: boolean;
}

// The advisory campaign controller's read of where the run stands.
export interface Recommendation {
  action: 'continue' | 'stop';
  phase: 'seed' | 'explore' | 'exploit' | 'converged';
  suggest_queue: boolean;
  top_k_to_queue?: number;
  reason: string;
}

export interface QueueSummary {
  n_queued: number;
  n_benchmarked: number;
  n_simulated: number;
  n_simulating?: number;
  n_failed?: number;
  by_status: Record<string, number>;
  top: { id: string; name: string; score: number }[];
  note: string;
}

export interface IterateResult {
  campaign_id: string;
  iteration: number;
  goal: DesignGoal;
  candidates: StoredCandidate[];
  metrics: IterationMetrics;
  metrics_history: IterationMetrics[];
  recommendation: Recommendation;
  queue_summary: QueueSummary;
  limits: string;
  orchestration: string;
  verdict: string;
  max_grade: string;
}

export interface Health {
  ok: boolean;
  model_configured: boolean;
  precedent_lookup: boolean;
  precedent_index: { loaded: boolean; cached_on_disk: boolean; last_error: string | null };
}

// Same-origin in production (Caddy proxies /screen and /health to the api
// container). Falls back to localhost:8000 for local dev.
export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Every call goes through here so the session cookie always travels with it:
// the API knows who is asking only from that cookie (api/users.py). 'include'
// rather than the default 'same-origin' because in local dev the API is on
// another port; in production it is the same origin and either would do.
async function call(path: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${API_URL}${path}`, { ...init, credentials: 'include' });
  if (res.status === 401) {
    // Signed out in another tab, or the session expired. Reloading lets the
    // server render the sign-in screen instead of a run that can never succeed.
    window.location.reload();
    throw new Error('Your session has ended. Sign in again.');
  }
  return res;
}

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const res = await call('/health', { signal });
  if (!res.ok) throw new Error(`health returned ${res.status}`);
  return res.json();
}

interface ValidationIssue {
  loc: (string | number)[];
  msg: string;
}

export async function runDesign(
  prompt: string,
  goal: DesignGoal | null,
  signal?: AbortSignal,
): Promise<DesignResult> {
  const res = await call('/design', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(goal ? { goal, limit: 12 } : { prompt, limit: 12 }),
    signal,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail =
      typeof body?.detail === 'string'
        ? body.detail
        : Array.isArray(body?.detail)
          ? (body.detail as ValidationIssue[]).map((d) => d.msg).join('; ')
          : '';
    throw new Error(detail || `Server returned ${res.status}`);
  }
  return res.json();
}

// One active-learning iteration. Pass a campaignId to continue a run, else a
// prompt/goal starts a fresh campaign.
export async function iterateDesign(
  args: { campaignId?: string; prompt?: string; goal?: DesignGoal; batchSize?: number },
  signal?: AbortSignal,
): Promise<IterateResult> {
  const res = await call('/design/iterate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...(args.campaignId ? { campaign_id: args.campaignId } : {}),
      ...(args.prompt ? { prompt: args.prompt } : {}),
      ...(args.goal ? { goal: args.goal } : {}),
      batch_size: args.batchSize ?? 8,
    }),
    signal,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail =
      typeof body?.detail === 'string'
        ? body.detail
        : Array.isArray(body?.detail)
          ? (body.detail as ValidationIssue[]).map((d) => d.msg).join('; ')
          : '';
    throw new Error(detail || `Server returned ${res.status}`);
  }
  return res.json();
}

// Thrown when the campaign names no biologic structure and none was given.
export class StructureRequired extends Error {}

export async function queueCandidates(
  candidateIds: string[],
  structureId = '',
  signal?: AbortSignal,
): Promise<{ queued: number; queue_summary: QueueSummary }> {
  const res = await call('/design/queue', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ candidate_ids: candidateIds, structure_id: structureId }),
    signal,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    if (body?.detail?.code === 'structure_required') throw new StructureRequired(body.detail.message);
    if (typeof body?.detail?.message === 'string') throw new Error(body.detail.message);
    const detail = Array.isArray(body?.detail)
      ? (body.detail as ValidationIssue[]).map((d) => d.msg.replace(/^Value error, /, '')).join('; ')
      : typeof body?.detail === 'string'
        ? body.detail
        : '';
    throw new Error(detail || `Server returned ${res.status}`);
  }
  return res.json();
}

export async function runScreen(
  prompt: string,
  polymer: PolymerSpec | null,
  exposure: ExposureInputs | null,
  // The form as filled in, kept in the user's history so it can be reopened.
  form: SavedForm | null,
  signal?: AbortSignal,
  // A designed candidate handed over with "Screen this candidate": the screen is
  // then reported with the designer's work, not as an excipient screen.
  candidateId?: string,
): Promise<Dossier> {
  const res = await call('/screen', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      prompt,
      ...(polymer ? { polymer } : {}),
      ...(exposure ? { exposure } : {}),
      ...(form ? { form } : {}),
      ...(candidateId ? { candidate_id: candidateId } : {}),
    }),
    signal,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    // FastAPI puts a string in detail for HTTPException, a list for a 422.
    const detail =
      typeof body?.detail === 'string'
        ? body.detail
        : Array.isArray(body?.detail)
          ? (body.detail as ValidationIssue[])
              .map((d) => `${d.loc.filter((l) => l !== 'body').join(' › ')}: ${d.msg.replace(/^Value error, /, '')}`)
              .join('\n')
          : '';
    throw new Error(detail || `Server returned ${res.status}`);
  }
  return res.json();
}

// ---- history and provenance (mirrors api/history.py) ------------------------

// What a screen's history record keeps of the form: either the structured form
// as filled in, or the plain-English text. Loose on purpose: a record saved by
// an older form still reopens, with anything missing taken from the defaults.
export type SavedForm =
  | { mode: 'form'; values: Record<string, unknown> }
  | { mode: 'text'; text: string };

export interface ScreenSummary {
  kind: 'screen';
  // 'design' when it screened a designed candidate ("Screen this candidate").
  origin?: 'excipient' | 'design';
  candidate_id?: string | null;
  id: string;
  at: string;
  status: 'ok' | 'failed';
  excipient: string | null;
  protein: string | null;
  route: string | null;
  worst_verdict: Verdict | null;
  needs_testing: boolean | null;
  smiles: string | null;
  error: string | null;
  prompt: string | null;
  app_version: string;
  duration_s: number;
}

export interface CampaignSummary {
  kind: 'campaign';
  id: string;
  at: string;
  goal: DesignGoal;
  prompt: string;
  ended_at: string | null;
  last_activity: string | null;
  n_candidates: number;
  n_iterations: number;
  n_queued: number;
  app_version: string;
  top: { name: string; score: number; smiles: string } | null;
}

export type HistoryItem = ScreenSummary | CampaignSummary;

// ---- approving simulations (admins only; the API checks ADMIN_EMAILS) ----

export interface AdminJob extends StoredCandidate {
  campaign_id: string;
  owner_email: string | null;
  updated_at?: string;
}

export interface WorkerSeen {
  name: string;
  platform: string;
  tier?: SimTier;
  last_seen: string;
}

export interface AdminSimulations {
  jobs: AdminJob[];
  recent: AdminJob[];
  workers: WorkerSeen[];
  pod_autostart: boolean;
  cpu_preview_ns: number;
}

async function detailOf(res: Response): Promise<string> {
  const body = await res.json().catch(() => null);
  return typeof body?.detail === 'string' ? body.detail : `Server returned ${res.status}`;
}

export async function getSimulations(signal?: AbortSignal): Promise<AdminSimulations> {
  const res = await call('/design/simulations', { signal });
  if (!res.ok) throw new Error(await detailOf(res));
  return res.json();
}

// Returns what happened to the worker: RunPod starting the GPU pod, or that a
// CPU preview needs none.
export async function approveSimulation(id: string, tier: SimTier): Promise<string> {
  const res = await call(`/design/simulations/${encodeURIComponent(id)}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tier }),
  });
  if (!res.ok) throw new Error(await detailOf(res));
  return (await res.json()).pod;
}

// ---- the signed-in user's own simulations (the Simulations tab) ----

export interface MySimulation extends StoredCandidate {
  campaign_id: string;
  updated_at: string;
  campaign: { protein: string; format: string; target_temp_c: number | null };
  // Only in the admin's view of someone's run: whose it is.
  owner_email?: string | null;
}

export async function getMySimulations(
  signal?: AbortSignal,
): Promise<{ simulations: MySimulation[]; cpu_preview_ns: number }> {
  const res = await call('/design/my-simulations', { signal });
  if (!res.ok) throw new Error(await detailOf(res));
  return res.json();
}

// One simulation in full. `admin`: any user's run, through the admin's route.
export async function getMySimulation(
  id: string,
  signal?: AbortSignal,
  admin = false,
): Promise<{ simulation: MySimulation; events: HistoryEvent[]; cpu_preview_ns: number }> {
  const base = admin ? '/design/simulations' : '/design/my-simulations';
  const res = await call(`${base}/${encodeURIComponent(id)}`, { signal });
  if (!res.ok) throw new Error(await detailOf(res));
  return res.json();
}

// ---- the Results tab: screens, candidates and simulations, with their metrics ----
// Shapes are api/results.py's; what each score means is written there.

export type GammaCall = 'excluded' | 'accumulated' | 'unclear';

export interface ExcipientResult {
  excipient: string;
  latest_id: string;
  latest_at: string;
  protein: string;
  route: string;
  n_screens: number;
  // Share of endpoints Precedented or Supported: how much is known, not safety.
  coverage: number | null;
  worst_verdict: string | null;
  n_endpoints: number;
  n_gaps: number;
  n_alerts: number;
  n_high_liabilities: number;
}

export interface CampaignResult {
  id: string;
  created_at: string;
  ended_at: string | null;
  prompt: string;
  protein: string;
  format: string;
  target_temp_c: number | null;
  n_candidates: number;
  n_iterations: number;
  best_score: number | null;
  median_score: number | null;
  alert_free: number | null;
  n_sent: number;
  n_screened: number;
  n_simulated: number;
  best_by_iteration: number[];
  // screen: the worst verdict of its full excipient screen, when it has had one.
  top: { id: string; name: string; score: number; status: CandidateStatus; screen: string | null } | null;
}

export interface SimulationRun {
  id: string;
  campaign_id: string;
  name: string;
  protein: string;
  triage_score: number;
  gamma23: number;
  gamma23_se: number | null;
  call: GammaCall;
  preview: boolean;
  production_ns: number | null;
  finished_at: string | null;
}

export interface MyResults {
  screens: {
    total: number;
    ok: number;
    failed: number;
    needs_testing: number;
    coverage_mean: number | null;
    worst_verdict: Record<string, number>;
    endpoint_verdicts: Record<string, number>;
    grades: Record<string, number>;
    liabilities: Record<'high' | 'moderate' | 'low', number>;
    excipients: ExcipientResult[];
  };
  design: {
    campaigns: number;
    candidates: number;
    iterations: number;
    by_status: Record<CandidateStatus, number>;
    alert_free: number | null;
    tg_in_domain: number | null;
    // Candidates given the full excipient screen ("Screen this candidate").
    screened: { total: number; worst_verdict: Record<string, number>; needs_testing: number };
    per_campaign: CampaignResult[];
  };
  simulations: {
    by_state: Record<'waiting' | 'running' | 'done' | 'failed', number>;
    calls: Record<GammaCall, number>;
    runs: SimulationRun[];
  };
  cpu_preview_ns: number;
}

// The 3D view (MolViewer): a run's last frame as PDB text, or the biologic's
// structure as mmCIF, both through the API so the session cookie travels.
async function getText(path: string, signal?: AbortSignal): Promise<string> {
  const res = await call(path, { signal });
  if (!res.ok) throw new Error(await detailOf(res));
  return res.text();
}

export function getSimulationSnapshot(id: string, signal?: AbortSignal, admin = false): Promise<string> {
  const base = admin ? '/design/simulations' : '/design/my-simulations';
  return getText(`${base}/${encodeURIComponent(id)}/snapshot.pdb`, signal);
}

export function getStructureModel(structureId: string, signal?: AbortSignal): Promise<string> {
  return getText(`/structure/model/${encodeURIComponent(structureId)}`, signal);
}

export function getConformer(smiles: string, signal?: AbortSignal): Promise<string> {
  return getText(`/structure/conformer?${new URLSearchParams({ smiles })}`, signal);
}

export function getMyResults(signal?: AbortSignal): Promise<MyResults> {
  return getJson('/design/my-results', signal);
}

// Denies a waiting or approved job, or stops a running one. True when it stopped a run.
export async function declineSimulation(id: string, reason: string): Promise<boolean> {
  const res = await call(`/design/simulations/${encodeURIComponent(id)}/decline`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) throw new Error(await detailOf(res));
  return Boolean((await res.json()).stopped);
}

// ---- the admin dashboard: platform-wide counts, never anyone's content ----

export interface Bin {
  lo: number | null;
  hi: number | null;
  n: number;
}

export interface AdminStats {
  generated_at: string;
  window_days: number;
  totals: Record<
    | 'users' | 'active_users_7d' | 'active_users_30d' | 'screens' | 'screens_ok' | 'screens_failed'
    | 'campaigns' | 'iterations' | 'polymers' | 'simulations_requested' | 'simulations_done'
    | 'simulations_running' | 'simulations_waiting' | 'simulations_approved' | 'measurements'
    | 'tokens_in' | 'tokens_out' | 'model_requests',
    number
  >;
  previous: Record<'screens' | 'polymers' | 'campaigns' | 'simulations', number>;
  series: {
    dates: string[];
    screens: number[];
    polymers: number[];
    campaigns: number[];
    simulations: number[];
    active_users: number[];
    users_cumulative: number[];
    events: number[];
  };
  screens: {
    // Share of each screen's endpoints that are Precedented or Supported.
    coverage_mean: number | null;
    coverage: Bin[];
    liabilities: Record<'high' | 'moderate' | 'low', number>;
    worst_verdict: Record<string, number>;
    endpoint_verdicts: Record<string, number>;
    grades: Record<string, number>;
    routes: Record<string, number>;
    needs_testing: number;
    duration_median_s: number | null;
    duration_p90_s: number | null;
  };
  polymers: {
    // Designed candidates put through the full excipient screen, by worst verdict.
    screened_in_full: Record<string, number>;
    candidates_per_campaign: Bin[];
    iterations_per_campaign: Bin[];
    alert_free: number | null;
    by_backbone: Record<string, number>;
    by_status: Record<string, number>;
    by_format: Record<string, number>;
    target_temp_c: Bin[];
    score_quartiles: number[];
  };
  simulations: {
    by_status: Record<'waiting' | 'approved' | 'running' | 'done' | 'failed', number>;
    by_tier: Record<string, number>;
    gamma23: Bin[];
    gamma23_excluded: number;
    gamma23_accumulated: number;
    calls: Record<GammaCall, number>;
    previews: number;
  };
  // Event kinds in the period: what kind of thing happened, never what.
  events: Record<HistoryEvent['kind'], number>;
}

export async function getAdminStats(days: number, signal?: AbortSignal): Promise<AdminStats> {
  const res = await call(`/design/admin/stats?days=${days}`, { signal });
  if (!res.ok) throw new Error(await detailOf(res));
  return res.json();
}

export interface HistoryEvent {
  at: string;
  kind:
    | 'screen.completed'
    | 'screen.failed'
    | 'campaign.started'
    | 'campaign.iterated'
    | 'candidate.queued'
    | 'candidate.simulating'
    | 'candidate.simulated'
    | 'candidate.simulation_failed'
    | 'candidate.requeued'
    | 'candidate.approved'
    | 'candidate.declined'
    | 'candidate.stopped'
    | 'campaign.ended'
    | 'campaign.reopened';
  data: Record<string, any>;
  app_version: string;
}

export interface ScreenProvenance {
  model: string;
  app_version: string;
  duration_s: number;
  usage: { requests: number; input_tokens: number; output_tokens: number; tool_calls: number } | null;
  // Exactly what each tool returned during the run. Shapes are the tools' own
  // (api/main.py, api/precedent.py), so they are read defensively.
  identity_calls: Record<string, any>[];
  precedent_calls: Record<string, any>[];
  exposure: ExposureAssessment | null;
  precedent_index: { loaded?: boolean; rows?: number; cached_on_disk?: boolean; last_error?: string | null };
  tool_trace: { tool: string; args: Record<string, unknown> }[];
  // Why the evidence gate sent a dossier back, once per rejection. Absent on
  // records saved before it was recorded.
  gate_rejections?: string[];
}

export interface ScreenRecord {
  id: string;
  created_at: string;
  finished_at: string;
  status: 'ok' | 'failed';
  request: {
    prompt: string;
    polymer: PolymerSpec | null;
    exposure: ExposureInputs | null;
    form: SavedForm | null;
    candidate_id?: string | null;
  };
  dossier: Dossier | null;
  error: string | null;
  provenance: ScreenProvenance;
  app_version: string;
  events: HistoryEvent[];
}

export interface CampaignState {
  campaign_id: string;
  goal: DesignGoal;
  prompt: string;
  created_at: string;
  ended_at: string | null;
  app_version: string;
  n_candidates: number;
  n_iterations: number;
  candidates_by_iteration: Record<string, StoredCandidate[]>;
  metrics_history: IterationMetrics[];
  queue_summary: QueueSummary;
  limits: string;
  recommendation: Recommendation;
  events: HistoryEvent[];
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await call(path, { signal });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : `Server returned ${res.status}`);
  }
  return res.json();
}

export function getHistory(
  args: { kind?: 'all' | 'screen' | 'campaign'; before?: string | null; limit?: number },
  signal?: AbortSignal,
): Promise<{ items: HistoryItem[]; next: string | null }> {
  const q = new URLSearchParams({ kind: args.kind ?? 'all', limit: String(args.limit ?? 20) });
  if (args.before) q.set('before', args.before);
  return getJson(`/history?${q}`, signal);
}

export function getScreenRecord(id: string, signal?: AbortSignal): Promise<ScreenRecord> {
  return getJson(`/history/screens/${encodeURIComponent(id)}`, signal);
}

export function getCampaign(id: string, signal?: AbortSignal): Promise<CampaignState> {
  return getJson(`/design/campaign/${encodeURIComponent(id)}`, signal);
}

export interface ResolvedStructure {
  structure_id: string;
  title: string;
  source: string;
  residues: number | null;
  why: string;
  alternatives: Omit<ResolvedStructure, 'alternatives'>[];
}

// The structure to simulate for a biologic named in the user's words, or null
// when nothing fits (then the user is asked for an ID). See api/structures.py.
export async function resolveStructure(q: string, signal?: AbortSignal): Promise<ResolvedStructure | null> {
  const r = await getJson<{ found: boolean } & ResolvedStructure>(
    `/structure/resolve?q=${encodeURIComponent(q.slice(0, 300))}`,
    signal,
  );
  return r.found ? r : null;
}

// "New experiment": closes the campaign. It stays in history and reopens if continued.
export async function endCampaign(id: string): Promise<void> {
  const res = await call(`/design/campaign/${encodeURIComponent(id)}/end`, { method: 'POST' });
  if (!res.ok) throw new Error(`Server returned ${res.status}`);
}
