from __future__ import annotations

import asyncio
import hashlib
import inspect
import subprocess
import threading
from typing import TYPE_CHECKING, Any

import pytest

from shell_hub import auth
from shell_hub.auth import (
    USERNAME_LENGTH,
    EnvironmentTokenAuthenticator,
    is_valid_token,
    username_for_token,
)
from shell_hub.quota import QuotaError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from shell_hub.settings import Settings

VALID_TOKEN = "AbC012_-" * 4


@pytest.mark.parametrize(
    ("token", "accepted"),
    [
        (VALID_TOKEN, True),
        ("x" * 32, True),
        ("x" * 128, True),
        ("x" * 31, False),
        ("x" * 129, False),
        ("", False),
        ("has space" + "x" * 24, False),
        ("has/slash" + "x" * 24, False),
        ("has.dot" + "x" * 26, False),
        ("x" * 32 + "\n", False),
    ],
)
def test_token_format_gate(token: str, accepted: bool) -> None:
    assert is_valid_token(token) is accepted


def test_username_is_the_truncated_sha256_of_the_token() -> None:
    expected = hashlib.sha256(VALID_TOKEN.encode()).hexdigest()[:32]

    derived = username_for_token(VALID_TOKEN)
    assert derived == expected
    assert len(derived) == USERNAME_LENGTH
    assert VALID_TOKEN not in derived


def test_username_is_stable_and_collision_free_for_neighbours() -> None:
    other = "AbC012_-" * 3 + "AbC012_X"

    assert username_for_token(VALID_TOKEN) == username_for_token(VALID_TOKEN)
    assert username_for_token(VALID_TOKEN) != username_for_token(other)


def test_username_is_a_safe_url_path_segment() -> None:
    derived = username_for_token(VALID_TOKEN)
    assert derived.isalnum()
    assert derived.islower()


def test_get_handlers_exposes_only_the_three_lab_routes() -> None:
    authenticator = EnvironmentTokenAuthenticator()

    routes = authenticator.get_handlers(app=None)

    assert [path for path, _ in routes] == [
        "/lab/login",
        "/lab/discard",
        "/lab/ready",
    ]
    assert all(path != "/login" for path, _ in routes)


def test_delete_user_is_a_coroutine_function() -> None:
    # The override must stay async so removal cannot block Hub I/O.
    assert inspect.iscoroutinefunction(EnvironmentTokenAuthenticator.delete_user)


class FakeUser:
    def __init__(self, name: str) -> None:
        self.name = name


def delete(username: str = "abcdef") -> None:
    asyncio.run(EnvironmentTokenAuthenticator().delete_user(FakeUser(username)))


def test_delete_user_removes_the_home_directory_and_releases_the_quota(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
    pool: Path,
) -> None:
    (pool / "users" / "abcdef").mkdir()
    (pool / ".projects").write_text("1001:abcdef\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: Any) -> Any:
        commands.append(list(args))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(auth, "get_settings", make_settings)

    delete()

    assert not (pool / "users" / "abcdef").exists()
    assert commands == [
        ["xfs_quota", "-x", "-c", "limit -p bhard=0 ihard=0 1001", str(pool)]
    ]


@pytest.mark.parametrize(
    "failure", [QuotaError("xfs_quota exited 1"), OSError("read-only file system")]
)
def test_delete_user_survives_a_failing_removal(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
    failure: Exception,
) -> None:
    def boom(_settings: Settings, _username: str) -> None:
        raise failure

    monkeypatch.setattr(auth, "get_settings", make_settings)
    monkeypatch.setattr(auth, "remove_home", boom)

    delete()


def test_delete_user_removes_the_home_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, make_settings: Callable[..., Settings]
) -> None:
    # The removal runs off the Hub event loop because the lock and xfs_quota can block.
    loop_thread = threading.current_thread()
    ran_on: list[threading.Thread] = []

    monkeypatch.setattr(auth, "get_settings", make_settings)
    monkeypatch.setattr(
        auth,
        "remove_home",
        lambda _settings, _username: ran_on.append(threading.current_thread()),
    )

    delete()

    assert ran_on
    assert ran_on[0] is not loop_thread
