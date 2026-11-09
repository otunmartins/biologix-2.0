import posthog from "posthog-js"

const projectToken = process.env.NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN
// Our own domain by default, which next.config.ts forwards to PostHog. Not
// eu.i.posthog.com directly: that host is on every tracker blocklist, so a fair
// share of real users would silently report nothing at all. Set the variable to
// an absolute URL to bypass the proxy.
const host = process.env.NEXT_PUBLIC_POSTHOG_HOST || "/_ph"

if (!projectToken) {
  if (process.env.NODE_ENV !== "production") {
    console.error(
      "NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN variable required by PostHog is missing or un-configured, this causes events to be silently missed. This error stops appearing once NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN is configured"
    )
  }
} else {
  posthog.init(projectToken, {
    api_host: host,
    // Where the PostHog toolbar and its links point. This has to be their real
    // domain -- api_host above is ours, and ours has no UI to link to.
    ui_host: process.env.NEXT_PUBLIC_POSTHOG_UI_HOST || "https://eu.posthog.com",
    defaults: "2026-08-30",
    capture_exceptions: true,
    debug: process.env.NODE_ENV === "development",
  })
}
