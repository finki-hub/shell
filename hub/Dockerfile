# Ubuntu 26.10
FROM ubuntu:26.10@sha256:b5194b8e58989da0865ebc80078bf9f5f8742ec6a867c53f6ae699f6036756f7 AS skel

# uv 0.13.8
FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv

# Python 3.14.10 (Debian trixie)
FROM python:3.14.10-trixie@sha256:8e4ff723e809a8e3bbd61bdceb92e9031689ad11edccff3306c0b3ebe60704ff AS builder

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
FROM python:3.14.10-trixie@sha256:8e4ff723e809a8e3bbd61bdceb92e9031689ad11edccff3306c0b3ebe60704ff AS final

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
