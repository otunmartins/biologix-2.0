import type { Dossier, EndpointResult, Grade, LiabilityFlag, Severity, Verdict } from './api';

export const GRADE_MEANING: Record<Grade, string> = {
  A: 'Approved-product precedent at this route, not above the highest level on record (FDA)',
  B: 'Experimental data',
  C: 'In-domain prediction',
  D: 'Out-of-domain or surrogate prediction',
  E: 'No data',
};

export const VERDICTS: Verdict[] = [
  'Precedented',
  'Supported without precedent',
  'Data gap: test',
  'Alert: avoid',
];

// The residues the backend's rule table actually reasons about.
export const TRACKED: [string, string, string][] = [
  ['M', 'Met', 'oxidation'],
  ['W', 'Trp', 'oxidation'],
  ['C', 'Cys', 'Michael addition'],
  ['K', 'Lys', 'glycation, Michael addition'],
  ['H', 'His', 'alkylation'],
  ['N', 'Asn', 'deamidation'],
];

const AMINO_ACIDS = 'ACDEFGHIKLMNPQRSTVWY';
const SEV_ORDER: Record<Severity, number> = { high: 0, moderate: 1, low: 2 };

export const ROUTES = ['Subcutaneous', 'Intravenous', 'Intramuscular'] as const;
export const TEMPS = ['4°C (fridge)', '25°C (room temp)', '40°C (stressed)'] as const;

// A polymer, a disaccharide stabiliser, and a small molecule — enough to show
// the surrogate path and the no-alert path without typing anything.
export const PRESETS = ['Polysorbate 80', 'Polysorbate 20', 'Sucrose', 'Trehalose', 'Glycerol', 'PEG'];

export const STRUCTURE_PRESETS: [string, string][] = [
  ['P01857', 'IgG1 Fc · AlphaFold'],
  ['1IGT', 'Intact IgG · PDB'],
];

// Trastuzumab heavy-chain variable region — short, real, and rich in the residues above.
export const SAMPLE_SEQ =
  'EVQLVESGGGLVQPGGSLRLSCAASGFNIKDTYIHWVRQAPGKGLEWVARIYPTNGYTRYADSVKGRFTISADTSKNTAYLQMNSLRAEDTAVYYCSRWGGDGFYAMDYWGQGTLVTVSS';

export interface ScreenForm {
  excipient: string;
  sequence: string;
  structureId: string;
  route: string;
  dose: string;
  concentration: string;
  temp: string;
}

export const DEFAULT_FORM: ScreenForm = {
  excipient: 'Polysorbate 80',
  sequence: '',
  structureId: '',
  route: 'Subcutaneous',
  dose: '100',
  concentration: '',
  temp: '25°C (room temp)',
};

export function cleanSequence(raw: string): string {
  return raw.replace(/[\s\d]/g, '').toUpperCase();
}

export function sequenceStats(seq: string) {
  const counts = Object.fromEntries(
    TRACKED.map(([one]) => [one, (seq.match(new RegExp(one, 'g')) || []).length]),
  ) as Record<string, number>;
  const invalid = Array.from(new Set(seq.split('').filter((c) => !AMINO_ACIDS.includes(c))));
  return { counts, invalid };
}

// Mirrors accessibility._looks_like_pdb_id on the backend: 4 characters,
// leading digit. Anything else is sent as a UniProt accession → AlphaFold.
export function structureKind(id: string): 'pdb' | 'uniprot' | null {
  const s = id.trim();
  if (!s) return null;
  return /^[0-9][A-Za-z0-9]{3}$/.test(s) ? 'pdb' : 'uniprot';
}

// The backend takes one free-text prompt; this is the same sentence the
// previous frontend sent, so the agent sees nothing new.
export function buildPrompt(f: ScreenForm): string {
  const seq = cleanSequence(f.sequence);
  return (
    `Screen excipient '${f.excipient}' for a biologic given by the ${f.route} route, ` +
    `protein dose ${f.dose} mg, excipient concentration ${f.concentration.trim() || 'not given'}, ` +
    `storage at ${f.temp}. Protein sequence: ${seq || 'not provided'}. ` +
    `Protein structure identifier: ${f.structureId.trim() || 'none'}.`
  );
}

export function sortLiabilities(l: LiabilityFlag[]): LiabilityFlag[] {
  return [...l].sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity]);
}

export function verdictCounts(endpoints: EndpointResult[]): Record<Verdict, number> {
  const out = Object.fromEntries(VERDICTS.map((v) => [v, 0])) as Record<Verdict, number>;
  for (const e of endpoints) out[e.verdict] += 1;
  return out;
}

// The overview tiles summarise what the dossier returned — nothing here is
// fetched separately or guessed.
export function overview(d: Dossier) {
  const counts = verdictCounts(d.endpoints);
  const precedent = d.endpoints.filter((e) => /precedent|regulator/i.test(e.endpoint));
  const bestGrade = d.endpoints.reduce<Grade | null>(
    (best, e) => (best === null || e.evidence_grade < best ? e.evidence_grade : best),
    null,
  );
  const high = d.liabilities.filter((l) => l.severity === 'high').length;
  const modelled = d.liabilities.filter((l) => !l.accessibility.startsWith('not modelled')).length;
  return { counts, precedent, bestGrade, high, modelled };
}
