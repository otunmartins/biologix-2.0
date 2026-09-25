import type { Grade, Severity, Verdict } from '@/lib/api';
import { GRADE_MEANING } from '@/lib/screen';
import { CheckCircle, Flask, Triangle } from './icons';

export const VERDICT_STYLE: Record<Verdict, { pill: string; icon: typeof CheckCircle; text: string }> = {
  Precedented: {
    pill: 'border-precedented-line bg-precedented-soft text-precedented',
    icon: CheckCircle,
    text: 'text-precedented',
  },
  'Supported without precedent': {
    pill: 'border-supported-line bg-supported-soft text-supported',
    icon: CheckCircle,
    text: 'text-supported',
  },
  'Data gap: test': {
    pill: 'border-gap-line bg-gap-soft text-gap',
    icon: Flask,
    text: 'text-gap',
  },
  'Alert: avoid': {
    pill: 'border-alert-line bg-alert-soft text-alert',
    icon: Triangle,
    text: 'text-alert',
  },
};

export function VerdictPill({ verdict, count }: { verdict: Verdict; count?: number }) {
  const s = VERDICT_STYLE[verdict];
  const Icon = s.icon;
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-[13px] font-semibold ${s.pill}`}
    >
      <Icon className="h-4 w-4 shrink-0" />
      {count !== undefined && <span className="tabular-nums">{count}</span>}
      {verdict}
    </span>
  );
}

const GRADE_TONE: Record<Grade, string> = {
  A: 'border-precedented-line text-precedented',
  B: 'border-supported-line text-supported',
  C: 'border-slate-300 text-slate-700',
  D: 'border-gap-line text-gap',
  E: 'border-alert-line text-alert',
};

export function GradeBox({ grade }: { grade: Grade }) {
  return (
    <span
      title={`Grade ${grade} — ${GRADE_MEANING[grade]}`}
      className={`inline-grid h-8 w-8 place-items-center rounded-md border-2 bg-white font-mono text-sm font-semibold ${GRADE_TONE[grade]}`}
    >
      {grade}
    </span>
  );
}

const SEV_STYLE: Record<Severity, string> = {
  high: 'bg-alert-soft text-alert border-alert-line',
  moderate: 'bg-gap-soft text-gap border-gap-line',
  low: 'bg-slate-100 text-slate-600 border-slate-200',
};

export function SeverityTag({ severity }: { severity: Severity }) {
  return (
    <span
      className={`rounded-md border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${SEV_STYLE[severity]}`}
    >
      {severity}
    </span>
  );
}
