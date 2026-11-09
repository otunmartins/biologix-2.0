import { SeverityNumber } from '@opentelemetry/api-logs';
import { OTLPLogExporter } from '@opentelemetry/exporter-logs-otlp-http';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { BatchLogRecordProcessor, LoggerProvider } from '@opentelemetry/sdk-logs';

const projectToken = process.env.NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN;
// An ABSOLUTE host, unlike the browser's. This runs on the server, where the
// relative /_ph that the browser uses is not a URL at all and there is no
// proxy to go through -- the server can reach PostHog directly, and nothing
// blocks requests it makes. Same default region as next.config.ts.
const host = process.env.POSTHOG_INGEST_HOST || 'https://eu.i.posthog.com';
const isConfigured = Boolean(projectToken);

export const posthogLoggerProvider = new LoggerProvider({
  resource: resourceFromAttributes({ 'service.name': 'biologix-web' }),
  processors: isConfigured
    ? [
        new BatchLogRecordProcessor({
          exporter: new OTLPLogExporter({
            url: `${host}/i/v1/logs`,
            headers: {
              Authorization: `Bearer ${projectToken}`,
              'Content-Type': 'application/json',
            },
          }),
        }),
      ]
    : [],
});

export const posthogLogger = posthogLoggerProvider.getLogger('posthog-wizard');

export function register() {
  if (process.env.NEXT_RUNTIME !== 'nodejs' || isConfigured || process.env.NODE_ENV === 'production') return;

  // The host now has a default, so the token is the only thing that can be
  // missing -- which is the ordinary state locally and in CI.
  console.warn(
    'NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN variable required by PostHog is missing or un-configured, this causes events to be silently missed. This error stops appearing once NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN is configured',
  );
}

export { SeverityNumber };
