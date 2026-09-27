'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';

// Small hand-built SVG charts for the admin dashboard. No chart library: each is
// a few dozen lines, and they share one tooltip and one look. Rules they follow
// (see the dataviz guidance): thin marks with 4px rounded data-ends on the
// baseline, a 2px surface gap between bars, hairline recessive grid, text in ink
// colours never the series colour, a hover/focus tooltip on every mark, and a
// table view so no value depends on hovering or on colour.

export const fmt = (n: number) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(n >= 1e5 ? 0 : 1)}k` : n.toLocaleString();

const shortDate = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });

// Width of a container, so charts fill their card and redraw on resize.
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.floor(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

// ---------------------------------------------------------------------------
// Tooltip: values lead, labels follow; a short stroke keys the series.
// ---------------------------------------------------------------------------

export interface TipRow {
  color?: string;
  label: string;
  value: string;
}
interface Tip {
  x: number;
  y: number;
  title: string;
  rows: TipRow[];
}

function TooltipBox({ tip, width }: { tip: Tip; width: number }) {
  const left = Math.min(Math.max(tip.x + 12, 0), Math.max(0, width - 180));
  return (
    <div
      role="status"
      className="pointer-events-none absolute z-10 min-w-[140px] rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg"
      style={{ left, top: Math.max(0, tip.y - 12) }}
    >
      <div className="mb-1 font-medium text-[color:var(--ink-2)]">{tip.title}</div>
      {tip.rows.map((r) => (
        <div key={r.label} className="flex items-center gap-2">
          {r.color && <span className="h-[2px] w-3 rounded" style={{ background: r.color }} aria-hidden="true" />}
          <span className="font-semibold tabular-nums text-[color:var(--ink-1)]">{r.value}</span>
          <span className="text-[color:var(--ink-muted)]">{r.label}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sparkline: trend only, inside a stat tile. Last point marked.
// ---------------------------------------------------------------------------

export function Sparkline({ values, color, height = 36 }: { values: number[]; color: string; height?: number }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const max = Math.max(1, ...values);
  const pts = values.map((v, i) => [(i / Math.max(1, values.length - 1)) * (w - 4) + 2, height - 3 - (v / max) * (height - 6)]);
  const d = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join('');
  const area = pts.length ? `${d}L${pts.at(-1)![0]},${height}L${pts[0][0]},${height}Z` : '';
  return (
    <div ref={ref} className="h-9 w-full" aria-hidden="true">
      {w > 0 && (
        <svg width={w} height={height}>
          <path d={area} fill={color} opacity={0.12} />
          <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {pts.length > 0 && <circle cx={pts.at(-1)![0]} cy={pts.at(-1)![1]} r={3} fill={color} stroke="var(--surface-1)" strokeWidth={2} />}
        </svg>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stat tile: the number is the chart.
// ---------------------------------------------------------------------------

export function StatTile({
  label,
  value,
  color,
  spark,
  current,
  previous,
  sub,
}: {
  label: string;
  value: number;
  color: string;
  spark?: number[];
  current?: number;
  previous?: number;
  sub?: string;
}) {
  const delta = current !== undefined && previous !== undefined ? current - previous : null;
  return (
    <div className="relative overflow-hidden rounded-2xl border border-slate-200 bg-[color:var(--surface-1)] p-4 shadow-card">
      <div className="absolute inset-x-0 top-0 h-1" style={{ background: color }} aria-hidden="true" />
      <div className="text-[13px] font-medium text-[color:var(--ink-2)]">{label}</div>
      <div className="mt-1 text-3xl font-semibold tracking-tight text-[color:var(--ink-1)]">{value.toLocaleString()}</div>
      <div className="mt-0.5 min-h-[18px] text-xs text-[color:var(--ink-muted)]">
        {delta !== null ? (
          <>
            <span className={delta > 0 ? 'font-semibold text-[#006300]' : delta < 0 ? 'font-semibold text-[#b42318]' : ''}>
              {delta > 0 ? '▲' : delta < 0 ? '▼' : '='} {Math.abs(delta).toLocaleString()}
            </span>{' '}
            vs previous period
          </>
        ) : (
          sub
        )}
      </div>
      {spark && (
        <div className="mt-2">
          <Sparkline values={spark} color={color} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card: a titled chart with an optional table view.
// ---------------------------------------------------------------------------

export function ChartCard({
  title,
  subtitle,
  children,
  table,
  className = '',
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  table?: { head: string[]; rows: (string | number)[][] };
  className?: string;
}) {
  const [showTable, setShowTable] = useState(false);
  return (
    <section className={`rounded-2xl border border-slate-200 bg-[color:var(--surface-1)] p-5 shadow-card ${className}`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-[15px] font-semibold text-[color:var(--ink-1)]">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs leading-relaxed text-[color:var(--ink-muted)]">{subtitle}</p>}
        </div>
        {table && (
          <button
            type="button"
            onClick={() => setShowTable((s) => !s)}
            className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-[color:var(--ink-2)] hover:bg-slate-100"
            aria-pressed={showTable}
          >
            {showTable ? 'Chart' : 'Table'}
          </button>
        )}
      </div>
      {showTable && table ? (
        <div className="max-h-72 overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                {table.head.map((h) => (
                  <th key={h} className="border-b border-slate-200 py-1.5 text-left text-xs font-medium text-[color:var(--ink-muted)]">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((r, i) => (
                <tr key={i} className="border-b border-slate-100">
                  {r.map((c, j) => (
                    <td key={j} className={`py-1.5 ${j ? 'text-right tabular-nums' : ''} text-[color:var(--ink-2)]`}>
                      {c}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        children
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Daily bars: one metric over the window, a bar per day.
// ---------------------------------------------------------------------------

export function DailyBars({
  dates,
  values,
  color,
  label,
  height = 150,
}: {
  dates: string[];
  values: number[];
  color: string;
  label: string;
  height?: number;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const padL = 28;
  const padB = 20;
  const plotW = Math.max(0, w - padL);
  const plotH = height - padB;
  const max = Math.max(1, ...values);
  const step = plotW / Math.max(1, values.length);
  const gap = step > 6 ? 2 : step > 3 ? 1 : 0;
  const bw = Math.max(1, step - gap);
  const ticks = [0, Math.ceil(max / 2), max];
  const labelEvery = Math.ceil(values.length / Math.max(2, Math.floor(plotW / 70)));
  const show = (i: number, x: number, y: number) => {
    setHover(i);
    setTip({ x, y, title: shortDate(dates[i]), rows: [{ color, label, value: values[i].toLocaleString() }] });
  };
  return (
    <div ref={ref} className="relative w-full" onPointerLeave={() => (setTip(null), setHover(null))}>
      {w > 0 && (
        <svg width={w} height={height} role="img" aria-label={`${label} per day`}>
          {ticks.map((t) => {
            const y = plotH - (t / max) * (plotH - 6);
            return (
              <g key={t}>
                <line x1={padL} x2={w} y1={y} y2={y} stroke="var(--grid)" strokeWidth={1} />
                <text x={padL - 6} y={y + 3} textAnchor="end" fontSize={10} fill="var(--ink-muted)">
                  {fmt(t)}
                </text>
              </g>
            );
          })}
          {values.map((v, i) => {
            const h = (v / max) * (plotH - 6);
            const x = padL + i * step;
            const r = Math.min(4, bw / 2, h);
            return (
              <g key={i}>
                {v > 0 && (
                  <path
                    d={`M${x},${plotH}V${plotH - h + r}Q${x},${plotH - h} ${x + r},${plotH - h}H${x + bw - r}Q${x + bw},${plotH - h} ${x + bw},${plotH - h + r}V${plotH}Z`}
                    fill={color}
                    opacity={hover === null || hover === i ? 1 : 0.45}
                  />
                )}
                <rect
                  x={x}
                  y={0}
                  width={step}
                  height={plotH}
                  fill="transparent"
                  tabIndex={0}
                  aria-label={`${shortDate(dates[i])}: ${v} ${label}`}
                  onPointerMove={(e) => show(i, e.nativeEvent.offsetX, e.nativeEvent.offsetY)}
                  onFocus={() => show(i, x, plotH - h)}
                  onBlur={() => (setTip(null), setHover(null))}
                  className="outline-none"
                />
              </g>
            );
          })}
          <line x1={padL} x2={w} y1={plotH} y2={plotH} stroke="var(--axis)" strokeWidth={1} />
          {dates.map((d, i) =>
            i % labelEvery === 0 ? (
              <text key={d} x={padL + i * step + step / 2} y={height - 5} textAnchor="middle" fontSize={10} fill="var(--ink-muted)">
                {shortDate(d)}
              </text>
            ) : null,
          )}
        </svg>
      )}
      {tip && <TooltipBox tip={tip} width={w} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Line with crosshair: a cumulative series.
// ---------------------------------------------------------------------------

export function LineChart({
  dates,
  values,
  color,
  label,
  height = 180,
}: {
  dates: string[];
  values: number[];
  color: string;
  label: string;
  height?: number;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [i, setI] = useState<number | null>(null);
  const padL = 28;
  const padB = 20;
  const plotW = Math.max(0, w - padL - 8);
  const plotH = height - padB;
  const max = Math.max(1, ...values);
  const X = (k: number) => padL + (k / Math.max(1, values.length - 1)) * plotW;
  const Y = (v: number) => plotH - (v / max) * (plotH - 10);
  const d = values.map((v, k) => `${k ? 'L' : 'M'}${X(k).toFixed(1)},${Y(v).toFixed(1)}`).join('');
  const labelEvery = Math.ceil(values.length / Math.max(2, Math.floor(plotW / 70)));
  const nearest = (px: number) => Math.min(values.length - 1, Math.max(0, Math.round(((px - padL) / plotW) * (values.length - 1))));
  return (
    <div
      ref={ref}
      className="relative w-full outline-none"
      tabIndex={0}
      aria-label={`${label}: ${values.at(-1) ?? 0} now`}
      onPointerMove={(e) => setI(nearest(e.nativeEvent.offsetX))}
      onPointerLeave={() => setI(null)}
      onKeyDown={(e) => {
        if (e.key === 'ArrowLeft') setI((k) => Math.max(0, (k ?? values.length) - 1));
        if (e.key === 'ArrowRight') setI((k) => Math.min(values.length - 1, (k ?? -1) + 1));
      }}
      onBlur={() => setI(null)}
    >
      {w > 0 && (
        <svg width={w} height={height} role="img" aria-label={label}>
          {[0, Math.ceil(max / 2), max].map((t) => (
            <g key={t}>
              <line x1={padL} x2={w} y1={Y(t)} y2={Y(t)} stroke="var(--grid)" />
              <text x={padL - 6} y={Y(t) + 3} textAnchor="end" fontSize={10} fill="var(--ink-muted)">
                {fmt(t)}
              </text>
            </g>
          ))}
          <path d={`${d}L${X(values.length - 1)},${plotH}L${X(0)},${plotH}Z`} fill={color} opacity={0.1} />
          <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
          <line x1={padL} x2={w} y1={plotH} y2={plotH} stroke="var(--axis)" />
          {dates.map((dt, k) =>
            k % labelEvery === 0 ? (
              <text key={dt} x={X(k)} y={height - 5} textAnchor="middle" fontSize={10} fill="var(--ink-muted)">
                {shortDate(dt)}
              </text>
            ) : null,
          )}
          {/* Direct label on the endpoint: the value that matters. */}
          {values.length > 0 && i === null && (
            <text x={X(values.length - 1) - 4} y={Math.max(12, Y(values.at(-1)!) - 8)} textAnchor="end" fontSize={11} fontWeight={600} fill="var(--ink-1)">
              {values.at(-1)!.toLocaleString()}
            </text>
          )}
          {i !== null && (
            <>
              <line x1={X(i)} x2={X(i)} y1={0} y2={plotH} stroke="var(--axis)" />
              <circle cx={X(i)} cy={Y(values[i])} r={4} fill={color} stroke="var(--surface-1)" strokeWidth={2} />
            </>
          )}
        </svg>
      )}
      {i !== null && (
        <TooltipBox tip={{ x: X(i), y: Y(values[i]), title: shortDate(dates[i]), rows: [{ color, label, value: values[i].toLocaleString() }] }} width={w} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Horizontal bars: a ranked breakdown, value labels at the bar ends.
// ---------------------------------------------------------------------------

export function HBars({
  items,
  color,
  colors,
}: {
  items: { label: string; value: number }[];
  color?: string;
  colors?: string[];
}) {
  const max = Math.max(1, ...items.map((i) => i.value));
  const total = items.reduce((a, i) => a + i.value, 0);
  if (!items.length) return <p className="text-sm text-[color:var(--ink-muted)]">Nothing recorded yet.</p>;
  return (
    <ul className="space-y-2.5">
      {items.map((it, k) => (
        <li key={it.label} className="group" title={`${it.label}: ${it.value.toLocaleString()} (${total ? Math.round((it.value / total) * 100) : 0}%)`}>
          <div className="mb-1 flex items-baseline justify-between gap-3 text-[13px]">
            <span className="truncate text-[color:var(--ink-2)]">{it.label}</span>
            <span className="shrink-0 tabular-nums text-[color:var(--ink-1)]">
              <b className="font-semibold">{it.value.toLocaleString()}</b>
              <span className="ml-1.5 text-xs text-[color:var(--ink-muted)]">{total ? Math.round((it.value / total) * 100) : 0}%</span>
            </span>
          </div>
          <div className="h-2 rounded-full bg-slate-100">
            <div
              className="h-2 rounded-full transition-[width] duration-500 group-hover:brightness-110"
              style={{ width: `${(it.value / max) * 100}%`, background: colors?.[k] ?? color, minWidth: it.value ? 4 : 0 }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Share bar: parts of one whole, with a legend that carries the numbers.
// ---------------------------------------------------------------------------

export function ShareBar({ parts }: { parts: { label: string; value: number; color: string; icon?: string }[] }) {
  const total = parts.reduce((a, p) => a + p.value, 0);
  const [tip, setTip] = useState<number | null>(null);
  if (!total) return <p className="text-sm text-[color:var(--ink-muted)]">Nothing recorded yet.</p>;
  return (
    <div>
      <div className="flex h-4 w-full gap-[2px] overflow-hidden rounded-full" onPointerLeave={() => setTip(null)}>
        {parts
          .filter((p) => p.value > 0)
          .map((p, k) => (
            <div
              key={p.label}
              tabIndex={0}
              aria-label={`${p.label}: ${p.value}`}
              onPointerEnter={() => setTip(k)}
              onFocus={() => setTip(k)}
              onBlur={() => setTip(null)}
              className="h-full outline-none transition-opacity"
              style={{ width: `${(p.value / total) * 100}%`, background: p.color, opacity: tip === null || tip === k ? 1 : 0.45 }}
            />
          ))}
      </div>
      <ul className="mt-3 grid grid-cols-1 gap-x-4 gap-y-1.5 sm:grid-cols-2">
        {parts.map((p) => (
          <li key={p.label} className="flex items-center gap-2 text-[13px]">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: p.color }} aria-hidden="true" />
            {p.icon && <span aria-hidden="true">{p.icon}</span>}
            <span className="truncate text-[color:var(--ink-2)]">{p.label}</span>
            <span className="ml-auto tabular-nums font-semibold text-[color:var(--ink-1)]">{p.value.toLocaleString()}</span>
            <span className="w-9 text-right text-xs tabular-nums text-[color:var(--ink-muted)]">
              {Math.round((p.value / total) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Column histogram: binned counts, each bin its own colour (ordinal or diverging).
// ---------------------------------------------------------------------------

export function Columns({
  bins,
  colors,
  height = 150,
  unit = '',
}: {
  // label is the axis text; tip, when given, the fuller name in the tooltip.
  bins: { label: string; value: number; tip?: string }[];
  colors: string[];
  height?: number;
  unit?: string;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip | null>(null);
  const padB = 22;
  const plotH = height - padB - 14;
  const max = Math.max(1, ...bins.map((b) => b.value));
  const step = w / Math.max(1, bins.length);
  const bw = Math.max(2, step - 4);
  // Thin the axis labels when bins are narrow; the tooltip and table keep every one.
  const labelEvery = step < 48 ? 2 : 1;
  return (
    <div ref={ref} className="relative w-full" onPointerLeave={() => setTip(null)}>
      {w > 0 && (
        <svg width={w} height={height} role="img" aria-label="Distribution">
          <line x1={0} x2={w} y1={plotH + 14} y2={plotH + 14} stroke="var(--axis)" />
          {bins.map((b, k) => {
            const h = (b.value / max) * plotH;
            const x = k * step + 2;
            const base = plotH + 14;
            const r = Math.min(4, bw / 2, h);
            const show = (px: number, py: number) =>
              setTip({ x: px, y: py, title: `${b.tip ?? b.label}${unit}`, rows: [{ color: colors[k], label: 'count', value: b.value.toLocaleString() }] });
            return (
              <g key={b.label}>
                {b.value > 0 && (
                  <path
                    d={`M${x},${base}V${base - h + r}Q${x},${base - h} ${x + r},${base - h}H${x + bw - r}Q${x + bw},${base - h} ${x + bw},${base - h + r}V${base}Z`}
                    fill={colors[k]}
                  />
                )}
                {b.value > 0 && (
                  <text x={x + bw / 2} y={base - h - 4} textAnchor="middle" fontSize={10} fontWeight={600} fill="var(--ink-2)">
                    {b.value}
                  </text>
                )}
                {k % labelEvery === 0 && (
                  <text x={x + bw / 2} y={height - 5} textAnchor="middle" fontSize={10} fill="var(--ink-muted)">
                    {b.label}
                  </text>
                )}
                <rect
                  x={k * step}
                  y={0}
                  width={step}
                  height={base}
                  fill="transparent"
                  tabIndex={0}
                  aria-label={`${b.tip ?? b.label}${unit}: ${b.value}`}
                  onPointerMove={(e) => show(e.nativeEvent.offsetX, e.nativeEvent.offsetY)}
                  onFocus={() => show(x, base - h)}
                  onBlur={() => setTip(null)}
                  className="outline-none"
                />
              </g>
            );
          })}
        </svg>
      )}
      {tip && <TooltipBox tip={tip} width={w} />}
    </div>
  );
}
