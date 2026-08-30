# syntax=docker/dockerfile:1

FROM node:26-alpine AS build
WORKDIR /repo

COPY package.json package-lock.json ./
COPY web/package.json web/package.json
RUN npm ci

COPY web/ web/

# Vite reads these values from the build environment, so declare and promote
# both BuildKit args; empty defaults disable analytics.
ARG VITE_POSTHOG_KEY=""
ARG VITE_POSTHOG_HOST=""
ENV VITE_POSTHOG_KEY=$VITE_POSTHOG_KEY \
    VITE_POSTHOG_HOST=$VITE_POSTHOG_HOST

RUN npm run build --workspace web

FROM caddy:2.11.4-alpine

COPY --from=build /repo/web/dist /srv
COPY web/Caddyfile /etc/caddy/Caddyfile
