from __future__ import annotations

import asyncio
import os
import subprocess
import threading
from typing import TYPE_CHECKING, Any, cast

import pytest

from shell_hub import quota, readiness
from shell_hub.quota import (
    QuotaError,
    QuotaRefusalError,
    assert_admission,
    chown_tree,
    ensure_home,
    pre_spawn_hook,
    provision_home,
    remove_home,
    run_command,
)
from shell_hub.readiness import PoolAssertionError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from shell_hub.settings import Settings

XFS_PRJQUOTA = "xfs /dev/sda1 rw,prjquota,noatime"


@pytest.fixture
def owners() -> list[tuple[str, int, int]]:
    return []


@pytest.fixture(autouse=True)
def _hermetic_pool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, owners: list[tuple[str, int, int]]
) -> None:
    # Given: the suite does not run as root and must not read the host's
    # /etc/skel, ownership is recorded and skel starts out empty.
    monkeypatch.setattr(
        quota,
        "chown_tree",
        lambda root, uid, gid: owners.append((root.name, uid, gid)),
    )
    empty_skel = tmp_path / "skel"
    empty_skel.mkdir()
    monkeypatch.setattr(quota, "SKEL_DIR", empty_skel)


@pytest.fixture
def mounted_pool(
    monkeypatch: pytest.MonkeyPatch,
    mountinfo: Callable[[str, str], Path],
    pool: Path,
) -> Path:
    monkeypatch.setattr(readiness, "MOUNTINFO", mountinfo(str(pool), XFS_PRJQUOTA))
    return pool


@pytest.fixture
def record(commands: list[list[str]]) -> Callable[[Sequence[str]], None]:
    """A command runner that records instead of invoking xfs_quota."""

    def _run(args: Sequence[str]) -> None:
        commands.append(list(args))

    return _run


# --- home directories --------------------------------------------------------


def test_provisioning_creates_and_quotas_a_home(
    make_settings: Callable[..., Settings],
    pool: Path,
    commands: list[list[str]],
    record: Callable[[Sequence[str]], None],
) -> None:
    settings = make_settings(lab_env_quota_mb=250, lab_env_max_inodes=1234)

    projid = provision_home(settings, "abcdef", run=record)

    home = pool / "users" / "abcdef"
    assert projid == 1001
    assert home.is_dir()
    assert (home / ".tmp").is_dir()
    assert (pool / ".projects").read_text(encoding="utf-8") == "1001:abcdef\n"
    assert (pool / ".projid-counter").read_text(encoding="utf-8").strip() == "1001"
    assert commands == [
        ["xfs_quota", "-x", "-c", f"project -s -p {home} 1001", str(pool)],
        ["xfs_quota", "-x", "-c", "limit -p bhard=250m ihard=1234 1001", str(pool)],
    ]


def test_project_ids_are_allocated_from_the_counter(
    make_settings: Callable[..., Settings],
    pool: Path,
    record: Callable[[Sequence[str]], None],
) -> None:
    settings = make_settings()

    first = provision_home(settings, "aaaa", run=record)
    second = provision_home(settings, "bbbb", run=record)

    assert (first, second) == (1001, 1002)
    assert (pool / ".projects").read_text(encoding="utf-8") == "1001:aaaa\n1002:bbbb\n"


def test_reprovisioning_reuses_the_project_id_and_keeps_the_contents(
    make_settings: Callable[..., Settings],
    pool: Path,
    commands: list[list[str]],
    record: Callable[[Sequence[str]], None],
) -> None:
    settings = make_settings()
    provision_home(settings, "abcdef", run=record)
    work = pool / "users" / "abcdef" / "notes.txt"
    work.write_text("mine", encoding="utf-8")
    commands.clear()

    # When: the same environment is provisioned again, as every spawn does.
    projid = provision_home(settings, "abcdef", run=record)

    assert projid == 1001
    assert work.read_text(encoding="utf-8") == "mine"
    assert (pool / ".projects").read_text(encoding="utf-8") == "1001:abcdef\n"
    assert len(commands) == 2


def test_skel_is_copied_into_a_new_home_only(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
    pool: Path,
    tmp_path: Path,
    record: Callable[[Sequence[str]], None],
) -> None:
    skel = tmp_path / "populated-skel"
    skel.mkdir()
    (skel / ".bashrc").write_text("export PS1='$ '\n", encoding="utf-8")
    monkeypatch.setattr(quota, "SKEL_DIR", skel)
    settings = make_settings()

    provision_home(settings, "abcdef", run=record)
    bashrc = pool / "users" / "abcdef" / ".bashrc"
    bashrc.write_text("mine\n", encoding="utf-8")

    # When: the environment is provisioned again, skel must not overwrite it.
    provision_home(settings, "abcdef", run=record)

    assert bashrc.read_text(encoding="utf-8") == "mine\n"


def test_a_home_is_owned_by_the_lab_user(
    make_settings: Callable[..., Settings],
    owners: list[tuple[str, int, int]],
    record: Callable[[Sequence[str]], None],
) -> None:
    provision_home(make_settings(lab_uid=1000, lab_gid=1000), "abcdef", run=record)

    assert owners == [("abcdef", 1000, 1000)]


def test_removal_deletes_the_home_and_zeroes_the_quota(
    make_settings: Callable[..., Settings],
    pool: Path,
    commands: list[list[str]],
    record: Callable[[Sequence[str]], None],
) -> None:
    settings = make_settings()
    provision_home(settings, "abcdef", run=record)
    commands.clear()

    remove_home(settings, "abcdef", run=record)

    assert not (pool / "users" / "abcdef").exists()
    assert commands == [
        ["xfs_quota", "-x", "-c", "limit -p bhard=0 ihard=0 1001", str(pool)]
    ]


def test_removal_is_idempotent(
    make_settings: Callable[..., Settings],
    pool: Path,
    commands: list[list[str]],
    record: Callable[[Sequence[str]], None],
) -> None:
    settings = make_settings()
    provision_home(settings, "abcdef", run=record)
    remove_home(settings, "abcdef", run=record)
    commands.clear()

    remove_home(settings, "abcdef", run=record)

    # The project id survives in .projects, so the limit is simply re-zeroed.
    assert commands == [
        ["xfs_quota", "-x", "-c", "limit -p bhard=0 ihard=0 1001", str(pool)]
    ]
    assert not (pool / "users" / "abcdef").exists()


def test_removing_an_environment_that_never_existed_is_a_no_op(
    make_settings: Callable[..., Settings],
    commands: list[list[str]],
    record: Callable[[Sequence[str]], None],
) -> None:
    remove_home(make_settings(), "neverseen", run=record)

    assert commands == []


@pytest.mark.parametrize("username", ["", "../escape", "with space", "a/b"])
def test_a_username_that_could_escape_the_pool_is_refused(
    make_settings: Callable[..., Settings],
    record: Callable[[Sequence[str]], None],
    username: str,
) -> None:
    with pytest.raises(QuotaError, match="invalid username"):
        provision_home(make_settings(), username, run=record)
    with pytest.raises(QuotaError, match="invalid username"):
        remove_home(make_settings(), username, run=record)


def test_every_mutation_holds_the_pool_lock(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
    record: Callable[[Sequence[str]], None],
) -> None:
    # Given: projid allocation is a read-increment-write, so a concurrent spawn
    # must be serialized by the flock rather than by luck.
    held: list[str] = []
    real_lock = quota.with_pool_lock

    def spy(pool: Any, action: Any) -> Any:
        def _wrapped() -> Any:
            held.append("locked")
            return action()

        return real_lock(pool, _wrapped)

    monkeypatch.setattr(quota, "with_pool_lock", spy)
    settings = make_settings()

    provision_home(settings, "abcdef", run=record)
    remove_home(settings, "abcdef", run=record)

    assert held == ["locked", "locked"]


def test_the_pool_lock_is_exclusive(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def hold() -> None:
        def _action() -> None:
            entered.set()
            release.wait(timeout=5)
            order.append("first")

        quota.with_pool_lock(tmp_path, _action)

    def follow() -> None:
        entered.wait(timeout=5)
        quota.with_pool_lock(tmp_path, lambda: order.append("second"))

    holder = threading.Thread(target=hold)
    follower = threading.Thread(target=follow)
    holder.start()
    follower.start()
    entered.wait(timeout=5)

    # Then: the second mutation is still blocked on the flock.
    assert order == []

    release.set()
    holder.join(timeout=5)
    follower.join(timeout=5)

    assert order == ["first", "second"]
    assert (tmp_path / ".lock").exists()


# --- the command runner ------------------------------------------------------


def test_the_runner_reports_a_failing_command(tmp_path: Path) -> None:
    script = tmp_path / "fail"
    script.write_text("#!/bin/sh\necho 'no such project' >&2\nexit 3\n")
    script.chmod(0o755)

    with pytest.raises(QuotaError, match="exited 3: no such project"):
        run_command([str(script)])


def test_the_runner_reports_a_missing_command(tmp_path: Path) -> None:
    with pytest.raises(QuotaError, match="could not be run"):
        run_command([str(tmp_path / "absent")])


def test_the_runner_captures_output_and_never_uses_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        calls.append({"args": args, **kwargs})
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_command(["xfs_quota", "-x"])

    assert calls == [
        {
            "args": ["xfs_quota", "-x"],
            "check": True,
            "capture_output": True,
            "text": True,
        }
    ]


# --- admission ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("home_exists", "free_pct", "admitted"),
    [
        (False, 10.0, True),
        (False, 2.0, True),
        (False, 1.9, False),
        (False, 0.0, False),
        (True, 0.0, True),
        (True, 1.9, True),
    ],
)
def test_the_free_space_floor_applies_to_creations_only(
    home_exists: bool, free_pct: float, admitted: bool
) -> None:
    if admitted:
        assert_admission(home_exists=home_exists, free_pct=free_pct, reserve_pct=2.0)
        return
    with pytest.raises(QuotaRefusalError, match="free-space floor"):
        assert_admission(home_exists=home_exists, free_pct=free_pct, reserve_pct=2.0)


def test_ensure_home_asserts_the_pool_admits_and_provisions(
    make_settings: Callable[..., Settings],
    mounted_pool: Path,
    record: Callable[[Sequence[str]], None],
) -> None:
    settings = make_settings(pool_mount=mounted_pool)

    projid = ensure_home(settings, "abcdef", free_pct=lambda _: 50.0, run=record)

    assert projid == 1001
    assert (mounted_pool / "users" / "abcdef").is_dir()


def test_ensure_home_refuses_a_creation_below_the_floor(
    make_settings: Callable[..., Settings],
    mounted_pool: Path,
    commands: list[list[str]],
    record: Callable[[Sequence[str]], None],
) -> None:
    settings = make_settings(pool_mount=mounted_pool, lab_pool_reserve_pct=5)

    with pytest.raises(QuotaRefusalError):
        ensure_home(settings, "abcdef", free_pct=lambda _: 1.0, run=record)

    # And: nothing was written to the pool.
    assert commands == []
    assert not (mounted_pool / "users" / "abcdef").exists()


def test_ensure_home_still_starts_an_existing_environment_when_full(
    make_settings: Callable[..., Settings],
    mounted_pool: Path,
    record: Callable[[Sequence[str]], None],
) -> None:
    settings = make_settings(pool_mount=mounted_pool, lab_pool_reserve_pct=90)
    (mounted_pool / "users" / "abcdef").mkdir()

    projid = ensure_home(settings, "abcdef", free_pct=lambda _: 0.5, run=record)

    assert projid == 1001


def test_ensure_home_refuses_a_pool_without_project_quotas(
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    mountinfo: Callable[[str, str], Path],
    pool: Path,
    record: Callable[[Sequence[str]], None],
) -> None:
    monkeypatch.setattr(
        readiness, "MOUNTINFO", mountinfo(str(pool), "ext4 /dev/sda1 rw,noatime")
    )

    with pytest.raises(PoolAssertionError, match="pool-not-xfs"):
        ensure_home(make_settings(), "abcdef", run=record)

    assert not (pool / "users" / "abcdef").exists()


def test_ensure_home_refuses_a_pool_bound_to_another_hub(
    make_settings: Callable[..., Settings],
    mounted_pool: Path,
    tmp_path: Path,
    record: Callable[[Sequence[str]], None],
) -> None:
    # Given: this hub was bound to a different pool at first start.
    hub_dir = tmp_path / "hub"
    hub_dir.mkdir()
    (hub_dir / "pool-id").write_text("another-pool\n", encoding="utf-8")
    settings = make_settings(pool_mount=mounted_pool, hub_data_dir=hub_dir)

    with pytest.raises(PoolAssertionError, match="pool-id-mismatch"):
        ensure_home(settings, "abcdef", run=record)


# --- the pre-spawn hook ------------------------------------------------------


class FakeSpawnerUser:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeSpawner:
    def __init__(self, name: str) -> None:
        self.user = FakeSpawnerUser(name)


def _record_subprocess(
    monkeypatch: pytest.MonkeyPatch, sink: list[tuple[list[str], threading.Thread]]
) -> None:
    def fake_run(args: list[str], **_kwargs: Any) -> Any:
        sink.append((list(args), threading.current_thread()))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_the_pre_spawn_hook_provisions_the_home_directory(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
    mounted_pool: Path,
) -> None:
    # Given: the hook reaches xfs_quota through the module's own default runner.
    calls: list[tuple[list[str], threading.Thread]] = []
    _record_subprocess(monkeypatch, calls)
    monkeypatch.setattr(
        quota, "get_settings", lambda: make_settings(pool_mount=mounted_pool)
    )

    asyncio.run(pre_spawn_hook(cast("Any", FakeSpawner("abcdef"))))

    assert (mounted_pool / "users" / "abcdef").is_dir()
    assert [args[3].split()[0] for args, _ in calls] == ["project", "limit"]


def test_the_pre_spawn_hook_provisions_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
    mounted_pool: Path,
) -> None:
    # Given: xfs_quota is a subprocess and the flock can wait on a concurrent
    # spawn, neither of which may run on JupyterHub's IO loop.
    loop_thread = threading.current_thread()
    calls: list[tuple[list[str], threading.Thread]] = []
    _record_subprocess(monkeypatch, calls)
    monkeypatch.setattr(
        quota, "get_settings", lambda: make_settings(pool_mount=mounted_pool)
    )

    asyncio.run(pre_spawn_hook(cast("Any", FakeSpawner("abcdef"))))

    assert calls
    assert all(thread is not loop_thread for _, thread in calls)


def test_chown_tree_walks_the_whole_home_without_following_symlinks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    (home / "sub").mkdir(parents=True)
    (home / "sub" / "file").write_text("x", encoding="utf-8")
    (home / "link").symlink_to(home / "sub" / "file")
    seen: list[tuple[str, bool]] = []

    def fake_chown(
        path: Any, uid: int, gid: int, *, follow_symlinks: bool = True
    ) -> None:
        assert (uid, gid) == (1000, 1000)
        seen.append((os.fspath(path), follow_symlinks))

    monkeypatch.setattr(os, "chown", fake_chown)

    chown_tree(home, 1000, 1000)

    assert {name for name, _ in seen} == {
        str(home),
        str(home / "sub"),
        str(home / "sub" / "file"),
        str(home / "link"),
    }
    assert all(not follow for name, follow in seen if name.endswith("link"))
