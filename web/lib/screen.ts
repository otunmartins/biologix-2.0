import type {
  Dossier,
  EndpointResult,
  Grade,
  LiabilityFlag,
  PolymerSpec,
  Severity,
  Verdict,
} from './api';

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

export interface PolymerForm {
  enabled: boolean;
  repeatUnit: string;
  endA: string;
  endB: string;
  dp: string;
  impurities: { name: string; level: string }[];
}

export interface ScreenForm {
  excipient: string;
  sequence: string;
  structureId: string;
  route: string;
  dose: string;
  concentration: string;
  temp: string;
  polymer: PolymerForm;
}

// Starting points only: the chemistry is standard, the DP is a typical grade.
// Linear chains — a polysorbate's sorbitan core is folded into its ester end
// group, which carries the same reactive groups but not the four-arm shape.
export const POLYMER_PRESETS: {
  id: string;
  label: string;
  repeatUnit: string;
  endA: string;
  endB: string;
  dp: string;
}[] = [
  { id: 'ps80', label: 'Polysorbate 80', repeatUnit: '[*]CCO[*]', endA: '[*]OC(=O)CCCCCCCC=CCCCCCCCC', endB: '[*][H]', dp: '20' },
  { id: 'ps20', label: 'Polysorbate 20', repeatUnit: '[*]CCO[*]', endA: '[*]OC(=O)CCCCCCCCCCC', endB: '[*][H]', dp: '20' },
  { id: 'peg', label: 'PEG (HO–…–H)', repeatUnit: '[*]CCO[*]', endA: '[*]O', endB: '[*][H]', dp: '75' },
  { id: 'mpeg', label: 'mPEG (methoxy)', repeatUnit: '[*]CCO[*]', endA: '[*]OC', endB: '[*][H]', dp: '45' },
  { id: 'pvp', label: 'Povidone (PVP)', repeatUnit: '[*]CC([*])N1CCCC1=O', endA: '[*][H]', endB: '[*][H]', dp: '360' },
  { id: 'pva', label: 'Polyvinyl alcohol', repeatUnit: '[*]CC([*])O', endA: '[*][H]', endB: '[*][H]', dp: '1000' },
];

// Residuals and degradants worth naming. Levels are left for the user's spec.
export const IMPURITY_PRESETS = [
  'Ethylene oxide',
  '1,4-Dioxane',
  'Formaldehyde',
  'Acetaldehyde',
  'Hydrogen peroxide',
  'N-Vinylpyrrolidone',
];

// Mirrors POLYMER_ALIASES in api/main.py: names the backend would otherwise
// screen on a fixed surrogate, capped at grade D.
const POLYMER_NAMES: [RegExp, string][] = [
  [/^(?:polysorbate|tween)\s*80\b|^9005-65-6$/i, 'ps80'],
  [/^(?:polysorbate|tween)\s*(?:20|21|40|60|65|85)\b|^9005-64-5$/i, 'ps20'],
  [/^(?:peg|macrogol|polyethylene\s*glycol|polyoxyethylene)\b/i, 'peg'],
  [/^(?:pvp|povidone|polyvinylpyrrolidone|kollidon)\b/i, 'pvp'],
  [/^(?:pva|polyvinyl\s*alcohol)\b/i, 'pva'],
  [/^poloxamer\b/i, ''],
  [/^(?:hpmc|hypromellose|cmc|(?:sodium\s+)?carboxymethylcellulose|carmellose|dextran)\b/i, ''],
];

/** undefined: not a known polymer. '': a polymer with no linear preset. */
export function polymerFamily(name: string): string | undefined {
  const n = name.trim();
  for (const [re, id] of POLYMER_NAMES) if (re.test(n)) return id;
  return undefined;
}

export function presetPolymer(id: string, current: PolymerForm): PolymerForm {
  const p = POLYMER_PRESETS.find((x) => x.id === id) ?? POLYMER_PRESETS[2];
  return { ...current, repeatUnit: p.repeatUnit, endA: p.endA, endB: p.endB, dp: p.dp };
}

export function attachmentPoints(smiles: string): number {
  return (smiles.match(/\*/g) || []).length;
}

export function toPolymerSpec(p: PolymerForm): PolymerSpec | null {
  if (!p.enabled) return null;
  const dp = parseFloat(p.dp);
  return {
    repeat_unit: p.repeatUnit.trim(),
    end_group_a: p.endA.trim() || '[*][H]',
    end_group_b: p.endB.trim() || '[*][H]',
    dp: Number.isFinite(dp) && dp > 0 ? dp : null,
    impurities: p.impurities
      .filter((i) => i.name.trim())
      .map((i) => ({ name: i.name.trim(), level: i.level.trim() })),
  };
}

export const DEFAULT_FORM: ScreenForm = {
  excipient: 'Polysorbate 80',
  sequence: '',
  structureId: '',
  route: 'Subcutaneous',
  dose: '100',
  concentration: '',
  temp: '25°C (room temp)',
  polymer: presetPolymer('ps80', {
    enabled: false,
    repeatUnit: '',
    endA: '',
    endB: '',
    dp: '',
    impurities: [],
  }),
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
    `Protein structure identifier: ${f.structureId.trim() || 'none'}.` +
    // The description itself travels as structured data; this only tells the
    // agent to expect it.
    (f.polymer.enabled ? ' A structured polymer description was supplied with this request.' : '')
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
