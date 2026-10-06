"""Nonprivileged contract tests for integration probe safety/assertion helpers."""

import importlib.util
import json
import unittest
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

from scripts.integration.probe import (
    ALLOWED_DENIALS,
    HTTP_TIMEOUT,
    SPAWN_TIMEOUT,
    STOP_TIMEOUT,
    WS_TIMEOUT,
    Probe,
    ProbeFailure,
    assert_status,
    failure_context,
    failure_json,
    failure_report,
    validate_origin,
)


class ProbeSafetyTests(unittest.TestCase):
    def test_accepts_only_explicit_private_loopback_origins(self) -> None:
        self.assertEqual(
            validate_origin("http://127.0.0.1:8000"),
            ("http://127.0.0.1:8000", "ws://127.0.0.1:8000"),
        )

    def test_rejects_lookalike_or_off_origin_hosts_and_paths(self) -> None:
        for origin in (
            "http://127.0.0.1.attacker.invalid:8000",
            "http://localhost.attacker.invalid:8000",
            "http://localhost:8000",
            "http://[::1]:8000",
            "http://user@localhost:8000",
            "https://127.0.0.1:8000",
            "http://127.0.0.1:80",
            "http://127.0.0.1:8080",
            "http://127.0.0.1:8000/hub",
            "http://127.0.0.1:8000/?next=https://attacker.invalid",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                validate_origin(origin)

    def test_status_assertion_does_not_accept_redirects_or_server_errors(self) -> None:
        assert_status(403, ALLOWED_DENIALS, "expected-denial")
        for status in (200, 302, 500):
            with self.subTest(status=status), self.assertRaises(AssertionError):
                assert_status(status, ALLOWED_DENIALS, "expected-denial")

    @unittest.skipUnless(
        importlib.util.find_spec("httpx"),
        "httpx is installed in the immutable Hub test image",
    )
    def test_probe_rejects_absolute_paths_and_never_follows_redirects(self) -> None:
        import httpx

        calls = []

        def handler(request):
            calls.append(request.url)
            return httpx.Response(
                302,
                headers={
                    "Location": "http://attacker.invalid/",
                    "Set-Cookie": "jupyterhub-hub-login=should-not-stick; Path=/",
                },
                request=request,
            )

        probe = Probe(
            "http://127.0.0.1:8000",
            "ws://127.0.0.1:8000",
            1,
            transport=httpx.MockTransport(handler),
        )
        try:
            with self.assertRaises(ValueError):
                probe.request("GET", "//attacker.invalid/path")
            with self.assertRaises(ProbeFailure) as raised:
                probe.request("GET", "/hub/api")
            self.assertEqual(
                failure_context("readiness", raised.exception),
                {
                    "checkpoint": "readiness",
                    "classification": "http-status",
                    "http_status": 302,
                },
            )
            self.assertEqual(len(calls), 1)
            self.assertFalse(probe.client.cookies.jar)
        finally:
            probe.client.close()

    @unittest.skipUnless(
        importlib.util.find_spec("httpx"),
        "httpx is installed in the locked Hub test environment",
    )
    def test_login_issues_isolated_cookies_and_private_oauth_rejects_off_origin(
        self,
    ) -> None:
        import httpx

        calls: list[tuple[str, str, str, str]] = []
        locations: list[str] = []

        def handler(request):
            parsed = urlsplit(str(request.url))
            cookies = request.headers.get("cookie", "")
            authorization = request.headers.get("authorization", "")
            calls.append((request.method, parsed.path, cookies, authorization))
            if parsed.path == "/hub/lab/login":
                payload = json.loads(request.content)
                username = sha256(payload["token"].encode()).hexdigest()[:32]
                expiry = (
                    (datetime.now(UTC) + timedelta(days=1))
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                body = {
                    "username": username,
                    "apiToken": "synthetic-token",
                    "tokenExpiresAt": expiry,
                    "created": False,
                    "maxTerminals": 4,
                }
                return httpx.Response(
                    200,
                    json=body,
                    headers={
                        "Cache-Control": "no-store",
                        "Set-Cookie": f"jupyterhub-hub-login={username}; Path=/hub/",
                    },
                    request=request,
                )
            if parsed.path == "/hub/":
                return httpx.Response(
                    302,
                    headers={"Location": "/hub/home"},
                    request=request,
                )
            if parsed.path == "/hub/home":
                if cookies:
                    return httpx.Response(200, text="authenticated", request=request)
                return httpx.Response(
                    302,
                    headers={"Location": "/hub/login?next=%2Fhub%2Fhome"},
                    request=request,
                )
            if parsed.path.startswith("/hub/api/oauth2/authorize"):
                values = parse_qs(parsed.query)
                callback = values["redirect_uri"][0]
                client_id = values["client_id"][0]
                prefix = "jupyterhub-user-"
                if not client_id.startswith(prefix):
                    return httpx.Response(400, request=request)
                username = client_id[len(prefix) :]
                registered_callback = f"/user/{username}/oauth_callback"
                if callback != registered_callback:
                    return httpx.Response(400, request=request)
                location = callback + "?code=synthetic-code&state=" + values["state"][0]
                locations.append(location)
                return httpx.Response(
                    302,
                    headers={"Location": location},
                    request=request,
                )
            if parsed.path.startswith("/hub/api/users/"):
                return httpx.Response(403, request=request)
            return httpx.Response(404, request=request)

        probe = Probe(
            "http://127.0.0.1:8000",
            "ws://127.0.0.1:8000",
            1,
            transport=httpx.MockTransport(handler),
        )
        try:
            identities = []
            for raw in ("browser-a", "browser-b"):
                login = probe.login(raw, "resume")
                identities.append({"username": login["username"]})
            probe.cookie_auth_boundary(identities)
            self.assertEqual(len(probe.cookie_clients), 2)
            self.assertTrue(
                all(
                    "hub-login" in cookie.name
                    for client in probe.cookie_clients.values()
                    for cookie in client.cookies.jar
                )
            )
            self.assertFalse(probe.client.cookies.jar)
            self.assertFalse(probe.anonymous_client.cookies.jar)
            self.assertIn(("GET", "/hub/home", "", ""), calls)
            self.assertIn(
                (
                    "GET",
                    "/hub/home",
                    "jupyterhub-hub-login=" + identities[0]["username"],
                    "",
                ),
                calls,
            )
            self.assertTrue(
                all(location.startswith("/user/") for location in locations)
            )
            self.assertTrue(
                any(
                    method == "GET"
                    and path.endswith("/oauth2/authorize")
                    and "hub-login" in cookie
                    for method, path, cookie, _auth in calls
                )
            )
        finally:
            probe.client.close()
            probe.anonymous_client.close()
            for session in probe.cookie_clients.values():
                session.close()

    @unittest.skipUnless(
        importlib.util.find_spec("httpx"),
        "httpx is installed in the locked Hub test environment",
    )
    def test_missing_nonbaseline_marker_fails_without_put(self) -> None:
        import httpx

        methods: list[str] = []

        def handler(request):
            methods.append(request.method)
            if request.url.path.endswith("/lab/storage"):
                return httpx.Response(
                    200,
                    json={
                        "bytesUsed": 1,
                        "bytesLimit": 2,
                        "inodesUsed": 1,
                        "inodesLimit": 2,
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        probe = Probe(
            "http://127.0.0.1:8000",
            "ws://127.0.0.1:8000",
            1,
            transport=httpx.MockTransport(handler),
        )
        try:
            with self.assertRaisesRegex(AssertionError, "baseline marker disappeared"):
                probe.own_lab_checks("user-a", "token", "abc123", "candidate")
            self.assertEqual(methods, ["GET", "GET"])
            self.assertNotIn("PUT", methods)
        finally:
            probe.client.close()
            probe.anonymous_client.close()

    @unittest.skipUnless(
        importlib.util.find_spec("httpx"),
        "httpx is installed in the locked Hub test environment",
    )
    def test_failure_report_identifies_http_checkpoint_without_private_values(
        self,
    ) -> None:
        import httpx

        def handler(request):
            if request.url.path == "/hub/lab/ready":
                return httpx.Response(200, json={"ready": True}, request=request)
            if request.url.path == "/hub/lab/login":
                return httpx.Response(
                    503,
                    text="token-secret https://private.invalid/?api=secret",
                    request=request,
                )
            return httpx.Response(404, request=request)

        probe = Probe(
            "http://127.0.0.1:8000",
            "ws://127.0.0.1:8000",
            1,
            transport=httpx.MockTransport(handler),
        )
        state = {
            "identities": [
                {
                    "browser_token": "browser-token-secret",
                    "api_token": "api-token-secret",
                    "username": sha256(b"browser-token-secret").hexdigest()[:32],
                    "marker": "marker-secret",
                },
                {
                    "browser_token": "browser-token-b",
                    "api_token": "api-token-b",
                    "username": sha256(b"browser-token-b").hexdigest()[:32],
                    "marker": "marker-b",
                },
            ]
        }
        try:
            with self.assertRaises(ProbeFailure) as raised:
                probe.run("candidate", state)
            emitted = failure_json("candidate", probe, raised.exception)
            report = json.loads(emitted)
            self.assertLessEqual(len(emitted.encode("utf-8")), 64 * 1024)
            self.assertEqual(
                set(report),
                {"stage", "status", "failure_context", "completed_cases"},
            )
            self.assertEqual(report["stage"], "candidate")
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["completed_cases"], ["readiness"])
            self.assertEqual(
                report["failure_context"],
                {
                    "checkpoint": "login-a",
                    "classification": "http-status",
                    "http_status": 503,
                },
            )
            for private_value in (
                "browser-token-secret",
                "api-token-secret",
                "marker-secret",
                "token-secret",
                "private.invalid",
            ):
                self.assertNotIn(private_value, emitted)
        finally:
            probe.client.close()
            probe.anonymous_client.close()
            for session in probe.cookie_clients.values():
                session.close()

    @unittest.skipUnless(
        importlib.util.find_spec("httpx"),
        "httpx is installed in the locked Hub test environment",
    )
    def test_request_timeout_and_transport_are_classified_without_exception_text(
        self,
    ) -> None:
        import httpx

        errors = (
            (httpx.ReadTimeout, "request-timeout"),
            (httpx.ConnectError, "request-transport"),
        )
        for error_type, classification in errors:
            with self.subTest(classification=classification):

                def handler(request, exception_type=error_type):
                    raise exception_type(
                        "token-secret https://private.invalid/?token=secret",
                        request=request,
                    )

                probe = Probe(
                    "http://127.0.0.1:8000",
                    "ws://127.0.0.1:8000",
                    1,
                    transport=httpx.MockTransport(handler),
                )
                try:
                    with self.assertRaises(ProbeFailure) as raised:
                        probe.request("GET", "/hub/api", "api-token-secret")
                    report = failure_report("restore", probe, raised.exception)
                    self.assertEqual(
                        report["failure_context"]["classification"], classification
                    )
                    self.assertNotIn("http_status", report["failure_context"])
                    self.assertNotIn("token-secret", json.dumps(report))
                    self.assertNotIn("private.invalid", json.dumps(report))
                finally:
                    probe.client.close()
                    probe.anonymous_client.close()

    def test_websocket_context_only_includes_bounded_http_status(self) -> None:
        class WebSocketFailure(RuntimeError):
            code = 403
            errno = 9999

        context = failure_context(
            "websocket", WebSocketFailure("api-token-secret private.invalid")
        )
        self.assertEqual(
            context,
            {
                "checkpoint": "websocket",
                "classification": "websocket-failed",
                "http_status": 403,
            },
        )
        self.assertNotIn("api-token-secret", json.dumps(context))

    def test_failure_context_uses_closed_enums_and_bounded_errno(self) -> None:
        context = failure_context(
            "untrusted-checkpoint", OSError(13, "private path and token-secret")
        )
        self.assertEqual(
            context,
            {
                "checkpoint": "readiness",
                "classification": "probe-error",
                "errno": 13,
            },
        )
        assertion = failure_context(
            "cookie-oauth", AssertionError("token-secret https://private.invalid")
        )
        self.assertEqual(
            assertion,
            {
                "checkpoint": "cookie-oauth",
                "classification": "assertion-failed",
            },
        )
        self.assertNotIn("token-secret", json.dumps(assertion))

    @unittest.skipUnless(
        importlib.util.find_spec("httpx"),
        "httpx is installed in the locked Hub test environment",
    )
    def test_unknown_checkpoint_cannot_enter_failure_report(self) -> None:
        import httpx

        probe = Probe(
            "http://127.0.0.1:8000",
            "ws://127.0.0.1:8000",
            1,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, request=request)
            ),
        )
        try:
            with self.assertRaises(ProbeFailure) as raised:
                probe.checkpoint("private-user-checkpoint")
            report = failure_report("candidate", probe, raised.exception)
            self.assertEqual(
                report["failure_context"],
                {"checkpoint": "readiness", "classification": "probe-error"},
            )
            self.assertNotIn("private-user-checkpoint", json.dumps(report))
        finally:
            probe.client.close()
            probe.anonymous_client.close()

    def test_terminal_status_failures_have_only_allowlisted_assertion_labels(
        self,
    ) -> None:
        assert_status(200, {200}, "terminal-create")
        reports = {}
        for label in ("url-token-attenuation", "url-token-no-mint"):
            with self.subTest(label=label), self.assertRaises(ProbeFailure) as raised:
                assert_status(200, ALLOWED_DENIALS, label)
            reports[label] = failure_context("terminals", raised.exception)
        self.assertEqual(
            reports["url-token-attenuation"],
            {
                "checkpoint": "terminals",
                "classification": "http-status",
                "http_status": 200,
                "assertion": "url-token-attenuation",
            },
        )
        self.assertEqual(reports["url-token-no-mint"]["assertion"], "url-token-no-mint")

        with self.assertRaises(ProbeFailure) as untrusted:
            assert_status(200, {201}, "short-url-token api-token-secret")
        failure = failure_json("candidate", None, untrusted.exception)
        self.assertNotIn("api-token-secret", failure)
        self.assertNotIn("short-url-token api-token-secret", failure)
        self.assertNotIn('"assertion"', failure)
        self.assertLessEqual(len(failure.encode("utf-8")), 64 * 1024)

    def test_runtime_deadlines_are_bounded_by_the_contract(self) -> None:
        self.assertLessEqual(HTTP_TIMEOUT, 15)
        self.assertLessEqual(SPAWN_TIMEOUT, 210)
        self.assertLessEqual(STOP_TIMEOUT, 60)
        self.assertLessEqual(WS_TIMEOUT, 10)


if __name__ == "__main__":
    unittest.main()
