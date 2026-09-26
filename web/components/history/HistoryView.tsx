'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { getHistory, type CampaignState, type HistoryItem, type ScreenRecord } from '@/lib/api';
import { Chevron } from '../icons';
import HistoryDetail from './HistoryDetail';
import HistoryList, { type Filter } from './HistoryList';

// The History tab: the timeline on the left, the selected experiment on the
// right. On a phone the two take turns, with a way back to the list.
export default function HistoryView({
  onOpenScreen,
  onRerunScreen,
  onOpenCampaign,
}: {
  onOpenScreen: (r: ScreenRecord) => void;
  onRerunScreen: (r: ScreenRecord) => void;
  onOpenCampaign: (s: CampaignState) => void;
}) {
  const [filter, setFilter] = useState<Filter>('all');
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<HistoryItem | null>(null);
  // Which filter the in-flight request was for, so a slow page for "All" never
  // lands in the list after the user has switched to "Screens".
  const current = useRef<Filter>(filter);

  const load = useCallback(async (f: Filter, before: string | null) => {
    current.current = f;
    setLoading(true);
    setError(null);
    try {
      const page = await getHistory({ kind: f, before });
      if (current.current !== f) return;
      setItems((prev) => (before ? [...prev, ...page.items] : page.items));
      setNext(page.next);
    } catch (e) {
      if (current.current === f) setError((e as Error).message);
    } finally {
      if (current.current === f) setLoading(false);
    }
  }, []);

  useEffect(() => {
    setItems([]);
    setNext(null);
    load(filter, null);
  }, [filter, load]);

  const more = useCallback(() => {
    if (next && !loading) load(filter, next);
  }, [next, loading, load, filter]);

  return (
    <div className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
      <aside
        className={`shrink-0 border-slate-200 bg-slate-50 lg:flex lg:w-[420px] lg:flex-col lg:border-r ${
          selected ? 'hidden' : 'flex flex-col'
        }`}
      >
        <HistoryList
          items={items}
          selectedId={selected?.id ?? null}
          onSelect={setSelected}
          filter={filter}
          onFilter={setFilter}
          loading={loading}
          hasMore={!!next}
          onMore={more}
          error={error}
        />
      </aside>

      <main className={`flex-1 lg:block lg:min-h-0 lg:overflow-y-auto ${selected ? 'block' : 'hidden'}`}>
        {selected && (
          <button
            type="button"
            onClick={() => setSelected(null)}
            className="flex items-center gap-1 px-6 pt-5 text-sm font-medium text-slate-600 hover:text-slate-900 lg:hidden"
          >
            <Chevron className="h-4 w-4 rotate-180" /> All history
          </button>
        )}
        <HistoryDetail
          item={selected}
          onOpenScreen={onOpenScreen}
          onRerunScreen={onRerunScreen}
          onOpenCampaign={onOpenCampaign}
        />
      </main>
    </div>
  );
}
