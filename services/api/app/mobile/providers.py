"""Push provider seams (constitution: third-party services behind interfaces).

One Protocol, one deterministic offline fake, three real adapter skeletons —
the same shape as `app/voice/providers.py`, for the same reason: a vendor path
must exist and be inspectable without ever being reachable from the test suite.

Testing discipline (deterministic + offline):

- ``FakePushProvider`` never touches the network. It records every delivery in
  a bounded in-process log, which is what the suite and the headless reference
  client read instead of an OS push socket. It is the whole "push notification
  for artifact ready" acceptance path on a machine with no device.
- Each real adapter splits into a pure ``build_request()`` (unit-testable
  request construction, zero I/O) and a lazily-imported ``_send()`` that uses
  httpx. With no credentials the *send* path raises
  ``MobileErrorClass.PROVIDER_AUTH_MISSING`` and performs no I/O, so the vendor
  path is present but inert. Provisioning FCM/APNs/VAPID credentials is an
  owner action and MUST NOT run in the test suite; the unit tests mock the
  transport and assert on the built request only.

**Where the live token lives.** The frozen schema stores only ``token_hash``.
A provider therefore keeps the plaintext token it was handed at registration
time in process memory, keyed by registration id, and the database can never
hand an attacker something replayable. The cost is explicit: an API restart
drops the live tokens and the client must re-register — which is exactly what a
native client does anyway when the OS rotates its token.
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from app.mobile.config import (
    ApnsCredentials,
    FcmCredentials,
    WebPushCredentials,
    delivery_log_size,
)
from app.mobile.errors import MobileError, MobileErrorClass

#: Provider tokens are opaque vendor strings; bound them so a client cannot
#: push an unbounded blob into memory or into a request body.
MIN_TOKEN_CHARS = 8
MAX_TOKEN_CHARS = 4096

#: Notification payloads are deliberately tiny: the constitution's "notify
#: briefly and wait" rule means a push carries readiness, never the report.
MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 400


def hash_token(token: str) -> str:
    """SHA-256 of a provider token. Full-entropy vendor strings, so a password
    KDF buys nothing here (same reasoning as ADR-0027 for session tokens)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def fingerprint(token: str) -> str:
    """Short, non-reversible marker safe to put in a log or an audit detail."""
    return hash_token(token)[:12]


def utc_now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ value types


@dataclass(frozen=True, slots=True)
class PushCapabilities:
    """What a transport can actually do, so the orchestrator can query rather
    than assume (same contract style as the browser and voice capability
    declarations)."""

    name: str
    platforms: tuple[str, ...]
    #: Can the OS wake a terminated/backgrounded app for this message?
    background_delivery: bool
    #: Does the transport carry a structured data payload alongside the alert?
    data_payload: bool
    #: Can we ask the OS for high priority / time-sensitive delivery?
    priority_control: bool
    #: Can we replace an earlier unread notification instead of stacking?
    collapse: bool
    max_payload_bytes: int
    requires_credentials: bool

    def supports_platform(self, platform: str) -> bool:
        return platform.strip().lower() in {p.lower() for p in self.platforms}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "platforms": list(self.platforms),
            "background_delivery": self.background_delivery,
            "data_payload": self.data_payload,
            "priority_control": self.priority_control,
            "collapse": self.collapse,
            "max_payload_bytes": self.max_payload_bytes,
            "requires_credentials": self.requires_credentials,
        }


@dataclass(frozen=True, slots=True)
class PushMessage:
    """A readiness notification. Short by construction (constitution §3)."""

    title: str
    body: str
    data: dict[str, str] = field(default_factory=dict)
    #: Replaces an earlier unread message with the same key (one artifact, one
    #: notification, however many times the workflow retries).
    collapse_key: str = ""
    priority: str = "high"

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise MobileError(MobileErrorClass.VALIDATION_ERROR, "push title is empty")
        if len(self.title) > MAX_TITLE_CHARS:
            raise MobileError(
                MobileErrorClass.VALIDATION_ERROR,
                f"push title exceeds {MAX_TITLE_CHARS} characters",
            )
        if len(self.body) > MAX_BODY_CHARS:
            raise MobileError(
                MobileErrorClass.VALIDATION_ERROR,
                f"push body exceeds {MAX_BODY_CHARS} characters",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "data": dict(self.data),
            "collapse_key": self.collapse_key,
            "priority": self.priority,
        }


@dataclass(frozen=True, slots=True)
class RegisteredToken:
    """What `register()` returns: never the token, only what may be persisted."""

    registration_key: str
    provider: str
    token_hash: str
    token_fingerprint: str


@dataclass(frozen=True, slots=True)
class PushDelivery:
    """One recorded delivery attempt. Carries no token, ever."""

    registration_key: str
    provider: str
    message: PushMessage
    delivered_at: datetime
    token_fingerprint: str
    status: str = "delivered"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "registration_key": self.registration_key,
            "provider": self.provider,
            "status": self.status,
            "detail": self.detail,
            "delivered_at": self.delivered_at.isoformat(),
            "token_fingerprint": self.token_fingerprint,
            **self.message.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PushRequest:
    """A fully-built HTTP request for a real adapter, produced without any I/O.

    Unit tests assert on this to prove each adapter targets the correct vendor
    endpoint / headers / body without ever opening a socket.
    """

    method: str
    url: str
    headers: dict[str, str]
    json_body: dict[str, Any] | None = None
    data: bytes | None = None


# ---------------------------------------------------------------------- Protocol


@runtime_checkable
class PushProvider(Protocol):
    """The seam every push transport implements."""

    name: str

    def capabilities(self) -> PushCapabilities: ...

    def register(
        self, *, registration_key: str, token: str, platform: str = ""
    ) -> RegisteredToken: ...

    def deliver(self, *, registration_key: str, message: PushMessage) -> PushDelivery: ...

    def invalidate(self, *, registration_key: str, reason: str = "") -> bool: ...


def validate_token(token: str, *, provider: str) -> str:
    token = (token or "").strip()
    if len(token) < MIN_TOKEN_CHARS or len(token) > MAX_TOKEN_CHARS:
        raise MobileError(
            MobileErrorClass.VALIDATION_ERROR,
            f"push token must be {MIN_TOKEN_CHARS}..{MAX_TOKEN_CHARS} characters",
            provider=provider,
        )
    return token


# ============================================================ deterministic fake


class FakePushProvider:
    """Offline push fake: records deliveries in-process, never opens a socket.

    This is the acceptance path on a machine with no device. A "delivered" push
    is a row in `deliveries`, which the reference client polls through
    `GET /v1/mobile/notifications` the way a native app would receive an OS
    notification. It is honest about what it is: proof that the *server* side
    fires the right message to the right live registration at the right moment,
    and nothing at all about whether Apple or Google would carry it.
    """

    name = "fake"

    def __init__(self, *, log_size: int | None = None) -> None:
        self._live: dict[str, str] = {}
        self._invalid: dict[str, str] = {}
        self.deliveries: deque[PushDelivery] = deque(
            maxlen=log_size if log_size is not None else delivery_log_size()
        )

    @property
    def configured(self) -> bool:
        """Always: the fake needs no owner action to work."""
        return True

    def capabilities(self) -> PushCapabilities:
        return PushCapabilities(
            name=self.name,
            platforms=("ios", "android", "web", "headless"),
            background_delivery=True,
            data_payload=True,
            priority_control=True,
            collapse=True,
            max_payload_bytes=4096,
            requires_credentials=False,
        )

    def register(
        self, *, registration_key: str, token: str, platform: str = ""
    ) -> RegisteredToken:
        token = validate_token(token, provider=self.name)
        self._live[registration_key] = token
        self._invalid.pop(registration_key, None)
        return RegisteredToken(
            registration_key=registration_key,
            provider=self.name,
            token_hash=hash_token(token),
            token_fingerprint=fingerprint(token),
        )

    def deliver(self, *, registration_key: str, message: PushMessage) -> PushDelivery:
        token = self._live.get(registration_key)
        if token is None:
            raise MobileError(
                MobileErrorClass.PUSH_TOKEN_INVALID,
                "no live token for this registration (re-register from the client)",
                provider=self.name,
                details={"registration_key": registration_key},
            )
        delivery = PushDelivery(
            registration_key=registration_key,
            provider=self.name,
            message=message,
            delivered_at=utc_now(),
            token_fingerprint=fingerprint(token),
        )
        # Collapse: one artifact yields one unread notification however many
        # times a retrying workflow announces it.
        if message.collapse_key:
            for existing in list(self.deliveries):
                if (
                    existing.registration_key == registration_key
                    and existing.message.collapse_key == message.collapse_key
                ):
                    self.deliveries.remove(existing)
        self.deliveries.append(delivery)
        return delivery

    def invalidate(self, *, registration_key: str, reason: str = "") -> bool:
        existed = self._live.pop(registration_key, None) is not None
        if existed:
            self._invalid[registration_key] = reason or "invalidated"
        return existed

    # ------------------------------------------------------------- test helpers

    def has_live_token(self, registration_key: str) -> bool:
        return registration_key in self._live

    def deliveries_for(self, registration_key: str) -> list[PushDelivery]:
        return [d for d in self.deliveries if d.registration_key == registration_key]

    def clear(self) -> None:
        self._live.clear()
        self._invalid.clear()
        self.deliveries.clear()


# ============================================================ real adapters
#
# Each real adapter reads its credentials from the environment (see
# `app/mobile/config.py`). With no credentials the *real send path* raises
# PROVIDER_AUTH_MISSING and does NO I/O. `build_request()` is pure and
# unit-tested; `_send()` lazily imports httpx and is only reached once an owner
# has provisioned credentials.


def _require(configured: bool, provider: str, env_hint: str) -> None:
    if not configured:
        raise MobileError(
            MobileErrorClass.PROVIDER_AUTH_MISSING,
            f"{provider}: no push credentials configured (owner action: set {env_hint})",
            provider=provider,
        )


def _send(req: PushRequest, *, timeout_s: float, provider: str) -> Any:
    """Lazily-imported real HTTP send. Never reached without credentials; unit
    tests mock the transport so no real network call happens."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx present in dev group
        raise MobileError(
            MobileErrorClass.OPTIONAL_DEPENDENCY_MISSING,
            "httpx is required for real push provider calls",
            provider=provider,
        ) from exc
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.request(
                req.method, req.url, headers=req.headers, json=req.json_body, content=req.data
            )
            resp.raise_for_status()
            return resp
    except httpx.TimeoutException as exc:
        raise MobileError(
            MobileErrorClass.TIMEOUT,
            f"{provider}: request timed out",
            provider=provider,
            retryable=True,
        ) from exc
    except httpx.HTTPStatusError as exc:
        # 404/410 is the universal "this token is dead" signal across FCM and
        # APNs; it is NOT retryable — the registration must be invalidated.
        status = exc.response.status_code
        if status in (404, 410):
            raise MobileError(
                MobileErrorClass.PUSH_TOKEN_INVALID,
                f"{provider}: provider reports the token is no longer valid",
                provider=provider,
                details={"status_code": status},
            ) from exc
        raise MobileError(
            MobileErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{provider}: HTTP {status}",
            provider=provider,
            retryable=True,
            details={"status_code": status},
        ) from exc
    except httpx.HTTPError as exc:
        raise MobileError(
            MobileErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{provider}: {type(exc).__name__}: {exc}",
            provider=provider,
            retryable=True,
        ) from exc


class _RealPushProviderBase:
    """Shared plumbing for the inert vendor adapters.

    The live token map is the same design as the fake: the database has only a
    hash, so the plaintext token the client presented at registration time
    lives here, in process memory, and nowhere else.
    """

    name = "real"

    def __init__(self, *, timeout_s: float = 15.0) -> None:
        self._live: dict[str, str] = {}
        self._timeout_s = timeout_s
        self._creds: Any = None

    @property
    def configured(self) -> bool:
        """Whether an owner has provisioned this transport's credentials."""
        return bool(getattr(self._creds, "configured", False))

    def register(
        self, *, registration_key: str, token: str, platform: str = ""
    ) -> RegisteredToken:
        token = validate_token(token, provider=self.name)
        self._live[registration_key] = token
        return RegisteredToken(
            registration_key=registration_key,
            provider=self.name,
            token_hash=hash_token(token),
            token_fingerprint=fingerprint(token),
        )

    def invalidate(self, *, registration_key: str, reason: str = "") -> bool:
        return self._live.pop(registration_key, None) is not None

    def _token(self, registration_key: str) -> str:
        token = self._live.get(registration_key)
        if token is None:
            raise MobileError(
                MobileErrorClass.PUSH_TOKEN_INVALID,
                "no live token for this registration (re-register from the client)",
                provider=self.name,
                details={"registration_key": registration_key},
            )
        return token

    def _delivered(
        self, registration_key: str, token: str, message: PushMessage, detail: str
    ) -> PushDelivery:
        return PushDelivery(
            registration_key=registration_key,
            provider=self.name,
            message=message,
            delivered_at=utc_now(),
            token_fingerprint=fingerprint(token),
            detail=detail,
        )


class FcmPushProvider(_RealPushProviderBase):
    """Firebase Cloud Messaging HTTP v1 (Android + web). INERT without creds."""

    name = "fcm"
    _BASE = "https://fcm.googleapis.com/v1/projects"

    def __init__(
        self, credentials: FcmCredentials | None = None, *, timeout_s: float = 15.0
    ) -> None:
        super().__init__(timeout_s=timeout_s)
        self._creds = credentials or FcmCredentials.from_env()

    def capabilities(self) -> PushCapabilities:
        return PushCapabilities(
            name=self.name,
            platforms=("android", "web"),
            background_delivery=True,
            data_payload=True,
            priority_control=True,
            collapse=True,
            max_payload_bytes=4096,
            requires_credentials=True,
        )

    def build_request(self, *, token: str, message: PushMessage) -> PushRequest:
        body: dict[str, Any] = {
            "message": {
                "token": token,
                "notification": {"title": message.title, "body": message.body},
                "data": dict(message.data),
                "android": {
                    "priority": "HIGH" if message.priority == "high" else "NORMAL",
                    **({"collapse_key": message.collapse_key} if message.collapse_key else {}),
                },
            }
        }
        return PushRequest(
            method="POST",
            url=f"{self._BASE}/{self._creds.project_id}/messages:send",
            headers={
                "Authorization": f"Bearer {self._creds.access_token}",
                "Content-Type": "application/json",
            },
            json_body=body,
        )

    def deliver(self, *, registration_key: str, message: PushMessage) -> PushDelivery:
        token = self._token(registration_key)
        _require(
            self._creds.configured,
            self.name,
            "PAGENTOS_PUSH_FCM_PROJECT_ID + PAGENTOS_PUSH_FCM_ACCESS_TOKEN",
        )
        req = self.build_request(token=token, message=message)
        started = time.perf_counter()
        resp = _send(req, timeout_s=self._timeout_s, provider=self.name)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        name = ""
        try:
            name = str(resp.json().get("name", ""))
        except Exception:  # noqa: BLE001 - a body we cannot parse is not a failure
            name = ""
        return self._delivered(registration_key, token, message, f"{name} ({latency_ms} ms)")


class ApnsPushProvider(_RealPushProviderBase):
    """Apple Push Notification service, HTTP/2 token auth. INERT without creds."""

    name = "apns"
    _PROD = "https://api.push.apple.com/3/device"
    _SANDBOX = "https://api.sandbox.push.apple.com/3/device"

    def __init__(
        self, credentials: ApnsCredentials | None = None, *, timeout_s: float = 15.0
    ) -> None:
        super().__init__(timeout_s=timeout_s)
        self._creds = credentials or ApnsCredentials.from_env()

    def capabilities(self) -> PushCapabilities:
        return PushCapabilities(
            name=self.name,
            platforms=("ios", "ipados", "macos"),
            background_delivery=True,
            data_payload=True,
            priority_control=True,
            collapse=True,
            max_payload_bytes=4096,
            requires_credentials=True,
        )

    def build_request(self, *, token: str, message: PushMessage) -> PushRequest:
        base = self._SANDBOX if self._creds.sandbox else self._PROD
        headers = {
            "authorization": f"bearer {self._creds.jwt}",
            "apns-topic": self._creds.bundle_id,
            "apns-push-type": "alert",
            "apns-priority": "10" if message.priority == "high" else "5",
            "content-type": "application/json",
        }
        if message.collapse_key:
            headers["apns-collapse-id"] = message.collapse_key[:64]
        body = {
            "aps": {
                "alert": {"title": message.title, "body": message.body},
                "sound": "default",
                # Time Sensitive so a readiness notification is not silently
                # held back by Focus; it is the whole point of the message.
                "interruption-level": "time-sensitive",
            },
            **{f"pagentos_{k}": v for k, v in message.data.items()},
        }
        return PushRequest(method="POST", url=f"{base}/{token}", headers=headers, json_body=body)

    def deliver(self, *, registration_key: str, message: PushMessage) -> PushDelivery:
        token = self._token(registration_key)
        _require(
            self._creds.configured,
            self.name,
            "PAGENTOS_PUSH_APNS_BUNDLE_ID + PAGENTOS_PUSH_APNS_JWT",
        )
        req = self.build_request(token=token, message=message)
        started = time.perf_counter()
        resp = _send(req, timeout_s=self._timeout_s, provider=self.name)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        apns_id = ""
        try:
            apns_id = str(resp.headers.get("apns-id", ""))
        except Exception:  # noqa: BLE001 - header access must not fail a delivery
            apns_id = ""
        return self._delivered(registration_key, token, message, f"{apns_id} ({latency_ms} ms)")


class WebPushProvider(_RealPushProviderBase):
    """RFC 8030 Web Push with VAPID. INERT without a VAPID key pair.

    The token here is the browser's push *subscription endpoint URL*, not an
    opaque vendor id — which is why the built request POSTs to the token
    itself. Encryption of the payload (RFC 8291) needs the subscription's p256dh
    and auth keys and a crypto dependency; until an owner provisions VAPID this
    adapter deliberately stops at request construction rather than shipping a
    half-implemented cipher.
    """

    name = "webpush"

    def __init__(
        self, credentials: WebPushCredentials | None = None, *, timeout_s: float = 15.0
    ) -> None:
        super().__init__(timeout_s=timeout_s)
        self._creds = credentials or WebPushCredentials.from_env()

    def capabilities(self) -> PushCapabilities:
        return PushCapabilities(
            name=self.name,
            platforms=("web", "android", "desktop"),
            background_delivery=True,
            # Payload encryption (RFC 8291) is not implemented; a data payload
            # would have to travel in clear, so the capability says no.
            data_payload=False,
            priority_control=True,
            collapse=True,
            max_payload_bytes=4096,
            requires_credentials=True,
        )

    def build_request(self, *, token: str, message: PushMessage) -> PushRequest:
        headers = {
            "TTL": "600",
            "Urgency": "high" if message.priority == "high" else "normal",
            "Authorization": f"vapid t={self._creds.private_key}, k={self._creds.public_key}",
        }
        if message.collapse_key:
            headers["Topic"] = message.collapse_key[:32]
        return PushRequest(method="POST", url=token, headers=headers, data=b"")

    def deliver(self, *, registration_key: str, message: PushMessage) -> PushDelivery:
        token = self._token(registration_key)
        _require(
            self._creds.configured,
            self.name,
            "PAGENTOS_PUSH_VAPID_PUBLIC_KEY + PAGENTOS_PUSH_VAPID_PRIVATE_KEY",
        )
        req = self.build_request(token=token, message=message)
        started = time.perf_counter()
        _send(req, timeout_s=self._timeout_s, provider=self.name)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return self._delivered(registration_key, token, message, f"({latency_ms} ms)")


def build_registry(*, fake: FakePushProvider | None = None) -> dict[str, PushProvider]:
    """All known transports. The reals are constructed but inert without creds,
    so `GET /v1/mobile/push/providers` can tell the owner exactly which one a
    credential would activate."""
    return {
        FakePushProvider.name: fake or FakePushProvider(),
        FcmPushProvider.name: FcmPushProvider(),
        ApnsPushProvider.name: ApnsPushProvider(),
        WebPushProvider.name: WebPushProvider(),
    }


__all__ = [
    "MAX_BODY_CHARS",
    "MAX_TITLE_CHARS",
    "MAX_TOKEN_CHARS",
    "MIN_TOKEN_CHARS",
    "ApnsPushProvider",
    "FakePushProvider",
    "FcmPushProvider",
    "PushCapabilities",
    "PushDelivery",
    "PushMessage",
    "PushProvider",
    "PushRequest",
    "RegisteredToken",
    "WebPushProvider",
    "build_registry",
    "fingerprint",
    "hash_token",
    "validate_token",
]
