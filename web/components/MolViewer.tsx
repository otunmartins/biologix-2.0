'use client';

import { useEffect, useRef, useState } from 'react';
import { Spinner, Triangle } from '@/components/icons';
import { getConformer, getSimulationSnapshot, getStructureModel } from '@/lib/api';

// The biologic in 3D, and the polymer around it when a run has left a snapshot.
//
// - With a snapshot (worker/snapshot.py): the run's last frame. Protein as a
//   ribbon, each polymer chain as sticks where it actually sat, and the residues
//   the polymer touched most picked out.
// - Without one: the protein from its PDB or AlphaFold entry, with those residues
//   picked out once a run has named them, and -- given the candidate's chain --
//   a 3D model of one chain (RDKit) set BESIDE the protein, not against it. That
//   is an illustration of the two molecules, labelled so: only a simulation says
//   where the chain sits.
//
// 3Dmol.js is loaded only when this mounts, so a page of candidates never pays
// for it until someone asks for the 3D view.

type Contact = { residue: string; fraction: number };

const COLORS = {
  ribbon: '#9b9a94',
  polymer: '#ffa500', // 3Dmol's orangeCarbon, so the legend matches the sticks
  contact: '#1c5cab', // --ramp-2
};

// "LYS 45 A" -> the residue name and number (chain ids differ between a PDB
// entry's author and label chains, so they are not relied on).
function residueSel(label: string): { resn: string; resi: number } | null {
  const [resn, resi] = label.split(' ');
  const n = Number.parseInt(resi, 10);
  return resn && Number.isFinite(n) ? { resn, resi: n } : null;
}

export default function MolViewer({
  structureId,
  candidateId,
  hasSnapshot,
  polymerSmiles,
  contacts = [],
  admin = false,
}: {
  structureId?: string;
  candidateId?: string;
  hasSnapshot?: boolean;
  // The candidate's screened chain, drawn beside the protein when there is no snapshot.
  polymerSmiles?: string;
  contacts?: Contact[];
  // The snapshot through the admin's route: any user's run.
  admin?: boolean;
}) {
  const el = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<any>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');
  const [surface, setSurface] = useState(false);
  const [spin, setSpin] = useState(false);
  const snapshot = Boolean(hasSnapshot && candidateId);
  const top = contacts.slice(0, 8);

  useEffect(() => {
    let dead = false;
    const ctl = new AbortController();
    (async () => {
      try {
        const [$3Dmol, protein, chain] = await Promise.all([
          import('3dmol'),
          snapshot
            ? getSimulationSnapshot(candidateId!, ctl.signal, admin)
            : structureId
              ? getStructureModel(structureId, ctl.signal)
              : Promise.resolve(null),
          !snapshot && polymerSmiles ? getConformer(polymerSmiles, ctl.signal) : Promise.resolve(null),
        ]);
        if (dead || !el.current) return;
        const v = $3Dmol.createViewer(el.current, { backgroundColor: 'white', antialias: true } as any);
        viewerRef.current = v;
        const polymerStyle = { stick: { colorscheme: 'orangeCarbon', radius: 0.2 } };
        if (protein) {
          // The API sends one model as PDB, or the mmCIF when PDB cannot hold the entry.
          const m = v.addModel(protein, protein.startsWith('data_') ? 'cif' : 'pdb');
          // Protein: a calm ribbon, so the polymer and contacts carry the colour.
          // Ligands and waters from a deposited entry are not what this is about.
          m.setStyle({ hetflag: true }, {});
          m.setStyle({ hetflag: false }, { cartoon: { color: COLORS.ribbon } });
          if (snapshot) m.setStyle({ resn: 'POL' }, polymerStyle);
          for (const c of top) {
            const sel = residueSel(c.residue);
            if (sel) v.addStyle({ model: m, ...sel, hetflag: false } as any, { stick: { color: COLORS.contact, radius: 0.22 } });
          }
        }
        if (chain) {
          const m = v.addModel(chain, 'sdf');
          m.setStyle({}, polymerStyle);
          if (protein) {
            // Beside the protein with a clear gap, centred on it: two molecules
            // shown together, not a pose.
            const pa = v.getModel(0).selectedAtoms({ hetflag: false });
            const ca: any[] = m.selectedAtoms({});
            const span = (xs: number[]) => [Math.min(...xs), Math.max(...xs)];
            const [, pxMax] = span(pa.map((a: any) => a.x));
            const [cxMin] = span(ca.map((a: any) => a.x));
            const mid = (as: any[], k: 'y' | 'z') => as.reduce((t, a) => t + a[k], 0) / as.length;
            const dx = pxMax + 8 - cxMin;
            const dy = mid(pa, 'y') - mid(ca, 'y');
            const dz = mid(pa, 'z') - mid(ca, 'z');
            for (const a of ca) {
              a.x += dx;
              a.y += dy;
              a.z += dz;
            }
          }
        }
        v.zoomTo();
        v.render();
        setState('ready');
      } catch (e) {
        if (dead || (e as Error).name === 'AbortError') return;
        setError((e as Error).message);
        setState('error');
      }
    })();
    return () => {
      dead = true;
      ctl.abort();
      viewerRef.current?.clear?.();
      viewerRef.current = null;
    };
    // contacts are part of the picture; a new set redraws it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structureId, candidateId, snapshot, polymerSmiles, admin, JSON.stringify(top)]);

  // A translucent surface shows the protein's shape the polymer is working against.
  useEffect(() => {
    const v = viewerRef.current;
    if (!v || state !== 'ready') return;
    v.removeAllSurfaces();
    if (surface) {
      import('3dmol').then(($3Dmol) => {
        v.addSurface($3Dmol.SurfaceType.VDW, { opacity: 0.22, color: 'white' }, { hetflag: false });
        v.render();
      });
    } else v.render();
  }, [surface, state]);

  useEffect(() => {
    const v = viewerRef.current;
    if (!v || state !== 'ready') return;
    v.spin(spin ? 'y' : false);
  }, [spin, state]);

  return (
    <div className="space-y-2">
      <div className="relative h-[340px] w-full overflow-hidden rounded-lg border border-slate-200 bg-white sm:h-[400px]">
        <div ref={el} className="absolute inset-0" aria-label={`3D structure of ${structureId ?? 'the polymer'}`} role="img" />
        {state === 'loading' && (
          <div className="absolute inset-0 grid place-items-center text-sm text-slate-500">
            <span className="flex items-center gap-2">
              <Spinner className="h-4 w-4" />{' '}
              {snapshot ? 'Loading the simulation’s last frame' : polymerSmiles ? 'Building the 3D model' : `Loading ${structureId}`}
            </span>
          </div>
        )}
        {state === 'error' && (
          <div className="absolute inset-0 grid place-items-center px-6 text-center text-sm text-slate-600">
            <span className="flex gap-2">
              <Triangle className="mt-0.5 h-4 w-4 shrink-0 text-alert" /> Could not load the structure: {error}
            </span>
          </div>
        )}
        {state === 'ready' && (
          <div className="absolute right-2 top-2 flex gap-1.5">
            {(
              [
                ['Surface', surface, setSurface],
                ['Spin', spin, setSpin],
              ] as const
            ).map(([label, on, set]) => (
              <button
                key={label}
                type="button"
                aria-pressed={on}
                onClick={() => set(!on)}
                className={`rounded-md border px-2 py-1 text-xs font-medium shadow-sm transition ${
                  on ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 bg-white/90 text-slate-600 hover:bg-white'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
        {structureId && (
          <li className="flex items-center gap-1.5">
            <span className="h-2.5 w-4 rounded-sm" style={{ background: COLORS.ribbon }} aria-hidden="true" />
            {structureId} backbone
          </li>
        )}
        {(snapshot || polymerSmiles) && (
          <li className="flex items-center gap-1.5">
            <span className="h-[3px] w-4 rounded" style={{ background: COLORS.polymer }} aria-hidden="true" />
            {snapshot ? 'Polymer chains, where they sat at the end of the run' : 'One polymer chain, as screened'}
          </li>
        )}
        {top.length > 0 && (
          <li className="flex items-center gap-1.5">
            <span className="h-[3px] w-4 rounded" style={{ background: COLORS.contact }} aria-hidden="true" />
            Residues the polymer touched most
          </li>
        )}
      </ul>
      {!snapshot && polymerSmiles && structureId && (
        <p className="text-xs text-slate-500">
          An illustration: the chain is set beside the protein, not where it would sit. A simulation shows that.
        </p>
      )}
      <p className="text-xs text-slate-400">Drag to turn, scroll to zoom, right-drag to move.</p>
    </div>
  );
}
