from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import pytest

from shell_hub.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_MANAGED_PREFIXES = ("LAB_", "TURNSTILE_", "CONFIGPROXY_", "JUPYTERHUB_")
_MANAGED_KEYS = frozenset({"HUB_DATA_DIR", "LOG_LEVEL", "POOL_MOUNT", "TZ"})


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a developer shell that may export TZ, LAB_* or TURNSTILE_* already,
    # settings under test must depend only on what each test passes explicitly.
    for key in list(os.environ):
        if key.startswith(_MANAGED_PREFIXES) or key in _MANAGED_KEYS:
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def pool(tmp_path: Path) -> Path:
    root = tmp_path / "pool"
    (root / "users").mkdir(parents=True)
    (root / ".pool-id").write_text("pool-under-test\n", encoding="utf-8")
    return root


@pytest.fixture
def make_settings(tmp_path: Path, pool: Path) -> Callable[..., Settings]:
    def _make(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "configproxy_auth_token": "0" * 32,
            "pool_mount": pool,
            "hub_data_dir": tmp_path / "hub",
            "lab_pool_dir": tmp_path / "host-pool",
        }
        values.update(overrides)
        return Settings(**values)

    return _make


@pytest.fixture
def mountinfo(tmp_path: Path) -> Callable[[str, str], Path]:
    def _write(mount_point: str, line_tail: str) -> Path:
        path = tmp_path / "mountinfo"
        path.write_text(
            f"25 0 8:1 / {mount_point} rw,relatime shared:1 - {line_tail}\n",
            encoding="utf-8",
        )
        return path

    return _write


@pytest.fixture
def commands() -> list[list[str]]:
    """Records every command the quota code would have run."""
    return []
