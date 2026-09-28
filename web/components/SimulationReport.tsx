'use client';

import { useState } from 'react';
import { ChartCard, ForestPlot, HBars, type ForestRow } from '@/components/admin/charts';
import { readGamma, TIER_LABEL } from '@/components/DesignResults';
import MolViewer from '@/components/MolViewer';
import SimulationMetrics from '@/components/SimulationMetrics';
import { getSimulationSnapshot, type ConditionKey, type SimulationJob } from '@/lib/api';
import { formatCondition } from '@/lib/conditions';

// A finished simulation in full: what it found, where the polymer sat, whether
// the number had settled, and exactly how it was run. Everything shown is what
// the worker reported (api/main.py SimulationResult), nothing recomputed here.
// Charts reuse the validated admin set (components/admin/charts.tsx): Gamma23 on
// the blue<->red diverging pair about zero, contacts as one-hue bars.

const signColor = (g: number) => (g < 0 ? 'var(--div-neg-2)' : g > 0 ? 'var(--div-pos-2)' : 'var(--ink-muted)');
const signed = (v: number) => `${v > 0 ? '+' : ''}${v}`;
const pct = (f: number) => `${Math.round(f * 100)}%`;

function duration(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

function download(name: string, body: string, type: string) {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}

function Fact({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3.5 py-3">
      <div className="text-xs text-[color:var(--ink-muted)]">{label}</div>
      <div className="mt-0.5 text-lg font-semibold tabular-nums text-[color:var(--ink-1)]">{value}</div>
      {hint && <div className="text-[11px] text-[color:var(--ink-muted)]">{hint}</div>}
    </div>
  );
}

export default function SimulationReport({
  sim,
  candidateId,
  name,
  admin = false,
}: {
  sim: SimulationJob;
  candidateId: string;
  name: string;
  // Viewing someone else's run through the admin routes.
  admin?: boolean;
}) {
  const r = sim.result!;
  const read = readGamma(r.gamma23, r.gamma23_se);
  const preview = Boolean(r.preview) || sim.tier === 'cpu';
  const [busy, setBusy] = useState(false);
  const [dlError, setDlError] = useState<string | null>(null);

  const blocks: ForestRow[] = [
    ...r.gamma23_blocks.map((g, k) => ({
      id: `b${k}`,
      label: `Block ${k + 1}`,
      sub: `of ${r.gamma23_blocks.length}`,
      value: g,
      se: null,
      color: signColor(g),
      tip: [{ color: signColor(g), label: `block ${k + 1}`, value: `Γ23 ${signed(g)}` }],
    })),
    {
      id: 'mean',
      label: 'Whole run',
      sub: '± 2 SE',
      value: r.gamma23,
      se: r.gamma23_se,
      color: signColor(r.gamma23),
      tip: [{ color: signColor(r.gamma23), label: 'mean', value: `Γ23 ${signed(r.gamma23)}${r.gamma23_se !== null ? ` ± ${r.gamma23_se}` : ''}` }],
    },
  ];
  const profile: ForestRow[] = Object.entries(r.gamma23_profile)
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([nm, g]) => ({
      id: nm,
      label: `${nm} nm`,
      sub: Number(nm) === r.r_local_nm ? 'the cutoff reported' : undefined,
      value: g,
      se: null,
      color: signColor(g),
      tip: [{ color: signColor(g), label: `within ${nm} nm`, value: `Γ23 ${signed(g)}` }],
    }));
  const contacts = [...r.contacts].sort((a, b) => b.fraction - a.fraction);
  const conds = r.conditions && Object.keys(r.conditions).length ? (Object.entries(r.conditions) as [ConditionKey, number][]) : null;

  const details: [string, string][] = [
    ['Production', `${r.production_ns} ns, ${r.n_frames.toLocaleString()} frames analysed`],
    ['System', `${r.n_atoms.toLocaleString()} atoms, ${r.n_chains} polymer chains`],
    ...(r.bulk_chain_molar ? [['Bulk polymer', `${(r.bulk_chain_molar * 1000).toFixed(2)} mM chains, as the protein saw it`] as [string, string]] : []),
    ['Domains', `local within ${r.r_local_nm} nm of the protein, bulk beyond ${r.r_bulk_nm} nm`],
    ['Engine', r.engine],
    ['Force fields', r.forcefields],
    ['Structure', r.structure_source || sim.structure_id],
    ...(r.qc
      ? [[
          'Run check',
          `${r.qc.temperature_k} ± ${r.qc.temperature_sd} K (asked ${r.qc.target_k} K), density ${r.qc.density_g_ml} g/mL${r.qc.ok ? '' : ' — temperature off target'}`,
        ] as [string, string]]
      : []),
    ['Compute time', duration(r.wall_seconds)],
    ...(sim.finished_at ? [['Finished', new Date(sim.finished_at).toLocaleString()] as [string, string]] : []),
  ];

  const saveSnapshot = async () => {
    setBusy(true);
    setDlError(null);
    try {
      download(`${name || 'simulation'}-last-frame.pdb`.replace(/[^\w.-]+/g, '_'), await getSimulationSnapshot(candidateId, undefined, admin), 'chemical/x-pdb');
    } catch (e) {
      setDlError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="viz-root space-y-5">
      {/* The finding, first and in words. */}
      <section className="rounded-2xl border border-slate-200 bg-[color:var(--surface-1)] p-5">
        <div className="eyebrow mb-1.5">
          OpenMM · against {sim.structure_id}
          {sim.tier && ` · ${TIER_LABEL[sim.tier]}`}
        </div>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="font-mono text-2xl font-semibold tabular-nums text-[color:var(--ink-1)]">
            Γ23 = {signed(r.gamma23)}
            {r.gamma23_se !== null && <span className="text-lg font-normal text-[color:var(--ink-muted)]"> ± {r.gamma23_se}</span>}
          </span>
          <span className={`text-sm font-semibold ${read.tone}`}>{read.label}</span>
          {r.smoke && <span className="rounded-full border border-gap-line bg-gap-soft px-2 py-0.5 text-[11px] font-semibold text-gap">smoke test, not a measurement</span>}
          {preview && !r.smoke && <span className="rounded-full border border-gap-line bg-gap-soft px-2 py-0.5 text-[11px] font-semibold text-gap">CPU preview, not converged</span>}
        </div>
        <p className="mt-2 text-sm leading-relaxed text-[color:var(--ink-2)]">{read.body}</p>
        <p className="mt-2 text-xs leading-relaxed text-[color:var(--ink-muted)]">
          Γ23 counts polymer chains per protein: how many more (or fewer) sit near the protein than the bulk solution
          would put there. Grade D until an experiment agrees.
        </p>
      </section>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Fact label="Γ23" value={signed(r.gamma23)} hint={r.gamma23_se !== null ? `± ${r.gamma23_se} (1 SE)` : undefined} />
        <Fact label="Residues touched" value={`${contacts.length}`} hint={contacts[0] ? `most: ${contacts[0].residue} (${pct(contacts[0].fraction)})` : undefined} />
        <Fact label="Simulated" value={`${r.production_ns} ns`} hint={`${r.n_frames.toLocaleString()} frames`} />
        <Fact label="System" value={`${Math.round(r.n_atoms / 1000)}k atoms`} hint={`${r.n_chains} chains`} />
      </div>

      {/* Where it sat. */}
      <section className="rounded-2xl border border-slate-200 bg-[color:var(--surface-1)] p-5">
        <h3 className="text-[15px] font-semibold text-[color:var(--ink-1)]">The last frame in 3D</h3>
        <p className="mb-3 mt-0.5 text-xs leading-relaxed text-[color:var(--ink-muted)]">
          The protein as a ribbon, each polymer chain where it sat at the end of the run, and the residues it touched
          most in blue.
        </p>
        <MolViewer
          structureId={sim.structure_id}
          candidateId={candidateId}
          hasSnapshot={Boolean(r.has_snapshot)}
          contacts={contacts}
          admin={admin}
        />
        {!r.has_snapshot && (
          <p className="mt-2 text-xs text-[color:var(--ink-muted)]">
            This run finished before the worker saved its last frame, so the polymer is not drawn; the residues it
            touched most are.
          </p>
        )}
      </section>

      <ChartCard
        title="Where the polymer touched the protein"
        subtitle="Share of analysed frames in which a polymer heavy atom was in contact with each residue, most-touched first. A residue that is touched often is where the polymer binds, or where it shields."
        table={{ head: ['Residue', 'Frames in contact'], rows: contacts.map((c) => [c.residue, pct(c.fraction)]) }}
      >
        {contacts.length ? (
          <HBars items={contacts.map((c) => ({ label: c.residue, value: c.fraction }))} color="var(--ramp-2)" format={pct} share={false} max={1} />
        ) : (
          <p className="text-sm text-[color:var(--ink-muted)]">The polymer touched no residue often enough to list.</p>
        )}
      </ChartCard>

      <SimulationMetrics r={r} />

      {blocks.length > 1 && (
        <ChartCard
          title="Did the number settle?"
          subtitle="Γ23 in each successive block of the run, and the whole-run mean with ±2 SE. Blocks that scatter on both sides of zero mean the run cannot yet tell the polymer from water; blocks that agree mean it has settled."
          table={{ head: ['Block', 'Γ23'], rows: blocks.map((b) => [b.label, signed(b.value)]) }}
        >
          <ForestPlot rows={blocks} unit="chains per protein" />
        </ChartCard>
      )}

      {profile.length > 1 && (
        <ChartCard
          title="Γ23 by how near counts as 'near'"
          subtitle="The same run, counted with different local-domain cutoffs. Where the value stops changing with the cutoff, the local domain has captured every chain the protein perturbs."
          table={{ head: ['Cutoff', 'Γ23'], rows: profile.map((p) => [p.label, signed(p.value)]) }}
        >
          <ForestPlot rows={profile} unit="chains per protein" />
        </ChartCard>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <section className="rounded-2xl border border-slate-200 bg-[color:var(--surface-1)] p-5">
          <h3 className="mb-3 text-[15px] font-semibold text-[color:var(--ink-1)]">Conditions it ran at</h3>
          {conds ? (
            <dl className="space-y-1.5 text-sm">
              {conds.map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3">
                  <dt className="text-[color:var(--ink-muted)]">{{ temperature_c: 'Temperature', ph: 'pH', salt_mm: 'Salt', polymer_wv_percent: 'Polymer' }[k] ?? k}</dt>
                  <dd className="font-medium tabular-nums text-[color:var(--ink-1)]">{formatCondition(k, v)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-sm text-[color:var(--ink-2)]">
              {Math.round(r.temperature_k - 273.15)} °C. This run predates the worker reporting its other conditions;
              it used the fixed setup of the time (0.15 M NaCl, 5% w/v polymer).
            </p>
          )}
          <p className="mt-3 text-xs leading-relaxed text-[color:var(--ink-muted)]">
            What the worker applied, which is what counts. Protein backbone held to its native structure during production{r.stability ? `, then released for the stress stage at ${r.stability.temperature_c} °C` : ''}.
          </p>
        </section>

        <section className="rounded-2xl border border-slate-200 bg-[color:var(--surface-1)] p-5">
          <h3 className="mb-3 text-[15px] font-semibold text-[color:var(--ink-1)]">How it was run</h3>
          <dl className="space-y-1.5 text-sm">
            {details.map(([k, v]) => (
              <div key={k} className="grid grid-cols-[7.5rem_1fr] gap-3">
                <dt className="text-[color:var(--ink-muted)]">{k}</dt>
                <dd className="break-words text-[color:var(--ink-1)]">{v}</dd>
              </div>
            ))}
          </dl>
        </section>
      </div>

      {r.notes.length > 0 && (
        <section className="rounded-2xl border border-slate-200 bg-[color:var(--surface-1)] p-5">
          <h3 className="mb-2 text-[15px] font-semibold text-[color:var(--ink-1)]">The worker's notes</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-[color:var(--ink-2)]">
            {r.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </section>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {r.has_snapshot && (
          <button type="button" className="btn-ghost" onClick={saveSnapshot} disabled={busy}>
            {busy ? 'Preparing…' : 'Download the last frame (.pdb)'}
          </button>
        )}
        <button
          type="button"
          className="btn-ghost"
          onClick={() =>
            download(`${name || 'simulation'}-result.json`.replace(/[^\w.-]+/g, '_'), JSON.stringify({ candidate: name, structure: sim.structure_id, tier: sim.tier, ...r }, null, 2), 'application/json')
          }
        >
          Download the result (.json)
        </button>
        {dlError && <span className="text-xs text-alert">{dlError}</span>}
      </div>
    </div>
  );
}
