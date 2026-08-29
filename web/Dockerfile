# syntax=docker/dockerfile:1

FROM node:26-alpine AS build
WORKDIR /repo

# Root workspace manifest first (cache layer for `npm ci`), then the web
# workspace's own manifest, before the rest of the source.
COPY package.json package-lock.json ./
COPY web/package.json web/package.json
RUN npm ci

COPY web/ web/

# PostHog is configured at build time: `src/lib/analytics.ts` reads
# `import.meta.env.VITE_POSTHOG_KEY` / `VITE_POSTHOG_HOST`, which Vite inlines
# into the bundle. Both must be declared here — BuildKit drops a `--build-arg`
# with no matching `ARG` — and promoted to `ENV`, because Vite reads them from
# the build process's environment, not from Docker's build-arg table. Empty
# defaults keep analytics simply disabled when the workflow passes nothing.
ARG VITE_POSTHOG_KEY=""
ARG VITE_POSTHOG_HOST=""
ENV VITE_POSTHOG_KEY=$VITE_POSTHOG_KEY \
    VITE_POSTHOG_HOST=$VITE_POSTHOG_HOST

RUN npm run build --workspace web

FROM caddy:2.11.4-alpine

COPY --from=build /repo/web/dist /srv
COPY web/Caddyfile /etc/caddy/Caddyfile
