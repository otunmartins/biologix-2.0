// PostHog, started in the browser. Next.js runs this file on every page load
// after the HTML arrives and before React hydrates, which is why the init is
// here and not in a provider component: analytics that loads with the app
// catches the first pageview, analytics that loads with a component misses it.
//
// Keep it synchronous and small. Next.js warns in development if this file
// takes more than 16ms, and only top-level synchronous code is guaranteed to
// finish before hydration.
import posthog from 'posthog-js';
import { ANALYTICS_ON, POSTHOG_KEY } from '@/lib/analytics';

// Wrapped, as the Next.js guide asks: a failure to start analytics must never
// be a failure to load the app. Nothing below is load-bearing for a user.
try {
  if (ANALYTICS_ON) {
    posthog.init(POSTHOG_KEY, {
      // Our own domain, which next.config.ts forwards to PostHog. Not
      // us.i.posthog.com directly: that host is on every tracker blocklist, so
      // a fair share of real users would silently report nothing at all.
      api_host: '/_ph',
      // Where the PostHog toolbar and its links point. This has to be their
      // real domain -- api_host above is ours, and ours has no UI to link to.
      // EU, matching the project's region (next.config.ts).
      ui_host: process.env.NEXT_PUBLIC_POSTHOG_UI_HOST || 'https://eu.posthog.com',
      // Opt in to the behaviour current as of this date rather than the
      // library's original defaults. The one that matters here: pageviews
      // follow History API navigation, so moving between /screen, /design,
      // /results and /history counts as four pageviews instead of one -- the
      // App Router changes the URL without ever reloading the page.
      defaults: '2026-08-30',
      // Unhandled exceptions and rejected promises, reported as they happen.
      // This is what the PostHog<->GitHub integration consumes: without it
      // that connection has no stack traces to map back to this repository.
      //
      // Note what this does NOT cover, which is most of what goes wrong here:
      // a screen or a design that fails is caught, shown to the user and
      // recorded as a 'screen failed' event (lib/analytics.ts). This catches
      // only the errors nothing caught -- the ones that break the page.
      capture_exceptions: true,
    });
  }
} catch (e) {
  // Development only; in production this is silent by design.
  if (process.env.NODE_ENV !== 'production') console.warn('PostHog did not start:', e);
}
