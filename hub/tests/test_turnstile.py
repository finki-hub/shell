from __future__ import annotations

import asyncio
from typing import Any

import pytest

from finki_hub.turnstile import (
    SITEVERIFY_TIMEOUT_S,
    SITEVERIFY_URL,
    check,
)

LIVE_SECRET = "0xLIVESECRET"


class _Response:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeHttp:
    """A siteverify client that records every call it is asked to make."""

    def __init__(self, payload: Any = None, raises: Exception | None = None) -> None:
        self._payload = payload if payload is not None else {"success": True}
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, *, data: dict[str, str], timeout: float) -> Any:
        self.calls.append({"url": url, "data": data, "timeout": timeout})
        if self._raises is not None:
            raise self._raises
        return _Response(self._payload)


def run_check(http: FakeHttp, token: str | None) -> Any:
    return asyncio.run(check(LIVE_SECRET, http, token))


def test_a_pass_calls_siteverify_exactly_once_without_the_remote_address() -> None:
    http = FakeHttp({"success": True, "hostname": "shell.finki.ukim.mk"})

    verdict = run_check(http, "  response-token  ")

    assert verdict.outcome == "passed"
    assert verdict.allowed is True
    assert len(http.calls) == 1
    assert http.calls[0]["url"] == SITEVERIFY_URL
    assert http.calls[0]["timeout"] == SITEVERIFY_TIMEOUT_S
    # Every request arrives through two reverse proxies, so no remoteip is sent.
    assert http.calls[0]["data"] == {
        "secret": LIVE_SECRET,
        "response": "response-token",
    }


@pytest.mark.parametrize("token", [None, "", "   "])
def test_a_missing_response_is_challenge_required(token: str | None) -> None:
    http = FakeHttp()

    verdict = run_check(http, token)

    assert (verdict.outcome, verdict.error, verdict.reason) == (
        "refused",
        "challenge-required",
        "missing-token",
    )
    assert http.calls == []


def test_a_rejected_response_fails_closed() -> None:
    http = FakeHttp({"success": False, "error-codes": ["invalid-input-response"]})

    verdict = run_check(http, "response-token")

    assert (verdict.outcome, verdict.error) == ("refused", "challenge-failed")
    assert verdict.reason == "invalid-input-response"
    assert len(http.calls) == 1


def test_a_rejection_without_codes_still_fails_closed() -> None:
    verdict = run_check(FakeHttp({"success": False}), "response-token")

    assert verdict.error == "challenge-failed"
    assert verdict.reason == "rejected"


def test_an_unreachable_siteverify_fails_closed() -> None:
    http = FakeHttp(raises=TimeoutError("siteverify"))

    verdict = run_check(http, "response-token")

    assert (verdict.outcome, verdict.error) == ("refused", "challenge-failed")
    assert verdict.reason == "verify-unavailable: TimeoutError"


def test_a_malformed_body_fails_closed() -> None:
    http = FakeHttp(payload=["not", "an", "object"])

    verdict = run_check(http, "response-token")

    assert verdict.error == "challenge-failed"
    assert verdict.reason == "verify-unavailable: malformed-response"


def test_the_hostname_is_not_checked_here() -> None:
    # Cloudflare already binds a sitekey to its allowed hostnames; the hub does
    # not keep a second list.
    verdict = run_check(FakeHttp({"success": True, "hostname": "anything"}), "r")

    assert verdict.outcome == "passed"
