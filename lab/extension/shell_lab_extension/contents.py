"""The handler stores the `X-Upload-Size` header in a request-bound context variable
so the manager can reject oversized transfers before the first chunk reaches disk.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Final

from jupyter_core.paths import is_hidden
from jupyter_server.services.contents.handlers import ContentsHandler
from jupyter_server.services.contents.largefilemanager import LargeFileManager
from tornado import web

UPLOAD_SIZE_HEADER: Final = "X-Upload-Size"
FIRST_CHUNK: Final = 1

_declared_upload_size: ContextVar[int | None] = ContextVar(
    "shell_lab_extension_declared_upload_size",
    default=None,
)


def set_declared_upload_size(raw: str | None) -> None:
    try:
        value = int(raw) if raw is not None else None
    except ValueError:
        value = None
    _declared_upload_size.set(value if value is not None and value >= 0 else None)


def declared_upload_size() -> int | None:
    return _declared_upload_size.get()


class UploadSizeContentsHandler(ContentsHandler):
    async def prepare(self) -> None:  # type: ignore[override]
        set_declared_upload_size(self.request.headers.get(UPLOAD_SIZE_HEADER))
        await super().prepare()


class QuotaAwareFileManager(LargeFileManager):
    """Reject symlink destinations before LargeFileManager resolves them."""

    def save(self, model: dict[str, Any], path: str = "") -> dict[str, Any]:
        os_path = Path(self._get_os_path(path.strip("/")))  # type: ignore[no-untyped-call]
        self._refuse_unsafe_destination(os_path, model)
        chunk = model.get("chunk")
        if chunk is None or chunk == FIRST_CHUNK:
            self._preflight_quota(model)
        saved: dict[str, Any] = super().save(model, path)  # type: ignore[no-untyped-call]
        return saved

    def _refuse_unsafe_destination(
        self,
        os_path: Path,
        model: dict[str, Any],
    ) -> None:
        if os_path.is_symlink():
            raise web.HTTPError(409, "symlink-destination")
        if model.get("type") != "directory" and os_path.is_dir():
            raise web.HTTPError(409, "directory-destination")

    def _preflight_quota(self, model: dict[str, Any]) -> None:
        declared = declared_upload_size()
        if declared is None:
            size = model.get("size")
            declared = size if isinstance(size, int) else None
        if declared is None:
            return
        if declared > self.available_bytes():
            raise web.HTTPError(507, "insufficient-storage")

    def rename_file(self, old_path: str, new_path: str) -> None:
        """Atomically replace a regular file to complete a `.part` upload."""
        old_path = old_path.strip("/")
        new_path = new_path.strip("/")
        new_os_path = Path(self._get_os_path(new_path))  # type: ignore[no-untyped-call]
        if new_os_path.is_symlink():
            raise web.HTTPError(409, "symlink-destination")
        if new_os_path.is_dir():
            raise web.HTTPError(409, "directory-destination")
        if new_path == old_path or not new_os_path.exists():
            super().rename_file(old_path, new_path)  # type: ignore[no-untyped-call]
            return
        old_os_path = Path(self._get_os_path(old_path))  # type: ignore[no-untyped-call]
        if not self.allow_hidden and (
            is_hidden(old_os_path, self.root_dir)
            or is_hidden(new_os_path, self.root_dir)
        ):
            raise web.HTTPError(
                400, f"Cannot rename file or directory {str(old_os_path)!r}"
            )
        try:
            with self.perm_to_403():
                old_os_path.replace(new_os_path)
        except web.HTTPError:
            raise
        except FileNotFoundError:
            raise web.HTTPError(
                404, f"File or directory does not exist: {old_path}"
            ) from None
        except OSError as error:
            raise web.HTTPError(
                500, f"Unknown error renaming file: {old_path}"
            ) from error

    def available_bytes(self) -> int:
        stats = os.statvfs(self.root_dir)
        return stats.f_bavail * stats.f_frsize
