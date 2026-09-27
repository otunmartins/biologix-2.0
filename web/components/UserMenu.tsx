'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { signOutAndReturn } from '@/app/actions';

export interface SessionUser {
  name: string | null;
  email: string | null;
  image: string | null;
}

function initials(user: SessionUser) {
  const source = user.name || user.email || '?';
  return source
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join('');
}

export default function UserMenu({ user, isAdmin = false }: { user: SessionUser; isAdmin?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on a click anywhere else, or on Escape.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account"
        className="grid h-8 w-8 place-items-center overflow-hidden rounded-full bg-slate-200 text-xs font-semibold text-slate-700 ring-offset-2 transition hover:ring-2 hover:ring-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
      >
        {user.image ? (
          // Google's avatar URL; a plain img keeps next/image's domain allowlist out of it.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={user.image} alt="" referrerPolicy="no-referrer" className="h-full w-full object-cover" />
        ) : (
          initials(user)
        )}
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-10 z-20 w-64 rounded-xl border border-slate-200 bg-white p-2 shadow-lg"
        >
          <div className="px-3 py-2">
            {user.name && <div className="truncate text-sm font-semibold text-slate-900">{user.name}</div>}
            {user.email && <div className="truncate text-xs text-slate-500">{user.email}</div>}
          </div>
          {isAdmin && (
            <Link
              href="/admin"
              role="menuitem"
              className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-100"
            >
              Admin
              <span className="text-xs text-slate-400">dashboard &amp; approvals</span>
            </Link>
          )}
          <form action={signOutAndReturn}>
            <button
              type="submit"
              role="menuitem"
              className="w-full rounded-lg px-3 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-100"
            >
              Sign out
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
