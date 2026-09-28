// Brand marks, drawn as SVG so they stay sharp at any size.
//
// Algonix AI is the parent company. Its mark is a polymer repeat unit in
// bracket notation -- [ ring-bond-dot ]n -- redrawn here from the supplied logo
// at its own proportions and colours. Biologix 2.0 is the product; its mark
// takes the ring-bond-dot out of the brackets, on the same near-black.

export const BRAND = {
  ink: '#13140e', // the logo's background
  cream: '#f2efe8', // brackets, ring and wordmark
  cyan: '#2fced4', // the dot, the subscript and "AI"
  // The cyan for text on white: the logo's own cyan is about 1.9:1 there, too
  // faint to read; this one is 4.8:1.
  cyanInk: '#0b7f87',
} as const;

// The mark alone: brackets, ring, bond, dot and the subscript n. Geometry
// measured off the 300px logo (viewBox in its coordinates).
export function AlgonixMark({ className = '', title = 'Algonix AI' }: { className?: string; title?: string }) {
  return (
    <svg viewBox="62 70 190 108" className={className} role="img" aria-label={title}>
      <g fill={BRAND.cream}>
        {/* [ */}
        <path d="M69 76h32v9H78v78h23v9H69z" />
        {/* ] */}
        <path d="M218 76h-33v9h24v78h-24v9h33z" />
        {/* the bond */}
        <rect x="134" y="120" width="20" height="8" />
      </g>
      <circle cx="119.5" cy="124.5" r="15.5" fill="none" stroke={BRAND.cream} strokeWidth="8" />
      <circle cx="170" cy="124" r="19" fill={BRAND.cyan} />
      {/* n, the repeat count */}
      <path d="M229 172v-20m0 6.5c2-4.5 5.5-6.5 8.5-6.5 4 0 6.5 2.6 6.5 7.5V172" fill="none" stroke={BRAND.cyan} strokeWidth="5" />
    </svg>
  );
}

// "ALGONIX AI" in the logo's monospaced capitals, "AI" in cyan.
export function AlgonixWordmark({ className = '', onDark = true }: { className?: string; onDark?: boolean }) {
  return (
    <span className={`font-mono uppercase tracking-[0.28em] ${className}`}>
      <span style={{ color: onDark ? BRAND.cream : BRAND.ink }}>Algonix</span>{' '}
      <span style={{ color: BRAND.cyan }}>AI</span>
    </span>
  );
}

// The product's own mark: the ring bonded to the cyan dot, on a rounded square.
export function BiologixMark({ className = 'h-8 w-8', title = 'Biologix 2.0' }: { className?: string; title?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} role="img" aria-label={title}>
      <rect width="32" height="32" rx="8" fill={BRAND.ink} />
      <circle cx="11" cy="16" r="4.6" fill="none" stroke={BRAND.cream} strokeWidth="2.6" />
      <rect x="15.4" y="14.8" width="3.4" height="2.4" fill={BRAND.cream} />
      <circle cx="22.4" cy="16" r="5" fill={BRAND.cyan} />
    </svg>
  );
}

// Name and mark together, for headers.
export function BiologixLockup({ suffix }: { suffix?: string }) {
  return (
    <span className="flex items-center gap-2.5">
      <BiologixMark className="h-8 w-8 shrink-0" />
      <span className="whitespace-nowrap text-[15px] font-semibold tracking-tight text-slate-900">
        Biologix <span style={{ color: BRAND.cyanInk }}>2.0</span>
        {suffix && <span className="font-normal text-slate-400"> · {suffix}</span>}
      </span>
    </span>
  );
}
