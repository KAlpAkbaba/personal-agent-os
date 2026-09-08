"""``calendar_index`` / ``calendar_proposals`` (docs/M21_MAIL_CALENDAR_SPEC.md §3, ADR-0084).

Same discipline as ``app.mail.models``: ``calendar_index`` grows only from owner-initiated
reads (``agenda``/``find_slot``), never a background sync; portable types throughout.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

PROPOSAL_STATE_PREPARED = "prepared"
PROPOSAL_STATE_COMMITTED = "committed"
PROPOSAL_STATE_DISCARDED = "discarded"
PROPOSAL_STATES: tuple[str, ...] = (
    PROPOSAL_STATE_PREPARED,
    PROPOSAL_STATE_COMMITTED,
    PROPOSAL_STATE_DISCARDED,
)

PROPOSAL_KIND_CREATE = "create"
PROPOSAL_KIND_RESCHEDULE = "reschedule"
PROPOSAL_KINDS: tuple[str, ...] = (PROPOSAL_KIND_CREATE, PROPOSAL_KIND_RESCHEDULE)


class CalendarIndexRow(Base):
    """One calendar event occurrence the owner has actually seen (agenda/find_slot)."""

    __tablename__ = "calendar_index"
    __table_args__ = (
        Index("ix_calendar_index_uid", "uid"),
        Index("ix_calendar_index_last_used_at", "last_used_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    uid: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(String(998), nullable=False, default="")
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CalendarProposalRow(Base):
    """A PREPARE-tier object (spec §1): a proposed create/reschedule, read back with its
    conflicts before ``CalendarService.commit`` may ever touch a real calendar."""

    __tablename__ = "calendar_proposals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The event this proposal targets — empty for a fresh ``create``, the existing
    #: event's uid for a ``reschedule``.
    event_uid: Mapped[str | None] = mapped_column(String(500), nullable=True)
    summary: Mapped[str] = mapped_column(String(998), nullable=False, default="")
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: ``[{uid, summary}]`` of every existing event this proposal would overlap — named in
    #: the read-back verbatim (spec §3), never silently dropped.
    conflicts_json: Mapped[list[Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=PROPOSAL_STATE_PREPARED)
    read_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_event_uid: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "PROPOSAL_KINDS",
    "PROPOSAL_KIND_CREATE",
    "PROPOSAL_KIND_RESCHEDULE",
    "PROPOSAL_STATES",
    "PROPOSAL_STATE_COMMITTED",
    "PROPOSAL_STATE_DISCARDED",
    "PROPOSAL_STATE_PREPARED",
    "CalendarIndexRow",
    "CalendarProposalRow",
]
