"""Realtime session ORM models (M12). Canonical schema:
alembic/versions/20260902_0011_realtime_voice.py — these MUST match it.

Portable types (generic ``Uuid``, JSON with a JSONB variant) exactly like the
other modules, so the unit suite runs the service on SQLite and the
integration suite on real PostgreSQL.

What is deliberately NOT here: the provider credential (minted per leg,
returned once, never stored), audio of any kind, transcripts beyond a bounded
rolling summary the client sends for continuity.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

REALTIME_STATE_CREATED = "created"  # record exists, media leg not yet reported
REALTIME_STATE_ACTIVE = "active"  # the client has reported events / tool calls
REALTIME_STATE_CLOSED = "closed"
REALTIME_STATE_EXPIRED = "expired"
REALTIME_STATES = (
    REALTIME_STATE_CREATED,
    REALTIME_STATE_ACTIVE,
    REALTIME_STATE_CLOSED,
    REALTIME_STATE_EXPIRED,
)

TOOL_STATUS_RUNNING = "running"
TOOL_STATUS_SUCCEEDED = "succeeded"
TOOL_STATUS_FAILED = "failed"
#: docs/DECISIONS.md ADR-0077: a research follow-up whose target could not be resolved
#: from the turn ends as a QUESTION, in its own word. A ``succeeded`` row can therefore
#: never carry no target and no owner-facing result; ``result_json["speech"]`` of a row
#: in this status is the one question the owner is asked. Migration 0023 widened the
#: column and re-stated the CHECK constraint for it.
TOOL_STATUS_NEEDS_CLARIFICATION = "needs_clarification"
TOOL_STATUSES = (
    TOOL_STATUS_RUNNING,
    TOOL_STATUS_SUCCEEDED,
    TOOL_STATUS_FAILED,
    TOOL_STATUS_NEEDS_CLARIFICATION,
)


class RealtimeSessionRow(Base):
    """One ConversationRealtime session (spec §4). ``owner_session_id`` is the
    owner API session of the CURRENT media leg: tool execution runs under it,
    and a continuity attach moves it to the new client (spec §7)."""

    __tablename__ = "realtime_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    client_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    owner_session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="tr-TR")
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=REALTIME_STATE_CREATED, index=True
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    narration_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: open plan, pending sideband messages, last intent, counters — the
    #: continuity state a new client receives on attach (spec §7).
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    transcript_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: NULL means "no expiry": this session ends when the OWNER ends it. A fixed horizon
    #: written at creation and never renewed is what killed every web session at exactly
    #: one hour, mid-conversation (migration 0038, owner directive 2026-09-10).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RealtimeToolCall(Base):
    """One provider tool call relayed through the sideband (spec §4 step 3).
    ``call_id`` is the provider's identifier and is unique PER SESSION — the
    idempotency key of ``POST .../tool-calls``."""

    __tablename__ = "realtime_tool_calls"
    __table_args__ = (UniqueConstraint("session_id", "call_id", name="uq_realtime_tool_call"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("realtime_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    arguments_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TOOL_STATUS_RUNNING, index=True
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    long_running: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "REALTIME_STATES",
    "REALTIME_STATE_ACTIVE",
    "REALTIME_STATE_CLOSED",
    "REALTIME_STATE_CREATED",
    "REALTIME_STATE_EXPIRED",
    "TOOL_STATUSES",
    "TOOL_STATUS_FAILED",
    "TOOL_STATUS_NEEDS_CLARIFICATION",
    "TOOL_STATUS_RUNNING",
    "TOOL_STATUS_SUCCEEDED",
    "RealtimeSessionRow",
    "RealtimeToolCall",
]
