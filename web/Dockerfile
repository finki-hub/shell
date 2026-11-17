# syntax=docker/dockerfile:1

# node:26.13.0
FROM node:26.13.1@sha256:e18c3ccdf7d71619990b0e85639f506e2028a95410999cab24cc373cbd10e0ac AS build
WORKDIR /repo

COPY package.json package-lock.json ./
COPY web/package.json web/package.json
RUN npm ci --ignore-scripts

COPY web/ web/

# Vite reads these values from the build environment, so declare and promote
# both BuildKit args; empty defaults disable analytics.
ARG VITE_POSTHOG_KEY=""
ARG VITE_POSTHOG_HOST=""
ENV VITE_POSTHOG_KEY=$VITE_POSTHOG_KEY \
    VITE_POSTHOG_HOST=$VITE_POSTHOG_HOST

RUN npm run build --workspace web

# caddy:2.11.9-alpine
FROM caddy:2.11.9-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0

RUN addgroup -S -g 10001 caddy \
    && adduser -S -D -H -u 10001 -G caddy caddy \
    && chown -R caddy:caddy /config /data

COPY --from=build /repo/web/dist /srv
COPY --chmod=644 web/Caddyfile /etc/caddy/Caddyfile

USER 10001:10001
