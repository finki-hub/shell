"""Docker and XFS-pool readiness checks with reason reporting."""

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

PRJQUOTA_OPTIONS: Final[frozenset[str]] = frozenset({"prjquota", "pquota"})

MOUNTINFO: Final[Path] = Path("/proc/self/mountinfo")


@lru_cache(maxsize=1)
def docker_client() -> DockerClient:
    """Cached client factory; construction performs a blocking daemon request."""
    return docker.from_env()


class DockerPing(Protocol):
    def ping(self) -> Any: ...  # ruff: ignore[any-type] - docker-py returns a bare truthy value


@dataclass(frozen=True, slots=True)
class MountFacts:
    fstype: str
    options: frozenset[str]


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, object]:
        if self.ready:
            return {"ready": True}
        return {"ready": False, "reasons": list(self.reasons)}


def read_mount_facts(target: Path, mountinfo: Path | None = None) -> MountFacts | None:
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
        # The last matching mount is the visible one.
        found = MountFacts(fstype=tail_fields[0], options=frozenset(options))
    return found


def pool_reasons(
    pool_mount: Path,
    *,
    expected_pool_id: str | None = None,
    mountinfo: Path | None = None,
) -> list[str]:
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
    """Return daemon failures as readiness reasons instead of raising."""
    try:
        client_factory().ping()
    except Exception as exc:  # ruff: ignore[blind-except] - any daemon failure is one reason
        return [f"docker-unavailable: {type(exc).__name__}"]
    return []


def free_space_pct(pool_mount: Path) -> float:
    stats = os.statvfs(pool_mount)
    if stats.f_blocks == 0:
        return 0.0
    return 100.0 * stats.f_bavail / stats.f_blocks


def has_free_space(pool_mount: Path, reserve_pct: float) -> bool:
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
    reasons = docker_reasons(client_factory) + pool_reasons(
        pool_mount, expected_pool_id=expected_pool_id, mountinfo=mountinfo
    )
    return ReadinessReport(ready=not reasons, reasons=tuple(reasons))


class PoolAssertionError(RuntimeError):
    pass


def assert_pool(
    pool_mount: Path,
    *,
    expected_pool_id: str | None = None,
    mountinfo: Path | None = None,
) -> None:
    reasons = pool_reasons(
        pool_mount, expected_pool_id=expected_pool_id, mountinfo=mountinfo
    )
    if reasons:
        raise PoolAssertionError("; ".join(reasons))
