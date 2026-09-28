import { notFound, redirect } from 'next/navigation';
import { auth } from '@/auth';
import AdminApp from '@/components/admin/AdminApp';
import { isAdminEmail } from '@/lib/admin';

// The owner's screen. Signed out: back to sign-in. Signed in but not an admin:
// a plain 404, so the page does not even admit to existing.
export const dynamic = 'force-dynamic';

export const metadata = { title: 'Admin · Biologix 2.0' };

export default async function AdminPage() {
  const session = await auth();
  if (!session?.user) redirect('/');
  if (!isAdminEmail(session.user.email)) notFound();
  const { name, email, image } = session.user;
  return <AdminApp user={{ name: name ?? null, email: email ?? null, image: image ?? null }} />;
}
