"""Owner identity ORM models (frozen foundation).

Canonical schema lives in alembic/versions/20260901_0009_identity_mobile.py
(lead-authored, applied). Types are portable (generic Uuid, JSON with JSONB
variant) like the other modules so the service layer unit-tests on SQLite; the
migration keeps the PostgreSQL-only constructs (CHECK constraints, BigInteger
identity), which is why `alembic check` reports those as differences by design.

`push_registrations` from the same migration belongs to the M9 mobile surface
(app/mobile/models.py) and is deliberately not modelled here: this module owns
identity, not delivery.

Semantics baked in (ADR-0027):
- `token_hash` is the only representation of a session token that exists after
  issuance; it is unique, so a lookup is an indexed equality probe followed by
  a constant-time comparison.
- `status` is the durable authority: `active` sessions can still be refused at
  verification time by absolute expiry or idle timeout, and are then flipped to
  `expired` so the refusal is recorded once rather than recomputed forever.
- `session_events` is append-only. Every row explains one decision, carries a
  reason, and never carries the token.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

#: Which kind of client holds the session. Not a role and not a permission —
#: one owner, so this is provenance for the audit trail and for revoking "the
#: session on my phone" without touching the desktop.
CLIENT_KINDS = ("web", "mobile", "desktop", "device", "cli")

SESSION_STATUS_ACTIVE = "active"
SESSION_STATUS_REVOKED = "revoked"
SESSION_STATUS_EXPIRED = "expired"
SESSION_STATUSES = (SESSION_STATUS_ACTIVE, SESSION_STATUS_REVOKED, SESSION_STATUS_EXPIRED)

EVENT_ISSUED = "issued"
EVENT_REFRESHED = "refreshed"
EVENT_REVOKED = "revoked"
EVENT_EXPIRED = "expired"
EVENT_REJECTED = "rejected"
SESSION_EVENT_ACTIONS = (
    EVENT_ISSUED,
    EVENT_REFRESHED,
    EVENT_REVOKED,
    EVENT_EXPIRED,
    EVENT_REJECTED,
)


class OwnerSession(Base):
    """An opaque bearer session held by one of the owner's clients."""

    __tablename__ = "owner_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    client_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    client_label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    #: Set when the session belongs to an enrolled device; revoking that device
    #: revokes this session (M9 acceptance).
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SESSION_STATUS_ACTIVE, index=True
    )
    #: Empty list = unrestricted owner authority. A non-empty list *narrows* the
    #: session to those scopes; it never grants anything the owner lacks.
    scopes_json: Mapped[list[str]] = mapped_column(JSONColumn, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)


class SessionEvent(Base):
    """Append-only audit of authentication decisions. Never contains a token."""

    __tablename__ = "session_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("owner_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    client_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "CLIENT_KINDS",
    "EVENT_EXPIRED",
    "EVENT_ISSUED",
    "EVENT_REFRESHED",
    "EVENT_REJECTED",
    "EVENT_REVOKED",
    "SESSION_EVENT_ACTIONS",
    "SESSION_STATUSES",
    "SESSION_STATUS_ACTIVE",
    "SESSION_STATUS_EXPIRED",
    "SESSION_STATUS_REVOKED",
    "OwnerSession",
    "SessionEvent",
]
