"""Mobile push-registration ORM model (matches the frozen M9 migration).

Canonical schema lives in `alembic/versions/20260901_0009_identity_mobile.py`
(lead-authored, applied, FROZEN). Like the other modules the ORM types stay
portable (generic `Uuid`, `JSON` with a JSONB variant) so the service layer
unit-tests on SQLite, while the migration keeps the PostgreSQL-only CHECK
constraints — which is why `alembic check` reports those as differences by
design.

Two semantics are baked into the frozen columns and this module honours both:

1. **`token_hash`, never the token.** The provider token is stored only as its
   SHA-256. The delivery adapter holds the live token in process memory from
   registration time onward, so a database leak yields nothing that can be
   replayed against FCM/APNs. The consequence is deliberate and documented on
   `providers.FakePushProvider`: after an API restart the live tokens are gone
   and the client re-registers, exactly as a native app does when the OS
   re-issues its token.

2. **`session_id` with `ON DELETE CASCADE`.** A registration is a property of
   an owner *session*, not of a person or a device record. Deleting a session
   deletes its registrations at the database level; *revoking* a session is a
   status flip, so the live-target query in `service.py` joins `owner_sessions`
   and requires `status = 'active'`. Both halves are tested — the join is what
   makes "device revocation invalidates session" also mean "and stops the push".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

#: Provider names allowed by the frozen CHECK constraint.
PUSH_PROVIDER_FAKE = "fake"
PUSH_PROVIDER_FCM = "fcm"
PUSH_PROVIDER_APNS = "apns"
PUSH_PROVIDER_WEBPUSH = "webpush"
PUSH_PROVIDERS = (
    PUSH_PROVIDER_FAKE,
    PUSH_PROVIDER_FCM,
    PUSH_PROVIDER_APNS,
    PUSH_PROVIDER_WEBPUSH,
)

#: Statuses allowed by the frozen CHECK constraint.
PUSH_STATUS_ACTIVE = "active"
#: The owner (or the owner's client) unregistered on purpose.
PUSH_STATUS_REVOKED = "revoked"
#: The provider told us the token is dead (uninstalled app, rotated token).
PUSH_STATUS_INVALID = "invalid"
PUSH_STATUSES = (PUSH_STATUS_ACTIVE, PUSH_STATUS_REVOKED, PUSH_STATUS_INVALID)


class PushRegistration(Base):
    """One provider token belonging to one owner session."""

    __tablename__ = "push_registrations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("owner_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    #: SHA-256 of the provider token. The plaintext token never lands here.
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PUSH_STATUS_ACTIVE, index=True
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="tr-TR")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # One registration per (session, provider): a client re-registering after an
    # OS token rotation updates its row instead of accumulating dead targets.
    __table_args__ = (
        UniqueConstraint("session_id", "provider", name="uq_push_registrations_session_provider"),
    )


__all__ = [
    "PUSH_PROVIDERS",
    "PUSH_PROVIDER_APNS",
    "PUSH_PROVIDER_FAKE",
    "PUSH_PROVIDER_FCM",
    "PUSH_PROVIDER_WEBPUSH",
    "PUSH_STATUSES",
    "PUSH_STATUS_ACTIVE",
    "PUSH_STATUS_INVALID",
    "PUSH_STATUS_REVOKED",
    "PushRegistration",
]
