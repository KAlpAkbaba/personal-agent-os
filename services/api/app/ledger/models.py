"""Activity Ledger ORM rows (M16 track A).

Canonical schema: ``alembic/versions/20260904_0015_activity_ledger.py``.
Named with a ``Row`` suffix (mirrors ``app.research.models.ResearchRunRow``,
``app.voice.realtime_sessions.models.RealtimeSessionRow``) to keep the
durable-row type visually distinct from the write-contract dataclass
(``app.ledger.service.ActivityEvent``) that shares a similar name.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")


class ActivityEventRow(Base):
    """One fact about what the system did (spec §1.1). Never mutated after
    insert except by nothing — the ledger is append-only; ``record`` either
    inserts a new row or returns the existing one for the same
    ``(source, source_ref)``."""

    __tablename__ = "activity_events"
    __table_args__ = (
        UniqueConstraint("source", "source_ref", name="uq_activity_events_source_ref"),
        Index("ix_activity_events_occurred_at", "occurred_at"),
        Index("ix_activity_events_subsystem", "subsystem"),
        Index("ix_activity_events_event_type", "event_type"),
        Index("ix_activity_events_research_job_id", "research_job_id"),
        Index("ix_activity_events_trace_id", "trace_id"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: when the thing happened (from the evidence), not when the ledger wrote it.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subsystem: Mapped[str] = mapped_column(String(32), nullable=False)
    module: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[str | None] = mapped_column(String(256), nullable=True)
    production_state: Mapped[str] = mapped_column(String(24), nullable=False, default="n/a")
    command_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: the research task id (app.artifacts.models.Task.id) when this event is
    #: about a research job; reserved for the Cognitive Core / Self Model
    #: otherwise (related_goal_id, related_module_id).
    research_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    browser_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    related_goal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    related_module_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: [{"kind": "research_report", "ref": "<task_id>"}, ...] — never empty for
    #: a backfilled event (spec §1.4: "never fabricate").
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: one Turkish sentence stating only what the evidence supports.
    factual_summary: Mapped[str] = mapped_column(Text, nullable=False)
    #: structured counts and identifiers; never page text, never transcripts,
    #: never credentials (enforced by the writers, not by this column).
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    #: "live" or "backfill:<table>".
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    #: idempotency key within `source`, e.g. "research_runs:<task_id>:ready".
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)


class PendingBriefingRow(Base):
    """A briefing queued for delivery but not yet spoken (spec §4).

    ``priority`` is a reversible internal ranking (lower = spoken first) the
    briefing engine derives from ``policy`` — not part of the owner-facing
    contract — so several pending briefings can be ordered without a second
    lookup: immediate=0, completion=10, once=20, digest=30.
    """

    __tablename__ = "pending_briefings"
    __table_args__ = (Index("ix_pending_briefings_delivered_at", "delivered_at"),)

    briefing_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: "immediate" | "completion" | "once" | "digest" (never "ledger_only" —
    #: those events are never queued at all).
    policy: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    speech: Mapped[str] = mapped_column(Text, nullable=False)
    #: the activity_events.event_id values this briefing summarizes.
    event_ids: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: "say" | "session_instructions" | "push".
    delivered_via: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: B07 req 14/15/16/17: bounded delivery. Before these three columns existed the
    #: announcer had no way to know it had already tried, so a permanently failing item was
    #: re-attempted on every sweep for ever. `attempts` counts failures, `next_attempt_at`
    #: holds the backoff, and `quarantined_at` is the announcer giving up on ONE item so the
    #: queue behind it can move (app.notifications.delivery).
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quarantined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["ActivityEventRow", "PendingBriefingRow"]
