// Product analytics (PostHog). Two jobs, both in one file so that what this app
// reports about its users is readable in one place:
//
//   1. The guard. Without NEXT_PUBLIC_POSTHOG_KEY in the build -- local
//      development, CI's image build, anyone's fork -- posthog-js is never
//      initialised and every function here does nothing. No warnings in the
//      console, no events sent to a project that is not yours.
//   2. The event names, as a union type. A typo in a capture() string is a new
//      event in PostHog rather than an error, which is how an analytics project
//      ends up with "screen run", "screen_run" and "Screen Run" counted apart.
//      Here the compiler rejects the fourth spelling.
import posthog from 'posthog-js';

// Baked into the bundle at build time, not read at container start, so changing
// it needs a rebuild (web/Dockerfile, and PUBLIC_POSTHOG_KEY in deploy.yml).
// Public by design: it can only write events, never read them.
export const POSTHOG_KEY = process.env.NEXT_PUBLIC_POSTHOG_KEY || '';

export const ANALYTICS_ON = POSTHOG_KEY !== '';

// The things this app does. Autocapture already records every click, pageview
// and form submit on its own; these are the events that cost a user real time
// and are worth counting deliberately, with the properties worth grouping by.
//
// PostHog's convention, kept to here: "[object] [past-tense verb]", lower case.
export type AppEvent =
  | 'screen run'
  | 'screen failed'
  | 'campaign started'
  | 'campaign iterated'
  | 'campaign ended'
  | 'candidates queued';

export type EventProperties = Record<string, string | number | boolean | null | undefined>;

export function track(event: AppEvent, properties?: EventProperties): void {
  if (!ANALYTICS_ON) return;
  posthog.capture(event, properties);
}

// Ties this browser's events to an account. The id is the `users` row id, never
// the email: it survives someone changing their address, and an email in the
// person's key cannot be redacted later, whereas a property can. The email and
// name ride along as person properties, which is where PostHog expects them.
export function identify(userId: string, properties?: EventProperties): void {
  if (!ANALYTICS_ON) return;
  posthog.identify(userId, properties);
}

// Sign-out. Without this the next person to use the browser -- a shared
// laptop, a demo machine -- is recorded as still being the one who signed out,
// and their events land on that account's timeline.
export function resetIdentity(): void {
  if (!ANALYTICS_ON) return;
  posthog.reset();
}
