from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from finki_hub.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def test_defaults_match_the_contract(make_settings: Callable[..., Settings]) -> None:
    # Given/When: nothing but the required token is supplied.
    settings = make_settings()

    # Then: every documented default is in force.
    assert settings.log_level == "INFO"
    assert settings.tz == "Europe/Skopje"
    assert settings.lab_pool_reserve_pct == 2
    assert settings.lab_env_quota_mb == 100
    assert settings.lab_env_max_inodes == 20_000
    assert settings.lab_image == "ghcr.io/finki-hub/shell-lab:latest"
    assert (settings.lab_user, settings.lab_uid, settings.lab_gid) == (
        "ubuntu",
        1000,
        1000,
    )
    assert settings.lab_memory_mb == 384
    assert settings.lab_cpus == 0.5
    assert settings.lab_pids == 256
    assert settings.lab_max_sessions == 20
    assert settings.lab_max_terminals == 4
    assert settings.lab_idle_min == 10
    assert settings.lab_container_max_age_h == 24
    assert settings.lab_retention_h == 48
    assert settings.lab_max_age_h == 0
    assert settings.lab_max_creates_per_min == 60
    assert settings.lab_log_max_size == "512k"
    assert settings.lab_log_max_files == 2
    assert settings.turnstile_configured is False


def test_memory_must_clear_the_tmpfs_total_plus_headroom(
    make_settings: Callable[..., Settings],
) -> None:
    # Given: tmpfs mounts totalling 72 MiB, which needs > 168 MiB of memory.
    with pytest.raises(ValidationError, match="must exceed the tmpfs total"):
        make_settings(lab_memory_mb=168)

    # When: one more MiB is granted, the invariant holds.
    assert make_settings(lab_memory_mb=169).lab_memory_mb == 169


def test_lab_uid_zero_is_refused(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="LAB_UID must not be 0"):
        make_settings(lab_uid=0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"turnstile_sitekey": "0xLIVEKEY"},
        {"turnstile_secret": "0xLIVESECRET"},
    ],
)
def test_turnstile_keys_must_be_paired(
    make_settings: Callable[..., Settings], overrides: dict[str, str]
) -> None:
    with pytest.raises(ValidationError, match="must both be set or both be unset"):
        make_settings(**overrides)


def test_both_turnstile_keys_turn_enforcement_on(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(
        turnstile_sitekey="0xLIVEKEY", turnstile_secret="0xLIVESECRET"
    )

    assert settings.turnstile_configured is True


def test_blank_optional_values_read_as_unset(
    make_settings: Callable[..., Settings],
) -> None:
    # Given: an .env that declares the keys but leaves them empty.
    settings = make_settings(turnstile_sitekey="  ", turnstile_secret="")

    assert settings.turnstile_sitekey is None
    assert settings.turnstile_secret is None
    assert settings.turnstile_configured is False


def test_derived_paths(make_settings: Callable[..., Settings], pool: Path) -> None:
    settings = make_settings()

    assert settings.pool_users_dir == pool / "users"
    assert settings.pool_id_file == pool / ".pool-id"


def test_bind_pool_id_seeds_from_the_sentinel_and_then_sticks(
    make_settings: Callable[..., Settings], pool: Path
) -> None:
    # Given: a fresh hub whose data directory holds no pool id yet.
    settings = make_settings()
    assert settings.pool_id is None

    # When: the hub binds itself to the pool at first start.
    bound = settings.bind_pool_id()

    # Then: it adopts the pool's sentinel and remembers it.
    assert bound == "pool-under-test"
    assert settings.pool_id == "pool-under-test"

    # And: a later start against a re-created pool keeps the original binding.
    (pool / ".pool-id").write_text("a-different-pool\n", encoding="utf-8")
    assert settings.bind_pool_id() == "pool-under-test"


def test_bind_pool_id_invents_nothing_when_the_pool_is_not_mounted(
    make_settings: Callable[..., Settings], pool: Path, tmp_path: Path
) -> None:
    # Given: a hub started before the pool sentinel exists.
    (pool / ".pool-id").unlink()
    settings = make_settings()

    # When/Then: nothing is bound and nothing is written -- a random id would
    # make every later start fail with pool-id-mismatch, forever.
    assert settings.bind_pool_id() is None
    assert settings.pool_id is None
    assert not (tmp_path / "hub" / "pool-id").exists()

    # And: once the pool is there, the real sentinel is adopted.
    (pool / ".pool-id").write_text("pool-under-test\n", encoding="utf-8")
    assert settings.bind_pool_id() == "pool-under-test"


def test_bind_pool_id_treats_a_blank_sentinel_as_absent(
    make_settings: Callable[..., Settings], pool: Path, tmp_path: Path
) -> None:
    (pool / ".pool-id").write_text("  \n", encoding="utf-8")
    settings = make_settings()

    assert settings.bind_pool_id() is None
    assert not (tmp_path / "hub" / "pool-id").exists()


def test_a_container_age_ceiling_of_zero_is_accepted(
    make_settings: Callable[..., Settings],
) -> None:
    assert make_settings(lab_container_max_age_h=0).lab_container_max_age_h == 0


def test_settings_are_frozen(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    with pytest.raises(ValidationError):
        settings.lab_max_terminals = 99  # type: ignore[misc]
