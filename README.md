# FINKI Hub / Shell

Shell provides disposable, resource-limited Ubuntu environments in the browser. Each visitor
gets a terminal and file browser backed by a quota-limited home directory. Files remain when a
container stops and are removed only when the environment expires or the user chooses **Start a
new one**.

## Features

- Ubuntu terminal and file management in the browser
- Persistent home directories with XFS project quotas
- Fixed CPU, memory, process, inode, temporary-storage, and terminal limits
- Automatic idle shutdown and configurable environment retention
- Isolated user containers with no direct internet access
- Optional Cloudflare Turnstile protection

## Quick Setup (Production)

Shell requires:

- A 64-bit x86 Linux host with loop-device and XFS project-quota support
- Rootful Docker Engine with the Docker Compose plugin
- `xfsprogs`, `util-linux`, and OpenSSL
- Root access for the one-time storage-pool setup
- Enough disk space for the preallocated pool and network access to pull the images

1. Clone the repository:

   ```sh
   git clone https://github.com/finki-hub/shell.git
   cd shell
   ```

2. Create the storage pool and isolated users network:

   ```sh
   sudo ./scripts/pool-init.sh /var/lib/finki-hub-shell/pool 150G
   ```

   Replace `150G` with the capacity to allocate. The value must include a unit accepted by
   `fallocate`. The script refuses to overwrite an existing pool.

3. Create the runtime configuration and generate its required secret:

   ```sh
   cp .env.example .env
   chmod 600 .env
   sed -i "s/^CONFIGPROXY_AUTH_TOKEN=.*/CONFIGPROXY_AUTH_TOKEN=$(openssl rand -hex 32)/" .env
   ```

   Review `.env` before continuing. Leave both Turnstile settings empty for an immediate local
   trial, or set both to enable the challenge.

4. Pull every image, start Shell, and wait for the services to become healthy:

   ```sh
   docker compose --profile images pull
   docker compose up -d --wait
   ```

5. On the host running Shell, open [http://localhost:8080](http://localhost:8080). If
   `HOST_PORT` is changed in `.env`, use that port instead.

The web entry point listens only on the host loopback address. User containers are created when
visitors arrive, so they do not appear as running Compose services at startup.

## Quick Setup (Development)

Complete the production setup first so the Hub is available, then start the web development
server from the repository root:

```sh
npm ci
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). The development server forwards Shell API
and terminal traffic to the running stack.

## Configuration

All settings and defaults are documented in [`.env.example`](./.env.example). The settings most
commonly changed for a deployment are:

| Setting | Default | Description |
| --- | ---: | --- |
| `CONFIGPROXY_AUTH_TOKEN` | required | Internal shared secret; use at least 32 hexadecimal characters. |
| `HOST_PORT` | `8080` | Host-loopback port used to access Shell. |
| `LAB_POOL_DIR` | `/var/lib/finki-hub-shell/pool` | XFS storage pool created during setup. |
| `LAB_ENV_QUOTA_MB` | `100` | Home-directory quota for each environment, in megabytes. |
| `LAB_ENV_MAX_INODES` | `20000` | Inode limit for each environment. |
| `LAB_MEMORY_MB` | `384` | Memory limit for each user container, in megabytes. |
| `LAB_CPUS` | `0.5` | CPU limit for each user container. |
| `LAB_PIDS` | `256` | Process limit for each user container. |
| `LAB_MAX_SESSIONS` | `20` | Maximum number of concurrently running environments. |
| `LAB_MAX_TERMINALS` | `4` | Maximum number of terminals in one environment. |
| `LAB_IDLE_MIN` | `10` | Inactivity period before a running container stops. |
| `LAB_CONTAINER_MAX_AGE_H` | `24` | Maximum running-container age; `0` disables the limit. |
| `LAB_RETENTION_H` | `48` | Inactivity period before an environment and its files are deleted. |
| `LAB_MAX_AGE_H` | `0` | Absolute environment age limit; `0` disables the limit. |
| `TURNSTILE_SITEKEY` | empty | Optional Turnstile site key. |
| `TURNSTILE_SECRET` | empty | Optional Turnstile secret key. |

Turnstile is enabled only when both values are set, and its keys must match the hostname used to
access Shell. `LAB_MEMORY_MB` must be strictly greater than the four configured tmpfs sizes plus
96 MB. `LAB_USER`, `LAB_UID`, and `LAB_GID` must match the user in the selected environment image.

## Operations

Inspect pool usage, quotas, environments, and running user containers:

```sh
sudo bash scripts/pool-status.sh /var/lib/finki-hub-shell/pool
```

Pull current images and recreate changed services:

```sh
./scripts/update.sh
```

Stop the Compose-managed services:

```sh
docker compose down
```

The external users network, XFS pool, and `data/hub` remain in place.

## Components and Naming

Shell uses plain component names in prose and namespace-specific identifiers for source,
packaging, and deployment. These identifiers describe different artifacts; they are not
alternative product names.

| Component | Source | Role | Packaged identifiers |
| --- | --- | --- | --- |
| Web application | `web/` | Browser interface and web entry point | workspace and image `shell-web` |
| [Hub](./hub/README.md) | `hub/` | JupyterHub-based control service for identity, environments, quotas, and lifecycle | distribution `finki-hub`, import `finki_hub`, image `shell-hub` |
| User environment | `lab/` | Ubuntu image used for each visitor's container | image `shell-lab` |
| [Lab Extension](./lab/ext/README.md) | `lab/ext/` | Jupyter Server extension embedded in the user-environment image | distribution `finki-lab-ext`, import `finki_lab` |

`ext` is short for “extension.” It is a separately tested Python project installed inside the
user-environment image, not a standalone service or container. The `lab` Compose profile entry
exists only so the image can be pulled with the rest of the stack; the Hub creates the actual
user containers.

## Security

- User containers have a read-only root filesystem, no Linux capabilities, no privilege
  escalation, fixed resource limits, and capped logs.
- Their internal network blocks container-to-container traffic and has no direct internet route.
- The Hub is trusted with the Docker socket and storage pool so it can create containers and
  enforce XFS project quotas. Its internal service ports must remain host-local.
- Accepting the refresh or tab-close prompt closes the terminal. The environment's home files
  remain until its retention limit is reached or the user starts a new environment.

## Local Checks

The Python projects require Python 3.14 and [`uv`](https://docs.astral.sh/uv/).

Hub:

```sh
cd hub
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest -q
```

Lab Extension, starting again from the repository root:

```sh
cd lab/ext
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest -q
```

Web, from the repository root:

```sh
npm ci
cd web
npm run check
npm run lint
npm test
```

Stack configuration, from the repository root with `.env` configured:

```sh
docker compose config
```

## License

This project is licensed under the terms of the MIT license.
