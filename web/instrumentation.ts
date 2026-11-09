import { OTLPLogExporter } from '@opentelemetry/exporter-logs-otlp-http';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { BatchLogRecordProcessor, LoggerProvider } from '@opentelemetry/sdk-logs';

const posthogProjectToken = process.env.NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN;
const posthogHost = process.env.NEXT_PUBLIC_POSTHOG_HOST;

export const loggerProvider = posthogProjectToken && posthogHost
  ? new LoggerProvider({
      resource: resourceFromAttributes({ 'service.name': 'biologix-web' }),
      processors: [
        new BatchLogRecordProcessor({
          exporter: new OTLPLogExporter({
            url: `${posthogHost}/i/v1/logs`,
            headers: {
              Authorization: `Bearer ${posthogProjectToken}`,
              'Content-Type': 'application/json',
            },
          }),
        }),
      ],
    })
  : null;

// This dedicated logger is the only source this integration exports. Existing
// application console output and third-party loggers remain untouched.
export const posthogLogsLogger = loggerProvider?.getLogger('biologix.posthog.logs');

export function register() {
  if (process.env.NEXT_RUNTIME !== 'nodejs' || loggerProvider || process.env.NODE_ENV === 'production') return;

  const missingVariable = posthogProjectToken
    ? 'NEXT_PUBLIC_POSTHOG_HOST'
    : 'NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN';
  console.warn(
    `${missingVariable} variable required by PostHog is missing or un-configured, this causes events to be silently missed. This error stops appearing once ${missingVariable} is configured`,
  );
}
