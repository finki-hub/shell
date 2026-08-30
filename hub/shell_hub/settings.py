from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

POOL_MOUNT: Final[Path] = Path("/srv/pool")

HUB_DATA_DIR: Final[Path] = Path("/srv/hub")

MEMORY_HEADROOM_MB: Final[int] = 96

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _blank_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    configproxy_auth_token: SecretStr

    log_level: LogLevel = "INFO"
    tz: str = "Europe/Skopje"

    lab_pool_dir: Path = Path("/var/lib/finki-hub-shell/pool")
    pool_mount: Path = POOL_MOUNT
    hub_data_dir: Path = HUB_DATA_DIR

    lab_pool_reserve_pct: Annotated[int, Field(ge=0, le=100)] = 2
    lab_env_quota_mb: Annotated[int, Field(gt=0)] = 100
    lab_env_max_inodes: Annotated[int, Field(gt=0)] = 20_000

    lab_image: str = "ghcr.io/finki-hub/shell-lab:latest"

    lab_user: str = "ubuntu"
    lab_uid: Annotated[int, Field(ge=0)] = 1000
    lab_gid: Annotated[int, Field(ge=0)] = 1000

    lab_memory_mb: Annotated[int, Field(gt=0)] = 384
    lab_cpus: Annotated[float, Field(gt=0)] = 0.5
    lab_pids: Annotated[int, Field(gt=0)] = 256

    lab_tmp_mb: Annotated[int, Field(gt=0)] = 32
    lab_vartmp_mb: Annotated[int, Field(gt=0)] = 16
    lab_run_mb: Annotated[int, Field(gt=0)] = 8
    lab_shm_mb: Annotated[int, Field(gt=0)] = 16

    lab_max_sessions: Annotated[int, Field(gt=0)] = 20
    lab_max_terminals: Annotated[int, Field(gt=0)] = 4
    lab_idle_min: Annotated[int, Field(gt=0)] = 10
    lab_container_max_age_h: Annotated[int, Field(ge=0)] = 24
    lab_retention_h: Annotated[int, Field(gt=0)] = 48
    lab_max_age_h: Annotated[int, Field(ge=0)] = 0

    lab_max_creates_per_min: Annotated[int, Field(gt=0)] = 60

    lab_log_max_size: str = "512k"
    lab_log_max_files: Annotated[int, Field(gt=0)] = 2

    turnstile_sitekey: str | None = None
    turnstile_secret: SecretStr | None = None

    @field_validator("turnstile_sitekey", "turnstile_secret", mode="before")
    @classmethod
    def _unset_when_blank(cls, value: object) -> object:
        return _blank_to_none(value)

    @model_validator(mode="after")
    def _check_memory_headroom(self) -> Self:
        tmpfs_mb = (
            self.lab_tmp_mb + self.lab_vartmp_mb + self.lab_run_mb + self.lab_shm_mb
        )
        floor = tmpfs_mb + MEMORY_HEADROOM_MB
        if self.lab_memory_mb <= floor:
            raise ValueError(
                f"LAB_MEMORY_MB={self.lab_memory_mb} must exceed the tmpfs total "
                f"({tmpfs_mb} MiB) plus {MEMORY_HEADROOM_MB} MiB of headroom, i.e. > {floor}"
            )
        return self

    @model_validator(mode="after")
    def _check_lab_user_is_not_root(self) -> Self:
        if self.lab_uid == 0:
            raise ValueError("LAB_UID must not be 0: user containers never run as root")
        return self

    @model_validator(mode="after")
    def _check_turnstile(self) -> Self:
        if (self.turnstile_sitekey is None) != (self.turnstile_secret is None):
            raise ValueError(
                "TURNSTILE_SITEKEY and TURNSTILE_SECRET must both be set or both be unset"
            )
        return self

    @property
    def turnstile_configured(self) -> bool:
        return self.turnstile_secret is not None

    @property
    def pool_users_dir(self) -> Path:
        return self.pool_mount / "users"

    @property
    def pool_id_file(self) -> Path:
        return self.pool_mount / ".pool-id"

    @property
    def pool_id(self) -> str | None:
        path = self.hub_data_dir / "pool-id"
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def bind_pool_id(self) -> str | None:
        """Seed pool identity only from a non-empty sentinel to avoid mismatches."""
        existing = self.pool_id
        if existing is not None:
            return existing
        try:
            seed = self.pool_id_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not seed:
            return None
        self.hub_data_dir.mkdir(parents=True, exist_ok=True)
        (self.hub_data_dir / "pool-id").write_text(f"{seed}\n", encoding="utf-8")
        return seed


COOKIE_MAX_AGE_DAYS: Final[int] = 14


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
