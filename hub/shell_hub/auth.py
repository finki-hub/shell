"""Identity for the shell: an opaque environment token minted by the SPA.

There is no login page. ``get_handlers`` returns only the three ``/lab/*`` routes
and no ``('/login', ...)`` entry, so ``/hub/login`` is never routed and answers
404 -- the SPA is the only entry point. The hub stores nothing but the username
derived from the token; the raw token never reaches a URL, a log line or the
database.
"""

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

#: The token format the SPA mints in ``web/src/lib/environment.ts``.
TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{32,128}$")

#: Length of the derived username, in hex characters.
USERNAME_LENGTH: Final[int] = 32

logger = logging.getLogger(__name__)


class UserLike(Protocol):
    """The slice of ``jupyterhub.user.User`` this module reads."""

    @property
    def name(self) -> str: ...


def is_valid_token(token: str) -> bool:
    """True when ``token`` matches the accepted environment-token format."""
    return bool(TOKEN_PATTERN.fullmatch(token))


def username_for_token(token: str) -> str:
    """Derive the hub username from an environment token.

    ``sha256(token).hexdigest()[:32]`` -- stable, opaque, and safe to place in a
    ``/user/<username>/`` path.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:USERNAME_LENGTH]


class EnvironmentTokenAuthenticator(Authenticator):  # type: ignore[misc] # jupyterhub ships no type information
    """Authenticator whose only routes are the SPA's three ``/lab/*`` endpoints."""

    async def authenticate(
        self,
        handler: RequestHandler,  # ruff: ignore[unused-method-argument] - the base signature is fixed
        data: dict[str, Any] | None,  # ruff: ignore[unused-method-argument]
    ) -> None:
        """Never reached: no login form is routed. Always refuses."""
        logger.warning("authenticate() reached unexpectedly; refusing")

    def get_handlers(
        self,
        app: Any,  # ruff: ignore[any-type, unused-method-argument] - JupyterHub hands over its application object
    ) -> list[tuple[str, type[RequestHandler]]]:
        """The routes JupyterHub mounts under ``/hub``; deliberately no login page."""
        # Imported here because shell_hub.handlers imports this module.
        from shell_hub import handlers  # ruff: ignore[import-outside-top-level]

        return [
            ("/lab/login", handlers.LabLoginHandler),
            ("/lab/discard", handlers.LabDiscardHandler),
            ("/lab/ready", handlers.LabReadyHandler),
        ]

    async def delete_user(self, user: UserLike) -> None:
        """Remove the environment's home directory and release its XFS quota.

        This is the only path that deletes a home directory: nothing sweeps the
        pool. Declared ``async`` on purpose -- the only caller awaits the result
        through ``maybe_future``, and the removal takes the pool lock and runs
        ``xfs_quota``, neither of which may stall the hub's IO loop. The base
        implementation only discards the name from ``allowed_users``.
        """
        await maybe_future(super().delete_user(user))
        settings = get_settings()
        try:
            await asyncio.to_thread(remove_home, settings, user.name)
        except QuotaError, OSError:
            # Never block the deletion: a 500 from DELETE /hub/api/users/<u>
            # would wedge the user culler on this user forever.
            logger.exception("removing the home directory of %s failed", user.name)
