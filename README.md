# FINKI Hub Shell

FINKI Hub Shell gives each visitor a resource-limited Ubuntu shell in the browser. Each
environment has one container and a quota-limited home directory. Containers are disposable;
files remain until the environment reaches its retention limit or the user chooses **Start a
new one**.

The production stack consists of a React SPA served by Caddy, JupyterHub with DockerSpawner,
configurable-http-proxy, and isolated Jupyter Server containers. Only the Caddy loopback port
is published.

## Requirements

- A Linux host with Docker Engine and the Docker Compose plugin.
- `xfsprogs` and enough disk space for the preallocated XFS storage pool.
- Root access for the one-time pool setup.
- An outer reverse proxy that terminates TLS and forwards WebSocket upgrades.
- Outbound HTTPS to GitHub Container Registry and, when enabled, Cloudflare Turnstile.

## Install

Clone the repository into its permanent location, then initialize the storage pool and isolated
users network once:

```sh
sudo scripts/pool-init.sh /var/lib/finki-hub-shell/pool 150G
```

The size must include a unit accepted by `fallocate`, such as `150G`. The script preallocates
and formats an XFS image, adds its `prjquota,nosuid,nodev,noatime` mount to `/etc/fstab`, writes
the pool sentinel, and creates the `finki-hub-shell-users` network. It refuses to overwrite an
existing pool.

Create the runtime configuration and set the required proxy token:

```sh
cp .env.example .env
openssl rand -hex 32
chmod 600 .env
```

Put the generated value in `CONFIGPROXY_AUTH_TOKEN`, review the remaining settings, and start
the stack:

```sh
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8080/config.json
curl -fsS http://172.30.0.1:8081/hub/lab/ready
```

The `web`, `proxy`, and `hub` services should become healthy. User containers are created by
the hub as visitors arrive; they are not Compose services.

## Settings

| Key | Default | Purpose |
|---|---:|---|
| `CONFIGPROXY_AUTH_TOKEN` | required | Shared secret between JupyterHub and configurable-http-proxy; use at least 32 hexadecimal characters. |
| `HOST_PORT` | `8080` | Loopback port exposed by Caddy for the SPA and proxied API routes. |
| `LOG_LEVEL` | `INFO` | Hub log level. |
| `TZ` | `Europe/Skopje` | Timezone used by the stack. |
| `LAB_POOL_DIR` | `/var/lib/finki-hub-shell/pool` | Host path to the mounted XFS pool. |
| `LAB_POOL_RESERVE_PCT` | `2` | Free-space percentage reserved before new environment creation is refused. |
| `LAB_ENV_QUOTA_MB` | `100` | Per-environment home-directory quota in megabytes. |
| `LAB_ENV_MAX_INODES` | `20000` | Per-environment inode limit. |
| `LAB_IMAGE` | `ghcr.io/finki-hub/shell-lab:latest` | Image used for user containers. |
| `LAB_USER` | `ubuntu` | Username inside the user image. |
| `LAB_UID` | `1000` | User ID inside the user image; must not be zero. |
| `LAB_GID` | `1000` | Group ID inside the user image. |
| `LAB_MEMORY_MB` | `384` | Memory and memory-plus-swap limit for each user container. |
| `LAB_CPUS` | `0.5` | CPU limit for each user container. |
| `LAB_PIDS` | `256` | Process limit for each user container. |
| `LAB_TMP_MB` | `32` | Size of the `/tmp` tmpfs. |
| `LAB_VARTMP_MB` | `16` | Size of the `/var/tmp` tmpfs. |
| `LAB_RUN_MB` | `8` | Size of the `/run` tmpfs. |
| `LAB_SHM_MB` | `16` | Size of the `/dev/shm` tmpfs. |
| `LAB_MAX_SESSIONS` | `20` | Maximum number of simultaneously running user containers. |
| `LAB_MAX_TERMINALS` | `4` | Maximum number of terminals in one user container. |
| `LAB_IDLE_MIN` | `10` | Inactivity period before a running container is stopped. |
| `LAB_CONTAINER_MAX_AGE_H` | `24` | Maximum running-container age; `0` disables it. Files are unaffected. |
| `LAB_RETENTION_H` | `48` | Inactivity period before an environment and its files are deleted. |
| `LAB_MAX_AGE_H` | `0` | Absolute environment age limit; `0` disables it. |
| `LAB_MAX_CREATES_PER_MIN` | `60` | Global environment-creation rate limit. |
| `LAB_LOG_MAX_SIZE` | `512k` | Per-file Docker log limit for user containers. |
| `LAB_LOG_MAX_FILES` | `2` | Number of Docker log files retained per user container. |
| `TURNSTILE_SITEKEY` | empty | Cloudflare Turnstile site key. |
| `TURNSTILE_SECRET` | empty | Cloudflare Turnstile secret key. |

Turnstile is disabled when both keys are empty and enabled when both are set. Supplying only one
key is invalid. Keys are hostname-specific, so production keys must include the public hostname.

`LAB_MEMORY_MB` must be at least the sum of the four tmpfs sizes plus 96 MB. `LAB_USER`,
`LAB_UID`, and `LAB_GID` must match the user baked into the lab image.

## Reverse proxy

Point the public HTTPS site at Caddy's loopback port. A host-level Caddy configuration can be as
small as:

```caddyfile
shell.example.mk {
	reverse_proxy 127.0.0.1:8080
}
```

Caddy forwards the host, client address, scheme, and WebSocket upgrade headers automatically.
Configure equivalent forwarding when using another reverse proxy. Do not expose ports 8000,
8001, or 8081 publicly.

## Operations

Inspect the pool, quotas, environments, and running user containers without changing them:

```sh
sudo scripts/pool-status.sh /var/lib/finki-hub-shell/pool
```

Pull current images, recreate only changed services, and prune unused images:

```sh
scripts/update.sh
```

For automatic updates, install the supplied systemd units. They expect the checkout at
`/opt/finki-hub-shell`; edit both paths in the service unit first if the checkout is elsewhere.

```sh
sudo cp scripts/finki-hub-shell-update.service /etc/systemd/system/
sudo cp scripts/finki-hub-shell-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now finki-hub-shell-update.timer
```

Check scheduled and completed updates with:

```sh
systemctl list-timers finki-hub-shell-update.timer
journalctl -u finki-hub-shell-update.service
```

### Backup and restore

The storage pool and `data/hub` must be backed up and restored together. Stop the stack before
copying them so the XFS quota state and hub database are consistent:

```sh
docker compose down
tar czf finki-hub-shell-backup-$(date +%Y%m%d).tar.gz \
    ./data/hub \
    /var/lib/finki-hub-shell/pool
docker compose up -d
```

Keep `.env` separately as a secret. To restore, initialize and mount an empty pool first, stop
the stack, extract `data/hub` into the checkout and the pool contents at their original path,
then start the stack. Restore the pool's `.pool-id` with its contents; the hub refuses to start
when its recorded pool ID and the pool sentinel differ.

### Stop or remove the stack

```sh
docker compose down
```

This leaves the external users network, XFS pool, and `data/hub` intact. Removing the pool or
`data/hub` is permanent and is intentionally not part of the normal shutdown procedure.

## Security model

- User containers have a read-only root filesystem, no Linux capabilities, no privilege
  escalation, fixed memory/CPU/process/tmpfs limits, and capped Docker logs.
- The internal users network blocks container-to-container traffic and internet access. User
  containers can reach only the hub on the bridge gateway.
- The hub is trusted with the Docker socket and the storage pool so it can create containers and
  apply XFS project quotas. Keep its API ports host-local.
- Browser identity is stored locally. Accepting the refresh or tab-close prompt closes that
  terminal; a refresh starts a fresh terminal while the environment files remain.

## Repository layout

```text
compose.yaml, .env.example      production stack and runtime settings
hub/                            JupyterHub image and tests
lab/                            user image; lab/ext contains the Jupyter Server extension
web/                            React SPA and Caddy image
scripts/                        pool initialization, status, and update tooling
.github/workflows/              lint, test, and image workflow wrappers
```

## Development checks

```sh
cd hub && uv sync --dev
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q

cd ../lab/ext && uv sync --dev
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q

cd ../../web
npm run check && npm run lint && npm test

cd ..
docker compose config
```
