# FINKI Hub / Shell / Hub

The Hub is [Shell's](../README.md) JupyterHub-based control service. It manages browser identity,
environment admission and lifecycle, persistent home directories, XFS project quotas, and the
creation of isolated user containers. It has no standalone user interface.

## Responsibilities

- Handles environment login and deletion requests from the web application
- Derives environment identities from opaque browser tokens and issues scoped API tokens
- Creates and removes persistent home directories with XFS project quotas
- Starts user containers with the configured resource and security limits
- Enforces session, creation-rate, idle, age, retention, and storage-admission limits
- Reports whether Docker and the storage pool are ready
- Verifies Turnstile responses for new environments when Turnstile is configured

## Runtime Integration

[`jupyterhub_config.py`](./jupyterhub_config.py) is the JupyterHub entry point. It loads typed
settings from `shell_hub`, validates the host and pool prerequisites, configures DockerSpawner,
and registers Shell's request handlers.

The Hub runs as a trusted service because it must access the Docker socket and manage project
quotas in the storage pool. Full-stack setup and configuration belong in the
[root README](../README.md).

## Local Checks

Requires Python 3.14 and [`uv`](https://docs.astral.sh/uv/).

```sh
cd hub
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest -q
```

## Test Scope

The tests use fake Docker clients, temporary directories, and injected command runners. They do
not start Docker containers, JupyterHub, use a real XFS pool, or make outbound network requests.

## License

This project is licensed under the terms of the MIT license.
