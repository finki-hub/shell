# syntax=docker/dockerfile:1

# node:26-alpine
FROM node@sha256:438603bbb5cda404b9416f0b5c8026f61501ab911760d861ec28c36b5d675725 AS build
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

# caddy:2.11.7-alpine
FROM caddy@sha256:36c24ad91a7ff7bc4c87fe22bfa8df3c4b18da0773d73fc12ee2c52b98c0bb7e

RUN addgroup -S -g 10001 caddy \
    && adduser -S -D -H -u 10001 -G caddy caddy \
    && chown -R caddy:caddy /config /data

COPY --from=build /repo/web/dist /srv
COPY --chmod=644 web/Caddyfile /etc/caddy/Caddyfile

USER 10001:10001
