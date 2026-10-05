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
    assert_status,
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
            with self.assertRaises(AssertionError):
                probe.request("GET", "/hub/api")
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
                if cookies:
                    return httpx.Response(200, text="authenticated", request=request)
                return httpx.Response(
                    302,
                    headers={"Location": "/hub/login?next=%2Fhub%2F"},
                    request=request,
                )
            if parsed.path.startswith("/hub/api/oauth2/authorize"):
                values = parse_qs(parsed.query)
                callback = values["redirect_uri"][0]
                if urlsplit(callback).netloc == "attacker.invalid":
                    return httpx.Response(400, request=request)
                return httpx.Response(
                    302,
                    headers={
                        "Location": (
                            callback
                            + "?code=synthetic-code&state="
                            + values["state"][0]
                        )
                    },
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
            self.assertIn(("GET", "/hub/", "", ""), calls)
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

    def test_runtime_deadlines_are_bounded_by_the_contract(self) -> None:
        self.assertLessEqual(HTTP_TIMEOUT, 15)
        self.assertLessEqual(SPAWN_TIMEOUT, 210)
        self.assertLessEqual(STOP_TIMEOUT, 60)
        self.assertLessEqual(WS_TIMEOUT, 10)


if __name__ == "__main__":
    unittest.main()
