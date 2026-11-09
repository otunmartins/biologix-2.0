import type { NextConfig } from 'next';

// PostHog's own hosts, which /_ph/* is forwarded to. EU cloud, which is where
// this project's data lives -- the region is fixed when the PostHog project is
// created and events sent to the wrong one are simply not accepted. Override
// both (and NEXT_PUBLIC_POSTHOG_UI_HOST) for a US or self-hosted project:
//   https://us.i.posthog.com, https://us-assets.i.posthog.com
// Read at build time like everything else here, so a change needs a rebuild.
const POSTHOG_INGEST = process.env.POSTHOG_INGEST_HOST || 'https://eu.i.posthog.com';
const POSTHOG_ASSETS = process.env.POSTHOG_ASSETS_HOST || 'https://eu-assets.i.posthog.com';

// A build-time guard, because the cost of getting this wrong is asymmetric.
// NEXT_PUBLIC_POSTHOG_KEY is baked into the client bundle and served to every
// visitor, so it must be the *project* token (phc_), which can only write
// events. PostHog's other keys look similar and sit a few lines apart in the
// same settings area -- a personal API key (phx_) can read and change the
// account, and shipping one to every browser would be a real credential leak.
// Empty stays allowed: that is how CI and local builds run.
const key = process.env.NEXT_PUBLIC_POSTHOG_KEY || '';
if (key !== '' && !key.startsWith('phc_')) {
  throw new Error(
    `NEXT_PUBLIC_POSTHOG_KEY starts "${key.slice(0, 4)}", not "phc_", so it is not a project token.\n` +
      'Only the project token belongs in the client bundle: PostHog > Settings > Project > Project API key.\n' +
      'A phx_ key is a personal API key -- a secret. If one has been used here, revoke it in\n' +
      'PostHog > Settings > Personal API keys rather than reusing it.',
  );
}

const nextConfig: NextConfig = {
  // PostHog's API paths end in a slash (/_ph/e/, /_ph/flags/). Without this,
  // Next.js answers each one with a 308 to the slashless form and the events
  // are dropped on the redirect. It changes nothing for the app's own pages:
  // none of them is linked with a trailing slash.
  skipTrailingSlashRedirect: true,

  // A first-party path for analytics, in the same spirit as /_api. Caddy sends
  // everything that is not /_api, /health or /worker* to this app, so /_ph
  // arrives here and is proxied on from the server -- the browser only ever
  // talks to our own domain, which is what gets past tracker blocking.
  rewrites() {
    return [
      // posthog-js's own bundle and the project's remote config. These come
      // from the assets host: it is the one that sends cache headers for them.
      { source: '/_ph/static/:path*', destination: `${POSTHOG_ASSETS}/static/:path*` },
      { source: '/_ph/array/:path*', destination: `${POSTHOG_ASSETS}/array/:path*` },
      // Everything else: events, feature flags, surveys. Last of the three,
      // because this pattern also matches the two above.
      { source: '/_ph/:path*', destination: `${POSTHOG_INGEST}/:path*` },
    ];
  },
};

export default nextConfig;
