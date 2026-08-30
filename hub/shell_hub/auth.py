"""Token identity; raw tokens never enter URLs, logs, or the database."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import TYPE_CHECKING, Any, Final, Protocol

from jupyterhub.auth import Authenticator
from jupyterhub.utils import maybe_future

from shell_hub.quota import QuotaError, remove_home
from shell_hub.settings import get_settings

if TYPE_CHECKING:
    from tornado.web import RequestHandler

TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{32,128}$")

USERNAME_LENGTH: Final[int] = 32

logger = logging.getLogger(__name__)


class UserLike(Protocol):
    @property
    def name(self) -> str: ...


def is_valid_token(token: str) -> bool:
    return bool(TOKEN_PATTERN.fullmatch(token))


def username_for_token(token: str) -> str:
    """Hash tokens to opaque, path-safe usernames."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:USERNAME_LENGTH]


class EnvironmentTokenAuthenticator(Authenticator):  # type: ignore[misc] # jupyterhub ships no type information
    async def authenticate(
        self,
        handler: RequestHandler,  # ruff: ignore[unused-method-argument] - the base signature is fixed
        data: dict[str, Any] | None,  # ruff: ignore[unused-method-argument]
    ) -> None:
        logger.warning("authenticate() reached unexpectedly; refusing")

    def get_handlers(
        self,
        app: Any,  # ruff: ignore[any-type, unused-method-argument] - JupyterHub hands over its application object
    ) -> list[tuple[str, type[RequestHandler]]]:
        # Lazy import avoids the handlers/auth circular dependency.
        from shell_hub import handlers  # ruff: ignore[import-outside-top-level]

        return [
            ("/lab/login", handlers.LabLoginHandler),
            ("/lab/discard", handlers.LabDiscardHandler),
            ("/lab/ready", handlers.LabReadyHandler),
        ]

    async def delete_user(self, user: UserLike) -> None:
        await maybe_future(super().delete_user(user))
        settings = get_settings()
        try:
            # Home removal takes a lock and invokes xfs_quota, so keep it off-loop.
            await asyncio.to_thread(remove_home, settings, user.name)
        except QuotaError, OSError:
            # Keep culler progress despite cleanup failures.
            logger.exception("removing the home directory of %s failed", user.name)
