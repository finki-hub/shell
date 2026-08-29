"""The three ``/hub/lab/*`` endpoints the SPA talks to.

The decision logic lives in :func:`perform_login`, a pure orchestration over the
:class:`LoginBackend` protocol, so the whole table -- resume, create, 410, 403,
429 -- is exercised without a running hub. The Tornado handlers below are thin
adapters that bound the request body, translate refusals into status codes and
mint the scoped API token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

import httpx
from jupyterhub.apihandlers.base import APIHandler
from jupyterhub.handlers.base import BaseHandler
from jupyterhub.utils import maybe_future
from tornado import web

from finki_hub.auth import is_valid_token, username_for_token
from finki_hub.readiness import docker_client, has_free_space, readiness_report
from finki_hub.settings import get_settings
from finki_hub.turnstile import TurnstileVerdict, check

if TYPE_CHECKING:
    from finki_hub.settings import Settings

#: Ceiling on a ``/hub/lab/*`` request body, enforced per handler.
MAX_BODY_BYTES: Final[int] = 16 * 1024

#: How long ``/hub/lab/discard`` waits for a user container to stop.
STOP_TIMEOUT_S: Final[float] = 30.0

#: Lifetime of the SPA's API token. One day: the SPA re-logs in with the
#: environment token it keeps in localStorage.
SPA_TOKEN_TTL_S: Final[int] = 86_400

#: Scopes granted to the SPA's API token (contract section 7).
SPA_TOKEN_SCOPES: Final[tuple[str, ...]] = (
    "access:servers!user={username}",
    "servers!user={username}",
    "read:servers!user={username}",
    "read:users!user={username}",
    "tokens!user={username}",
)

#: Width of the creation counter's window, in seconds.
CREATE_WINDOW_S: Final[float] = 60.0

Intent = Literal["resume", "create"]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CreationWindow:
    """Counts environment creations inside a fixed 60-second window."""

    started_at: float = 0.0
    count: int = 0

    def allow(self, limit: int, now: float) -> bool:
        """Record one creation attempt and report whether it stays within ``limit``."""
        if now - self.started_at >= CREATE_WINDOW_S:
            self.started_at, self.count = now, 0
        self.count += 1
        return self.count <= limit


#: The one global creation counter, shared process-wide.
creations = CreationWindow()


class LoginError(Exception):
    """A login refusal carrying the exact status and body the contract fixes."""

    def __init__(self, status: int, error: str, reason: str | None = None) -> None:
        super().__init__(f"{status} {error}")
        self.status = status
        self.error = error
        self.reason = reason

    def to_json(self) -> dict[str, str]:
        if self.reason is None:
            return {"error": self.error}
        return {"error": self.error, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class LoginRequest:
    """A validated ``POST /hub/lab/login`` body."""

    token: str
    intent: Intent
    turnstile: str | None = None


@dataclass(frozen=True, slots=True)
class LoginResponse:
    """The ``200`` body of ``POST /hub/lab/login``."""

    username: str
    api_token: str
    token_expires_at: str
    created: bool
    max_terminals: int

    def to_json(self) -> dict[str, object]:
        return {
            "username": self.username,
            "apiToken": self.api_token,
            "tokenExpiresAt": self.token_expires_at,
            "created": self.created,
            "maxTerminals": self.max_terminals,
        }


class LoginBackend(Protocol):
    """Everything :func:`perform_login` needs from the running hub."""

    @property
    def settings(self) -> Settings: ...
    def hub_user_exists(self, username: str) -> bool: ...
    def home_exists(self, username: str) -> bool: ...
    def free_space_ok(self) -> bool: ...
    async def verify_turnstile(self, token: str | None) -> TurnstileVerdict: ...
    async def create_environment(self, username: str) -> None: ...
    def issue(self, username: str, *, created: bool) -> LoginResponse: ...


def parse_login_body(raw: bytes) -> LoginRequest:
    """Validate a login body; raise :class:`LoginError` with 400 or 413."""
    if len(raw) > MAX_BODY_BYTES:
        raise LoginError(413, "payload-too-large")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoginError(400, "bad-request", "invalid-json") from exc
    if not isinstance(body, dict):
        raise LoginError(400, "bad-request", "not-an-object")

    token = body.get("token")
    if not isinstance(token, str) or not is_valid_token(token):
        raise LoginError(400, "bad-request", "invalid-token")

    intent = body.get("intent", "resume")
    if intent not in ("resume", "create"):
        raise LoginError(400, "bad-request", "invalid-intent")

    turnstile = body.get("turnstile")
    if turnstile is not None and not isinstance(turnstile, str):
        raise LoginError(400, "bad-request", "invalid-turnstile")

    return LoginRequest(token=token, intent=intent, turnstile=turnstile)


async def perform_login(backend: LoginBackend, request: LoginRequest) -> LoginResponse:
    """The login decision table of contract section 7."""
    settings = backend.settings
    username = username_for_token(request.token)
    if backend.hub_user_exists(username) or backend.home_exists(username):
        return backend.issue(username, created=False)

    if request.intent == "resume":
        raise LoginError(410, "gone")

    if not creations.allow(settings.lab_max_creates_per_min, time.monotonic()):
        raise LoginError(429, "rate-limited", "create-rate")
    if not backend.free_space_ok():
        raise LoginError(429, "rate-limited", "insufficient-storage")

    verdict = await backend.verify_turnstile(request.turnstile)
    if verdict.error is not None:
        raise LoginError(403, verdict.error, verdict.reason)

    await backend.create_environment(username)
    return backend.issue(username, created=True)


def _write_json(handler: web.RequestHandler, status: int, body: object) -> None:
    handler.set_status(status)
    handler.set_header("Content-Type", "application/json")
    handler.set_header("Cache-Control", "no-store")
    handler.write(json.dumps(body))


def _bounded_body(handler: web.RequestHandler) -> bytes:
    declared = handler.request.headers.get("Content-Length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise LoginError(413, "payload-too-large")
        except ValueError as exc:
            raise LoginError(400, "bad-request", "invalid-content-length") from exc
    return handler.request.body


def check_login_headers(content_type: str | None, fetch_site: str | None) -> None:
    """Refuse anything that is not the SPA's own ``fetch`` of this endpoint.

    A form post cannot set ``Content-Type: application/json``, and a browser
    labels a cross-site request as such in ``Sec-Fetch-Site`` -- together they
    keep an unauthenticated POST that mints an API token off-limits to other
    origins. Requests without the header (curl, older browsers) are unaffected.
    """
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise LoginError(415, "unsupported-media-type")
    if fetch_site is not None and fetch_site not in ("same-origin", "none"):
        raise LoginError(403, "forbidden")


class LabLoginHandler(BaseHandler):  # type: ignore[misc] # jupyterhub ships no type information
    """``POST /hub/lab/login`` -- resume or create an environment."""

    def check_xsrf_cookie(self) -> None:
        """No-op: the request body carries the credential, not a cookie."""
        return

    async def post(self) -> None:
        try:
            headers = self.request.headers
            check_login_headers(
                headers.get("Content-Type"), headers.get("Sec-Fetch-Site")
            )
            request = parse_login_body(_bounded_body(self))
            response = await perform_login(_HubLoginBackend(self), request)
        except LoginError as refusal:
            logger.info("login refused: %s %s", refusal.status, refusal.error)
            _write_json(self, refusal.status, refusal.to_json())
            return
        _write_json(self, 200, response.to_json())


class LabDiscardHandler(APIHandler):  # type: ignore[misc] # jupyterhub ships no type information
    """``POST /hub/lab/discard`` -- stop the container and delete the environment.

    ``check_xsrf_cookie`` is deliberately *not* overridden: ``APIHandler``
    already returns early for the ``Authorization: token`` path the SPA uses, so
    an override would only strip the guard from the cookie-authenticated path of
    an endpoint that deletes a user, its home directory and its XFS project.
    """

    async def post(self) -> None:
        user = self.current_user
        if user is None:
            raise web.HTTPError(403)
        if user.running:
            stop = self.stop_single_user(user)
            try:
                await asyncio.wait_for(stop, timeout=STOP_TIMEOUT_S)
            except TimeoutError as exc:
                message = f"{user.name}'s server did not stop in time"
                raise web.HTTPError(409, message) from exc

        await maybe_future(self.authenticator.delete_user(user))
        await user.delete_spawners()
        self.users.delete(user)
        self.clear_login_cookie()
        self.set_status(204)


class LabReadyHandler(BaseHandler):  # type: ignore[misc] # jupyterhub ships no type information
    """``GET /hub/lab/ready`` -- Docker answers and the pool is usable."""

    async def get(self) -> None:
        settings = get_settings()
        # The factory, not a client: building one talks to the daemon, so a dead
        # daemon must be reported as a reason instead of raising a 500 here.
        report = await asyncio.to_thread(
            readiness_report,
            docker_client,
            settings.pool_mount,
            expected_pool_id=settings.pool_id,
        )
        _write_json(self, 200 if report.ready else 503, report.to_json())


class _HubLoginBackend:
    """The running hub, seen through :class:`LoginBackend`."""

    def __init__(self, handler: Any) -> None:  # ruff: ignore[any-type] - an untyped jupyterhub BaseHandler
        self._handler = handler
        self._settings = get_settings()

    @property
    def settings(self) -> Settings:
        return self._settings

    def hub_user_exists(self, username: str) -> bool:
        return self._handler.find_user(username) is not None

    def home_exists(self, username: str) -> bool:
        return (self._settings.pool_users_dir / username).is_dir()

    def free_space_ok(self) -> bool:
        return has_free_space(
            self._settings.pool_mount, self._settings.lab_pool_reserve_pct
        )

    async def verify_turnstile(self, token: str | None) -> TurnstileVerdict:
        secret = self._settings.turnstile_secret
        if secret is None:
            return TurnstileVerdict(outcome="not-configured")
        async with httpx.AsyncClient() as client:
            return await check(secret.get_secret_value(), client, token)

    async def create_environment(self, username: str) -> None:
        self._handler.user_from_username(username)

    def issue(self, username: str, *, created: bool) -> LoginResponse:
        user = self._handler.user_from_username(username)
        self._handler.set_login_cookie(user)
        api_token = user.new_api_token(
            note="spa",
            expires_in=SPA_TOKEN_TTL_S,
            scopes=[scope.format(username=username) for scope in SPA_TOKEN_SCOPES],
        )
        expires_at = datetime.now(UTC) + timedelta(seconds=SPA_TOKEN_TTL_S)
        return LoginResponse(
            username=username,
            api_token=api_token,
            token_expires_at=expires_at.isoformat().replace("+00:00", "Z"),
            created=created,
            max_terminals=self._settings.lab_max_terminals,
        )
