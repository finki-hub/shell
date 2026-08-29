"""Storage report endpoint for the user container.

`statvfs` inside the container reports the XFS *project* quota as the
filesystem size and inode count, so the numbers below are per environment
rather than per pool.
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
    """The directory whose project quota the badge reports."""
    return Path(os.environ.get("HOME") or DEFAULT_HOME)


def storage_report(path: Path) -> dict[str, int]:
    """Bytes and inodes used/allowed for the project quota covering `path`."""
    stats = os.statvfs(path)
    block_size = stats.f_frsize
    return {
        "bytesUsed": (stats.f_blocks - stats.f_bfree) * block_size,
        "bytesLimit": stats.f_blocks * block_size,
        "inodesUsed": stats.f_files - stats.f_ffree,
        "inodesLimit": stats.f_files,
    }


class StorageHandler(APIHandler):
    """`GET <base_url>lab/storage` — the environment's quota usage.

    `APIHandler.finish` skips the activity stamp when `no_track_activity` is
    present in the query string, which is what keeps the SPA's storage poll
    from defeating the idle culler.
    """

    @web.authenticated
    def get(self) -> None:
        self.finish(json.dumps(storage_report(home_directory())))
