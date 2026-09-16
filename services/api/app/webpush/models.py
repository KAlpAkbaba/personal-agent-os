"""B11 req 372: one row per browser subscribed to Web Push.

A subscription is not owner identity - the owner is one person (CLAUDE.md: "one human
owner, no SaaS tenant model") who may have several BROWSERS each holding their own
subscription (a work laptop, a phone, a second desktop). Every route in
``app.webpush.routes`` is owner-session-gated regardless, so this table never needs an
owner_id column of its own - the same reasoning ``app.alarms.models`` and
``app.ambient.models`` already document for their own singleton-owner tables.

``endpoint`` is the push service's per-subscription URL. It behaves like a bearer
credential (whoever holds it can ask the push service to notify this browser), which is
exactly why ``app.webpush.provider``'s SSRF allowlist exists and why this value is never
logged in full (``app.webpush.service`` logs only the host).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

#: RFC 8291 §3.1: the auth secret is exactly 16 bytes; base64url of 16 bytes is at most
#: 24 characters (ceil(16/3)*4, no padding needed since 16 % 3 != 0 gives 22-24 with our
#: encoder). 48 leaves headroom without inviting an oversized value in unexamined.
_AUTH_B64_MAX: Final = 48
#: A raw uncompressed P-256 point is exactly 65 bytes; base64url of 65 bytes is 88
#: characters (padding stripped). 128 leaves headroom the same way.
_P256DH_B64_MAX: Final = 128


class PushSubscriptionRow(Base):
    """One browser's ``PushSubscription`` (the object ``pushManager.subscribe()``
    returns), stored so the push rung can reach every browser the owner has enabled."""

    __tablename__ = "webpush_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: The push service URL for this browser. Unique: re-subscribing the same browser
    #: (a refreshed permission, a re-registered service worker) updates the existing
    #: row rather than accumulating duplicates the ladder would then double-send to.
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    #: RFC 8291 keys, both base64url as the browser hands them back — decoded to raw
    #: bytes only at the point of use (app.webpush.service), never stored decoded.
    p256dh: Mapped[str] = mapped_column(String(_P256DH_B64_MAX), nullable=False)
    auth: Mapped[str] = mapped_column(String(_AUTH_B64_MAX), nullable=False)
    #: A label for the settings UI ("Chrome on this PC") — free text from the browser's
    #: own User-Agent header, bounded the same way app.news.provider bounds third-party
    #: free text before it reaches a fixed-width column or the owner's screen.
    user_agent: Mapped[str] = mapped_column(String(256), nullable=False, default="")

    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The reason class of the most recent failed attempt (app.webpush.provider.PushError
    #: reasons), or NULL if the most recent attempt succeeded or none was made yet. Never
    #: the raw provider exception text - a reason CLASS, the same discipline
    #: app.notifications.events.task_failed follows for the owner-facing error class.
    last_error_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


Index("ix_webpush_subscriptions_created_at", PushSubscriptionRow.created_at)


__all__ = ["PushSubscriptionRow"]
