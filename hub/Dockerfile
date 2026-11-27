# Ubuntu 26.10
FROM ubuntu:26.10@sha256:29ac5e68db830ab61b7d77c1ffdb78c73f0631de88a5d77bf4399868f64f38dd AS skel

# uv 0.13.8
FROM ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f AS uv

# Python 3.14.10 (Debian trixie)
FROM python:3.14.10-trixie@sha256:8a6b0a0ed63fb9d4ea59ee320591109f14205bbe7a787c51d9f72ad2a2f7e89c AS builder

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

# Python 3.14.10 (Debian trixie)
FROM python:3.14.10-trixie@sha256:8a6b0a0ed63fb9d4ea59ee320591109f14205bbe7a787c51d9f72ad2a2f7e89c AS final

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
