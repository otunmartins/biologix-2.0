FROM node:22-slim AS build
WORKDIR /app
# npm ci installs exactly what package-lock.json records and fails if the two
# disagree; npm install would re-resolve the ^ ranges on every deploy.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# NEXT_PUBLIC_* vars are baked in at BUILD time, not read at container start —
# so these have to be build args, not docker-compose `environment:` entries.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
# Analytics. Without this the bundle is built with no token, posthog-js never
# starts, and the deployed site reports nothing — which is how it ran before
# this arg existed. Unset is still a supported state: that is how CI builds.
# The token is a write-only project key that ships in the bundle anyway; it is
# passed as a secret only so it stays out of the Actions log.
ARG NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN
ENV NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN=$NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN
# The project is on EU cloud, which is the default in next.config.ts; these are
# only for pointing a build at a US or self-hosted project instead.
ARG NEXT_PUBLIC_POSTHOG_UI_HOST
ENV NEXT_PUBLIC_POSTHOG_UI_HOST=$NEXT_PUBLIC_POSTHOG_UI_HOST
ARG POSTHOG_INGEST_HOST
ENV POSTHOG_INGEST_HOST=$POSTHOG_INGEST_HOST
ARG POSTHOG_ASSETS_HOST
ENV POSTHOG_ASSETS_HOST=$POSTHOG_ASSETS_HOST
RUN npm run build

FROM node:22-slim
WORKDIR /app
COPY --from=build /app ./
EXPOSE 3000
CMD ["npm", "start"]
