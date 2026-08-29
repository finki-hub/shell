"""The pool: home directories, XFS project quotas and spawn admission.

The hub is the only writer to the pool. Its bind mount of ``/srv/pool`` is
read-write and the container runs as root, so creating a home directory, setting
its XFS project quota and removing it again all happen in-process -- there is no
helper container. Every mutation holds an exclusive ``flock`` on ``<pool>/.lock``
for its whole duration, because project-id allocation (read-increment-write on
the counter file) is not otherwise atomic against a concurrent spawn.

The blocking work lives in plain functions; :func:`pre_spawn_hook` is the only
coroutine and pushes them onto a worker thread.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from shell_hub.readiness import assert_pool, free_space_pct
from shell_hub.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from shell_hub.settings import Settings

#: Files ``scripts/pool-init.sh`` creates and this module maintains.
LOCK_NAME: Final[str] = ".lock"
PROJECTS_NAME: Final[str] = ".projects"
COUNTER_NAME: Final[str] = ".projid-counter"

#: The counter starts here; the first environment therefore gets 1001.
FIRST_PROJID: Final[int] = 1000

#: Copied into a home directory once, when it is created.
SKEL_DIR: Final[Path] = Path("/opt/skel")

#: Usernames are sha256 hex digests, but the pool paths are built from them.
USERNAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]+$")

#: How the command runner is injected; tests pass a fake instead of xfs_quota.
type CommandRunner = Callable[[Sequence[str]], None]

logger = logging.getLogger(__name__)


class QuotaError(RuntimeError):
    """A pool mutation did not finish successfully."""


class QuotaRefusalError(Exception):
    """Raised to abort a spawn; JupyterHub surfaces the message to the SPA."""


class UserLike(Protocol):
    """The slice of ``jupyterhub.user.User`` this module reads."""

    @property
    def name(self) -> str: ...


class SpawnerLike(Protocol):
    """The slice of ``dockerspawner.DockerSpawner`` the hook reads."""

    @property
    def user(self) -> UserLike: ...


def run_command(args: Sequence[str]) -> None:
    """Run one command, raising :class:`QuotaError` on any failure."""
    try:
        subprocess.run(list(args), check=True, capture_output=True, text=True)  # ruff: ignore[subprocess-without-shell-equals-true] - a fixed argument list, never a shell string
    except OSError as exc:
        raise QuotaError(f"{args[0]} could not be run: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise QuotaError(f"{args[0]} exited {exc.returncode}: {detail}") from exc


def with_pool_lock[T](pool: Path, action: Callable[[], T]) -> T:
    """Run ``action`` holding the exclusive pool lock. Blocking."""
    fd = os.open(pool / LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return action()
    finally:
        os.close(fd)


def check_username(username: str) -> None:
    """Refuse anything that could escape ``<pool>/users`` or corrupt ``.projects``."""
    if not USERNAME_PATTERN.fullmatch(username):
        raise QuotaError(f"invalid username: {username!r}")


def read_projid(pool: Path, username: str) -> int | None:
    """The project id already allocated to ``username``, if any."""
    try:
        lines = (pool / PROJECTS_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        projid, _, name = line.partition(":")
        if name == username and projid.isdigit():
            return int(projid)
    return None


def allocate_projid(pool: Path, username: str) -> int:
    """Reuse or allocate the project id of ``username``. Call under the lock."""
    existing = read_projid(pool, username)
    if existing is not None:
        return existing
    counter = pool / COUNTER_NAME
    try:
        last = int(counter.read_text(encoding="utf-8").strip())
    except OSError, ValueError:
        last = FIRST_PROJID
    projid = last + 1
    counter.write_text(f"{projid}\n", encoding="utf-8")
    with (pool / PROJECTS_NAME).open("a", encoding="utf-8") as projects:
        projects.write(f"{projid}:{username}\n")
    return projid


def chown_tree(root: Path, uid: int, gid: int) -> None:
    """``chown -R uid:gid``, never following a symlink out of the home."""
    os.chown(root, uid, gid)
    for path in root.rglob("*"):
        os.chown(path, uid, gid, follow_symlinks=False)


def create_home(home: Path, uid: int, gid: int) -> None:
    """Make one home directory, seeded from ``/opt/skel`` (the lab image's skel). Call under the lock.

    Seeding happens at creation only: re-provisioning an existing environment to
    change its quota limits must never touch its contents again.
    """
    home.mkdir(parents=True)
    if SKEL_DIR.is_dir():
        shutil.copytree(SKEL_DIR, home, dirs_exist_ok=True, symlinks=True)
    (home / ".tmp").mkdir(exist_ok=True)
    chown_tree(home, uid, gid)


def apply_quota(
    pool: Path, home: Path, projid: int, settings: Settings, run: CommandRunner
) -> None:
    """Bind ``home`` to ``projid`` and set that project's block and inode limits."""
    run(["xfs_quota", "-x", "-c", f"project -s -p {home} {projid}", str(pool)])
    run(
        [
            "xfs_quota",
            "-x",
            "-c",
            (
                f"limit -p bhard={settings.lab_env_quota_mb}m "
                f"ihard={settings.lab_env_max_inodes} {projid}"
            ),
            str(pool),
        ]
    )


def provision_home(
    settings: Settings, username: str, *, run: CommandRunner = run_command
) -> int:
    """Create (idempotently) the home directory of ``username`` and quota it.

    Blocking: call it from a worker thread, never from the hub's IO loop.
    Returns the XFS project id in force for the environment.
    """
    check_username(username)
    pool = settings.pool_mount
    home = settings.pool_users_dir / username

    def _provision() -> int:
        projid = allocate_projid(pool, username)
        if not home.is_dir():
            create_home(home, settings.lab_uid, settings.lab_gid)
        apply_quota(pool, home, projid, settings, run)
        return projid

    try:
        projid = with_pool_lock(pool, _provision)
    except OSError as exc:
        raise QuotaError(f"could not provision {home}: {exc}") from exc
    logger.info("provisioned home for %s with projid %s", username, projid)
    return projid


def remove_home(
    settings: Settings, username: str, *, run: CommandRunner = run_command
) -> None:
    """Delete the home directory of ``username`` and release its quota.

    Idempotent and blocking: an environment that was never provisioned, or one
    already removed, both leave the pool exactly as they found it.
    """
    check_username(username)
    pool = settings.pool_mount
    home = settings.pool_users_dir / username

    def _remove() -> None:
        shutil.rmtree(home, ignore_errors=True)
        projid = read_projid(pool, username)
        if projid is not None:
            run(
                [
                    "xfs_quota",
                    "-x",
                    "-c",
                    f"limit -p bhard=0 ihard=0 {projid}",
                    str(pool),
                ]
            )

    try:
        with_pool_lock(pool, _remove)
    except OSError as exc:
        raise QuotaError(f"could not remove {home}: {exc}") from exc
    logger.info("removed home for %s", username)


def assert_admission(*, home_exists: bool, free_pct: float, reserve_pct: float) -> None:
    """Apply the free-space floor. Creations only -- resumes are never refused."""
    if home_exists:
        return
    if free_pct < reserve_pct:
        raise QuotaRefusalError(
            f"pool below the {reserve_pct}% free-space floor ({free_pct:.1f}% free): "
            "no new environments can be created right now"
        )


def ensure_home(
    settings: Settings,
    username: str,
    *,
    free_pct: Callable[[Path], float] = free_space_pct,
    run: CommandRunner = run_command,
) -> int:
    """Assert the pool, admit the spawn and provision the home directory."""
    assert_pool(settings.pool_mount, expected_pool_id=settings.pool_id)
    home = settings.pool_users_dir / username
    assert_admission(
        home_exists=home.is_dir(),
        free_pct=free_pct(settings.pool_mount),
        reserve_pct=settings.lab_pool_reserve_pct,
    )
    return provision_home(settings, username, run=run)


async def pre_spawn_hook(spawner: SpawnerLike) -> None:
    """``c.Spawner.pre_spawn_hook``: make the environment ready to be mounted.

    The provisioning runs in a worker thread: ``xfs_quota`` is a subprocess and
    the ``flock`` can wait on a concurrent spawn, neither of which may block
    JupyterHub's IO loop.
    """
    settings = get_settings()
    username = spawner.user.name
    projid = await asyncio.to_thread(ensure_home, settings, username)
    logger.info("pre-spawn ready for %s (projid %s)", username, projid)
