'use client';

import { useEffect, useRef } from 'react';
import { IS_DEV, type HistoryItem, type Verdict } from '@/lib/api';
import { dayKey, dayLabel, timeOfDay } from '@/lib/when';
import { VERDICT_STYLE } from '../badges';
import { Spinner } from '../icons';
import Structure from '../Structure';

export type Filter = 'all' | 'screen' | 'campaign';

// The dot on the rail says how the experiment came out, in the same colours as
// the verdicts everywhere else: a screen by its worst verdict, a failed run in
// red, a campaign in slate (it ranks, it does not judge).
const DOT: Record<Verdict, string> = {
  Precedented: 'bg-precedented',
  'Supported without precedent': 'bg-supported',
  'Data gap: test': 'bg-gap',
  'Alert: avoid': 'bg-alert',
};

function dotClass(item: HistoryItem): string {
  if (item.kind === 'campaign') return item.ended_at ? 'bg-slate-300' : 'bg-slate-900';
  if (item.status === 'failed') return 'bg-alert';
  return item.worst_verdict ? DOT[item.worst_verdict] : 'bg-slate-300';
}

function smilesOf(item: HistoryItem): string | null {
  return item.kind === 'screen' ? item.smiles : item.top?.smiles || null;
}

function Title({ item }: { item: HistoryItem }) {
  if (item.kind === 'screen') {
    if (item.status === 'failed') {
      return <>{item.prompt ? item.prompt.slice(0, 80) : 'Screen'}</>;
    }
    return (
      <>
        {item.excipient ?? 'Screen'}
        {/* The model writes `protein` freely; a sentence is noise in a list. */}
        {item.protein && item.protein.length <= 32 && (
          <>
            {' '}
            <span className="font-normal text-slate-400">×</span> {item.protein}
          </>
        )}
      </>
    );
  }
  const g = item.goal;
  return (
    <>
      {g.protein || 'Design campaign'}
      {g.target_temp_c !== null && <span className="font-normal text-slate-500"> · {g.target_temp_c} °C</span>}
    </>
  );
}

function Meta({ item }: { item: HistoryItem }) {
  if (item.kind === 'screen') {
    if (item.status === 'failed') {
      // The raw error is for developers; the full text stays on the record.
      return <span className="text-alert">{IS_DEV ? `Failed · ${item.error?.replace(/^agent run failed: /, '').slice(0, 60)}` : 'Failed · did not finish'}</span>;
    }
    return (
      <>
        {item.route && <span>{item.route}</span>}
        {item.worst_verdict && (
          <span className={`font-medium ${VERDICT_STYLE[item.worst_verdict].text}`}>{item.worst_verdict}</span>
        )}
      </>
    );
  }
  return (
    <>
      <span>
        {item.n_iterations} iteration{item.n_iterations === 1 ? '' : 's'} · {item.n_candidates} candidates
      </span>
      {item.n_queued > 0 && <span className="text-supported">{item.n_queued} queued</span>}
      {item.ended_at && <span className="text-slate-400">ended</span>}
    </>
  );
}

export default function HistoryList({
  items,
  selectedId,
  onSelect,
  filter,
  onFilter,
  loading,
  hasMore,
  onMore,
  error,
}: {
  items: HistoryItem[];
  selectedId: string | null;
  onSelect: (item: HistoryItem) => void;
  filter: Filter;
  onFilter: (f: Filter) => void;
  loading: boolean;
  hasMore: boolean;
  onMore: () => void;
  error: string | null;
}) {
  // Load the next page when the bottom of the list scrolls into view.
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !hasMore) return;
    const io = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting && !loading) onMore();
    });
    io.observe(el);
    return () => io.disconnect();
  }, [hasMore, loading, onMore]);

  // Group into days, keeping the newest-first order the API returns.
  const days: { key: string; label: string; items: HistoryItem[] }[] = [];
  for (const item of items) {
    const key = dayKey(item.at);
    if (days[days.length - 1]?.key !== key) days.push({ key, label: dayLabel(item.at), items: [] });
    days[days.length - 1]!.items.push(item);
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-slate-200 bg-white px-5 pb-4 pt-5">
        <h2 className="text-[15px] font-semibold text-slate-900">Your history</h2>
        <p className="hint mt-1 leading-relaxed">
          Every screen and design campaign you run, and how each result was produced.
        </p>
        <div className="mt-3 flex rounded-lg bg-slate-100 p-1 text-[13px] font-medium" role="tablist">
          {(
            [
              ['all', 'All'],
              ['screen', 'Screens'],
              ['campaign', 'Designs'],
            ] as const
          ).map(([f, label]) => (
            <button
              key={f}
              type="button"
              role="tab"
              aria-selected={filter === f}
              onClick={() => onFilter(f)}
              className={`flex-1 rounded-md px-3 py-1.5 transition ${
                filter === f ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-5">
        {error && <p className="rounded-lg border border-alert-line bg-alert-soft px-3 py-2 text-sm text-slate-800">{error}</p>}

        {!loading && !error && items.length === 0 && (
          <div className="px-2 py-12 text-center">
            <p className="font-medium text-slate-700">Nothing here yet</p>
            <p className="mt-1 text-sm text-slate-500">
              {filter === 'campaign'
                ? 'Design campaigns you start will appear here.'
                : filter === 'screen'
                  ? 'Screens you run will appear here.'
                  : 'Run a screen or start a design, and it will appear here with its full record.'}
            </p>
          </div>
        )}

        <ol className="space-y-6">
          {days.map((day) => (
            <li key={day.key}>
              <div className="eyebrow sticky top-0 z-10 -mx-1 mb-2 bg-slate-50/95 px-1 py-1 backdrop-blur">
                {day.label}
              </div>
              {/* The rail: a thin line with one dot per experiment. */}
              <ol className="relative space-y-2 border-l border-slate-200 pl-5">
                {day.items.map((item) => {
                  const selected = item.id === selectedId;
                  const smiles = smilesOf(item);
                  return (
                    <li key={item.id} className="relative">
                      <span
                        className={`absolute -left-[25px] top-5 h-2.5 w-2.5 rounded-full ring-4 ring-slate-50 ${dotClass(item)}`}
                        aria-hidden="true"
                      />
                      <button
                        type="button"
                        onClick={() => onSelect(item)}
                        aria-current={selected}
                        className={`flex w-full items-start gap-3 rounded-xl border bg-white p-3 text-left shadow-card transition ${
                          selected
                            ? 'border-slate-900 ring-1 ring-slate-900'
                            : 'border-slate-200 hover:border-slate-300 hover:shadow-md'
                        }`}
                      >
                        {smiles ? (
                          <Structure smiles={smiles} width={64} height={52} label="" className="border-slate-100" />
                        ) : (
                          <span className="grid h-[52px] w-16 shrink-0 place-items-center rounded-lg border border-dashed border-slate-200 text-[11px] text-slate-400">
                            {item.kind === 'screen' && item.status === 'failed' ? 'failed' : 'no structure'}
                          </span>
                        )}
                        <span className="min-w-0 flex-1">
                          <span className="flex items-baseline justify-between gap-2">
                            <span className="eyebrow text-[10px]">
                              {/* A designed candidate's screen is design work: say so where it is listed. */}
                              {item.kind === 'screen'
                                ? item.origin === 'design'
                                  ? 'Screen · designed candidate'
                                  : 'Screen'
                                : 'Design'}
                            </span>
                            <span className="shrink-0 text-xs tabular-nums text-slate-400">{timeOfDay(item.at)}</span>
                          </span>
                          <span className="mt-0.5 block truncate text-sm font-semibold text-slate-900">
                            <Title item={item} />
                          </span>
                          <span className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-slate-500">
                            <Meta item={item} />
                          </span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            </li>
          ))}
        </ol>

        <div ref={sentinel} className="h-8" />
        {loading && (
          <div className="flex items-center justify-center gap-2 py-4 text-sm text-slate-500">
            <Spinner className="h-4 w-4" /> Loading
          </div>
        )}
      </div>
    </div>
  );
}
