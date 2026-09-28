import type { ConditionKey, DesignGoal } from '@/lib/api';

// The conditions a simulation runs at, as api/conditions.py decides them: the
// user's own where they stated one, the default otherwise. Kept in step with
// DEFAULTS and LIMITS there; the result reports what the worker really applied.

export const DEFAULTS: Record<ConditionKey, number> = {
  temperature_c: 25,
  ph: 7,
  salt_mm: 150,
  polymer_wv_percent: 5,
};

const LIMITS: Record<ConditionKey, [number, number]> = {
  temperature_c: [0, 95],
  ph: [2, 12],
  salt_mm: [0, 1000],
  polymer_wv_percent: [0.5, 20],
};

export function formatCondition(key: ConditionKey, v: number): string {
  switch (key) {
    case 'temperature_c':
      return `${v} °C`;
    case 'ph':
      return `pH ${v}`;
    case 'salt_mm':
      return `${v} mM NaCl`;
    case 'polymer_wv_percent':
      return `${v}% w/v polymer`;
  }
}

export interface PlannedCondition {
  key: ConditionKey;
  text: string;
  fromRequest: boolean;
}

export function planned(goal: DesignGoal): PlannedCondition[] {
  const asked: Record<ConditionKey, number | null | undefined> = {
    temperature_c: goal.target_temp_c,
    ph: goal.ph,
    salt_mm: goal.salt_mm,
    polymer_wv_percent: goal.polymer_wv_percent,
  };
  return (Object.keys(DEFAULTS) as ConditionKey[]).map((key) => {
    const a = asked[key];
    if (a === null || a === undefined) return { key, text: formatCondition(key, DEFAULTS[key]), fromRequest: false };
    const [lo, hi] = LIMITS[key];
    return { key, text: formatCondition(key, Math.min(Math.max(a, lo), hi)), fromRequest: true };
  });
}
