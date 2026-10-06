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

### Manual Image Updates

Run a one-time image update from the repository checkout:

```sh
./scripts/update.sh
```

The updater pulls every Compose image, including the user-environment image, and activates
immutable image IDs only after compatible Hub/Lab metadata and retained Labs pass inspection.
Version changes or unknown legacy metadata require explicit maintenance. It does not drain Labs,
prune images, modify the repository checkout, or change `.env`.

### Automatic Image Updates

The supplied systemd units can run the updater automatically. The service runs as root and
expects the checkout at `/opt/finki-hub-shell` by default:

```sh
sudo install -m 0644 scripts/finki-hub-shell-update.service /etc/systemd/system/
sudo install -m 0644 scripts/finki-hub-shell-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

For a checkout in another directory, override the service command before enabling the timer:

```sh
sudo systemctl edit finki-hub-shell-update.service
```

Enter the following, replacing both paths with the absolute path to the checkout:

```ini
[Service]
ExecStart=
ExecStart=/absolute/path/to/shell/scripts/update.sh /absolute/path/to/shell
```

The default schedule runs every ten minutes at wall-clock minutes `00`, `10`, `20`, and so on.
To use another systemd calendar interval, create a timer override:

```sh
sudo systemctl edit finki-hub-shell-update.timer
```

For example, the following changes the schedule to hourly. The empty assignment clears the
original ten-minute schedule before adding the replacement:

```ini
[Timer]
OnCalendar=
OnCalendar=hourly
```

Once the path and schedule are correct, enable and start the timer:

```sh
sudo systemctl enable --now finki-hub-shell-update.timer
```

After changing either override, reload systemd and restart the timer:

```sh
sudo systemctl daemon-reload
sudo systemctl restart finki-hub-shell-update.timer
```

Inspect the next scheduled run or invoke and inspect an update immediately:

```sh
systemctl list-timers finki-hub-shell-update.timer
sudo systemctl start finki-hub-shell-update.service
sudo journalctl -u finki-hub-shell-update.service -n 50 --no-pager
```

`Persistent=true` causes one missed update to run after the host starts again.

Stop the Compose-managed services:

```sh
docker compose down
```

The external users network, XFS pool, and `data/hub` remain in place.

### JupyterHub 5.5.1 → 6.0.1: manual migration

**Draft READY for rehearsal; not operator-validated. Do not merge, publish tracked tags, or deploy until the owner approves the production gate and the validation owner rehearses this exact procedure on disposable storage.** Earlier PR #17 runtime attempt 18 does not validate this lean tree. Default-branch builds publish `latest`; a production updater tracking it must be paused **before** publication. This section grants no production access or deployment permission.

Follow the official [6.0.1 migration guide](https://jupyterhub.readthedocs.io/en/6.0.1/howto/upgrading-v6.html), [upgrade sequence](https://jupyterhub.readthedocs.io/en/6.0.1/howto/upgrading.html), and [database guidance](https://jupyterhub.readthedocs.io/en/6.0.1/explanation/database.html): cold backup, then `upgrade-db`, then server startup. Schema downgrade is unsupported; rollback restores the old database **and** old Hub/Lab images/configuration, never an old image against the migrated database.

`Settings.jupyterhub_allow_db_upgrade` defaults to `False`; Compose explicitly sets `JUPYTERHUB_ALLOW_DB_UPGRADE: "false"` (a `.env` value cannot override it). Normal startup will not upgrade an existing 5.x schema. The startup guard checks the actual Hub package against the Lab image label and every retained canonical Lab, including stopped containers. Unknown/mismatched images or ownership abort startup. The repository updater refuses version transitions and missing legacy labels; it neither drains Labs nor prunes images. Do not forge labels or bypass these refusals. Only the one-off migration below sets the flag true; that also suppresses the two source-configured cullers and their roles.

The deployed `/usr/local/sbin/finki-hub-shell-update` may have separate, every-minute logic: changing `scripts/update.sh` does not change it. Identify its **actual** timer/service and other schedulers/processes; pause and verify all inactive before publication. Identify/fence every ingress, including any host-network proxy listening publicly, external reverse proxies, direct Hub/API access, and existing WebSockets; stopping Caddy alone is insufficient. Keep that fence until explicit owner release acceptance. Do not guess unit names or use broad Docker stop/remove/prune commands.

The following is a **root Bash/Linux Compose template**, executed in stages, not a paste-and-run production script. Supply absolute `ROOT` (candidate checkout), `OLD_COMPOSE` and `OLD_ENV` (**mandatory protected captures of deployed Compose and `.env` before replacing/changing the checkout**), `BACKUP_PARENT`, `PROJECT` (existing Compose project), `UPDATE_TIMER`, `UPDATE_SERVICE`, `OWNED_LABS` (protected file of owner-proven full container IDs), and all eight `OLD_*`/`NEW_*` image references. Never assume current `ROOT/.env` is the old environment; also record effective shell/Compose overrides and abort if any are unrecorded. Resolve old Hub/Lab from deployed containers/inventory, not a moving tag. Inventory must reconcile old Hub spawner state, `finki.role=lab`, `finki.user`, canonical `lab-{username}`, the exact `${LAB_POOL_DIR}/users/{username}` home bind, image ID, and this deployment's network. Labels/names alone do not prove site ownership. Abort on foreign/ambiguous Labs rather than deleting them to unblock the guard. This template assumes the base Compose deployment without additional layers; otherwise preserve their order and obtain an independently rehearsed adaptation.

```bash
set -euo pipefail; umask 077
: "${ROOT:?}" "${OLD_COMPOSE:?}" "${OLD_ENV:?}" "${BACKUP_PARENT:?}" "${PROJECT:?}" "${UPDATE_TIMER:?}" "${UPDATE_SERVICE:?}" "${OWNED_LABS:?}" "${OLD_WEB:?}" "${OLD_PROXY:?}" "${OLD_HUB:?}" "${OLD_LAB:?}" "${NEW_WEB:?}" "${NEW_PROXY:?}" "${NEW_HUB:?}" "${NEW_LAB:?}"
timeout 30s systemctl stop "$UPDATE_TIMER" "$UPDATE_SERVICE"
test "$(timeout 10s systemctl show --property=ActiveState --value "$UPDATE_TIMER")" = inactive
test "$(timeout 10s systemctl show --property=ActiveState --value "$UPDATE_SERVICE")" = inactive
# OWNER GATE: installed wrapper, every scheduler/worker, and all ingress verified inactive/fenced.
exec 9>"${UPDATE_LOCK_FILE:-/run/lock/finki-hub-shell-update.lock}"; flock -n 9
test ! -e "$ROOT/.jupyterhub-maintenance.json"
(set -o noclobber; : > "$ROOT/.jupyterhub-maintenance.json") # existence gate, not controller state
RUN=$(mktemp -d "$BACKUP_PARENT/jh6.XXXXXXXX"); chmod 700 "$RUN"
cp -p "$OLD_COMPOSE" "$RUN/old-compose.yaml"; cp -p "$OLD_ENV" "$RUN/old.env"; chmod 600 "$RUN/old-compose.yaml" "$RUN/old.env"
d() { timeout 60s docker "$@"; }
for key in OLD_WEB OLD_PROXY OLD_HUB OLD_LAB NEW_WEB NEW_PROXY NEW_HUB NEW_LAB; do
  printf -v "$key" '%s' "$(d image inspect --format '{{.Id}}' "${!key}")"
  [[ ${!key} == sha256:* ]]; d image inspect --format '{{.Id}} {{json .RepoDigests}}' "${!key}" >> "$RUN/images.txt"
done
pkg() { d run --rm --network none --entrypoint "$2" "$1" -c "import importlib.metadata as m; assert m.version('jupyterhub') == '$3'; print('package PASS')"; }
pkg "$OLD_HUB" /app/.venv/bin/python 5.5.1; pkg "$OLD_LAB" /opt/jupyter/bin/python 5.5.1; pkg "$NEW_HUB" /app/.venv/bin/python 6.0.1; pkg "$NEW_LAB" /opt/jupyter/bin/python 6.0.1
for image in "$NEW_HUB" "$NEW_LAB"; do
  test "$(d image inspect --format '{{index .Config.Labels "org.finki-hub.jupyterhub-version"}}' "$image")" = 6.0.1
done # old labels may be absent: manual package proof is required, routine update still refuses
timeout 300s docker image save -o "$RUN/old-images.tar" "$OLD_WEB" "$OLD_PROXY" "$OLD_HUB" "$OLD_LAB"
for pair in OLD NEW; do # durable YAML/JSON pins override even hardcoded baseline references
  w=${pair}_WEB; p=${pair}_PROXY; h=${pair}_HUB; l=${pair}_LAB
  printf '{"services":{"web":{"image":"%s"},"proxy":{"image":"%s"},"hub":{"image":"%s","environment":{"LAB_IMAGE":"%s","JUPYTERHUB_ALLOW_DB_UPGRADE":"false"}},"lab":{"image":"%s"}}}\n' "${!w}" "${!p}" "${!h}" "${!l}" "${!l}" > "$RUN/${pair,,}-images.yaml"
  chmod 600 "$RUN/${pair,,}-images.yaml"
done
base() { timeout 180s docker compose --project-directory "$ROOT" --env-file "$RUN/old.env" -p "$PROJECT" -f "$ROOT/compose.yaml" "$@"; }
new() { WEB_IMAGE="$NEW_WEB" PROXY_IMAGE="$NEW_PROXY" HUB_IMAGE="$NEW_HUB" LAB_IMAGE="$NEW_LAB" base "$@"; }
newaccept() { base -f "$RUN/new-images.yaml" "$@"; } # accepted startup/restarts use retained file pins, not temporary shell environment pins
old() { timeout 180s docker compose --project-directory "$ROOT" --env-file "$RUN/old.env" -p "$PROJECT" -f "$RUN/old-compose.yaml" -f "$RUN/old-images.yaml" "$@"; } # pins last among deployment layers; private/accepted config overlays follow
# Extract only paths; never print .env or the rendered Compose model (it contains credentials).
readarray -t paths < <(new --profile images config --format json | timeout 30s python3 -c 'import json,sys; h=json.load(sys.stdin)["services"]["hub"]; v={m["target"]:m["source"] for m in h["volumes"]}; print(v["/srv/hub"]); print(v["/srv/pool"])')
test "${#paths[@]}" = 2; HUB_DATA=${paths[0]}; POOL=${paths[1]}
test -d "$HUB_DATA"; test -d "$POOL/users"; test -f "$HUB_DATA/jupyterhub.sqlite"
checkmodel() { timeout 30s python3 -c '
import json,sys
s=json.load(sys.stdin)["services"]; h=s["hub"]; v={m["target"]:m for m in h["volumes"]}
assert all(s[n]["image"]==i for n,i in zip(("web","proxy","hub","lab"),sys.argv[1:5]))
assert h["environment"]["LAB_IMAGE"]==sys.argv[4]
assert str(h["environment"]["JUPYTERHUB_ALLOW_DB_UPGRADE"]).lower()=="false"
assert h["network_mode"]==s["proxy"]["network_mode"]==s["web"]["network_mode"]=="host"
assert all(v[t]["source"]==p and not v[t].get("read_only",False) for t,p in zip(("/srv/hub","/srv/pool","/var/run/docker.sock"),sys.argv[5:8]))
if len(sys.argv)>8:
 target=sys.argv[9] if len(sys.argv)>9 else "/run/private.py"; assert v[target]["source"]==sys.argv[8] and v[target].get("read_only") and h["command"]==["jupyterhub","-f",target]
 if target=="/run/private.py":
  cmd=s["proxy"]["command"]; assert all(cmd[cmd.index(k)+1]==x for k,x in (("--ip","127.0.0.1"),("--port","8000"),("--api-ip","127.0.0.1"),("--api-port","8001"),("--error-target","http://172.30.0.1:8081/hub/error")))
print("Compose pins/binds PASS")
' "$@"; }
new --profile images config --format json | checkmodel "$NEW_WEB" "$NEW_PROXY" "$NEW_HUB" "$NEW_LAB" "$HUB_DATA" "$POOL" /var/run/docker.sock
old --profile images config --format json | checkmodel "$OLD_WEB" "$OLD_PROXY" "$OLD_HUB" "$OLD_LAB" "$HUB_DATA" "$POOL" /var/run/docker.sock
d network inspect finki-hub-shell-users > "$RUN/network.json"
timeout 30s python3 -c 'import json,sys; n,=json.load(open(sys.argv[1])); assert n["Name"]=="finki-hub-shell-users" and n["Driver"]=="bridge" and n["Internal"] is True; assert n["IPAM"]["Config"]==[{"Subnet":"172.30.0.0/23","Gateway":"172.30.0.1"}]; assert n["Options"].get("com.docker.network.bridge.enable_icc")=="false" and n["Options"].get("com.docker.network.bridge.name")=="br-finki-users"' "$RUN/network.json" # deviations abort; do not rewrite the network
new stop -t 30 web proxy hub
# Verify old deployed binds equal HUB_DATA/POOL, DSN is sqlite:////srv/hub/jupyterhub.sqlite, and no Hub/culler/other DB writer remains; otherwise STOP and adapt/rehearse.
while IFS= read -r id; do
  [[ $id =~ ^[0-9a-f]{64}$ ]]; d inspect --format '{{.Id}} {{.Image}} {{json .Mounts}}' "$id" >> "$RUN/labs.txt"
  if test "$(d inspect --format '{{.State.Running}}' "$id")" = true; then
    d kill --signal TERM "$id"
    if ! timeout 90s docker wait "$id" > /dev/null; then
      ids=$(d ps -aq --no-trunc); ! grep -Fxq "$id" <<< "$ids" # exact-ID AutoRemove proof, otherwise abort
    fi
  fi
done < "$OWNED_LABS"
# OWNER GATE: all selected Labs stopped/gone; no writers; numeric owners, quotas/project maps recorded.
timeout 300s cp -a "$HUB_DATA" "$RUN/hub-cold" # complete root includes SQLite WAL/SHM and cookie/crypto state
timeout 300s tar --numeric-owner --acls --xattrs -cpf "$RUN/pool-cold.tar" -C "$POOL" .
timeout 30s xfs_quota -x -c 'report -p -b -i' "$POOL" > "$RUN/quotas.txt"; stat -c '%u:%g %a %n' "$HUB_DATA" "$POOL" > "$RUN/ownership.txt"
while IFS= read -r id; do
  ids=$(d ps -aq --no-trunc) # successful daemon listing distinguishes exact-ID absence from daemon failure
  if grep -Fxq "$id" <<< "$ids"; then
    test "$(d inspect --format '{{.State.Running}}' "$id")" = false; d rm "$id"
  fi
done < "$OWNED_LABS" # only after cold backup; no --force, no -v, never delete homes or unknown containers
new run --rm --no-deps -e JUPYTERHUB_ALLOW_DB_UPGRADE=true hub jupyterhub upgrade-db -f /app/jupyterhub_config.py
new run --rm --no-deps -e JUPYTERHUB_ALLOW_DB_UPGRADE=true hub jupyterhub upgrade-db -f /app/jupyterhub_config.py # idempotent; parent also checks schema head read-only
cat > "$RUN/private.py" <<'PY'
from pathlib import Path
scope = globals().copy()  # retain Traitlets get_config; runpy alone would lose it
path = "/app/jupyterhub_config.py"
exec(compile(Path(path).read_text(encoding="utf-8"), path, "exec"), scope)
c = scope["c"]
assert c.DockerSpawner.network_name == "finki-hub-shell-users" and c.DockerSpawner.hub_connect_url == c.JupyterHub.hub_bind_url == c.JupyterHub.hub_connect_url == "http://172.30.0.1:8081"
c.JupyterHub.upgrade_db = False  # essential also for old 5.x config, which auto-upgrades by default
c.JupyterHub.bind_url = "http://127.0.0.1:8000"
c.JupyterHub.hub_bind_url = "http://172.30.0.1:8081"
c.JupyterHub.hub_connect_url = "http://172.30.0.1:8081"
c.ConfigurableHTTPProxy.api_url = "http://127.0.0.1:8001"
names = {"idle-culler-servers", "idle-culler-users"}
c.JupyterHub.services = [s for s in c.JupyterHub.services if s.get("name") not in names]
c.JupyterHub.load_roles = [r for r in c.JupyterHub.load_roles if r.get("name") not in names]
PY
cat > "$RUN/private.yaml" <<EOF
services:
  proxy:
    command: [--ip, "127.0.0.1", --port, "8000", --api-ip, "127.0.0.1", --api-port, "8001", --error-target, "http://172.30.0.1:8081/hub/error"]
  hub:
    environment:
      JUPYTERHUB_ALLOW_DB_UPGRADE: "false"
    command: [jupyterhub, -f, /run/private.py]
    volumes:
      - "$RUN/private.py:/run/private.py:ro"
EOF
chmod 600 "$RUN/private.py" "$RUN/private.yaml"
cat > "$RUN/accepted-old.py" <<'PY'
from pathlib import Path; scope = globals().copy(); path = "/app/jupyterhub_config.py"
exec(compile(Path(path).read_text(encoding="utf-8"), path, "exec"), scope); c = scope["c"]; c.JupyterHub.upgrade_db = False
PY
printf 'services: {hub: {environment: {JUPYTERHUB_ALLOW_DB_UPGRADE: "false"}, command: [jupyterhub, -f, /run/accepted-old.py], volumes: ["%s:/run/accepted-old.py:ro"]}}\n' "$RUN/accepted-old.py" > "$RUN/accepted-old.yaml"
chmod 600 "$RUN/accepted-old.py" "$RUN/accepted-old.yaml"
old -f "$RUN/accepted-old.yaml" --profile images config --format json | checkmodel "$OLD_WEB" "$OLD_PROXY" "$OLD_HUB" "$OLD_LAB" "$HUB_DATA" "$POOL" /var/run/docker.sock "$RUN/accepted-old.py" /run/accepted-old.py
newaccept --profile images config --format json | checkmodel "$NEW_WEB" "$NEW_PROXY" "$NEW_HUB" "$NEW_LAB" "$HUB_DATA" "$POOL" /var/run/docker.sock
printf '%q ' docker compose --project-directory "$ROOT" --env-file "$RUN/old.env" -p "$PROJECT" -f "$ROOT/compose.yaml" -f "$RUN/new-images.yaml" > "$RUN/accepted-new-command.args"
printf '%q ' docker compose --project-directory "$ROOT" --env-file "$RUN/old.env" -p "$PROJECT" -f "$RUN/old-compose.yaml" -f "$RUN/old-images.yaml" -f "$RUN/accepted-old.yaml" > "$RUN/accepted-old-command.args"
new -f "$RUN/private.yaml" --profile images config --format json | checkmodel "$NEW_WEB" "$NEW_PROXY" "$NEW_HUB" "$NEW_LAB" "$HUB_DATA" "$POOL" /var/run/docker.sock "$RUN/private.py"
old -f "$RUN/private.yaml" --profile images config --format json | checkmodel "$OLD_WEB" "$OLD_PROXY" "$OLD_HUB" "$OLD_LAB" "$HUB_DATA" "$POOL" /var/run/docker.sock "$RUN/private.py"
new -f "$RUN/private.yaml" up -d --no-deps --wait --wait-timeout 120 proxy hub web
```

Before private startup, the validation owner must inspect a **protected** effective model and assert all four image IDs, Hub `LAB_IMAGE`, the original three writable Hub binds, the extra read-only config bind, unchanged `finki-hub-shell-users`, and the false environment flag. No model/environment dump in logs. The template asserts the source-created internal bridge, `172.30.0.0/23` subnet, `172.30.0.1` gateway, `br-finki-users` bridge name, disabled ICC, and image-owned Hub/Spawner URL constants; **abort on any deviation**, rather than silently rebinding or recreating a deployed network. Confirm both private culler service/role lists exclude exactly the two names above, preserving unrelated user/admin roles. Public-facing CHP/Web listen only on loopback; CHP API is `127.0.0.1:8001`; Hub API remains on the internal users bridge `172.30.0.1:8081` so Lab traffic works. Verify those actual listeners (`timeout 10s ss -lntp`), firewall/ingress fences, and absence of external access; host networking alone is not isolation. Hub runs as root for quotas; Lab remains unprivileged `ubuntu`, UID/GID 1000 unless a verified matching image/config says otherwise.

Private acceptance must cover schema head and repeated upgrade, preserved users/tokens/cookie/crypto identities (record PASS, never values), own-model API success and denied cross-user/admin access, spawn/stop/restart with matching 6.0.1 Lab, terminal WebSockets, home files, numeric ownership, XFS quotas/project maps, resource/security limits, and no token/credential leakage. Use only explicitly owned disposable checks; reconcile/remove any test Lab by full ID before rollback. Source cullers are disabled only by the transient upgrade flag; private checks use the temporary overlay instead of leaving that flag true. Ordinary accepted 6.x startup uses `/app/jupyterhub_config.py`, false upgrade flag, normal cullers, and **retained `new-images.yaml` pins**. Owner must accept culler resumption before `newaccept up -d --no-deps --wait --wait-timeout 120 proxy hub web` removes the private overlay, and explicitly accept public routing before opening ingress. Never substitute bare `new up` or assume temporary function environment pins survive another shell/reboot. Preserve the protected captured `.env`, pin/config files, and resolved Compose argument records at their referenced paths for every accepted restart. Keep the lock/marker and installed updater inactive through these decisions and the reconciliation gate below.

**Cold rollback, while still fenced:** stop the private candidate with `new -f "$RUN/private.yaml" stop -t 30 web proxy hub`; gracefully stop only proven candidate Labs and verify all writers off. Preserve failed 6.x data with `mv "$HUB_DATA" "$RUN/hub-failed-6"`, then `cp -a "$RUN/hub-cold" "$HUB_DATA"`. Keep the captured protected old `.env`, cookie/crypto material, old images (load with `timeout 300s docker image load -i "$RUN/old-images.tar"` if unavailable), numeric ownership, pool and project maps; do not overwrite/delete home data casually. Start old images privately with `old -f "$RUN/private.yaml" up -d --no-deps --wait --wait-timeout 120 proxy hub web`. The private Python override forces `upgrade_db=False` **after** loading original 5.x config: the new environment flag alone cannot protect rollback. Verify old 5.5.1 functionality and identities privately; only after owner acceptance restore normal cullers with `old -f "$RUN/accepted-old.yaml" up -d --no-deps --wait --wait-timeout 120 proxy hub web`, and release public routing only after its separate acceptance. **Never use bare `old up`: retain the protected accepted-old Python/Compose overlay for every later old-image restart, including after public acceptance, so automatic DB upgrade stays false while normal cullers remain unchanged.** Neither private nor accepted config overlays may change the preceding all-four image pins/Hub `LAB_IMAGE`; check their effective models against the actual hardcoded saved baseline. The pool archive includes `.projects`/`.projid-counter`, but is not an XFS image snapshot and does not alone restore inode project IDs/quota state. Pool restoration or failed-data cleanup needs separate explicit owner approval, never blind recursive deletion.

**Pending publication gates:** disposable rehearsal must run these exact migration/private-overlay/rollback commands, including legacy-label refusal, AutoRemove disappearance handling, durable all-four image pins against the actual hardcoded baseline, accepted-old runtime `upgrade_db=False` with normal culler service/role equality, network-deviation aborts, schema/identity checks, and retained foreign-Lab aborts. These commands have not been executed here; parent validation owns that evidence. **Unresolved deployment-specific gate: before marker removal or timer resumption, the owner must inspect/reconcile the actual installed wrapper, its service/timer and restart paths against the retained accepted Compose argument record; do not assume it honors image environment variables or extra files.** Re-run the protected `checkmodel` pipeline for the accepted branch using the wrapper's actual resolved invocation, assert all four IDs/Hub `LAB_IMAGE`, binds and false upgrade mode (for rollback, retained accepted-old Python config with unchanged normal cullers), and verify running image/config identities without dumping secrets. If the wrapper can bypass/drop pins/config or its behavior is unknown, **leave marker/lock and timer pause in place** until separately authorized reconciliation and rehearsal; this session supplies no production fix. Real deployed units, pre-change environment capture/effective overrides, ingress fencing and external-provider/Caddy URLs require owner verification. Only after all gates and explicit owner acceptance remove this session's marker (`rm -- "$ROOT/.jupyterhub-maintenance.json"`), release fd 9 (`flock -u 9; exec 9>&-`), and separately authorize timer resumption. Retain protected snapshots/image archives and accepted pin/config files; never globally prune images during the rollback window.

## Components and Naming

Shell uses plain component names in prose and namespace-specific identifiers for source,
packaging, and deployment. These identifiers describe different artifacts; they are not
alternative product names.

| Component | Source | Role | Packaged identifiers |
| --- | --- | --- | --- |
| Web application | `web/` | Browser interface and web entry point | workspace and image `shell-web` |
| [Hub](./hub/README.md) | `hub/` | JupyterHub-based control service for identity, environments, quotas, and lifecycle | distribution `shell-hub`, import `shell_hub`, image `ghcr.io/finki-hub/shell-hub` |
| User environment | `lab/` | Ubuntu image used for each visitor's container | image `shell-lab` |
| [Lab Extension](./lab/extension/README.md) | `lab/extension/` | Jupyter Server extension embedded in the user-environment image | distribution `shell-lab-extension`, import `shell_lab_extension` |

The Lab Extension is a separately tested Python project installed inside the user-environment
image, not a standalone service or container. The `lab` Compose profile entry exists only so the
image can be pulled with the rest of the stack; the Hub creates the actual user containers.

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
cd lab/extension
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
