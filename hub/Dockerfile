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

# Homes are seeded from the lab image\'s /etc/skel (Ubuntu), not this image\'s (Debian).
COPY --from=ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517 /etc/skel /opt/skel

# curl is the Compose healthcheck; xfsprogs supplies xfs_quota, which the hub
# runs against the pool it has mounted at /srv/pool.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl xfsprogs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# No USER: the hub runs as root because it mkdirs, chowns and sets XFS project
# quotas on the pool bind-mounted read-write at /srv/pool.
# Compose bind-mounts ./data/hub at /srv/hub and the XFS pool at /srv/pool.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://172.30.0.1:8081/hub/lab/ready || exit 1

EXPOSE 8081

CMD ["jupyterhub", "-f", "/app/jupyterhub_config.py"]
