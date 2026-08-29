from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast

import pytest
from docker.errors import DockerException
from jupyterhub.apihandlers.base import APIHandler

from finki_hub import handlers, readiness
from finki_hub.auth import username_for_token
from finki_hub.handlers import (
    CreationWindow,
    LoginError,
    LoginRequest,
    LoginResponse,
    check_login_headers,
    perform_login,
)
from finki_hub.turnstile import TurnstileVerdict
from tests.fakes import FakeDocker

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from finki_hub.settings import Settings

TOKEN = "AbC012_-" * 4
PASSED = TurnstileVerdict(outcome="passed")


@pytest.fixture(autouse=True)
def _fresh_creation_window(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the creation counter is process-wide, each test starts it empty.
    monkeypatch.setattr(handlers, "creations", CreationWindow())


class FakeBackend:
    """A hub whose answers the decision table can dictate."""

    def __init__(
        self,
        settings: Settings,
        *,
        hub_user: bool = False,
        home: bool = False,
        space: bool = True,
        verdict: TurnstileVerdict = PASSED,
    ) -> None:
        self._settings = settings
        self._hub_user = hub_user
        self._home = home
        self._space = space
        self._verdict = verdict
        self.created: list[str] = []
        self.turnstile_calls = 0
        self.issued: list[tuple[str, bool]] = []

    @property
    def settings(self) -> Settings:
        return self._settings

    def hub_user_exists(self, username: str) -> bool:
        return self._hub_user

    def home_exists(self, username: str) -> bool:
        return self._home

    def free_space_ok(self) -> bool:
        return self._space

    async def verify_turnstile(self, token: str | None) -> TurnstileVerdict:
        self.turnstile_calls += 1
        return self._verdict

    async def create_environment(self, username: str) -> None:
        self.created.append(username)

    def issue(self, username: str, *, created: bool) -> LoginResponse:
        self.issued.append((username, created))
        return LoginResponse(
            username=username,
            api_token="api-token",
            token_expires_at="2026-08-30T00:00:00Z",
            created=created,
            max_terminals=self._settings.lab_max_terminals,
        )


def login(backend: FakeBackend, intent: str = "resume") -> LoginResponse:
    request = LoginRequest(token=TOKEN, intent=intent, turnstile="response")  # type: ignore[arg-type]
    return asyncio.run(perform_login(backend, request))


# --- the decision table ------------------------------------------------------


@pytest.mark.parametrize("intent", ["resume", "create"])
def test_a_known_hub_user_resumes_without_a_challenge(
    make_settings: Callable[..., Settings], intent: str
) -> None:
    backend = FakeBackend(make_settings(), hub_user=True)

    response = login(backend, intent)

    assert response.created is False
    assert response.username == username_for_token(TOKEN)
    assert backend.turnstile_calls == 0
    assert backend.created == []


def test_a_home_directory_without_a_hub_user_still_resumes(
    make_settings: Callable[..., Settings],
) -> None:
    backend = FakeBackend(make_settings(), home=True)

    assert login(backend, "resume").created is False
    assert backend.turnstile_calls == 0


def test_resuming_a_vanished_environment_is_410(
    make_settings: Callable[..., Settings],
) -> None:
    backend = FakeBackend(make_settings())

    with pytest.raises(LoginError) as refusal:
        login(backend, "resume")

    assert refusal.value.status == 410
    assert refusal.value.to_json() == {"error": "gone"}
    assert backend.turnstile_calls == 0


def test_creating_an_environment_challenges_once_then_creates(
    make_settings: Callable[..., Settings],
) -> None:
    backend = FakeBackend(make_settings())

    response = login(backend, "create")

    assert response.created is True
    assert backend.turnstile_calls == 1
    assert backend.created == [username_for_token(TOKEN)]
    assert response.max_terminals == 4


@pytest.mark.parametrize("error", ["challenge-required", "challenge-failed"])
def test_a_refused_challenge_is_403(
    make_settings: Callable[..., Settings], error: str
) -> None:
    verdict = TurnstileVerdict(outcome="refused", reason="why", error=error)  # type: ignore[arg-type]
    backend = FakeBackend(make_settings(), verdict=verdict)

    with pytest.raises(LoginError) as refusal:
        login(backend, "create")

    assert refusal.value.status == 403
    assert refusal.value.to_json() == {"error": error, "reason": "why"}
    assert backend.created == []


def test_the_global_creation_limit_is_429(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(lab_max_creates_per_min=1)

    login(FakeBackend(settings), "create")
    with pytest.raises(LoginError) as refusal:
        login(FakeBackend(settings), "create")

    assert refusal.value.status == 429
    assert refusal.value.to_json() == {"error": "rate-limited", "reason": "create-rate"}


def test_resuming_is_never_rate_limited(
    make_settings: Callable[..., Settings],
) -> None:
    # Given: only creations are counted, so a busy campus never locks itself out
    # of the environments it already has.
    settings = make_settings(lab_max_creates_per_min=1)
    backend = FakeBackend(settings, hub_user=True)

    for _ in range(5):
        assert login(backend, "resume").created is False


def test_the_free_space_floor_refuses_creations_only(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()

    # Given: a pool below the floor, an existing environment still resumes.
    assert login(FakeBackend(settings, hub_user=True, space=False)).created is False

    # But: a creation is refused before Turnstile is ever consulted.
    backend = FakeBackend(settings, space=False)
    with pytest.raises(LoginError) as refusal:
        login(backend, "create")

    assert refusal.value.status == 429
    assert refusal.value.reason == "insufficient-storage"
    assert backend.turnstile_calls == 0


# --- the creation counter ----------------------------------------------------


def test_the_creation_window_admits_exactly_the_limit() -> None:
    window = CreationWindow()

    verdicts = [window.allow(3, 100.0) for _ in range(5)]

    assert verdicts == [True, True, True, False, False]


def test_the_creation_window_starts_fresh_after_sixty_seconds() -> None:
    window = CreationWindow()
    for _ in range(3):
        window.allow(3, 100.0)

    assert window.allow(3, 159.9) is False
    assert window.allow(3, 160.0) is True


# --- request headers ---------------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/json; charset=utf-8", "APPLICATION/JSON"],
)
def test_a_json_body_from_the_spa_is_accepted(content_type: str) -> None:
    check_login_headers(content_type, "same-origin")
    check_login_headers(content_type, "none")
    check_login_headers(content_type, None)


@pytest.mark.parametrize(
    "content_type",
    [
        None,
        "",
        "text/plain",
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    ],
)
def test_a_non_json_content_type_is_415(content_type: str | None) -> None:
    with pytest.raises(LoginError) as refusal:
        check_login_headers(content_type, "same-origin")

    assert refusal.value.status == 415
    assert refusal.value.to_json() == {"error": "unsupported-media-type"}


@pytest.mark.parametrize("fetch_site", ["cross-site", "same-site"])
def test_a_cross_origin_fetch_is_403(fetch_site: str) -> None:
    with pytest.raises(LoginError) as refusal:
        check_login_headers("application/json", fetch_site)

    assert refusal.value.status == 403
    assert refusal.value.to_json() == {"error": "forbidden"}


# --- response and token shapes ----------------------------------------------


def test_the_success_body_uses_the_spa_field_names(
    make_settings: Callable[..., Settings],
) -> None:
    backend = FakeBackend(make_settings(), hub_user=True)

    assert login(backend).to_json() == {
        "username": username_for_token(TOKEN),
        "apiToken": "api-token",
        "tokenExpiresAt": "2026-08-30T00:00:00Z",
        "created": False,
        "maxTerminals": 4,
    }


def test_the_token_scopes_are_filtered_to_the_user() -> None:
    username = username_for_token(TOKEN)

    scopes = [scope.format(username=username) for scope in handlers.SPA_TOKEN_SCOPES]

    assert scopes == [
        f"access:servers!user={username}",
        f"servers!user={username}",
        f"read:servers!user={username}",
        f"read:users!user={username}",
        f"tokens!user={username}",
    ]


def test_the_spa_token_lives_for_one_day() -> None:
    # The SPA re-logs in with the environment token it keeps in localStorage.
    assert handlers.SPA_TOKEN_TTL_S == 24 * 3600


def test_the_login_route_skips_the_xsrf_check() -> None:
    # Given: /hub/lab/login takes no credential but the one in its body, and the
    # SPA served from / cannot read a /hub/-scoped _xsrf cookie.
    handler = handlers.LabLoginHandler

    # Then: the override is present and does nothing.
    assert "check_xsrf_cookie" in vars(handler)
    handler.check_xsrf_cookie(handler)  # type: ignore[arg-type]


def test_the_discard_route_keeps_the_inherited_xsrf_check() -> None:
    # Given: APIHandler already returns early for the Authorization: token path
    # the SPA uses, so overriding the check here would only strip the guard from
    # the cookie-authenticated path of an endpoint that deletes an environment.
    assert "check_xsrf_cookie" not in vars(handlers.LabDiscardHandler)
    assert handlers.LabDiscardHandler.check_xsrf_cookie is APIHandler.check_xsrf_cookie


class FakeResponse:
    """The slice of tornado.web.RequestHandler that ``_write_json`` touches."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.body = ""

    def set_status(self, status: int) -> None:
        self.status = status

    def set_header(self, name: str, value: str) -> None:
        self.headers[name] = value

    def write(self, chunk: str) -> None:
        self.body += chunk


def test_the_ready_route_reports_an_unbuildable_client_as_503(
    monkeypatch: pytest.MonkeyPatch, make_settings: Callable[..., Settings]
) -> None:
    # Given: docker.from_env() raises -- it round-trips to the daemon in its
    # constructor -- which is the very condition /hub/lab/ready exists to report.
    def factory() -> Any:
        raise DockerException("Error while fetching server API version")

    monkeypatch.setattr(handlers, "docker_client", factory)
    monkeypatch.setattr(handlers, "get_settings", make_settings)
    response = FakeResponse()

    # When: the endpoint is asked
    asyncio.run(handlers.LabReadyHandler.get(cast("Any", response)))

    # Then: the contracted 503 body, not a 500 HTML error page.
    assert response.status == 503
    body = json.loads(response.body)
    assert body["ready"] is False
    assert "docker-unavailable: DockerException" in body["reasons"]


def test_the_ready_route_answers_200_when_everything_is_up(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
    pool: Path,
    mountinfo: Callable[[str, str], Path],
) -> None:
    monkeypatch.setattr(handlers, "docker_client", lambda: cast("Any", FakeDocker()))
    monkeypatch.setattr(handlers, "get_settings", make_settings)
    monkeypatch.setattr(
        readiness, "MOUNTINFO", mountinfo(str(pool), "xfs /dev/sda1 rw,prjquota")
    )
    response = FakeResponse()

    asyncio.run(handlers.LabReadyHandler.get(cast("Any", response)))

    assert response.status == 200
    assert json.loads(response.body) == {"ready": True}
