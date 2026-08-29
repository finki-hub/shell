from __future__ import annotations

import json

import pytest

from shell_hub.handlers import (
    MAX_BODY_BYTES,
    LoginError,
    LoginRequest,
    parse_login_body,
)

TOKEN = "AbC012_-" * 4


# --- body parsing ------------------------------------------------------------


def test_a_well_formed_body_parses() -> None:
    raw = json.dumps({"token": TOKEN, "intent": "create", "turnstile": "r"}).encode()

    assert parse_login_body(raw) == LoginRequest(TOKEN, "create", "r")


def test_intent_defaults_to_resume() -> None:
    assert parse_login_body(json.dumps({"token": TOKEN}).encode()).intent == "resume"


def test_an_oversized_body_is_413() -> None:
    raw = json.dumps({"token": TOKEN, "pad": "x" * MAX_BODY_BYTES}).encode()

    with pytest.raises(LoginError) as refusal:
        parse_login_body(raw)

    assert (refusal.value.status, refusal.value.error) == (413, "payload-too-large")


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"not json", "invalid-json"),
        (b'["a", "list"]', "not-an-object"),
        (b'{"token": 42}', "invalid-token"),
        (b'{"token": "short"}', "invalid-token"),
        (b'{"token": "' + TOKEN.encode() + b'", "intent": "delete"}', "invalid-intent"),
        (b'{"token": "' + TOKEN.encode() + b'", "turnstile": 7}', "invalid-turnstile"),
    ],
)
def test_a_malformed_body_is_400(raw: bytes, reason: str) -> None:
    with pytest.raises(LoginError) as refusal:
        parse_login_body(raw)

    assert refusal.value.status == 400
    assert refusal.value.reason == reason
    assert refusal.value.to_json() == {"error": "bad-request", "reason": reason}
