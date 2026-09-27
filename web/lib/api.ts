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
