# JupyterHub 5 → 6 integration fixture

This directory contains an opt-in full acceptance harness, **not evidence of a
successful migration until the workflow completes**. Runtime execution is
restricted to an ephemeral GitHub-hosted Ubuntu runner with an exclusive rootful
Docker daemon and verified XFS project-quota accounting and enforcement. The
production SSH host, its Docker daemon, local WSL, and Docker Desktop are forbidden.

## Safety boundary

`run_suite.py` requires all four transient runner/maintenance acknowledgments.
It refuses non-Linux/non-root, rootless or nonempty Docker, existing custom
networks/volumes, occupied ports, missing loop/XFS support, less than 25 GiB
free, and a missing pinned baseline object. It never loads a checkout `.env`,
calls `pool-init.sh`, edits fstab, invokes global prune, or accesses production.
It provisions its own one-GiB XFS loop filesystem, verifies project quota
accounting/enforcement with a bounded EDQUOT probe, and persists resource
creation intent before Docker calls, reconciling IDs and ownership afterward.
Cleanup removes only verified fixture resources.
The host-side runner requires Python 3.12 or newer; Hub workers use the pinned
image interpreter (Python 3.14).

The disposable proxy authentication token is generated once for the fixture and
kept in the runner's dedicated runtime environment dictionary. It is not written
to the mode-0600 fixture `.env`, Compose overrides, ownership manifest, backup
configuration, process argument list, or the runner's global environment. Only
Compose and the maintenance/updater child processes that consume the fixture
configuration receive that runtime environment. Runner-launched default
subprocesses—including direct Docker, image/build, metadata, probe, and archive
operations—remove inherited `CONFIGPROXY_AUTH_TOKEN` values. Compose and the
maintenance/updater children that consume fixture configuration receive the
scoped token environment; subprocesses launched by those children may inherit
it. This is not a claim that the token is hidden from the rootful Docker daemon:
daemon administrators can inspect container configuration/environment, and
that daemon is explicitly inside the disposable runner trust boundary.

```sh
sudo env RUNNER_TEMP="$RUNNER_TEMP" python3 scripts/integration/run_suite.py \
  --workspace "$GITHUB_WORKSPACE" \
  --acknowledge-disposable --acknowledge-interruption \
  --acknowledge-ingress-fenced --acknowledge-updater-paused
```

The workflow runs on relevant pull requests using read-only repository
permissions, and offers a separately acknowledged manual run. It does not use
`pull_request_target`, secrets, artifact upload, or production deployment.
The Linux nonprivileged checks run on each relevant PR. The 90-minute rootful
runtime job runs only after the parent applies the explicit
`jupyterhub-migration-runtime` PR label (or the acknowledged manual input).

`probe.py` is a reusable finite baseline/candidate/restore API-stage runner.
It creates two custom-login identities in baseline, stores raw test credentials
only in a mode-0600 state file beside the mode-0700 ownership marker, and checks
resume, bodyless spawn, file/storage APIs, scoped cross-user denials, token
attenuation, terminal WebSocket command execution (with a computed output
digest, not input echo), and stop/respawn home persistence. Candidate/restore
reuse the same browser and SPA tokens and compare the same per-user marker
files. Its origin is restricted to exact loopback hosts/ports; HTTP redirects
are not followed. Example, after the stack and marker are safely provisioned:

```sh
python scripts/integration/probe.py --base http://127.0.0.1:8000 \
  --acknowledge-disposable \
  --stage baseline --run-id "$RUN_ID" --marker-file "$RUN_ROOT/owner.json" \
  --state-file "$RUN_ROOT/probe-state.json"
```

The login endpoint also establishes JupyterHub's normal login cookie. The probe
keeps separate cookie jars for users A and B, checks cookie-authenticated Hub
pages and private-origin OAuth authorization, and uses a genuinely empty
anonymous client. Bearer-only and query-token checks do not inherit cookies.
The OAuth authorization flow is deliberately addressed to the private Hub
loopback origin. Public Caddy exposes `/hub/api/*`, the custom
`/hub/lab/login` and `/hub/lab/discard` endpoints, health/error/static paths,
and `/user/*`; its `/hub/*` fallback returns 404. Therefore public Caddy
browser-login/OAuth redirect routing is not claimed or tested, and the harness
does not add a source-auth rewrite. The full suite separately verifies real XFS
ownership and quota enforcement, installed Hub/Lab versions, and
shipping-helper behavior. The probe accepts only `http://127.0.0.1:8000`. Output is case/status only; credentials,
response bodies, environments, DB snapshots, and token-bearing URLs are not
published.

`test_db_migration.py` creates an empty test SQLite DB and synthetic two-user,
scoped-token, role, OAuth-client, and spawner-state fixture using the actual
immutable Hub 5.5.1 image's upstream ORM. It runs upstream `upgrade-db` in Hub
5, snapshots an allow-listed set of identity/token-hash/scope/role/spawner and
OAuth-client associations in private temporary storage, executes Hub 6.0.1
upgrade-db, checks the installed Alembic head and exact association preservation,
and repeats migration (schema-idempotence, not server startup). On a separate
cold Hub 5-schema copy, it starts the Hub 6 server with `upgrade_db=False`,
requires a schema/migration refusal, and verifies with the Hub 5 ORM that the
schema revision and identity snapshot were unchanged. It then restores another
cold copy and verifies old-image ORM compatibility. The fixture is automatically
seeded; no deployment DB is accepted or copied. Require immutable image IDs or
`@sha256` refs. Example:

```sh
python scripts/integration/test_db_migration.py \
  --acknowledge-disposable \
  --old-image sha256:<64-hex-image-id> --new-image sha256:<64-hex-image-id>
```

Its structured result contains image IDs, versions and schema revisions only.
The checked-in test has not been executed: it needs Docker plus both immutable
images. The full disposable suite separately proves a second positive Hub 6
server boot after explicit `upgrade-db`: the normal server is migration-disabled
(`JUPYTERHUB_ALLOW_DB_UPGRADE=false`), maintenance cullers remain suppressed,
and the Hub readiness/API probe must pass. ORM compatibility and repeated
schema-migration success are not treated as server-startup evidence.

## Full-suite stages

1. Build Hub/Lab from pinned pre-PR commit `6f682ee17c8affa988deffa44c56f2e39e28e462`
   and candidate `HEAD`; record image IDs, source hashes, and actual installed versions.
2. Run the upstream-ORM seeded DB migration/idempotence/cold-restore runner on an
   initially empty daemon.
3. Exercise two real API identities on Hub 5, including auth boundaries, home
   files/storage, cross-user denial, terminals, WebSocket command execution,
   and stop/respawn. Then prove the routine updater refuses the old image's
    unknown build label (without synthesizing a baseline label) and the old
    service IDs remain usable.
4. Call the shipping helper for preflight, migration, acceptance-failure
   rollback, and a separate successful candidate acceptance. Keep `web` fenced
   while private proxy acceptance probes run. Verify user/home/project/quota
   identity and repeat API/WebSocket checks after restore.
5. Emit sanitized structured results only; never upload raw state, DB files,
   credentials, container environments, or logs. Never issue global cleanup.

Private Hub cookie/OAuth authorization and off-origin redirect rejection are
covered. Public Caddy browser-login/OAuth redirect routing is not covered
because its `/hub/*` fallback intentionally returns 404; no source-auth route
rewrite is added. The unrelated/newer-server and destructive discard cases are
not substituted for these contracts. The scope is intentionally smaller than
browser QA.
