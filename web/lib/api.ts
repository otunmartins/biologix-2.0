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
export interface StoredCandidate extends Candidate {
  id: string;
  iteration: number;
  status: CandidateStatus;
  composition: CompositionPart[];
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

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const res = await fetch(`${API_URL}/health`, { signal });
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
  const res = await fetch(`${API_URL}/design`, {
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
  const res = await fetch(`${API_URL}/design/iterate`, {
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

export async function queueCandidates(
  candidateIds: string[],
  signal?: AbortSignal,
): Promise<{ queued: number; queue_summary: QueueSummary }> {
  const res = await fetch(`${API_URL}/design/queue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ candidate_ids: candidateIds }),
    signal,
  });
  if (!res.ok) throw new Error(`Server returned ${res.status}`);
  return res.json();
}

export async function runScreen(
  prompt: string,
  polymer: PolymerSpec | null,
  exposure: ExposureInputs | null,
  signal?: AbortSignal,
): Promise<Dossier> {
  const res = await fetch(`${API_URL}/screen`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      prompt,
      ...(polymer ? { polymer } : {}),
      ...(exposure ? { exposure } : {}),
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
