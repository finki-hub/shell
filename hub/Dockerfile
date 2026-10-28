FROM ubuntu:26.10@sha256:9c3a42b7c0016cbf87ceb71fd72d26c0e73b37e532e2c6071707f124c489c118 AS skel

FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.15.2-trixie@sha256:b9dc13dd9e9c69543a3a8ae44ecae5f6a17ddd45f52ac335315de0ff1d9a67ae AS builder

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --no-build

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --out-dir /wheels \
    && uv pip install --python /app/.venv/bin/python --no-deps --no-build /wheels/*.whl

FROM python:3.15.2-trixie@sha256:b9dc13dd9e9c69543a3a8ae44ecae5f6a17ddd45f52ac335315de0ff1d9a67ae AS final

# Seed homes from the Ubuntu lab image\'s /etc/skel.
COPY --from=skel /etc/skel /opt/skel

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
