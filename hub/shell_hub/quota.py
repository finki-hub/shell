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

LOCK_NAME: Final[str] = ".lock"
PROJECTS_NAME: Final[str] = ".projects"
COUNTER_NAME: Final[str] = ".projid-counter"

FIRST_PROJID: Final[int] = 1000

SKEL_DIR: Final[Path] = Path("/opt/skel")

USERNAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]+$")

type CommandRunner = Callable[[Sequence[str]], None]

logger = logging.getLogger(__name__)


class QuotaError(RuntimeError):
    pass


class QuotaRefusalError(Exception):
    pass


class UserLike(Protocol):
    @property
    def name(self) -> str: ...


class SpawnerLike(Protocol):
    @property
    def user(self) -> UserLike: ...


def run_command(args: Sequence[str]) -> None:
    try:
        subprocess.run(list(args), check=True, capture_output=True, text=True)  # ruff: ignore[subprocess-without-shell-equals-true] - a fixed argument list, never a shell string
    except OSError as exc:
        raise QuotaError(f"{args[0]} could not be run: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise QuotaError(f"{args[0]} exited {exc.returncode}: {detail}") from exc


def with_pool_lock[T](pool: Path, action: Callable[[], T]) -> T:
    """Run an action under the pool's exclusive lock."""
    fd = os.open(pool / LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return action()
    finally:
        os.close(fd)


def check_username(username: str) -> None:
    """Reject names that could escape the pool path or corrupt the project map."""
    if not USERNAME_PATTERN.fullmatch(username):
        raise QuotaError(f"invalid username: {username!r}")


def read_projid(pool: Path, username: str) -> int | None:
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
    """Reuse or allocate a project id; callers must hold the pool lock."""
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
    """Chown recursively without following symlinks out of the home."""
    os.chown(root, uid, gid)
    for path in root.rglob("*"):
        os.chown(path, uid, gid, follow_symlinks=False)


def create_home(home: Path, uid: int, gid: int) -> None:
    """Create and seed a home directory; callers must hold the pool lock."""
    home.mkdir(parents=True)
    if SKEL_DIR.is_dir():
        shutil.copytree(SKEL_DIR, home, dirs_exist_ok=True, symlinks=True)
    (home / ".tmp").mkdir(exist_ok=True)
    chown_tree(home, uid, gid)


def apply_quota(
    pool: Path, home: Path, projid: int, settings: Settings, run: CommandRunner
) -> None:
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
    """Create or update a home and apply its XFS quota (blocking)."""
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
    """Remove a home and release its XFS quota (blocking and idempotent)."""
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
    """Apply the free-space floor only to new homes."""
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
    assert_pool(settings.pool_mount, expected_pool_id=settings.pool_id)
    home = settings.pool_users_dir / username
    assert_admission(
        home_exists=home.is_dir(),
        free_pct=free_pct(settings.pool_mount),
        reserve_pct=settings.lab_pool_reserve_pct,
    )
    return provision_home(settings, username, run=run)


async def pre_spawn_hook(spawner: SpawnerLike) -> None:
    """Provision the home off the Hub's IO loop before spawning."""
    settings = get_settings()
    username = spawner.user.name
    projid = await asyncio.to_thread(ensure_home, settings, username)
    logger.info("pre-spawn ready for %s (projid %s)", username, projid)
