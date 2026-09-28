import { auth } from '@/auth';
import SignIn from '@/components/SignIn';
import Workbench from '@/components/Workbench';
import { isAdminEmail } from '@/lib/admin';

// Reads the session cookie on every request; never prerendered at build time.
export const dynamic = 'force-dynamic';

export default async function Page({ searchParams }: { searchParams: { error?: string } }) {
  const session = await auth();
  if (!session?.user) return <SignIn error={searchParams.error} />;
  const { name, email, image } = session.user;
  return (
    <Workbench
      user={{ name: name ?? null, email: email ?? null, image: image ?? null }}
      isAdmin={isAdminEmail(email)}
    />
  );
}
