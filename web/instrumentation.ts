import { SeverityNumber } from '@opentelemetry/api-logs';
import { OTLPLogExporter } from '@opentelemetry/exporter-logs-otlp-http';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { BatchLogRecordProcessor, LoggerProvider } from '@opentelemetry/sdk-logs';

const projectToken = process.env.NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN;
const host = process.env.NEXT_PUBLIC_POSTHOG_HOST;
const isConfigured = Boolean(projectToken && host);

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

  const missingVariable = projectToken ? 'NEXT_PUBLIC_POSTHOG_HOST' : 'NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN';
  console.warn(
    `${missingVariable} variable required by PostHog is missing or un-configured, this causes events to be silently missed. This error stops appearing once ${missingVariable} is configured`,
  );
}

export { SeverityNumber };
