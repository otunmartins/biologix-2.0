'use client';

import { useState } from 'react';
import { API_URL } from '@/lib/api';

// A 2D drawing of one molecule or polymer repeat unit, drawn by the API's RDKit
// (api/depict.py) from exactly the SMILES the screen used. Attachment points on
// a repeat unit come out as wavy bond ends.
//
// The box has a fixed size before the image arrives, so a list of candidates
// does not jump as drawings load. If the SMILES cannot be drawn, the SMILES
// itself is shown instead: the chemistry is never silently missing.
export default function Structure({
  smiles,
  width = 240,
  height = 180,
  label,
  className = '',
}: {
  smiles: string;
  width?: number;
  height?: number;
  // Read by screen readers; defaults to the SMILES.
  label?: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const src = `${API_URL}/structure.svg?${new URLSearchParams({
    smiles,
    w: String(width),
    h: String(height),
  })}`;

  return (
    <div
      className={`relative grid shrink-0 place-items-center overflow-hidden rounded-lg border border-slate-200 bg-white ${className}`}
      style={{ width, height, maxWidth: '100%' }}
    >
      {failed ? (
        <code className="break-all px-2 text-center font-mono text-[11px] leading-snug text-slate-500">{smiles}</code>
      ) : (
        // A plain img: the SVG comes from our own API, and next/image adds
        // nothing for vector drawings.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt={label ?? `Structure: ${smiles}`}
          width={width}
          height={height}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          className="h-full w-full object-contain"
        />
      )}
    </div>
  );
}

// The SMILES under a drawing, as text: selectable, and one click to copy into
// ChemDraw, RDKit or a lab notebook. Kept to the drawing's width; a long chain
// wraps rather than widening the card.
export function Smiles({ smiles, width }: { smiles: string; width?: number }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(smiles);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // No clipboard (plain http, or permission denied): the text stays selectable.
    }
  }

  return (
    <div className="flex items-start gap-1.5" style={{ maxWidth: width ?? '100%' }}>
      <code className="min-w-0 flex-1 select-all break-all font-mono text-[11px] leading-snug text-slate-600">
        {smiles}
      </code>
      <button
        type="button"
        onClick={copy}
        aria-label="Copy SMILES"
        className="shrink-0 rounded border border-slate-200 px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-slate-500 transition hover:bg-slate-50 hover:text-slate-700"
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  );
}
