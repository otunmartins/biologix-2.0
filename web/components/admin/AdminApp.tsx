'use client';

import { useState } from 'react';
import Link from 'next/link';
import { BiologixLockup } from '@/components/brand';
import UserMenu, { type SessionUser } from '@/components/UserMenu';
import Approvals from './Approvals';
import Dashboard from './Dashboard';

// The owner's own screen at /admin: platform numbers, and the simulation
// approvals. Reached from the user menu; the page is refused on the server to
// anyone not in ADMIN_EMAILS (app/admin/page.tsx).

type Tab = 'dashboard' | 'approvals';

export default function AdminApp({ user }: { user: SessionUser }) {
  const [tab, setTab] = useState<Tab>('dashboard');
  const [waiting, setWaiting] = useState<number | null>(null);

  return (
    <div className="min-h-screen bg-[#f9f9f7]">
      <header className="sticky top-0 z-30 flex flex-wrap items-center justify-between gap-y-2 border-b border-slate-200 bg-white/90 px-4 py-2 backdrop-blur sm:h-14 sm:flex-nowrap sm:px-5 sm:py-0">
        <BiologixLockup suffix="Admin" />
        <nav className="order-last flex w-full rounded-lg bg-slate-100 p-1 text-sm font-medium sm:order-none sm:w-auto" aria-label="Admin sections">
          {(
            [
              ['dashboard', 'Dashboard'],
              ['approvals', 'Approvals'],
            ] as const
          ).map(([t, label]) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              aria-current={tab === t}
              className={`flex flex-1 items-center justify-center gap-2 rounded-md px-4 py-1.5 transition sm:flex-none ${
                tab === t ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'
              }`}
            >
              {label}
              {t === 'approvals' && !!waiting && (
                <span className="rounded-full bg-gap px-1.5 text-[11px] font-semibold leading-5 text-white" aria-label={`${waiting} awaiting approval`}>
                  {waiting}
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className="flex items-center gap-3">
          <Link href="/" className="rounded-lg px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100">
            ← Back to app
          </Link>
          <UserMenu user={user} />
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl px-4 py-7 sm:px-6 lg:px-10">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            {tab === 'dashboard' ? 'Platform at a glance' : 'Simulation approvals'}
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            {tab === 'dashboard'
              ? 'Counts across every user. Totals only: nobody’s screens, prompts or structures are shown here.'
              : 'Every user’s simulations that are waiting, approved or running.'}
          </p>
        </div>
        {/* Approvals stays mounted so the waiting count in the tab stays current. */}
        <div hidden={tab !== 'dashboard'}>{tab === 'dashboard' && <Dashboard />}</div>
        <div hidden={tab !== 'approvals'}>
          <Approvals onWaiting={setWaiting} />
        </div>
      </main>
    </div>
  );
}
