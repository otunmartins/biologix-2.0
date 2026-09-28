'use client';

import { useLayoutEffect, useRef, useState } from 'react';
import { ChartCard, HBars } from '@/components/admin/charts';
import type { SimulationResult } from '@/lib/api';

// What a run measured beyond Γ23 (worker/metrics.py): Tier 1 from the same
// restrained frames -- interaction energy, hydrogen bonds, residence, liability
// coverage, self-association, QC -- and Tier 2 from the unrestrained stage at a
// stress temperature. Runs from before these metrics show nothing here.

const pct = (f: number) => `${Math.round(f * 100)}%`;
const kj = (v: number) => `${v > 0 ? '+' : ''}${v.toLocaleString(undefined, { maximumFractionDigits: 1 })} kJ/mol`;
const ps = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(2)} ns` : `${Math.round(v)} ps`);

function Fact({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3.5 py-3">
      <div className="text-xs text-[color:var(--ink-muted)]">{label}</div>
      <div className="mt-0.5 text-lg font-semibold tabular-nums text-[color:var(--ink-1)]">{value}</div>
      {hint && <div className="text-[11px] text-[color:var(--ink-muted)]">{hint}</div>}
    </div>
  );
}

function Section({ title, sub, children }: { title: string; sub: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-base font-semibold text-[color:var(--ink-1)]">{title}</h3>
        <p className="mt-0.5 text-xs leading-relaxed text-[color:var(--ink-muted)]">{sub}</p>
      </div>
      {children}
    </section>
  );
}

// Lines over simulated time on a fixed 0..max axis, each labelled at its end.
function Series({ t, lines, max, unit }: { t: number[]; lines: { label: string; values: number[]; color: string }[]; max: number; unit: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  const h = 170;
  const padL = 34;
  const padR = 64;
  const padB = 20;
  const plotW = Math.max(0, w - padL - padR);
  const plotH = h - padB;
  const tMax = Math.max(1e-9, t.at(-1) ?? 1);
  const X = (x: number) => padL + (x / tMax) * plotW;
  const Y = (v: number) => plotH - (Math.min(v, max) / max) * (plotH - 8);
  const ticks = [0, max / 2, max];
  return (
    <div ref={ref} className="w-full">
      {w > 0 && (
        <svg width={w} height={h} role="img" aria-label={lines.map((l) => `${l.label} ends at ${l.values.at(-1)}`).join('; ')}>
          {ticks.map((v) => (
            <g key={v}>
              <line x1={padL} x2={padL + plotW} y1={Y(v)} y2={Y(v)} stroke="var(--grid)" />
              <text x={padL - 6} y={Y(v) + 3} textAnchor="end" fontSize={10} fill="var(--ink-muted)">
                {+v.toFixed(2)}
              </text>
            </g>
          ))}
          <line x1={padL} x2={padL + plotW} y1={plotH} y2={plotH} stroke="var(--axis)" />
          {[0, tMax / 2, tMax].map((x) => (
            <text key={x} x={X(x)} y={h - 5} textAnchor="middle" fontSize={10} fill="var(--ink-muted)">
              {+x.toFixed(2)} ns
            </text>
          ))}
          {lines.map((l) => {
            const d = l.values.map((v, k) => `${k ? 'L' : 'M'}${X(t[k] ?? 0).toFixed(1)},${Y(v).toFixed(1)}`).join('');
            const last = l.values.at(-1) ?? 0;
            return (
              <g key={l.label}>
                <path d={d} fill="none" stroke={l.color} strokeWidth={2} strokeLinejoin="round" />
                <text x={X(t[l.values.length - 1] ?? tMax) + 6} y={Y(last) + 4} fontSize={11} fontWeight={600} fill={l.color}>
                  {l.label} {+last.toFixed(2)}
                </text>
              </g>
            );
          })}
        </svg>
      )}
      <p className="mt-1 text-[11px] text-[color:var(--ink-muted)]">{unit}</p>
    </div>
  );
}

function stabilityReading(s: NonNullable<SimulationResult['stability']>): { label: string; tone: string; body: string } {
  const q = s.q.mean_last_half;
  const rmsd = s.rmsd_nm.mean_last_half;
  if (q >= 0.85 && rmsd < 0.3)
    return { label: 'Held its fold', tone: 'text-precedented', body: `Native contacts stayed at ${pct(q)} and the backbone within ${rmsd} nm of where it started, over ${s.ns} ns at ${s.temperature_c} °C.` };
  if (q >= 0.7)
    return { label: 'Loosened', tone: 'text-gap', body: `Native contacts fell to ${pct(q)} (backbone RMSD ${rmsd} nm): the fold loosened under stress. Compare with other polymers on this protein.` };
  return { label: 'Began to unfold', tone: 'text-alert', body: `Only ${pct(q)} of native contacts remained (backbone RMSD ${rmsd} nm) at ${s.temperature_c} °C. Compare with other polymers on this protein before reading much into it.` };
}

export default function SimulationMetrics({ r }: { r: SimulationResult }) {
  const e = r.interaction_energy;
  const hb = r.hbonds;
  const res = r.residence;
  const sa = r.self_association;
  const liab = r.liability_coverage ?? [];
  const s = r.stability;
  if (!e && !s) return null;
  return (
    <div className="space-y-6">
      {e && (
        <Section
          title="How the polymer held on"
          sub="From the same frames as Γ23, with the protein held in its native fold. The energy is the direct polymer–protein interaction energy from the force field (not a binding free energy: no desolvation, no entropy)."
        >
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Fact label="Interaction energy" value={kj(e.total_kj)} hint={`elec ${kj(e.elec_kj)} · vdW ${kj(e.vdw_kj)}`} />
            {hb && <Fact label="Polymer–protein H-bonds" value={hb.polymer_protein.toLocaleString()} hint={`per frame · ${Math.round(hb.protein_water).toLocaleString()} protein–water`} />}
            {res && <Fact label="Chains in contact" value={pct(res.bound_fraction)} hint={`of chains, averaged over frames`} />}
            {res && <Fact label="Contact lasts" value={ps(res.chain_mean_ps)} hint={`mean · longest ${ps(res.chain_max_ps)} · ${res.binding_events} events`} />}
          </div>

          {e.per_residue.length > 0 && (
            <ChartCard
              title="Where the attraction is"
              subtitle="Residues with the most favourable (most negative) interaction energy with the polymer, averaged over frames. Electrostatic and van der Waals parts are in the table."
              table={{ head: ['Residue', 'Total', 'Electrostatic', 'van der Waals'], rows: e.per_residue.map((p) => [p.residue, kj(p.kj), kj(p.elec_kj), kj(p.vdw_kj)]) }}
            >
              <HBars items={e.per_residue.map((p) => ({ label: p.residue, value: -p.kj }))} color="var(--ramp-2)" format={(v) => kj(-v)} share={false} />
            </ChartCard>
          )}

          {liab.length > 0 && (
            <ChartCard
              title="Liability residues the polymer covers"
              subtitle="Share of frames the polymer touched each class of exposed liability residue, averaged over the sites. Covering an oxidation site or a hydrophobic patch can shield it; a reactive polymer on a Lys or Met can also be the problem."
              table={{ head: ['Class', 'Sites', 'Coverage', 'Most covered'], rows: liab.map((c) => [c.class, c.n_sites, pct(c.coverage), c.residues.slice(0, 3).map((x) => `${x.residue} ${pct(x.fraction)}`).join(', ')]) }}
            >
              <HBars items={liab.map((c) => ({ label: `${c.class} (${c.n_sites})`, value: c.coverage }))} color="var(--ramp-3)" format={pct} share={false} max={1} />
            </ChartCard>
          )}

          <div className="grid gap-4 md:grid-cols-2">
            {res && res.residues.length > 0 && (
              <ChartCard
                title="Longest-lived contacts"
                subtitle="Mean time the polymer stayed on a residue once it arrived. Long stays mean it binds there; brief ones, that it passes by."
                table={{ head: ['Residue', 'Mean stay', 'Longest', 'Visits'], rows: res.residues.map((x) => [x.residue, ps(x.mean_ps), ps(x.max_ps), x.events]) }}
              >
                <HBars items={res.residues.slice(0, 8).map((x) => ({ label: x.residue, value: x.mean_ps }))} color="var(--ramp-2)" format={ps} share={false} />
              </ChartCard>
            )}
            {hb && hb.per_residue.length > 0 && (
              <ChartCard
                title="Hydrogen bonds by residue"
                subtitle="Polymer–protein hydrogen bonds per frame at each residue (donor–acceptor ≤ 0.35 nm, ≥ 150°). Polymer H-bonds standing in for water's matter most for a dried product."
                table={{ head: ['Residue', 'H-bonds per frame'], rows: hb.per_residue.map((x) => [x.residue, x.per_frame]) }}
              >
                <HBars items={hb.per_residue.slice(0, 8).map((x) => ({ label: x.residue, value: x.per_frame }))} color="var(--ramp-3)" format={(v) => v.toFixed(2)} share={false} />
              </ChartCard>
            )}
          </div>

          {sa && (
            <p className="text-sm leading-relaxed text-[color:var(--ink-2)]">
              <b className="font-semibold">Self-association.</b> Of {sa.n_chains} chains, {pct(sa.free_fraction)} were on their own in an
              average frame; the largest cluster averaged {sa.mean_largest_cluster} chains (at most {sa.max_cluster}).
              {sa.mean_largest_cluster >= Math.max(2, sa.n_chains / 2) ? ' The polymer mostly sticks to itself, which lowers what is free to reach the protein.' : ''}
            </p>
          )}
        </Section>
      )}

      {s && (() => {
        const read = stabilityReading(s);
        return (
          <Section
            title={`Under stress: ${s.ns} ns unrestrained at ${s.temperature_c} °C`}
            sub="After production the hold on the backbone was released and the temperature raised. Measured against the structure at the start of this stage. Nanoseconds at high temperature compare polymers on the same protein; they are not a melting temperature, and there is no polymer-free control yet."
          >
            <div className="rounded-2xl border border-slate-200 bg-[color:var(--surface-1)] p-4">
              <span className={`text-sm font-semibold ${read.tone}`}>{read.label}.</span>{' '}
              <span className="text-sm text-[color:var(--ink-2)]">{read.body}</span>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Fact label="Native contacts Q" value={pct(s.q.final)} hint={`end · lowest ${pct(s.q.min)}`} />
              <Fact label="Backbone RMSD" value={`${s.rmsd_nm.final} nm`} hint={`end · max ${s.rmsd_nm.max} nm`} />
              <Fact label="Secondary structure kept" value={pct(s.secondary.retained)} hint={`helix ${pct(s.secondary.helix_start)} → ${pct(s.secondary.helix_final)} · strand ${pct(s.secondary.strand_start)} → ${pct(s.secondary.strand_final)}`} />
              <Fact label="Hydrophobic surface" value={`${s.sasa_nm2.hydrophobic_final} nm²`} hint={`from ${s.sasa_nm2.hydrophobic_start} · Rg ${s.rg_nm.start} → ${s.rg_nm.final} nm`} />
            </div>
            <ChartCard
              title="The fold over time"
              subtitle="Fraction of native contacts kept (Q) and C-alpha RMSD from the start of the stage. Q falling while RMSD climbs is unfolding; both flat is a stable fold."
              table={{ head: ['Time (ns)', 'Q', 'RMSD (nm)'], rows: s.series.t_ns.map((t, k) => [t, s.series.q[k], s.series.rmsd[k]]) }}
            >
              <Series
                t={s.series.t_ns}
                max={Math.max(1, ...s.series.rmsd)}
                unit="Q is a fraction (0–1); RMSD in nm."
                lines={[
                  { label: 'Q', values: s.series.q, color: 'var(--div-neg-2)' },
                  { label: 'RMSD', values: s.series.rmsd, color: 'var(--div-pos-2)' },
                ]}
              />
            </ChartCard>
            {s.rmsf_top.length > 0 && (
              <ChartCard
                title="The most mobile residues"
                subtitle="C-alpha fluctuation (RMSF) over the stage. Mobile loops are normal; a mobile stretch that was structured, or one the polymer sits on, is worth a look."
                table={{ head: ['Residue', 'RMSF (nm)'], rows: s.rmsf_residues.map((x, k) => [x, s.rmsf[k]]) }}
              >
                <HBars items={s.rmsf_top.slice(0, 10).map((x) => ({ label: x.residue, value: x.nm }))} color="var(--ramp-2)" format={(v) => `${v.toFixed(2)} nm`} share={false} />
              </ChartCard>
            )}
          </Section>
        );
      })()}
    </div>
  );
}
