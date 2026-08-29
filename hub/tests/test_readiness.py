from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

import pytest
from docker.errors import DockerException

from shell_hub.readiness import (
    ReadinessReport,
    docker_reasons,
    free_space_pct,
    has_free_space,
    pool_reasons,
    read_mount_facts,
    readiness_report,
)
from tests.fakes import FakeDocker

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

XFS_PRJQUOTA = "xfs /dev/sda1 rw,prjquota,noatime"


def test_mount_facts_merge_both_option_sets(
    mountinfo: Callable[[str, str], Path],
) -> None:
    facts = read_mount_facts(
        cast("Any", "/srv/pool"), mountinfo("/srv/pool", XFS_PRJQUOTA)
    )

    assert facts is not None
    assert facts.fstype == "xfs"
    assert {"rw", "relatime", "prjquota", "noatime"} <= facts.options


def test_an_unmounted_path_has_no_facts(mountinfo: Callable[[str, str], Path]) -> None:
    assert (
        read_mount_facts(cast("Any", "/srv/pool"), mountinfo("/other", XFS_PRJQUOTA))
        is None
    )


def test_a_missing_mountinfo_has_no_facts(tmp_path: Path) -> None:
    assert read_mount_facts(tmp_path, tmp_path / "absent") is None


def test_a_correct_pool_has_no_reasons(
    pool: Path, mountinfo: Callable[[str, str], Path]
) -> None:
    reasons = pool_reasons(
        pool,
        expected_pool_id="pool-under-test",
        mountinfo=mountinfo(str(pool), XFS_PRJQUOTA),
    )

    assert reasons == []


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        ("ext4 /dev/sda1 rw,noatime", "pool-not-xfs: ext4"),
        ("xfs /dev/sda1 rw,noatime", "pool-without-prjquota"),
    ],
)
def test_a_wrong_filesystem_is_reported(
    pool: Path, mountinfo: Callable[[str, str], Path], tail: str, expected: str
) -> None:
    assert expected in pool_reasons(pool, mountinfo=mountinfo(str(pool), tail))


def test_an_unmounted_pool_is_reported(
    pool: Path, mountinfo: Callable[[str, str], Path]
) -> None:
    reasons = pool_reasons(pool, mountinfo=mountinfo("/elsewhere", XFS_PRJQUOTA))

    assert reasons == [f"pool-not-mounted: {pool}"]


def test_a_missing_sentinel_is_reported(
    pool: Path, mountinfo: Callable[[str, str], Path]
) -> None:
    (pool / ".pool-id").unlink()

    reasons = pool_reasons(pool, mountinfo=mountinfo(str(pool), XFS_PRJQUOTA))

    assert reasons == [f"pool-id-missing: {pool / '.pool-id'}"]


def test_an_empty_sentinel_is_reported(
    pool: Path, mountinfo: Callable[[str, str], Path]
) -> None:
    (pool / ".pool-id").write_text("\n", encoding="utf-8")

    assert pool_reasons(pool, mountinfo=mountinfo(str(pool), XFS_PRJQUOTA)) == [
        f"pool-id-empty: {pool / '.pool-id'}"
    ]


def test_a_foreign_pool_is_reported(
    pool: Path, mountinfo: Callable[[str, str], Path]
) -> None:
    reasons = pool_reasons(
        pool, expected_pool_id="ours", mountinfo=mountinfo(str(pool), XFS_PRJQUOTA)
    )

    assert reasons == ["pool-id-mismatch"]


def test_a_silent_docker_daemon_is_reported() -> None:
    docker = FakeDocker(ping_error=OSError("connection refused"))

    assert docker_reasons(lambda: cast("Any", docker)) == [
        "docker-unavailable: OSError"
    ]


def test_a_live_docker_daemon_has_no_reasons() -> None:
    assert docker_reasons(lambda: cast("Any", FakeDocker())) == []


def test_a_client_that_cannot_be_built_is_a_reason_not_a_crash() -> None:
    # Given: docker.from_env() talks to the daemon in its constructor, so with
    # the daemon down the *factory* raises before any ping can be attempted.
    def factory() -> Any:
        raise DockerException("Error while fetching server API version")

    assert docker_reasons(factory) == ["docker-unavailable: DockerException"]


def test_a_report_stays_a_503_body_when_the_client_cannot_be_built(
    pool: Path, mountinfo: Callable[[str, str], Path]
) -> None:
    def factory() -> Any:
        raise DockerException("no such file or directory: /var/run/docker.sock")

    report = readiness_report(
        factory,
        pool,
        expected_pool_id="pool-under-test",
        mountinfo=mountinfo(str(pool), XFS_PRJQUOTA),
    )

    assert report.ready is False
    assert report.to_json() == {
        "ready": False,
        "reasons": ["docker-unavailable: DockerException"],
    }


def test_the_ready_body_is_bare_when_ready(
    pool: Path, mountinfo: Callable[[str, str], Path]
) -> None:
    report = readiness_report(
        lambda: cast("Any", FakeDocker()),
        pool,
        expected_pool_id="pool-under-test",
        mountinfo=mountinfo(str(pool), XFS_PRJQUOTA),
    )

    assert report.ready is True
    assert report.to_json() == {"ready": True}


def test_the_ready_body_lists_every_reason(
    pool: Path, mountinfo: Callable[[str, str], Path]
) -> None:
    (pool / ".pool-id").unlink()

    report = readiness_report(
        lambda: cast("Any", FakeDocker(ping_error=OSError("boom"))),
        pool,
        mountinfo=mountinfo(str(pool), "ext4 /dev/sda1 rw"),
    )

    assert report.ready is False
    body = report.to_json()
    assert body["ready"] is False
    assert body["reasons"] == [
        "docker-unavailable: OSError",
        "pool-not-xfs: ext4",
        "pool-without-prjquota",
        f"pool-id-missing: {pool / '.pool-id'}",
    ]


def test_an_empty_report_is_ready() -> None:
    assert ReadinessReport(ready=True).to_json() == {"ready": True}


def test_free_space_is_a_percentage(tmp_path: Path) -> None:
    stats = os.statvfs(tmp_path)
    expected = 100.0 * stats.f_bavail / stats.f_blocks

    assert free_space_pct(tmp_path) == pytest.approx(expected)
    assert has_free_space(tmp_path, 0.0) is True


def test_an_unreadable_pool_has_no_free_space(tmp_path: Path) -> None:
    assert has_free_space(tmp_path / "absent", 1.0) is False
