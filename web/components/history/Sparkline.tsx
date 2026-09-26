// A campaign's best triage score across iterations, as a small line. Enough to
// see at a glance whether the loop was still climbing or had levelled off.
export default function Sparkline({
  values,
  width = 120,
  height = 32,
  className = '',
}: {
  values: (number | null)[];
  width?: number;
  height?: number;
  className?: string;
}) {
  const pts = values.map((v, i) => [i, v] as const).filter((p): p is readonly [number, number] => p[1] !== null);
  if (pts.length === 0) return null;
  const ys = pts.map(([, v]) => v);
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const pad = 3;
  const x = (i: number) => (values.length === 1 ? width / 2 : pad + (i / (values.length - 1)) * (width - 2 * pad));
  const y = (v: number) => (hi === lo ? height / 2 : height - pad - ((v - lo) / (hi - lo)) * (height - 2 * pad));
  const d = pts.map(([i, v], k) => `${k ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const area = `${d} L${x(pts[pts.length - 1][0]).toFixed(1)},${height} L${x(pts[0][0]).toFixed(1)},${height} Z`;
  const [li, lv] = pts[pts.length - 1];

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={`overflow-visible text-supported ${className}`}
      role="img"
      aria-label={`Best score by iteration: ${ys.map((v) => v.toFixed(2)).join(', ')}`}
    >
      <path d={area} fill="currentColor" opacity={0.08} />
      <path d={d} fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(li)} cy={y(lv)} r={2.75} fill="currentColor" />
    </svg>
  );
}
