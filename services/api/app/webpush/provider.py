"""Where a Web Push message actually goes over the network (RFC 8030 delivery),
behind a Protocol per CLAUDE.md's "third-party services must be behind provider
interfaces" rule — ``app.webpush.service`` never imports ``httpx`` directly, the same
discipline ``app.weather.providers``/``app.mail.providers`` document for themselves.

**The SSRF allowlist.** A push subscription's ``endpoint`` is NOT trusted input in the
usual sense: it is stored because a browser handed it to the owner's own session, but
the Push API lets any script running on any page the owner's browser has ever visited
choose that URL (that is what ``pushManager.subscribe()`` returns — the *browser*
picks the push service, but a compromised or malicious page could in principle feed a
crafted "subscription" object to this system's own ``POST /v1/webpush/subscriptions``
if that route ever trusted an arbitrary origin, and defense in depth means this system
never dials an endpoint outside the known push-service vendors regardless of how it
arrived). Without this allowlist, this module would be a generic authenticated
HTTPS-POST-with-attacker-chosen-URL primitive sitting behind the owner's own API - the
textbook SSRF shape. ``ALLOWED_PUSH_HOSTS``/`is_allowed_push_host`` are the one gate
every send passes through first, before any network call and before any VAPID header
is even built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Protocol
from urllib.parse import urlsplit

import httpx

from app.logging import get_logger

logger = get_logger("app.webpush.provider")

#: Exact hostnames for the push services the browsers that implement the Push API use
#: today (verified against each vendor's own Web Push documentation): Chrome/Edge/
#: Chromium/Opera via Firebase Cloud Messaging, Firefox via Mozilla's autopush, Safari's
#: own web push relay. New Edge (Chromium-based) also uses FCM; the legacy
#: ``notify.windows.com``/``*.notify.windows.com`` (WNS) family is kept for any
#: EdgeHTML-era or Windows-native subscriber that still presents one.
_ALLOWED_EXACT_HOSTS: Final[frozenset[str]] = frozenset(
    {
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "push.services.mozilla.com",
        "web.push.apple.com",
        "notify.windows.com",
    }
)
#: WNS endpoints are sharded across subdomains (e.g. ``bn1.notify.windows.com``,
#: ``wns2-bn1p.notify.windows.com``) that this system cannot enumerate exactly; matched
#: by suffix rather than added one at a time as they are discovered.
_ALLOWED_HOST_SUFFIXES: Final[tuple[str, ...]] = (".notify.windows.com",)

#: Exported for tests and for a health/diagnostics surface that wants to show the
#: allowlist without reaching into the private suffix tuple above.
ALLOWED_PUSH_HOSTS: Final[frozenset[str]] = _ALLOWED_EXACT_HOSTS


def is_allowed_push_host(host: str) -> bool:
    normalised = host.strip().lower().rstrip(".")
    if not normalised:
        return False
    if normalised in _ALLOWED_EXACT_HOSTS:
        return True
    return any(normalised.endswith(suffix) for suffix in _ALLOWED_HOST_SUFFIXES)


def validate_push_endpoint(endpoint: str) -> None:
    """Raise :class:`PushError` (reason ``invalid_endpoint``) for anything that is not
    ``https://`` to a known push-service host. Called before every send AND before a
    subscription is ever stored (``app.webpush.service.subscribe``) — refusing at
    storage time means a bad endpoint never reaches the ladder's retry path at all.
    """
    parts = urlsplit(endpoint)
    if parts.scheme != "https":
        raise PushError(PushError.REASON_INVALID_ENDPOINT, "push endpoint must be https://")
    if parts.username or parts.password:
        raise PushError(PushError.REASON_INVALID_ENDPOINT, "push endpoint must not carry userinfo")
    host = parts.hostname or ""
    if not is_allowed_push_host(host):
        raise PushError(
            PushError.REASON_INVALID_ENDPOINT,
            f"push endpoint host {host!r} is not a known push service",
        )


class PushError(Exception):
    """A typed push-send failure. ``reason`` decides what ``app.webpush.service`` does
    next (expire the subscription, retry later, or give up on this attempt) — the same
    "reason class, never the raw exception" discipline ``app.weather.providers.
    WeatherError`` documents for itself."""

    #: RFC 8030 §7.3: the subscription is gone; the push service will never accept
    #: another message for it. Caller MUST delete the subscription (task brief).
    REASON_EXPIRED: Final = "expired"
    #: RFC 8030 §6.6: too many requests; ``retry_after_s`` carries the service's own
    #: ``Retry-After`` when it sent one.
    REASON_RATE_LIMITED: Final = "rate_limited"
    #: RFC 8030 §6.6 / RFC 8291: the encrypted body exceeded the service's own limit.
    #: Never retried as-is — the payload itself is the defect, not the delivery attempt.
    REASON_TOO_LARGE: Final = "too_large"
    #: 5xx: the push service's own fault. Worth a bounded retry.
    REASON_SERVER_ERROR: Final = "server_error"
    #: Refused before any network call — not an https URL, or not a known push host.
    REASON_INVALID_ENDPOINT: Final = "invalid_endpoint"
    REASON_TIMEOUT: Final = "timeout"
    #: Any other 4xx, or a transport error that is not a timeout.
    REASON_PROVIDER_ERROR: Final = "provider_error"

    def __init__(
        self,
        reason: str,
        message: str = "",
        *,
        status_code: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.status_code = status_code
        self.retry_after_s = retry_after_s


class PushProvider(Protocol):
    def send(
        self, *, endpoint: str, headers: dict[str, str], body: bytes, timeout_s: float
    ) -> None:
        """Raise :class:`PushError` for anything other than 201/202 Accepted."""
        ...


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # RFC 7231 also allows an HTTP-date; a bounded default covers it


@dataclass
class HttpPushProvider:
    """The real transport: one HTTPS POST per RFC 8030, through the SSRF allowlist."""

    default_timeout_s: float = 10.0

    def _client(self, timeout_s: float) -> httpx.Client:
        return httpx.Client(timeout=timeout_s)

    def send(
        self, *, endpoint: str, headers: dict[str, str], body: bytes, timeout_s: float | None = None
    ) -> None:
        validate_push_endpoint(endpoint)
        effective_timeout = timeout_s if timeout_s is not None else self.default_timeout_s
        with self._client(effective_timeout) as client:
            try:
                response = client.post(endpoint, headers=headers, content=body)
            except httpx.TimeoutException as exc:
                raise PushError(PushError.REASON_TIMEOUT, str(exc)) from exc
            except httpx.HTTPError as exc:
                raise PushError(PushError.REASON_PROVIDER_ERROR, str(exc)) from exc

        status = response.status_code
        if status in (201, 202):
            return
        if status in (404, 410):
            raise PushError(
                PushError.REASON_EXPIRED, f"push service returned {status}", status_code=status
            )
        if status == 413:
            raise PushError(
                PushError.REASON_TOO_LARGE,
                "push service rejected payload as too large",
                status_code=413,
            )
        if status == 429:
            raise PushError(
                PushError.REASON_RATE_LIMITED,
                "push service rate-limited this application server",
                status_code=429,
                retry_after_s=_parse_retry_after(response.headers.get("retry-after")),
            )
        if 500 <= status < 600:
            raise PushError(
                PushError.REASON_SERVER_ERROR, f"push service error {status}", status_code=status
            )
        raise PushError(
            PushError.REASON_PROVIDER_ERROR,
            f"unexpected push service status {status}",
            status_code=status,
        )


@dataclass
class FakePushProvider:
    """Deterministic fixture for tests (never imported by production — the same
    discipline ``app.weather.providers.FakeWeatherProvider`` documents for itself).

    ``responses`` maps an endpoint to either ``None`` (accepted) or a :class:`PushError`
    to raise. An endpoint with no entry defaults to accepted, so most tests need not
    register every subscription they create.
    """

    responses: dict[str, PushError | None] = field(default_factory=dict)
    calls: list[dict[str, object]] = field(default_factory=list)

    def send(
        self, *, endpoint: str, headers: dict[str, str], body: bytes, timeout_s: float
    ) -> None:
        validate_push_endpoint(endpoint)
        self.calls.append({"endpoint": endpoint, "headers": dict(headers), "body": bytes(body)})
        outcome = self.responses.get(endpoint)
        if outcome is not None:
            raise outcome


__all__ = [
    "ALLOWED_PUSH_HOSTS",
    "FakePushProvider",
    "HttpPushProvider",
    "PushError",
    "PushProvider",
    "is_allowed_push_host",
    "validate_push_endpoint",
]
