FROM ghcr.io/astral-sh/uv:python3.14-bookworm AS builder

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.14-bookworm AS final

# Seed homes from the Ubuntu lab image\'s /etc/skel.
COPY --from=ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517 /etc/skel /opt/skel

# curl serves the Compose healthcheck; xfsprogs provides xfs_quota for /srv/pool.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl xfsprogs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Run as root to create/chown homes and set XFS quotas on the read-write
# /srv/pool bind mount. Compose also mounts hub data at /srv/hub.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://172.30.0.1:8081/hub/lab/ready || exit 1

EXPOSE 8081

CMD ["jupyterhub", "-f", "/app/jupyterhub_config.py"]
