"""Inside the container, `statvfs` reports the XFS project quota as filesystem size
and inode counts, so these values are per environment rather than pool-wide.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Final

from jupyter_server.base.handlers import APIHandler
from tornado import web

DEFAULT_HOME: Final = "/home/ubuntu"


def home_directory() -> Path:
    return Path(os.environ.get("HOME") or DEFAULT_HOME)


def storage_report(path: Path) -> dict[str, int]:
    stats = os.statvfs(path)
    block_size = stats.f_frsize
    return {
        "bytesUsed": (stats.f_blocks - stats.f_bfree) * block_size,
        "bytesLimit": stats.f_blocks * block_size,
        "inodesUsed": stats.f_files - stats.f_ffree,
        "inodesLimit": stats.f_files,
    }


class StorageHandler(APIHandler):
    """`no_track_activity` polling preserves idle culling."""

    @web.authenticated
    def get(self) -> None:
        self.finish(json.dumps(storage_report(home_directory())))
