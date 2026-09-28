import { redirect } from 'next/navigation';
import { auth } from '@/auth';
import SignIn from '@/components/SignIn';

// Reads the session cookie on every request; never prerendered at build time.
export const dynamic = 'force-dynamic';

// Signed out: the sign-in screen. Signed in: the workspace, which starts on
// Screen. Sign-in and sign-out both land here, so this is the one front door.
// Next 15+ hands searchParams over as a promise.
export default async function Page({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const session = await auth();
  if (!session?.user) return <SignIn error={(await searchParams).error} />;
  redirect('/screen');
}
