# uv 0.13.6
FROM ghcr.io/astral-sh/uv@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

# Keep /opt/jupyter/bin off PATH to separate server Python from shell Python.
# Ubuntu 26.10
FROM ubuntu@sha256:9c3a42b7c0016cbf87ceb71fd72d26c0e73b37e532e2c6071707f124c489c118 AS jupyter

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_PYTHON_INSTALL_DIR=/opt/uv \
    UV_PROJECT_ENVIRONMENT=/opt/jupyter \
    UV_PYTHON_DOWNLOADS=manual \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

RUN uv python install 3.14.10

WORKDIR /src

COPY extension/pyproject.toml extension/uv.lock extension/.python-version ./
RUN uv sync --frozen --no-dev --no-editable --no-install-project --no-build

COPY extension/ ./
# Install non-editably so runtime does not depend on this stage's source tree;
# the extension config installs under /opt/jupyter/etc/jupyter for startup.
RUN uv build --wheel --out-dir /wheels \
    && uv pip install --python /opt/jupyter/bin/python --no-deps --no-build /wheels/*.whl

# Ubuntu 26.10
FROM ubuntu@sha256:9c3a42b7c0016cbf87ceb71fd72d26c0e73b37e532e2c6071707f124c489c118

RUN sed -i '\|path-exclude=/usr/share/man/\*|d' /etc/dpkg/dpkg.cfg.d/excludes \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    bash-completion \
    build-essential \
    file \
    git \
    htop \
    jq \
    less \
    libarchive-tools \
    lsof \
    man-db \
    manpages \
    manpages-dev \
    nano \
    ncdu \
    procps \
    psmisc \
    python3 \
    rsync \
    strace \
    tmux \
    tree \
    unzip \
    vim \
    zip \
    && apt-get install --reinstall -y --no-install-recommends \
    bash \
    coreutils \
    findutils \
    grep \
    mawk \
    sed \
    tar \
    && rm -f /usr/bin/man \
    && dpkg-divert --quiet --remove --rename /usr/bin/man \
    && rm -rf /var/lib/apt/lists/*

COPY --from=jupyter /opt/uv /opt/uv
COPY --from=jupyter /opt/jupyter /opt/jupyter
COPY jupyter_server_config.py /opt/jupyter/etc/jupyter/jupyter_server_config.py

# Spawner overrides these defaults; they keep the image runnable on its own.
# Runtime and data use /tmp because the root filesystem is read-only and /tmp is
# a tmpfs.
ENV EDITOR=nano \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TERM=xterm-256color \
    HOME=/home/ubuntu \
    TMPDIR=/home/ubuntu/.tmp \
    LAB_MAX_TERMINALS=4 \
    JUPYTER_CONFIG_PATH=/opt/jupyter/etc/jupyter \
    JUPYTER_RUNTIME_DIR=/tmp/jupyter/runtime \
    JUPYTER_DATA_DIR=/tmp/jupyter/data

USER ubuntu
WORKDIR /home/ubuntu
CMD ["/opt/jupyter/bin/jupyterhub-singleuser"]
