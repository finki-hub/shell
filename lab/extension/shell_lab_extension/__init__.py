"""Jupyter Server extension for Shell user environments.

Discovery is the `jupyter_server_config.d/shell_lab_extension.json` data file installed
into the venv's `etc/jupyter` (jupyter_server has no entry-point mechanism for
server extensions: `ServerApp.init_server_extension_config` merges
`jupyter_server_config.d/*.json` from every config path and nothing else).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from jupyter_server.base.handlers import path_regex
from jupyter_server.services.contents.handlers import (
    CheckpointsHandler,
    ModifyCheckpointsHandler,
    TrustNotebooksHandler,
)
from jupyter_server.utils import url_path_join

from shell_lab_extension.contents import (
    QuotaAwareFileManager,
    UploadSizeContentsHandler,
)
from shell_lab_extension.storage import StorageHandler
from shell_lab_extension.terminals import CappedTerminalManager

if TYPE_CHECKING:
    from jupyter_server.serverapp import ServerApp

__all__ = [
    "CappedTerminalManager",
    "QuotaAwareFileManager",
    "StorageHandler",
    "UploadSizeContentsHandler",
]


def _jupyter_server_extension_points() -> list[dict[str, str]]:
    return [{"module": "shell_lab_extension"}]


#: `(?P<checkpoint_id>...)` exactly as jupyter_server's own route spells it.
CHECKPOINT_ID_REGEX = r"(?P<checkpoint_id>[\w-]+)"

#: The temporary name a chunked upload writes to, `.<name>.part`.
PART_PATTERN: Final = re.compile(r"^\..*\.part$")


def _drop_upload_leftovers(root_dir: str) -> None:
    """Delete `.<name>.part` files an interrupted upload left in the home root."""
    try:
        for entry in Path(root_dir).iterdir():
            if PART_PATTERN.fullmatch(entry.name) and entry.is_file(
                follow_symlinks=False
            ):
                entry.unlink(missing_ok=True)
    except OSError:
        pass


def _load_jupyter_server_extension(server_app: ServerApp) -> None:
    _drop_upload_leftovers(server_app.root_dir)
    web_app = server_app.web_app
    base_url: str = web_app.settings["base_url"]
    contents = url_path_join(base_url, "api", "contents") + path_regex
    # `path_regex` is greedy, so a bare `/api/contents<path>` override would also
    # swallow `/api/contents/<path>/checkpoints`, `.../checkpoints/<id>` and
    # `.../trust` -- jupyter_server registers those *before* its own contents
    # route for exactly that reason. `add_handlers` inserts ahead of the wildcard
    # router holding them, so the same three have to be re-declared here, ahead
    # of the override, pointing back at jupyter_server's own handlers.
    handlers: list[tuple[str, Any]] = [
        (url_path_join(base_url, "lab", "storage"), StorageHandler),
        (contents + "/checkpoints", CheckpointsHandler),
        (f"{contents}/checkpoints/{CHECKPOINT_ID_REGEX}", ModifyCheckpointsHandler),
        (contents + "/trust", TrustNotebooksHandler),
        (contents, UploadSizeContentsHandler),
    ]
    web_app.add_handlers(".*$", handlers)  # type: ignore[no-untyped-call]
    server_app.log.info(
        "shell_lab_extension: storage and quota-aware contents routes registered"
    )
