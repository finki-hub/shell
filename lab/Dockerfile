# Build context is ./lab (the Dockerfile lives at ./lab/Dockerfile).
#
# The image users get a shell in. The official ubuntu image is Ubuntu Minimal
# (no editors, no docs), so the everyday toolbox is installed here. On top of it
# sits a uv-managed CPython 3.14 venv at /opt/jupyter that runs the single-user
# server; Ubuntu's own python3 is left alone for the people using the shell, and
# /opt/jupyter/bin is deliberately kept off PATH so the two never collide.
FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517 AS jupyter

COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /uvx /usr/local/bin/

ENV UV_PYTHON_INSTALL_DIR=/opt/uv \
    UV_PROJECT_ENVIRONMENT=/opt/jupyter \
    UV_PYTHON_DOWNLOADS=manual \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

RUN uv python install 3.14

WORKDIR /src

# Dependencies first: they change far less often than the extension itself.
COPY extension/pyproject.toml extension/uv.lock extension/.python-version ./
RUN uv sync --frozen --no-dev --no-editable --no-install-project

COPY extension/ ./
# --no-editable so the runtime image carries a real install rather than a .pth
# pointing back at this build stage's source tree. The extension's
# jupyter_server_config.d/shell_lab_extension.json data file lands in
# /opt/jupyter/etc/jupyter, which is what enables it at startup.
RUN uv sync --frozen --no-dev --no-editable

FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

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

# The spawner repeats HOME/TERM/LANG/TMPDIR/LAB_MAX_TERMINALS (and adds
# JUPYTERHUB_ALLOW_TOKEN_IN_URL) through c.DockerSpawner.environment; the
# defaults here keep the image runnable and correct on its own. The runtime and
# data directories sit under /tmp because the root filesystem is mounted
# read-only and /tmp is a tmpfs.
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
