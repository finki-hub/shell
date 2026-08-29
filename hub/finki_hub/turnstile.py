"""Cloudflare Turnstile verification for the environment-creation path.

Enforcement is on whenever both ``TURNSTILE_SITEKEY`` and ``TURNSTILE_SECRET``
are set, and off when neither is. There is no third state: with the keys in
place a missing, rejected or unverifiable response refuses the creation. The
siteverify endpoint is called **exactly once** per attempt, and only on the
creation path -- resuming an existing environment never issues a challenge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol

SITEVERIFY_URL: Final[str] = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

SITEVERIFY_TIMEOUT_S: Final[float] = 5.0

Outcome = Literal["not-configured", "passed", "refused"]

logger = logging.getLogger(__name__)


class SiteverifyClient(Protocol):
    """The slice of ``httpx.AsyncClient`` this module uses."""

    async def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        timeout: float,  # ruff: ignore[async-function-with-timeout] - mirrors httpx.AsyncClient.post
    ) -> Any: ...  # ruff: ignore[any-type] - an httpx.Response, typed loosely on purpose


@dataclass(frozen=True, slots=True)
class TurnstileVerdict:
    """The outcome of one login's challenge evaluation."""

    outcome: Outcome
    reason: str | None = None
    error: Literal["challenge-required", "challenge-failed"] | None = None

    @property
    def allowed(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class SiteverifyResult:
    """The parsed siteverify response, or a transport failure."""

    success: bool
    error_codes: tuple[str, ...] = field(default_factory=tuple)
    transport_error: str | None = None


def _reason(result: SiteverifyResult) -> str:
    if result.transport_error is not None:
        return f"verify-unavailable: {result.transport_error}"
    if result.error_codes:
        return ",".join(result.error_codes)
    return "rejected"


async def siteverify(
    client: SiteverifyClient, secret: str, token: str
) -> SiteverifyResult:
    """Call Cloudflare siteverify once; never raise.

    ``remoteip`` is deliberately not sent: every request reaches the hub through
    two reverse proxies, so the address available here is not the user's.
    """
    try:
        response = await client.post(
            SITEVERIFY_URL,
            data={"secret": secret, "response": token},
            timeout=SITEVERIFY_TIMEOUT_S,
        )
        body = response.json()
    except Exception as exc:  # ruff: ignore[blind-except] - any transport or decode failure is one verdict
        return SiteverifyResult(success=False, transport_error=type(exc).__name__)
    if not isinstance(body, dict):
        return SiteverifyResult(success=False, transport_error="malformed-response")
    codes = body.get("error-codes") or []
    return SiteverifyResult(
        success=bool(body.get("success")),
        error_codes=tuple(str(code) for code in codes),
    )


async def check(
    secret: str, client: SiteverifyClient, token: str | None
) -> TurnstileVerdict:
    """Evaluate one creation attempt. Any failure refuses the creation."""
    if not token or not token.strip():
        logger.warning("turnstile refused: missing-token")
        return TurnstileVerdict(
            outcome="refused", reason="missing-token", error="challenge-required"
        )

    result = await siteverify(client, secret, token.strip())
    if not result.success:
        reason = _reason(result)
        logger.warning("turnstile refused: %s", reason)
        return TurnstileVerdict(
            outcome="refused", reason=reason, error="challenge-failed"
        )
    return TurnstileVerdict(outcome="passed")
