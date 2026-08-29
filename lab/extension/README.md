# FINKI Hub / Shell / Lab Extension

The Lab Extension is an internal Jupyter Server extension installed in every
[Shell](../../README.md) user environment. It provides storage reporting and server-side file
and terminal safeguards for the web application. It is built into the user-environment image
and is not deployed as a standalone service or image.

The source directory is `lab/extension/`, the Python distribution is `shell-lab-extension`, and
its import and Jupyter Server extension package is `shell_lab_extension`. The complete
user-environment image remains `shell-lab`.

## Responsibilities

- Reports home-directory byte and inode usage through the authenticated `lab/storage` endpoint
- Checks declared upload sizes against available storage before the first chunk is written
- Refuses upload destinations that are directories or symbolic links
- Commits completed uploads atomically and replaces existing regular files
- Removes interrupted root-level upload files when Jupyter Server starts
- Enforces `LAB_MAX_TERMINALS` and returns `429` when the terminal limit is reached

The upload-size check improves when quota failures are reported, but the XFS project quota is
the authoritative storage limit.

## Runtime Integration

The [Lab Dockerfile](../Dockerfile) installs this Python project into the Jupyter environment
while building the user-environment image. The extension configuration at
[`jupyter-config/jupyter_server_config.d/shell_lab_extension.json`](./jupyter-config/jupyter_server_config.d/shell_lab_extension.json)
enables `shell_lab_extension`, and the parent
[`jupyter_server_config.py`](../jupyter_server_config.py) selects its contents and terminal
managers.

Full-stack setup and configuration belong in the [root README](../../README.md). The
[Hub](../../hub/README.md) is the complementary control service that creates the user containers
and applies their project quotas.

## Local Checks

Requires Python 3.14 and [`uv`](https://docs.astral.sh/uv/).

```sh
cd lab/extension
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest -q
```

## Test Scope

The tests cover the extension directly and through an in-process Jupyter Server. They use
temporary home directories and fake quota data; they do not start a container or require a real
XFS pool.

## License

This project is licensed under the terms of the MIT license.
