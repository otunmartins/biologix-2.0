'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

// Same-origin in production (Caddy proxies /screen to the api container).
// Falls back to localhost:8000 for local dev when running web and api separately.
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const GRADE_MEANING = {
  A: 'Approved-product precedent at this route (FDA Inactive Ingredient Database)',
  B: 'Experimental data',
  C: 'In-domain prediction',
  D: 'Out-of-domain or surrogate prediction',
  E: 'No data',
};

// The residues the rule table actually reasons about.
const TRACKED = [
  ['M', 'Met', 'oxidation'],
  ['W', 'Trp', 'oxidation'],
  ['C', 'Cys', 'Michael addition'],
  ['K', 'Lys', 'glycation, Michael addition'],
  ['H', 'His', 'alkylation'],
  ['N', 'Asn', 'deamidation'],
];

const AMINO_ACIDS = 'ACDEFGHIKLMNPQRSTVWY';
const SEV_ORDER = { high: 0, moderate: 1, low: 2 };

// A polymer, a disaccharide stabiliser, and a small molecule — enough to show
// the surrogate path and the no-alert path without typing anything.
const PRESETS = ['Polysorbate 80', 'Polysorbate 20', 'Sucrose', 'Trehalose', 'Glycerol', 'PEG'];

// Trastuzumab heavy-chain CDR region — short, real, and rich in the residues above.
const SAMPLE_SEQ =
  'EVQLVESGGGLVQPGGSLRLSCAASGFNIKDTYIHWVRQAPGKGLEWVARIYPTNGYTRYADSVKGRFTISADTSKNTAYLQMNSLRAEDTAVYYCSRWGGDGFYAMDYWGQGTLVTVSS';

export default function Home() {
  const [mode, setMode] = useState('form');
  const [excipient, setExcipient] = useState('Polysorbate 80');
  const [protein, setProtein] = useState('');
  const [structureId, setStructureId] = useState('');
  const [route, setRoute] = useState('Subcutaneous');
  const [dose, setDose] = useState(100);
  const [temp, setTemp] = useState('25°C (room temp)');
  const [freeText, setFreeText] = useState(
    'Is polysorbate 80 a concern for my antibody given subcutaneously, stored at room temperature?'
  );

  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState(null);
  const [dossier, setDossier] = useState(null);
  const [open, setOpen] = useState({});
  const [apiStatus, setApiStatus] = useState(null);
  const abortRef = useRef(null);

  // Tell the user the API is unreachable or keyless before they wait on a run.
  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => setApiStatus(d.model_configured ? 'ready' : 'nokey'))
      .catch(() => setApiStatus('down'));
  }, []);

  useEffect(() => {
    if (!loading) return;
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 250);
    return () => clearInterval(id);
  }, [loading]);

  const seq = protein.replace(/[\s\d]/g, '').toUpperCase();
  const counts = Object.fromEntries(
    TRACKED.map(([one]) => [one, (seq.match(new RegExp(one, 'g')) || []).length])
  );
  const badChars = [...new Set(seq.split('').filter((c) => !AMINO_ACIDS.includes(c)))];

  const runScreen = useCallback(async () => {
    setLoading(true);
    setElapsed(0);
    setError(null);
    setDossier(null);
    setOpen({});

    const prompt =
      mode === 'form'
        ? `Screen excipient '${excipient}' for a biologic given by the ${route} route, dose ${dose} mg, storage at ${temp}. Protein sequence: ${seq || 'not provided'}. Protein structure identifier: ${structureId.trim() || 'none'}.`
        : freeText;

    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      const res = await fetch(`${API_URL}/screen`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
        signal: ctrl.signal,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Server returned ${res.status}`);
      }
      setDossier(await res.json());
    } catch (e) {
      if (e.name !== 'AbortError') setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [mode, excipient, route, dose, temp, seq, structureId, freeText]);

  // Ctrl/Cmd+Enter runs from anywhere, including inside the textareas.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !loading) {
        e.preventDefault();
        runScreen();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [runScreen, loading]);

  function downloadJson() {
    const blob = new Blob([JSON.stringify(dossier, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `dossier-${(dossier.excipient || 'screen').replace(/\W+/g, '-').toLowerCase()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const liabilities = dossier
    ? [...dossier.liabilities].sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity])
    : [];

  return (
    <main>
      <h1 className="title">Excipient Screen</h1>
      <p className="subtitle">
        Triage an excipient against a protein, a route and a storage condition. It tells you what to
        test next — it never tells you something is safe.
      </p>

      <div className="banner">
        <span aria-hidden="true">⚠</span>
        <span>
          <b>Triage, not a safety assessment.</b> Structural alerts, a small liability rule
          table, solvent-accessibility weighting when you give it a structure, and precedent from
          the FDA Inactive Ingredient Database. Precedent means an excipient has been in an
          approved product at a route — not that it is compatible with your protein at your
          concentration. No compatibility simulation. A human checkpoint is required before any of
          this reaches a dossier.
        </span>
      </div>

      {apiStatus === 'down' && (
        <div className="err">
          <b>API unreachable at {API_URL}</b>
          Start it with <code>uvicorn main:app --port 8000</code> in <code>api/</code>.
        </div>
      )}
      {apiStatus === 'nokey' && (
        <div className="err">
          <b>API is up, but no model key is configured</b>
          Set <code>ANTHROPIC_API_KEY</code> in the API environment — screens will fail with 503
          until you do.
        </div>
      )}

      <div className="panel" style={{ marginTop: 18 }}>
        <div className="seg">
          <button data-on={mode === 'form'} onClick={() => setMode('form')}>
            Form
          </button>
          <button data-on={mode === 'text'} onClick={() => setMode('text')}>
            Natural language
          </button>
        </div>

        {mode === 'form' ? (
          <div className="grid">
            <label className="field">
              <span className="lab">
                Excipient <span className="hint">name, CAS, or SMILES</span>
              </span>
              <input value={excipient} onChange={(e) => setExcipient(e.target.value)} />
              <div className="chips">
                {PRESETS.map((p) => (
                  <button
                    key={p}
                    className="chip"
                    data-on={excipient === p}
                    onClick={() => setExcipient(p)}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </label>

            <label className="field">
              <span className="lab">
                <span>Protein sequence</span>
                <span className="hint">
                  {seq.length ? `${seq.length} residues` : 'one-letter code, optional'}
                  {' · '}
                  <button
                    className="chip"
                    style={{ padding: '0 7px' }}
                    onClick={(e) => {
                      e.preventDefault();
                      setProtein(protein ? '' : SAMPLE_SEQ);
                    }}
                  >
                    {protein ? 'clear' : 'use sample'}
                  </button>
                </span>
              </span>
              <textarea rows={4} value={protein} onChange={(e) => setProtein(e.target.value)} />
              {seq.length > 0 && (
                <>
                  <div className="residues">
                    {TRACKED.map(([one, three, why]) => (
                      <span
                        key={one}
                        className="res"
                        data-hit={counts[one] > 0}
                        title={`${three} — ${why}`}
                      >
                        {three} <b>{counts[one]}</b>
                      </span>
                    ))}
                  </div>
                  {badChars.length > 0 && (
                    <div className="hint" style={{ marginTop: 6, color: 'var(--bad)' }}>
                      Not valid one-letter codes: {badChars.join(', ')}
                    </div>
                  )}
                </>
              )}
            </label>

            <label className="field">
              <span className="lab">
                <span>Structure</span>
                <span className="hint">
                  {structureId.trim()
                    ? /^[0-9][A-Za-z0-9]{3}$/.test(structureId.trim())
                      ? 'PDB ID → RCSB (experimental)'
                      : 'UniProt accession → AlphaFold (predicted)'
                    : 'optional — without it, flags are raw counts'}
                </span>
              </span>
              <input
                value={structureId}
                onChange={(e) => setStructureId(e.target.value)}
                placeholder="P01857 or 1IGT"
              />
              <div className="chips">
                {[
                  ['P01857', 'IgG1 Fc (AlphaFold)'],
                  ['1IGT', 'intact IgG (PDB)'],
                ].map(([id, lbl]) => (
                  <button
                    key={id}
                    className="chip"
                    data-on={structureId.trim().toUpperCase() === id}
                    onClick={(e) => {
                      e.preventDefault();
                      setStructureId(structureId.trim().toUpperCase() === id ? '' : id);
                    }}
                  >
                    {lbl}
                  </button>
                ))}
              </div>
            </label>

            <div className="row2">
              <label className="field">
                <span className="lab">Route</span>
                <select value={route} onChange={(e) => setRoute(e.target.value)}>
                  <option>Subcutaneous</option>
                  <option>Intravenous</option>
                  <option>Intramuscular</option>
                </select>
              </label>
              <label className="field">
                <span className="lab">Dose (mg)</span>
                <input type="number" value={dose} onChange={(e) => setDose(e.target.value)} />
              </label>
            </div>

            <label className="field">
              <span className="lab">Storage temperature</span>
              <select value={temp} onChange={(e) => setTemp(e.target.value)}>
                <option>4°C (fridge)</option>
                <option>25°C (room temp)</option>
                <option>40°C (stressed)</option>
              </select>
            </label>
          </div>
        ) : (
          <label className="field">
            <span className="lab">
              Describe the screen <span className="hint">plain English, paste a sequence inline</span>
            </span>
            <textarea rows={6} value={freeText} onChange={(e) => setFreeText(e.target.value)} />
          </label>
        )}

        <div className="runbar">
          <button className="btn" onClick={runScreen} disabled={loading}>
            {loading && <span className="spinner" />}
            {loading ? 'Screening…' : 'Run screen'}
          </button>
          {loading ? (
            <span className="elapsed">{elapsed}s · resolving, alerting, drafting</span>
          ) : (
            <span className="elapsed">
              <span className="kbd">Ctrl</span> + <span className="kbd">↵</span>
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="err">
          <b>Screen failed</b>
          {error}
        </div>
      )}

      {loading && (
        <div className="results">
          <div className="sectitle">Working</div>
          <div className="skel" />
          <div className="skel" style={{ animationDelay: '0.15s' }} />
          <div className="skel" style={{ animationDelay: '0.3s' }} />
        </div>
      )}

      {dossier && !loading && (
        <div className="results">
          <div className="sectitle">Summary</div>
          <div className="meta">
            <span className="tag">
              Excipient <b>{dossier.excipient}</b>
            </span>
            <span className="tag">
              Protein <b>{dossier.protein}</b>
            </span>
            <span className="tag">
              Route <b>{dossier.route}</b>
            </span>
          </div>
          <p className="summary">{dossier.summary}</p>

          <div className={`flagline ${dossier.needs_testing ? 'flag-warn' : 'flag-ok'}`} style={{ marginTop: 14 }}>
            <span aria-hidden="true">{dossier.needs_testing ? '⚠' : '✓'}</span>
            <span>
              {dossier.needs_testing
                ? 'Needs testing — at least one endpoint is below grade B, or a high-severity liability was flagged.'
                : 'No testing demanded by these results alone. That is not a safety conclusion.'}
            </span>
          </div>

          <div className="sectitle">Endpoint verdicts</div>
          {dossier.endpoints.map((e, i) => {
            const isOpen = open[i] ?? i === 0;
            return (
              <div className="card" key={i}>
                <div
                  className="card-head"
                  onClick={() => setOpen((o) => ({ ...o, [i]: !isOpen }))}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(ev) => {
                    if (ev.key === 'Enter' || ev.key === ' ') {
                      ev.preventDefault();
                      setOpen((o) => ({ ...o, [i]: !isOpen }));
                    }
                  }}
                >
                  <span className="caret" data-open={isOpen} aria-hidden="true">
                    ▶
                  </span>
                  <span className="card-name">{e.endpoint}</span>
                  <span className="pill" data-v={e.verdict}>
                    {e.verdict}
                  </span>
                  <span
                    className="grade"
                    data-g={e.evidence_grade}
                    title={GRADE_MEANING[e.evidence_grade]}
                  >
                    {e.evidence_grade}
                  </span>
                </div>
                {isOpen && (
                  <div className="card-body">
                    <div style={{ color: 'var(--ink-3)', fontSize: 12, marginBottom: 6 }}>
                      Grade {e.evidence_grade} — {GRADE_MEANING[e.evidence_grade]}
                    </div>
                    {e.rationale}
                    {e.sources?.length > 0 && (
                      <div className="srcs">Sources: {e.sources.join(' · ')}</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}

          <div className="sectitle">Protein liabilities</div>
          {liabilities.length > 0 ? (
            liabilities.map((l, i) => (
              <div className="liab" key={i}>
                <div className="sev" data-s={l.severity} />
                <div style={{ minWidth: 0 }}>
                  <div className="liab-res">
                    {l.residue}
                    <span className="sevtag" data-s={l.severity}>
                      {l.severity}
                    </span>
                  </div>
                  <div className="liab-rx">{l.reaction_class}</div>
                  {l.accessibility && (
                    <div className="liab-acc" data-modelled={!l.accessibility.startsWith('not modelled')}>
                      {l.accessibility}
                    </div>
                  )}
                  <div className="liab-mit">{l.mitigation}</div>
                </div>
              </div>
            ))
          ) : (
            <div className="empty">
              No liabilities triggered by the structural alerts found. Absence of a flag is not
              evidence of compatibility — the rule table is small.
            </div>
          )}

          <div className="runbar">
            <button className="btn btn-ghost" onClick={downloadJson}>
              Download JSON
            </button>
            <button className="btn btn-ghost" onClick={runScreen}>
              Re-run
            </button>
          </div>
        </div>
      )}

      <div className="foot">
        Not a safety certification. Verdicts are limited to: Precedented, Supported without
        precedent, Data gap: test, Alert: avoid.
      </div>
    </main>
  );
}
