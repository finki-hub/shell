# syntax=docker/dockerfile:1

FROM node:26-alpine@sha256:40cd011b55415bc894e45b02fdd5a5dfde056921b7d23a64c74f80497af48797 AS build
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

FROM caddy:2.11.7-alpine@sha256:eb4d35b7452d1ab6171fbdbe8d9275b2301dad6dad92115d91e19fb9b24d510f

RUN addgroup -S -g 10001 caddy \
    && adduser -S -D -H -u 10001 -G caddy caddy \
    && chown -R caddy:caddy /config /data

COPY --from=build /repo/web/dist /srv
COPY --chmod=644 web/Caddyfile /etc/caddy/Caddyfile

USER 10001:10001
