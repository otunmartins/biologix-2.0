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
}

export interface Dossier {
  excipient: string;
  protein: string;
  route: string;
  summary: string;
  endpoints: EndpointResult[];
  liabilities: LiabilityFlag[];
  needs_testing: boolean;
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

export async function runScreen(prompt: string, signal?: AbortSignal): Promise<Dossier> {
  const res = await fetch(`${API_URL}/screen`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt }),
    signal,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    // FastAPI puts a string in detail for HTTPException, a list for a 422.
    const detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body?.detail);
    throw new Error(detail || `Server returned ${res.status}`);
  }
  return res.json();
}
