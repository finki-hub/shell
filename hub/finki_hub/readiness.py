"""Readiness checks shared by ``GET /hub/lab/ready`` and the Compose healthcheck.

Two dependencies decide whether the hub can serve anything useful: the Docker
daemon must answer, and the pool must be the XFS filesystem with project quotas
that the quota machinery assumes. Both are expressed as reason lists so the
endpoint can report *why* it is not ready.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol

import docker

if TYPE_CHECKING:
    from collections.abc import Callable

    from docker import DockerClient

#: Mount options that prove XFS project quota accounting is on.
PRJQUOTA_OPTIONS: Final[frozenset[str]] = frozenset({"prjquota", "pquota"})

MOUNTINFO: Final[Path] = Path("/proc/self/mountinfo")


@lru_cache(maxsize=1)
def docker_client() -> DockerClient:
    """Process-wide Docker client bound to the mounted host socket.

    Building one performs a blocking ``GET /version`` against the daemon, so it
    is created inside a worker thread and passed here as a *factory*.
    """
    return docker.from_env()


class DockerPing(Protocol):
    """The slice of ``docker.DockerClient`` readiness uses."""

    def ping(self) -> Any: ...  # ruff: ignore[any-type] - docker-py returns a bare truthy value


@dataclass(frozen=True, slots=True)
class MountFacts:
    """What ``/proc/self/mountinfo`` says about one mount point."""

    fstype: str
    options: frozenset[str]


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """The body of ``/hub/lab/ready``."""

    ready: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, object]:
        if self.ready:
            return {"ready": True}
        return {"ready": False, "reasons": list(self.reasons)}


def read_mount_facts(target: Path, mountinfo: Path | None = None) -> MountFacts | None:
    """Parse ``mountinfo`` for ``target``; ``None`` when it is not a mount point."""
    try:
        lines = (mountinfo or MOUNTINFO).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    wanted = str(target)
    found: MountFacts | None = None
    for line in lines:
        head, _, tail = line.partition(" - ")
        if not tail:
            continue
        head_fields = head.split()
        tail_fields = tail.split()
        if len(head_fields) < 6 or len(tail_fields) < 3:
            continue
        if head_fields[4] != wanted:
            continue
        options = set(head_fields[5].split(","))
        options.update(tail_fields[2].split(","))
        # Later lines win: the last mount at a path is the visible one.
        found = MountFacts(fstype=tail_fields[0], options=frozenset(options))
    return found


def pool_reasons(
    pool_mount: Path,
    *,
    expected_pool_id: str | None = None,
    mountinfo: Path | None = None,
) -> list[str]:
    """Everything wrong with the pool, as human-readable reasons."""
    reasons: list[str] = []
    facts = read_mount_facts(pool_mount, mountinfo)
    if facts is None:
        reasons.append(f"pool-not-mounted: {pool_mount}")
    else:
        if facts.fstype != "xfs":
            reasons.append(f"pool-not-xfs: {facts.fstype}")
        if not (facts.options & PRJQUOTA_OPTIONS):
            reasons.append("pool-without-prjquota")

    sentinel = pool_mount / ".pool-id"
    try:
        pool_id = sentinel.read_text(encoding="utf-8").strip()
    except OSError:
        reasons.append(f"pool-id-missing: {sentinel}")
        return reasons
    if not pool_id:
        reasons.append(f"pool-id-empty: {sentinel}")
    elif expected_pool_id is not None and pool_id != expected_pool_id:
        reasons.append("pool-id-mismatch")
    return reasons


def docker_reasons(client_factory: Callable[[], DockerPing]) -> list[str]:
    """Empty when a Docker client can be built and the daemon answers a ping.

    The client is built *inside* the ``try``: ``docker.from_env()`` performs a
    ``GET /version`` round trip in its constructor and raises when the daemon is
    down -- exactly the condition this endpoint exists to report, so it must
    become a reason rather than a 500.
    """
    try:
        client_factory().ping()
    except Exception as exc:  # ruff: ignore[blind-except] - any daemon failure is one reason
        return [f"docker-unavailable: {type(exc).__name__}"]
    return []


def free_space_pct(pool_mount: Path) -> float:
    """Percentage of the pool still available to unprivileged writers."""
    stats = os.statvfs(pool_mount)
    if stats.f_blocks == 0:
        return 0.0
    return 100.0 * stats.f_bavail / stats.f_blocks


def has_free_space(pool_mount: Path, reserve_pct: float) -> bool:
    """True when the pool is above the creation floor."""
    try:
        return free_space_pct(pool_mount) >= reserve_pct
    except OSError:
        return False


def readiness_report(
    client_factory: Callable[[], DockerPing],
    pool_mount: Path,
    *,
    expected_pool_id: str | None = None,
    mountinfo: Path | None = None,
) -> ReadinessReport:
    """Combine the Docker ping and the pool assertion into one report."""
    reasons = docker_reasons(client_factory) + pool_reasons(
        pool_mount, expected_pool_id=expected_pool_id, mountinfo=mountinfo
    )
    return ReadinessReport(ready=not reasons, reasons=tuple(reasons))


class PoolAssertionError(RuntimeError):
    """Raised when the pool is not usable and the caller must fail closed."""


def assert_pool(
    pool_mount: Path,
    *,
    expected_pool_id: str | None = None,
    mountinfo: Path | None = None,
) -> None:
    """Raise unless the pool is XFS with project quotas and carries its sentinel."""
    reasons = pool_reasons(
        pool_mount, expected_pool_id=expected_pool_id, mountinfo=mountinfo
    )
    if reasons:
        raise PoolAssertionError("; ".join(reasons))
