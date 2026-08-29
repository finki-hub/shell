# shell-hub

The JupyterHub image for the FINKI shell: identity without a login page, the
three `/hub/lab/*` endpoints the SPA calls, and the pool — home directories and
their XFS project quotas, created and removed in-process before every spawn and
on every deletion. `jupyterhub_config.py` is a thin file that reads
`finki_hub.settings.Settings`, asserts the boot invariants and sets traits.

## Local checks

A `uv` project on Python 3.14; `uv.lock` is committed and CI runs `--frozen`.

```sh
cd hub
uv sync --dev          # create .venv from the lockfile
uv run ruff check .    # lint (select = ALL, chat-bot ignore list)
uv run ruff format .   # apply formatting (--check in CI)
uv run mypy .          # strict overrides on finki_hub.* and jupyterhub_config
uv run pytest -q       # unit tests; no Docker, no hub, no network
```

Run all four before pushing; CI runs exactly the same commands through the
reusable `finki-hub/.github` workflows with `working-directory: ./hub`.

## Notes for contributors

Tests never touch Docker, `xfs_quota` or a real pool: `tests/fakes.py` supplies
the Docker client, `tests/conftest.py` builds `Settings` from explicit values and
points `pool_mount`/`hub_data_dir` at `tmp_path`, `/proc/self/mountinfo` is faked
through `finki_hub.readiness.MOUNTINFO`, and every pool mutation takes its
command runner as an argument (`provision_home(..., run=...)`). Keep the decision
logic in pure functions (`perform_login`, `assert_admission`, `check_login_headers`)
and the Tornado handlers thin, so behaviour stays testable without a running hub.
JupyterHub and DockerSpawner ship no type information, so their subclasses carry a
narrow `# type: ignore[misc]`; never widen it to a blanket ignore.
