#!/usr/bin/env python3
"""Finite real Hub/proxy/Lab API acceptance stage with sanitized diagnostics.

Run from a disposable Hub image with its installed httpx/tornado dependencies.
The loopback URL and ownership marker are mandatory. Browser/API credentials
are kept in a mode-0600 run-local state file and never sent to stdout/logs.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import signal
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

RUN_ID_RE = re.compile(r"^[a-f0-9]{12,32}$")
ALLOWED_DENIALS = {401, 403, 404}
SPAWN_TIMEOUT = 210
STOP_TIMEOUT = 60
HTTP_TIMEOUT = 15
WS_TIMEOUT = 10
CHECKPOINTS = (
    "readiness",
    "login-a",
    "login-b",
    "spawn-a",
    "spawn-b",
    "storage-a",
    "storage-b",
    "cross-user",
    "cookie-oauth",
    "terminals",
    "websocket",
    "reconnect",
    "complete",
)
CHECKPOINT_SET = frozenset(CHECKPOINTS)
FAILURE_CLASSIFICATIONS = frozenset(
    {
        "http-status",
        "request-timeout",
        "request-transport",
        "assertion-failed",
        "websocket-failed",
        "probe-error",
    }
)


def validate_origin(base: str) -> tuple[str, str]:
    """Return canonical loopback origin/path; reject host-prefix tricks/userinfo."""
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.username or parsed.password:
        raise ValueError("origin must be plain HTTP loopback without userinfo")
    try:
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid origin") from exc
    if host != "127.0.0.1" or parsed.query or parsed.fragment:
        raise ValueError("origin must be exactly 127.0.0.1")
    if port != 8000:
        raise ValueError("private acceptance origin must use port 8000")
    path = parsed.path.rstrip("/")
    if path not in {"", "/"}:
        raise ValueError("base must be the origin root")
    origin = urlunsplit(("http", parsed.netloc, "", "", ""))
    ws_origin = urlunsplit(("ws", parsed.netloc, "", "", ""))
    return origin, ws_origin


class ProbeFailure(AssertionError):
    def __init__(
        self,
        message: str,
        *,
        classification: str = "assertion-failed",
        http_status: int | None = None,
        errno: int | None = None,
    ) -> None:
        super().__init__(message)
        self.classification = (
            classification
            if isinstance(classification, str)
            and classification in FAILURE_CLASSIFICATIONS
            else "probe-error"
        )
        self.http_status = (
            http_status
            if type(http_status) is int and 100 <= http_status <= 599
            else None
        )
        self.errno = errno if type(errno) is int and 0 <= errno <= 4095 else None


def assert_status(status: int, allowed: set[int], case: str) -> None:
    if status not in allowed:
        raise ProbeFailure(
            f"contract failed: {case}",
            classification="http-status",
            http_status=status,
        )


def failure_context(checkpoint: str, error: Exception) -> dict[str, object]:
    """Return only enum diagnostics and bounded numeric fields, never exception text."""
    if checkpoint not in CHECKPOINT_SET:
        checkpoint = "readiness"
    http_status: int | None = None
    errno: int | None = None
    if isinstance(error, ProbeFailure):
        classification = error.classification
        http_status = error.http_status
    elif checkpoint == "websocket":
        classification = "websocket-failed"
        code = getattr(error, "code", None)
        if type(code) is int and 100 <= code <= 599:
            http_status = code
    elif isinstance(error, TimeoutError):
        classification = "request-timeout"
    elif isinstance(error, AssertionError):
        classification = "assertion-failed"
    else:
        classification = "probe-error"
    if classification not in FAILURE_CLASSIFICATIONS:
        classification = "probe-error"
    raw_errno = getattr(error, "errno", None)
    if type(raw_errno) is int and 0 <= raw_errno <= 4095:
        errno = raw_errno
    result: dict[str, object] = {
        "checkpoint": checkpoint,
        "classification": classification,
    }
    if http_status is not None:
        result["http_status"] = http_status
    if errno is not None:
        result["errno"] = errno
    return result


def failure_report(
    stage: str, probe: Probe | None, error: Exception
) -> dict[str, object]:
    checkpoint = probe.current_checkpoint if probe is not None else "readiness"
    completed = probe.completed_cases if probe is not None else []
    safe_completed = [case for case in completed if case in CHECKPOINT_SET]
    return {
        "stage": stage,
        "status": "failed",
        "failure_context": failure_context(checkpoint, error),
        "completed_cases": safe_completed,
    }


def failure_json(stage: str, probe: Probe | None, error: Exception) -> str:
    return json.dumps(
        failure_report(stage, probe, error), sort_keys=True, separators=(",", ":")
    )


def check_marker(path: Path, run_id: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("run ownership marker must be a regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value != {"kind": "jupyterhub-migration-integration", "run_id": run_id}:
        raise ValueError("run ownership marker mismatch")


def check_private_state(path: Path, create: bool = False) -> None:
    if path.is_symlink():
        raise ValueError("state path may not be a symlink")
    if path.exists():
        if create:
            raise ValueError(
                "baseline state already exists; will not adopt or overwrite it"
            )
        mode = stat.S_IMODE(path.stat().st_mode)
        if not path.is_file() or mode & 0o077:
            raise ValueError("existing state file must be regular and private")
    elif not create:
        raise ValueError("stage state file does not exist")
    parent_mode = stat.S_IMODE(path.parent.stat().st_mode)
    if parent_mode & 0o077:
        raise ValueError("state directory must be private (0700 or stricter)")


class Probe:
    def __init__(
        self, origin: str, ws_origin: str, timeout: float, transport=None
    ) -> None:
        import httpx

        self.httpx = httpx
        self.origin, self.ws_origin = origin, ws_origin
        self.timeout = timeout
        self.transport = transport
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            headers={"Accept": "application/json", "Sec-Fetch-Site": "same-origin"},
            transport=transport,
        )
        self.anonymous_client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            headers={"Accept": "application/json", "Sec-Fetch-Site": "same-origin"},
            transport=transport,
        )
        self.cookie_clients: dict[str, Any] = {}
        self.results: dict[str, str] = {}
        self.current_checkpoint = "readiness"
        self.completed_cases: list[str] = []

    def checkpoint(self, name: str) -> None:
        if not isinstance(name, str) or name not in CHECKPOINT_SET:
            raise ProbeFailure("invalid probe checkpoint", classification="probe-error")
        self.current_checkpoint = name

    def complete_checkpoint(self) -> None:
        if self.current_checkpoint not in self.completed_cases:
            self.completed_cases.append(self.current_checkpoint)

    def _http_failure(self, error: Exception) -> ProbeFailure:
        raw_errno = getattr(error, "errno", None)
        if isinstance(error, self.httpx.TimeoutException):
            return ProbeFailure(
                "HTTP request timed out",
                classification="request-timeout",
                errno=raw_errno,
            )
        return ProbeFailure(
            "HTTP request transport failed",
            classification="request-transport",
            errno=raw_errno,
        )

    def _http_call(self, call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except self.httpx.TimeoutException as exc:
            raise self._http_failure(exc) from None
        except self.httpx.TransportError as exc:
            raise self._http_failure(exc) from None

    def record(self, name: str, status: str = "pass") -> None:
        self.results[name] = status

    def request(
        self,
        method: str,
        path: str,
        token: str | None = None,
        *,
        json_body: object | None = None,
        timeout: float | None = None,
        session: Any | None = None,
    ):
        # Relative paths only: no caller-supplied absolute URL or off-origin hop.
        if (
            not path.startswith("/")
            or path.startswith("//")
            or "\\" in path
            or any(ord(char) < 0x20 for char in path)
        ):
            raise ValueError("request path must be origin-relative")
        headers = {"Authorization": f"token {token}"} if token else {}
        client = session or self.client
        if session is None:
            client.cookies.clear()
        try:
            response = self._http_call(
                client.request,
                method,
                self.origin + path,
                headers=headers,
                json=json_body,
                timeout=timeout or self.timeout,
            )
        finally:
            if session is None:
                client.cookies.clear()
        if response.is_redirect:
            location = response.headers.get("location", "")
            if location:
                target = urlsplit(location)
                if target.netloc and target.netloc != urlsplit(self.origin).netloc:
                    raise ProbeFailure(
                        "off-origin redirect rejected",
                        classification="http-status",
                        http_status=response.status_code,
                    )
            raise ProbeFailure(
                "unexpected redirect",
                classification="http-status",
                http_status=response.status_code,
            )
        return response

    @staticmethod
    def json(response):
        try:
            return response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise AssertionError("invalid JSON response") from exc

    def login(self, raw_token: str, intent: str) -> dict[str, object]:
        expected_user = hashlib.sha256(raw_token.encode()).hexdigest()[:32]
        session = self.cookie_clients.get(expected_user)
        if session is None:
            session = self.httpx.Client(
                timeout=self.httpx.Timeout(self.timeout),
                follow_redirects=False,
                headers={"Accept": "application/json", "Sec-Fetch-Site": "same-origin"},
                transport=self.transport,
            )
            self.cookie_clients[expected_user] = session
        response = self.request(
            "POST",
            "/hub/lab/login",
            json_body={"token": raw_token, "intent": intent},
            session=session,
        )
        assert_status(response.status_code, {200}, "browser-login")
        result = self.json(response)
        required = {"username", "apiToken", "tokenExpiresAt", "created", "maxTerminals"}
        if not isinstance(result, dict) or not required <= result.keys():
            raise AssertionError("login response shape mismatch")
        if result["username"] != expected_user or not isinstance(
            result["apiToken"], str
        ):
            raise AssertionError("login identity/token contract mismatch")
        if response.headers.get("cache-control", "").lower() != "no-store":
            raise AssertionError("login response is not marked no-store")
        if not any(
            "hub-login" in cookie.name.lower() for cookie in session.cookies.jar
        ):
            raise AssertionError("browser login did not issue a Hub login cookie")
        try:
            expires = datetime.fromisoformat(
                str(result["tokenExpiresAt"]).replace("Z", "+00:00")
            )
            remaining = (expires - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError) as exc:
            raise AssertionError("login token expiry is malformed") from exc
        if not 86_000 <= remaining <= 86_500:
            raise AssertionError("login token lifetime differs from one day")
        if type(result["maxTerminals"]) is not int or result["maxTerminals"] < 1:
            raise AssertionError("terminal cap missing")
        return result

    def ready(self) -> None:
        response = self.request("GET", "/hub/lab/ready")
        assert_status(response.status_code, {200}, "hub-readiness")
        if self.json(response) != {"ready": True}:
            raise AssertionError("Hub readiness payload mismatch")
        self.record("hub-readiness")

    def cookie_auth_boundary(self, identities: list[dict]) -> None:
        if len(self.cookie_clients) != 2:
            raise AssertionError("login cookie sessions are not isolated per identity")
        cookie_values: list[str] = []
        for identity in identities:
            username = str(identity["username"])
            session = self.cookie_clients.get(username)
            if session is None:
                raise AssertionError("per-user Hub login cookie session is missing")
            cookies = [
                cookie
                for cookie in session.cookies.jar
                if "hub-login" in cookie.name.lower()
            ]
            if len(cookies) != 1:
                raise AssertionError("expected exactly one Hub login cookie per user")
            cookie_values.append(cookies[0].value)
            page = self.request("GET", "/hub/home", session=session)
            assert_status(page.status_code, {200}, "cookie-authenticated-hub-page")
            self.record("cookie-authenticated-hub-page")
        if cookie_values[0] == cookie_values[1]:
            raise AssertionError("user A and B unexpectedly share a login cookie")

        username = quote(str(identities[0]["username"]), safe="")
        self.anonymous_client.cookies.clear()
        anonymous_api = self.request(
            "GET", f"/hub/api/users/{username}", session=self.anonymous_client
        )
        self.anonymous_client.cookies.clear()
        assert_status(anonymous_api.status_code, ALLOWED_DENIALS, "cookie-less-hub-api")
        try:
            anonymous_page = self._http_call(
                self.anonymous_client.get,
                self.origin + "/hub/home",
                timeout=self.timeout,
            )
        finally:
            self.anonymous_client.cookies.clear()
        assert_status(
            anonymous_page.status_code,
            {302, 303},
            "empty-anonymous-session-redirect",
        )
        anonymous_location = urlsplit(
            urljoin(self.origin + "/", anonymous_page.headers.get("location", ""))
        )
        private_origin = urlsplit(self.origin)
        if (
            anonymous_location.scheme != private_origin.scheme
            or anonymous_location.netloc != private_origin.netloc
            or not anonymous_location.path.endswith("/hub/login")
        ):
            raise AssertionError("anonymous Hub home did not redirect to private login")
        self.record("empty-anonymous-session-denied")

        username_value = str(identities[0]["username"])
        session = self.cookie_clients[username_value]
        callback = f"/user/{quote(username_value, safe='')}/oauth_callback"
        state = secrets.token_urlsafe(18)
        oauth_path = "/hub/api/oauth2/authorize?" + urlencode(
            {
                "client_id": f"jupyterhub-user-{username_value}",
                "redirect_uri": callback,
                "response_type": "code",
                "state": state,
            }
        )
        oauth = self._http_call(
            session.get, self.origin + oauth_path, timeout=self.timeout
        )
        assert_status(oauth.status_code, {302, 303}, "private-cookie-oauth-redirect")
        target = urlsplit(urljoin(self.origin + "/", oauth.headers.get("location", "")))
        params = dict(parse_qsl(target.query))
        if (
            target.scheme != private_origin.scheme
            or target.netloc != private_origin.netloc
            or target.path != callback
            or params.get("state") != state
            or not params.get("code")
        ):
            raise AssertionError(
                "private OAuth callback was not same-origin/state-bound"
            )
        off_origin_path = "/hub/api/oauth2/authorize?" + urlencode(
            {
                "client_id": f"jupyterhub-user-{username_value}",
                "redirect_uri": "https://attacker.invalid/oauth_callback",
                "response_type": "code",
                "state": state,
            }
        )
        off_origin = self._http_call(
            session.get, self.origin + off_origin_path, timeout=self.timeout
        )
        if off_origin.status_code in {302, 303}:
            target = urlsplit(
                urljoin(self.origin + "/", off_origin.headers.get("location", ""))
            )
            if target.netloc and (target.scheme, target.netloc) != (
                private_origin.scheme,
                private_origin.netloc,
            ):
                raise ProbeFailure(
                    "OAuth rejected redirect escaped the private origin",
                    classification="http-status",
                    http_status=off_origin.status_code,
                )
            raise ProbeFailure(
                "off-origin OAuth redirect was not rejected",
                classification="http-status",
                http_status=off_origin.status_code,
            )
        assert_status(off_origin.status_code, {400, 403}, "off-origin-oauth-redirect")
        self.record("private-cookie-oauth-flow")
        self.record("off-origin-oauth-redirect-rejected")

    def prepare_identity(self, state: dict, stage: str) -> None:
        identities = state.get("identities")
        self.checkpoint("login-a")
        if not isinstance(identities, list) or len(identities) != 2:
            raise AssertionError("state must contain exactly two fixture identities")
        for index, identity in enumerate(identities):
            self.checkpoint("login-a" if index == 0 else "login-b")
            raw = identity.get("browser_token")
            original_user = identity.get("username")
            if not isinstance(raw, str) or not isinstance(
                identity.get("api_token"), str
            ):
                raise AssertionError("private stage credentials missing")
            session = self.login(raw, "create" if stage == "baseline" else "resume")
            if session["username"] != original_user:
                raise AssertionError("resumed identity changed")
            if stage == "baseline" and session["created"] is not True:
                raise AssertionError("new fixture identity was not created")
            if stage != "baseline" and session["created"] is not False:
                raise AssertionError("existing identity was unexpectedly recreated")
            if stage == "baseline":
                identity["api_token"] = session["apiToken"]
            self.record("identity-create" if stage == "baseline" else "identity-resume")
            self.complete_checkpoint()

    def spawn(self, username: str, token: str) -> None:
        response = self.request(
            "POST",
            f"/hub/api/users/{quote(username, safe='')}/server",
            token,
            timeout=SPAWN_TIMEOUT,
        )
        assert_status(response.status_code, {201, 202, 400}, "bodyless-spawn")
        deadline = time.monotonic() + SPAWN_TIMEOUT
        while time.monotonic() < deadline:
            model = self.request(
                "GET",
                f"/hub/api/users/{quote(username, safe='')}",
                token,
                timeout=self.timeout,
            )
            assert_status(model.status_code, {200}, "spawn-poll")
            body = self.json(model)
            if isinstance(body, dict) and isinstance(body.get("servers"), dict):
                default = body["servers"].get("")
                if isinstance(default, dict) and default.get("ready") is True:
                    self.record("bodyless-spawn")
                    return
            time.sleep(min(2.0, max(0, deadline - time.monotonic())))
        raise TimeoutError(
            "default server did not become ready before bounded deadline"
        )

    def own_lab_checks(
        self, username: str, token: str, marker: str, stage: str
    ) -> tuple[str, str]:
        prefix = f"/user/{quote(username, safe='')}"
        storage = self.request(
            "GET", prefix + "/lab/storage?no_track_activity=1", token
        )
        assert_status(storage.status_code, {200}, "storage-report")
        report = self.json(storage)
        fields = ("bytesUsed", "bytesLimit", "inodesUsed", "inodesLimit")
        if (
            not isinstance(report, dict)
            or any(type(report.get(k)) is not int for k in fields)
            or report["bytesLimit"] <= 0
            or report["inodesLimit"] <= 0
        ):
            raise AssertionError("storage report shape mismatch")
        self.record("storage-report")
        path = f"migration-{marker[:16]}.txt"
        api_path = prefix + "/api/contents/" + quote(path, safe="")
        get = self.request("GET", api_path, token)
        if get.status_code == 404:
            if stage != "baseline":
                raise AssertionError("persistent baseline marker disappeared")
            put = self.request(
                "PUT",
                api_path,
                token,
                json_body={"type": "file", "format": "text", "content": marker},
            )
            assert_status(put.status_code, {200, 201}, "contents-write")
            self.record("contents-write")
            get = self.request("GET", api_path, token)
        else:
            assert_status(get.status_code, {200}, "contents-existing")
            if self.json(get).get("content") != marker:
                raise AssertionError("persistent marker changed between stages")
            self.record("contents-persisted")
        assert_status(get.status_code, {200}, "contents-read")
        if self.json(get).get("content") != marker:
            raise AssertionError("contents bytes differ")
        file_get = self.request("GET", prefix + "/files/" + quote(path, safe=""), token)
        assert_status(file_get.status_code, {200}, "file-download")
        if file_get.content.decode("utf-8") != marker:
            raise AssertionError("download bytes differ")
        self.record("contents-roundtrip")
        return path, marker

    def deny_other_user(self, owner: dict, other: dict, other_path: str) -> None:
        user_b = quote(str(other["username"]), safe="")
        token_a = str(owner["api_token"])
        cases = {
            "cross-user-model": self.request(
                "GET", f"/hub/api/users/{user_b}", token_a
            ).status_code,
            "cross-user-spawn": self.request(
                "POST",
                f"/hub/api/users/{user_b}/server",
                token_a,
                timeout=SPAWN_TIMEOUT,
            ).status_code,
            "cross-user-stop": self.request(
                "DELETE",
                f"/hub/api/users/{user_b}/server",
                token_a,
                timeout=STOP_TIMEOUT,
            ).status_code,
            "cross-user-file": self.request(
                "GET",
                f"/user/{user_b}/api/contents/{quote(other_path, safe='')}",
                token_a,
            ).status_code,
            "cross-user-download": self.request(
                "GET", f"/user/{user_b}/files/{quote(other_path, safe='')}", token_a
            ).status_code,
            "cross-user-storage": self.request(
                "GET", f"/user/{user_b}/lab/storage?no_track_activity=1", token_a
            ).status_code,
            "cross-user-terminal-list": self.request(
                "GET", f"/user/{user_b}/api/terminals", token_a
            ).status_code,
            "cross-user-token-mint": self.request(
                "POST",
                f"/hub/api/users/{user_b}/tokens",
                token_a,
                json_body={"expires_in": 60, "note": "denial-check"},
            ).status_code,
        }
        for name, status in cases.items():
            assert_status(status, ALLOWED_DENIALS, name)
            self.record(name)
        unauth = self.request("GET", f"/hub/api/users/{user_b}")
        assert_status(unauth.status_code, ALLOWED_DENIALS, "unauthenticated-hub-api")
        self.record("unauthenticated-hub-api")
        unchanged = self.request(
            "GET",
            f"/user/{user_b}/api/contents/{quote(other_path, safe='')}",
            str(other["api_token"]),
        )
        assert_status(unchanged.status_code, {200}, "cross-user-mutation-witness")
        if self.json(unchanged).get("content") != other["marker"]:
            raise AssertionError("cross-user denied mutation changed B's file")
        model = self.request("GET", f"/hub/api/users/{user_b}", str(other["api_token"]))
        assert_status(model.status_code, {200}, "cross-user-state-witness")
        user_model = self.json(model)
        default_server = (
            user_model.get("servers", {}).get("")
            if isinstance(user_model, dict)
            else None
        )
        if (
            not isinstance(default_server, dict)
            or default_server.get("ready") is not True
        ):
            raise AssertionError("cross-user denial changed B's running server")
        self.record("cross-user-mutation-witness")

    async def terminal_check(
        self, username: str, token: str, other_username: str, other_token: str
    ) -> tuple[str, str]:
        import tornado.websocket
        from tornado.httpclient import HTTPClientError, HTTPRequest

        self.checkpoint("terminals")
        response = self.request(
            "POST", f"/user/{quote(username, safe='')}/api/terminals", token
        )
        assert_status(response.status_code, {200}, "terminal-create")
        name = self.json(response).get("name")
        if not isinstance(name, str) or not name:
            raise AssertionError("terminal name missing")
        minted = self.request(
            "POST",
            f"/hub/api/users/{quote(username, safe='')}/tokens",
            token,
            json_body={
                "expires_in": 60,
                "note": "integration-url-token",
                "scopes": [f"access:servers!user={username}"],
            },
        )
        assert_status(minted.status_code, {201}, "short-url-token")
        url_token = self.json(minted).get("token")
        if not isinstance(url_token, str) or not url_token:
            raise AssertionError("short-lived terminal token missing")
        forbidden = self.request(
            "GET", f"/hub/api/users/{quote(other_username, safe='')}", url_token
        )
        assert_status(forbidden.status_code, ALLOWED_DENIALS, "url-token-attenuation")
        own_model = self.request(
            "GET", f"/hub/api/users/{quote(username, safe='')}", url_token
        )
        assert_status(own_model.status_code, ALLOWED_DENIALS, "url-token-no-hub-model")
        no_escalation = self.request(
            "POST",
            f"/hub/api/users/{quote(username, safe='')}/tokens",
            url_token,
            json_body={"expires_in": 60, "note": "should-not-escalate"},
        )
        assert_status(no_escalation.status_code, ALLOWED_DENIALS, "url-token-no-mint")
        self.record("url-token-attenuation")
        marker = secrets.token_hex(16)
        expected = hashlib.sha256(bytes.fromhex(marker)).hexdigest()
        command = f"python3 -c 'import hashlib; print(hashlib.sha256(bytes.fromhex(\"{marker}\")).hexdigest())'\r"
        query = urlencode({"token": url_token})
        path = f"/user/{quote(username, safe='')}/terminals/websocket/{quote(name, safe='')}?{query}"
        request = HTTPRequest(
            self.ws_origin + path,
            headers={"Origin": self.origin},
            connect_timeout=WS_TIMEOUT,
            request_timeout=WS_TIMEOUT,
        )
        self.complete_checkpoint()
        self.checkpoint("websocket")
        ws = await tornado.websocket.websocket_connect(
            request, connect_timeout=WS_TIMEOUT
        )
        try:
            ws.write_message(json.dumps(["stdin", command]))
            deadline = time.monotonic() + WS_TIMEOUT
            seen = False
            stdout = ""
            while time.monotonic() < deadline:
                message = await asyncio.wait_for(
                    ws.read_message(), timeout=max(0.1, deadline - time.monotonic())
                )
                if message is None:
                    break
                try:
                    packet = json.loads(message)
                except (TypeError, ValueError):
                    continue
                if (
                    isinstance(packet, list)
                    and len(packet) >= 2
                    and packet[0] == "stdout"
                ):
                    stdout += str(packet[1])
                    if expected in stdout:
                        seen = True
                        break
            if not seen:
                raise AssertionError("terminal execution output marker missing")
        finally:
            ws.close()
        self.record("terminal-websocket-execution")
        # No browser cookies or bearer headers are supplied to this handshake.
        no_auth_path = f"/user/{quote(username, safe='')}/terminals/websocket/{quote(name, safe='')}"
        try:
            no_auth = await tornado.websocket.websocket_connect(
                HTTPRequest(
                    self.ws_origin + no_auth_path,
                    headers={"Origin": self.origin},
                    connect_timeout=WS_TIMEOUT,
                    request_timeout=WS_TIMEOUT,
                ),
                connect_timeout=WS_TIMEOUT,
            )
        except HTTPClientError as exc:
            if exc.code not in ALLOWED_DENIALS:
                raise ProbeFailure(
                    "unauthenticated WebSocket failed outside authorization",
                    classification="websocket-failed",
                    http_status=exc.code,
                ) from None
            self.record("unauthenticated-terminal-websocket")
        else:
            no_auth.close()
            raise AssertionError("unauthenticated terminal WebSocket was accepted")
        other_terminal = self.request(
            "POST", f"/user/{quote(other_username, safe='')}/api/terminals", other_token
        )
        assert_status(other_terminal.status_code, {200}, "other-terminal-create")
        other_name = self.json(other_terminal).get("name")
        if not isinstance(other_name, str):
            raise AssertionError("other-user terminal unavailable")
        cross_path = (
            f"/user/{quote(other_username, safe='')}/terminals/websocket/"
            f"{quote(other_name, safe='')}?{urlencode({'token': url_token})}"
        )
        try:
            cross_ws = await tornado.websocket.websocket_connect(
                HTTPRequest(
                    self.ws_origin + cross_path,
                    headers={"Origin": self.origin},
                    connect_timeout=WS_TIMEOUT,
                    request_timeout=WS_TIMEOUT,
                ),
                connect_timeout=WS_TIMEOUT,
            )
        except HTTPClientError as exc:
            if exc.code not in ALLOWED_DENIALS:
                raise ProbeFailure(
                    "cross-user WebSocket failed outside authorization",
                    classification="websocket-failed",
                    http_status=exc.code,
                ) from None
            self.record("cross-user-terminal-websocket")
        else:
            cross_ws.close()
            raise AssertionError("cross-user terminal WebSocket was accepted")
        removed = self.request(
            "DELETE",
            f"/user/{quote(other_username, safe='')}/api/terminals/{quote(other_name, safe='')}",
            other_token,
        )
        assert_status(removed.status_code, {204}, "other-terminal-cleanup")
        removed = self.request(
            "DELETE",
            f"/user/{quote(username, safe='')}/api/terminals/{quote(name, safe='')}",
            token,
        )
        assert_status(removed.status_code, {204}, "terminal-cleanup")
        self.complete_checkpoint()
        return name, marker

    def stop_respawn(self, identity: dict, path: str) -> None:
        username, token = identity["username"], identity["api_token"]
        user_path = f"/hub/api/users/{quote(username, safe='')}"
        stopped = self.request(
            "DELETE", user_path + "/server", token, timeout=STOP_TIMEOUT
        )
        assert_status(stopped.status_code, {202, 204}, "stop-server")
        deadline = time.monotonic() + STOP_TIMEOUT
        while time.monotonic() < deadline:
            response = self.request("GET", user_path, token)
            assert_status(response.status_code, {200}, "stop-poll")
            body = self.json(response)
            servers = body.get("servers", {}) if isinstance(body, dict) else {}
            if (
                isinstance(body, dict)
                and body.get("pending") is None
                and not servers.get("")
            ):
                break
            time.sleep(0.5)
        else:
            raise TimeoutError("server stop exceeded bounded deadline")
        self.spawn(username, token)
        read = self.request(
            "GET",
            f"/user/{quote(username, safe='')}/api/contents/{quote(path, safe='')}",
            token,
        )
        assert_status(read.status_code, {200}, "respawn-persistence")
        content = self.json(read)
        if (
            not isinstance(content, dict)
            or content.get("content") != identity["marker"]
        ):
            raise AssertionError("home marker did not survive stop/respawn")
        self.record("stop-respawn-home-persistence")

    def stop_for_cleanup(self, identity: dict) -> None:
        username, token = identity["username"], identity["api_token"]
        user_path = f"/hub/api/users/{quote(username, safe='')}"
        stopped = self.request(
            "DELETE", user_path + "/server", token, timeout=STOP_TIMEOUT
        )
        assert_status(stopped.status_code, {202, 204}, "cleanup-stop")
        deadline = time.monotonic() + STOP_TIMEOUT
        while time.monotonic() < deadline:
            response = self.request("GET", user_path, token)
            assert_status(response.status_code, {200}, "cleanup-stop-poll")
            body = self.json(response)
            servers = body.get("servers", {}) if isinstance(body, dict) else {}
            if (
                isinstance(body, dict)
                and body.get("pending") is None
                and not servers.get("")
            ):
                self.record("cleanup-user-server-stopped")
                return
            time.sleep(0.5)
        raise TimeoutError("fixture server cleanup exceeded deadline")

    def resumed_smoke(self, identities: list[dict], case: str) -> None:
        for identity in identities:
            username = quote(identity["username"], safe="")
            model = self.request(
                "GET", f"/hub/api/users/{username}", identity["api_token"]
            )
            assert_status(model.status_code, {200}, case + "-user-model")
            content = self.request(
                "GET",
                f"/user/{username}/api/contents/migration-{identity['marker'][:16]}.txt",
                identity["api_token"],
            )
            assert_status(content.status_code, {200}, case + "-home-file")
            if self.json(content).get("content") != identity["marker"]:
                raise AssertionError("resumed marker content changed")
            storage = self.request(
                "GET",
                f"/user/{username}/lab/storage?no_track_activity=1",
                identity["api_token"],
            )
            assert_status(storage.status_code, {200}, case + "-storage")
            self.record(case + "-resume")

    def run(self, stage: str, state: dict) -> dict[str, str]:
        self.checkpoint("readiness")
        self.ready()
        self.complete_checkpoint()
        self.prepare_identity(state, stage)
        identities = state["identities"]
        if stage == "cleanup":
            self.checkpoint("complete")
            for identity in identities:
                self.stop_for_cleanup(identity)
            self.complete_checkpoint()
            return self.results
        if stage in {"updater-smoke", "accepted-smoke"}:
            self.checkpoint("complete")
            self.resumed_smoke(identities, stage)
            self.complete_checkpoint()
            return self.results
        # Reuse the baseline-issued SPA token across migration/restore stages.
        for index, identity in enumerate(identities):
            self.checkpoint("spawn-a" if index == 0 else "spawn-b")
            self.spawn(identity["username"], identity["api_token"])
            self.complete_checkpoint()
        paths = []
        for index, identity in enumerate(identities):
            self.checkpoint("storage-a" if index == 0 else "storage-b")
            path, _ = self.own_lab_checks(
                identity["username"],
                identity["api_token"],
                identity["marker"],
                stage,
            )
            paths.append(path)
            self.complete_checkpoint()
        self.checkpoint("cross-user")
        self.deny_other_user(identities[0], identities[1], paths[1])
        self.complete_checkpoint()
        self.checkpoint("cookie-oauth")
        self.cookie_auth_boundary(identities)
        self.complete_checkpoint()
        asyncio.run(
            self.terminal_check(
                identities[0]["username"],
                identities[0]["api_token"],
                identities[1]["username"],
                identities[1]["api_token"],
            )
        )
        self.checkpoint("reconnect")
        self.stop_respawn(identities[0], paths[0])
        self.complete_checkpoint()
        self.checkpoint("complete")
        self.complete_checkpoint()
        return self.results


def make_state(probe: Probe, stage: str) -> dict:
    if stage == "baseline":
        identities = []
        for _ in range(2):
            raw = secrets.token_urlsafe(32)
            identities.append(
                {
                    "browser_token": raw,
                    "username": hashlib.sha256(raw.encode()).hexdigest()[:32],
                    "api_token": "",
                    "marker": secrets.token_hex(24),
                }
            )
        return {"identities": identities}
    raise ValueError("non-baseline state must be loaded from the private file")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument(
        "--stage",
        required=True,
        choices=(
            "baseline",
            "candidate",
            "restore",
            "updater-smoke",
            "accepted-smoke",
            "cleanup",
        ),
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--marker-file", required=True, type=Path)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument(
        "--acknowledge-disposable",
        action="store_true",
        help="confirm this Hub origin belongs only to the disposable fixture",
    )
    parser.add_argument("--stage-deadline", type=int, default=1200)
    args = parser.parse_args()
    if not RUN_ID_RE.fullmatch(args.run_id):
        parser.error("run-id must be 12..32 lowercase hex characters")
    if not 1 <= args.stage_deadline <= 1800:
        parser.error("stage deadline must be 1..1800 seconds")
    if sys.platform != "linux":
        parser.error("API stage requires an approved disposable Linux runner")
    if not args.acknowledge_disposable:
        parser.error(
            "--acknowledge-disposable is required before creating test identities"
        )
    probe: Probe | None = None
    try:
        # Client libraries must not emit exception URLs containing short tokens.
        logging.disable(logging.CRITICAL)
        origin, ws_origin = validate_origin(args.base)
        check_marker(args.marker_file, args.run_id)
        if (
            args.state_file.absolute().parent.resolve()
            != args.marker_file.absolute().parent.resolve()
        ):
            raise ValueError("state file must live beside the ownership marker")
        check_private_state(args.state_file, create=args.stage == "baseline")
        probe = Probe(origin, ws_origin, HTTP_TIMEOUT)
        if args.stage == "baseline":
            state = make_state(probe, args.stage)
            save_state(args.state_file, state)
        else:
            state = json.loads(args.state_file.read_text(encoding="utf-8"))

        def timeout_handler(_signum, _frame):
            raise TimeoutError("stage deadline exceeded")

        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(args.stage_deadline)
        try:
            results = probe.run(args.stage, state)
        finally:
            signal.alarm(0)
            probe.client.close()
            probe.anonymous_client.close()
            for session in probe.cookie_clients.values():
                session.close()
        if args.stage == "baseline":
            save_state(args.state_file, state)
        print(json.dumps({"stage": args.stage, "cases": results}, sort_keys=True))
        return 1 if "fail" in results.values() else 0
    except Exception as exc:
        # No exception details: they may contain raw auth headers, URLs, or tokens.
        print(failure_json(args.stage, probe, exc))
    return 1


def save_state(path: Path, state: dict) -> None:
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(state, sort_keys=True))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
