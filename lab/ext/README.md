# finki-lab-ext

The `jupyter_server` extension baked into the `shell-lab` user container. It adds the three
server-side limits the SPA cannot be trusted to keep: the person using the shell reaches the
single-user server on `127.0.0.1:8888` from inside their own container.

- `finki_lab.storage` — `GET <base_url>lab/storage` answers
  `{bytesUsed, bytesLimit, inodesUsed, inodesLimit}` from `statvfs($HOME)`, which inside the
  container reports the XFS *project* quota rather than the pool. `?no_track_activity=1` keeps
  the storage badge's poll from resetting the idle culler.
- `finki_lab.contents` — `QuotaAwareFileManager` refuses an upload whose declared size does not
  fit the remaining quota (507) before the first chunk is written, and refuses a destination that
  is an existing directory or a symlink (409). The declared size arrives as `X-Upload-Size`; a
  contents manager cannot read headers, so a thin `/api/contents` handler binds it to the request.
  It also makes the `.part` commit rename *replace* an existing file, which the base manager 409s.
- `finki_lab.terminals` — `CappedTerminalManager` answers 429 once `LAB_MAX_TERMINALS` ptys are
  open. `create` is the hook: terminado's `new_terminal` also runs on websocket reconnect.

`jupyter-config/jupyter_server_config.d/finki_lab.json`, installed as hatchling shared data into
the venv's `etc/jupyter`, enables it — `jupyter_server` discovers extensions from those files and
has no entry-point mechanism. Develop with `uv sync --dev`; check with `uv run ruff check . &&
uv run ruff format --check . && uv run mypy . && uv run pytest -q`.
