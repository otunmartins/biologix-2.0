import { redirect } from 'next/navigation';
import { auth } from '@/auth';
import Workbench from '@/components/Workbench';
import { isAdminEmail } from '@/lib/admin';

// The signed-in workspace: /screen, /design, /results and /history. The
// Workbench lives here, not in the pages, because a layout stays mounted while
// its pages change -- so the open campaign, the screen form and a candidate
// handed from Design to Screen survive moving between tabs, as they did when
// the tabs were one page. The pages only name the route; Workbench reads which
// one from the URL.
export const dynamic = 'force-dynamic';

export default async function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const session = await auth();
  if (!session?.user) redirect('/');
  const { name, email, image } = session.user;
  return (
    <>
      <Workbench
        user={{ name: name ?? null, email: email ?? null, image: image ?? null }}
        isAdmin={isAdminEmail(email)}
      />
      {children}
    </>
  );
}
