'use client';

import { useEffect } from 'react';
import { identify } from '@/lib/analytics';

// Names the person behind the events. Rendered by the layouts that have already
// loaded the session, so it is handed the user id rather than fetching it, and
// it draws nothing.
//
// Everything before this -- the sign-in screen, the pageview that brought
// someone there -- is recorded against an anonymous id, and PostHog stitches
// that history onto the account the first time identify() runs in the browser.
export default function Analytics({
  userId,
  email,
  name,
}: {
  userId: string;
  email: string | null;
  name: string | null;
}) {
  useEffect(() => {
    identify(userId, { email: email ?? undefined, name: name ?? undefined });
  }, [userId, email, name]);

  return null;
}
