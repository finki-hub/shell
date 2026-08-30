"""Jupyter Server has no Python entry point for server extensions; it merges
`jupyter_server_config.d/*.json` from configured paths.
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


# Match Jupyter Server's checkpoint route parameter.
CHECKPOINT_ID_REGEX = r"(?P<checkpoint_id>[\w-]+)"

PART_PATTERN: Final = re.compile(r"^\..*\.part$")


def _drop_upload_leftovers(root_dir: str) -> None:
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
    # path_regex is greedy; preserve Jupyter's checkpoint/trust routes before the
    # contents override because add_handlers prepends this handler set.
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
