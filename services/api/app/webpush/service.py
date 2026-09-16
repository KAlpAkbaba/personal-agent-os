"""B11 req 372: subscription storage, and ``send_to_all`` — the one function that turns
a notification row into encrypted RFC 8291 bodies, signs each with a fresh RFC 8292
VAPID header, and hands them to the provider.

**What a 201/202 means, and what it does not.** RFC 8030's push service answers 201 (or
202, if ``Prefer: respond-async`` was sent, which this module never does) when it has
ACCEPTED the message for delivery to the browser - not when the browser has received
it, and never when the owner has seen it. ``app.notifications.ladder.PushRung`` reads
``PushOutcome.accepted`` to decide whether the ladder may stop, and its own docstring
says the same thing again at the point that decision is made - "a queue is not a
delivery" applies here exactly as it does to every other transport this system talks to.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.notifications.models import PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_URGENT
from app.webpush import ece
from app.webpush.encoding import b64url_decode
from app.webpush.models import PushSubscriptionRow
from app.webpush.provider import PushError, PushProvider, validate_push_endpoint
from app.webpush.vapid import DEFAULT_EXP_TTL_S, build_authorization_header

logger = get_logger("app.webpush.service")

#: Bounded the same way app.notifications.toast.build bounds a toast title/body: what
#: the owner reads is this system's decision, never a silently truncated fragment of
#: whatever produced the notification. Well inside ece.MAX_PLAINTEXT_BYTES (3993) once
#: wrapped in the small JSON envelope below.
MAX_TITLE_CHARS: Final = 128
MAX_BODY_CHARS: Final = 400
#: RFC 8030 §5.2: TTL is how long the PUSH SERVICE holds the message for an offline
#: browser, in seconds - not related to the encryption record size. Mapped from
#: notification priority: an urgent notice stale by the time the browser reconnects is
#: better replaced by the inbox than delivered late (req 378's whole reason urgent
#: exists is immediacy); a low-priority one loses nothing by waiting three days.
_TTL_BY_PRIORITY: Final[dict[str, int]] = {
    PRIORITY_URGENT: 5 * 60,
    PRIORITY_NORMAL: 12 * 3600,
    PRIORITY_LOW: 3 * 24 * 3600,
}
_DEFAULT_TTL_S: Final = _TTL_BY_PRIORITY[PRIORITY_NORMAL]
#: RFC 8030 §5.3's own vocabulary; "very-low" is never produced because nothing this
#: system emits (app.notifications.events.EVENTS) is currently that unimportant.
_URGENCY_BY_PRIORITY: Final[dict[str, str]] = {
    PRIORITY_URGENT: "high",
    PRIORITY_NORMAL: "normal",
    PRIORITY_LOW: "low",
}
_DEFAULT_URGENCY: Final = "normal"

_AUTH_SECRET_LEN: Final = 16
_P256DH_RAW_LEN: Final = 65

_ENDPOINT_HOST_RE = re.compile(r"^https://([^/]+)")

#: Security review finding (LOW): nothing bounded how many browsers could subscribe.
#: One owner, realistically a handful of browsers/devices — 32 is generous headroom
#: over that without leaving the table free to grow without limit from a route an
#: owner-session token can call repeatedly. Re-subscribing an ENDPOINT already stored
#: (a refreshed permission, the same browser again) never counts against this: it
#: updates the existing row rather than adding one (see `subscribe` below).
MAX_SUBSCRIPTIONS: Final = 32


class SubscriptionError(ValueError):
    """A subscription payload does not decode to valid RFC 8291 key material, the
    endpoint fails the SSRF allowlist, or the subscription table is already at
    `MAX_SUBSCRIPTIONS`. Raised at STORE time (``subscribe``) so a bad subscription
    never reaches the ladder's retry path at all.

    ``error_class`` is one of ``app.errors.catalog``'s keys — the route reads it to
    answer the owner in Turkish (``app.errors.owner.log_and_detail``) rather than
    mapping every ``SubscriptionError`` to the same generic sentence.
    """

    def __init__(self, message: str, *, error_class: str = "validation_error") -> None:
        super().__init__(message)
        self.error_class = error_class


def endpoint_host(endpoint: str) -> str:
    """For logging only - never the full endpoint, which behaves like a bearer
    credential (module docstring of ``app.webpush.models``)."""
    match = _ENDPOINT_HOST_RE.match(endpoint)
    return match.group(1) if match else "?"


def subscribe(
    db: Session,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str = "",
    now: datetime | None = None,
) -> PushSubscriptionRow:
    """Store (or refresh) one browser's subscription. Validates the endpoint and the
    key material BEFORE anything is written - a subscription this system cannot ever
    use is not a savings for later, it is a row the ladder would fail against on every
    notification.
    """
    validate_push_endpoint(endpoint)  # raises PushError(invalid_endpoint); caller maps it
    try:
        p256dh_raw = b64url_decode(p256dh)
        auth_raw = b64url_decode(auth)
    except Exception as exc:  # noqa: BLE001 - re-raised typed below
        raise SubscriptionError(f"subscription keys are not valid base64url: {exc}") from exc
    if len(p256dh_raw) != _P256DH_RAW_LEN:
        raise SubscriptionError(f"p256dh must decode to {_P256DH_RAW_LEN} bytes")
    if len(auth_raw) != _AUTH_SECRET_LEN:
        raise SubscriptionError(f"auth must decode to {_AUTH_SECRET_LEN} bytes")
    ece.load_public_key(p256dh_raw)  # raises WebPushCryptoError if not a valid P-256 point

    moment = now or datetime.now(UTC)
    existing = db.execute(
        select(PushSubscriptionRow).where(PushSubscriptionRow.endpoint == endpoint)
    ).scalar_one_or_none()
    if existing is not None:
        existing.p256dh = p256dh
        existing.auth = auth
        existing.user_agent = user_agent[:256]
        row = existing
    elif (
        db.execute(select(func.count()).select_from(PushSubscriptionRow)).scalar_one()
        >= MAX_SUBSCRIPTIONS
    ):
        raise SubscriptionError(
            f"already at the {MAX_SUBSCRIPTIONS}-subscription limit; remove an old "
            "browser before adding another",
            error_class="resource_budget_exceeded",
        )
    else:
        row = PushSubscriptionRow(
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=user_agent[:256],
            created_at=moment,
        )
        db.add(row)
    db.commit()
    logger.info("webpush_subscribed", subscription_id=str(row.id), host=endpoint_host(endpoint))
    return row


def list_subscriptions(db: Session) -> list[PushSubscriptionRow]:
    return list(
        db.execute(select(PushSubscriptionRow).order_by(PushSubscriptionRow.created_at.asc()))
        .scalars()
        .all()
    )


def unsubscribe(db: Session, subscription_id: uuid.UUID) -> bool:
    row = db.get(PushSubscriptionRow, subscription_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    logger.info("webpush_unsubscribed", subscription_id=str(subscription_id))
    return True


def _mark_success(db: Session, row: PushSubscriptionRow, *, now: datetime) -> None:
    row.last_success_at = now
    row.last_attempt_at = now
    row.last_error_reason = None
    row.failure_count = 0
    db.commit()


def _mark_failure(db: Session, row: PushSubscriptionRow, *, reason: str, now: datetime) -> None:
    row.last_attempt_at = now
    row.last_error_reason = reason
    row.failure_count = (row.failure_count or 0) + 1
    db.commit()


#: RFC 8030 §5.2 has no upper bound "for values less than 2^31"; this is OUR ceiling
#: (module docstring's TTL table already never exceeds it) so a caller cannot accidentally
#: ask a push service to hold a message for years.
_MAX_TTL_S: Final = 30 * 24 * 3600


def build_payload(
    *, notification_id: uuid.UUID, title: str, body: str, group_key: str, url: str = ""
) -> bytes:
    """The plaintext this system encrypts (RFC 8291) and the browser's own service
    worker JSON-decodes in its ``push`` event handler (apps/web's ``public/sw.js``).

    Bounded the same way ``app.notifications.toast.build`` bounds a toast: a title or
    body longer than the limit is trimmed HERE, at the one seam that writes it, rather
    than discovered as an oversized-payload failure from the push service later.
    """
    trimmed_title = (title or "")[:MAX_TITLE_CHARS]
    trimmed_body = (body or "")[:MAX_BODY_CHARS]
    payload = {
        "notification_id": str(notification_id),
        "title": trimmed_title,
        "body": trimmed_body,
        "tag": group_key or str(notification_id),
    }
    if url:
        payload["url"] = url
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > ece.MAX_PLAINTEXT_BYTES:
        # The envelope itself (JSON keys, punctuation) plus two already-bounded fields
        # cannot exceed the record budget; reaching this means a caller passed a `url`
        # far longer than anything this system generates. Refuse rather than silently
        # drop the notification_id/tag fields a truncated JSON body would corrupt.
        raise SubscriptionError(
            f"push payload is {len(encoded)} bytes, over the {ece.MAX_PLAINTEXT_BYTES}-byte "
            "single-record limit"
        )
    return encoded


@dataclass(frozen=True, slots=True)
class PushOutcome:
    attempted: int
    accepted: int
    expired_subscription_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)


def send_to_all(
    db: Session,
    *,
    provider: PushProvider,
    vapid_private_key: ec.EllipticCurvePrivateKey,
    vapid_subject: str,
    notification_id: uuid.UUID,
    title: str,
    body: str,
    group_key: str,
    priority: str,
    url: str = "",
    timeout_s: float = 10.0,
    now: datetime | None = None,
) -> PushOutcome:
    """Encrypt and send one notification to every stored subscription.

    Each subscription is independent: one browser's expired subscription (404/410) is
    deleted and does not stop the others from being tried, and one browser's rate limit
    (429) or server error (5xx) is recorded against ONLY that subscription — the same
    per-attempt isolation ``app.notifications.ladder.deliver_one`` gives each rung.
    """
    moment = now or datetime.now(UTC)
    subscriptions = list_subscriptions(db)
    if not subscriptions:
        return PushOutcome(attempted=0, accepted=0)

    ttl_s = min(_TTL_BY_PRIORITY.get(priority, _DEFAULT_TTL_S), _MAX_TTL_S)
    urgency = _URGENCY_BY_PRIORITY.get(priority, _DEFAULT_URGENCY)
    plaintext = build_payload(
        notification_id=notification_id, title=title, body=body, group_key=group_key, url=url
    )

    accepted = 0
    expired: list[uuid.UUID] = []
    for row in subscriptions:
        host = endpoint_host(row.endpoint)
        try:
            p256dh_raw = b64url_decode(row.p256dh)
            auth_raw = b64url_decode(row.auth)
            encrypted = ece.encrypt(plaintext, p256dh=p256dh_raw, auth_secret=auth_raw)
            # RFC 8292's exp is how long THIS AUTHORIZATION is valid, capped at 24h - a
            # wholly different clock from RFC 8030's TTL header (how long the push
            # service holds the MESSAGE, which for a low-priority notification is
            # deliberately days). Reusing `ttl_s` here was a real bug this module's own
            # test suite caught: a low-priority send's 3-day message TTL exceeded
            # RFC 8292's 24-hour ceiling and VapidKeyError took down the whole send.
            authorization = build_authorization_header(
                endpoint=row.endpoint,
                private_key=vapid_private_key,
                subject=vapid_subject,
                ttl_s=DEFAULT_EXP_TTL_S,
            )
            headers = {
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "TTL": str(ttl_s),
                "Urgency": urgency,
                "Authorization": authorization,
            }
            provider.send(
                endpoint=row.endpoint, headers=headers, body=encrypted.body, timeout_s=timeout_s
            )
        except Exception as exc:  # noqa: BLE001 - one subscription's failure never stops the rest
            reason = getattr(exc, "reason", None) or "provider_error"
            if isinstance(exc, PushError) and exc.reason == PushError.REASON_EXPIRED:
                expired.append(row.id)
                db.delete(row)
                db.commit()
                logger.info("webpush_subscription_expired", subscription_id=str(row.id), host=host)
                continue
            _mark_failure(db, row, reason=reason, now=moment)
            # `error` is this module's own typed exception text (a reason class plus a
            # short, non-secret sentence - never the endpoint, never key material), so
            # it is safe to log even in the fallback "provider_error" bucket where
            # `reason` alone would otherwise hide what actually went wrong.
            logger.info(
                "webpush_send_failed",
                subscription_id=str(row.id),
                host=host,
                reason=reason,
                error=f"{type(exc).__name__}: {exc}",
            )
            continue
        accepted += 1
        _mark_success(db, row, now=moment)
        logger.info("webpush_accepted_by_service", subscription_id=str(row.id), host=host)

    return PushOutcome(
        attempted=len(subscriptions), accepted=accepted, expired_subscription_ids=tuple(expired)
    )


__all__ = [
    "MAX_BODY_CHARS",
    "MAX_SUBSCRIPTIONS",
    "MAX_TITLE_CHARS",
    "PushOutcome",
    "SubscriptionError",
    "build_payload",
    "endpoint_host",
    "list_subscriptions",
    "send_to_all",
    "subscribe",
    "unsubscribe",
]
