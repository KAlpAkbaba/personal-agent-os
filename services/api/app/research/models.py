"""Browser-research ORM rows (M13 track C).

Canonical schema: alembic/versions/20260903_0012_browser_research.py. Named
with a ``Row`` suffix (mirrors ``app.voice.realtime_sessions.models.
RealtimeSessionRow``) to keep the durable-row types visually distinct from
the domain dataclasses in ``app.research.report``/``app.research.evidence``
that share similar names (``ResearchReport`` the dataclass vs.
``ResearchReportRow`` the table).
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
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

STAGE_PLANNED = "planned"
STAGE_SELECTING_DEVICE = "selecting_device"
STAGE_DISCOVERING = "discovering"
STAGE_WAITING_FOR_OWNER_VERIFICATION = "waiting_for_owner_verification"
STAGE_FETCHING = "fetching"
STAGE_RANKING = "ranking"
STAGE_SYNTHESIZING = "synthesizing"
STAGE_PERSISTING = "persisting"
STAGE_READY = "ready"
STAGE_FAILED = "failed"
STAGE_CANCELLED = "cancelled"

STAGES = (
    STAGE_PLANNED,
    STAGE_SELECTING_DEVICE,
    STAGE_DISCOVERING,
    STAGE_WAITING_FOR_OWNER_VERIFICATION,
    STAGE_FETCHING,
    STAGE_RANKING,
    STAGE_SYNTHESIZING,
    STAGE_PERSISTING,
    STAGE_READY,
    STAGE_FAILED,
    STAGE_CANCELLED,
)
#: waiting_for_owner_verification is NOT terminal (spec §5a/§4): polling
#: continues through it and the workflow resumes discovery on its own once
#: the owner clears the page or the interactive budget is spent.
TERMINAL_STAGES = frozenset({STAGE_READY, STAGE_FAILED, STAGE_CANCELLED})


class ResearchRunRow(Base):
    __tablename__ = "research_runs"

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default=STAGE_PLANNED)
    progress_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    events_json: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResearchCandidateRow(Base):
    __tablename__ = "research_candidates"
    __table_args__ = (
        UniqueConstraint("task_id", "url", name="uq_research_candidates_task_url"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(256), nullable=True)
    discovered_by: Mapped[str] = mapped_column(String(64), nullable=False)
    query_id: Mapped[str] = mapped_column(Text, nullable=False)
    published_hint: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResearchEvidenceRow(Base):
    __tablename__ = "research_evidence"
    __table_args__ = (
        UniqueConstraint("task_id", "url", name="uq_research_evidence_task_url"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    command_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    injection_suspected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    syndicated_of: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResearchReportRow(Base):
    __tablename__ = "research_reports"

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    report_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    synthesis_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    memory_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


#: docs/DECISIONS.md ADR-0076. Why a research became the one the owner is pointing at.
#: Closed vocabulary, mirrored by the CHECK constraint in
#: alembic/versions/20260907_0022_research_focus.py.
FOCUS_RESEARCH_JUST_COMPLETED = "research_just_completed"
FOCUS_RESULT_JUST_SPOKEN = "result_just_spoken"
FOCUS_OWNER_SELECTED_IN_UI = "owner_selected_in_ui"
FOCUS_OWNER_SELECTED_BY_VOICE = "owner_selected_by_voice"
FOCUS_FOLLOWUP_REFERENCE = "followup_reference"

FOCUS_SOURCES = (
    FOCUS_RESEARCH_JUST_COMPLETED,
    FOCUS_RESULT_JUST_SPOKEN,
    FOCUS_OWNER_SELECTED_IN_UI,
    FOCUS_OWNER_SELECTED_BY_VOICE,
    FOCUS_FOLLOWUP_REFERENCE,
)

#: The single owner (constitution: one human owner, no tenant model). The column exists
#: so the row says WHOSE focus this is rather than leaving it implied by the schema.
OWNER_ID = "owner"


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


#: ``ResearchFocusRow.id`` is TIME-ORDERED (an RFC 9562 UUIDv7 with a per-process
#: counter), never random. The stack reads ``ORDER BY selected_at, id``; with a random v4
#: id that second key was a lottery, and two rows stamped with one identical instant — the
#: completion hook and the announcer on Windows' coarse wall clock — came out in random
#: order (the flaky ``test_voice_research_followup`` binding, 2026-09-08).
#: ``app.research.focus.set_focus`` already refuses to write a tie (a new row's instant is
#: pushed past the newest row's); this is the guard for a tie that is nevertheless IN the
#: table — rows written before that rule, or two writers in concurrent transactions. Two
#: ids made in the same millisecond differ in the counter, so insertion order IS id order
#: and the later act reads as the more recent one. Hex-string (SQLite) and native
#: (PostgreSQL) uuid columns both sort these bytewise, which is numeric order.
_focus_id_lock = threading.Lock()
_focus_id_last_ms = 0
_focus_id_counter = 0


def focus_row_id() -> uuid.UUID:
    """A UUIDv7: 48 bits of unix milliseconds, a 12-bit in-millisecond counter, 62 random
    bits. Strictly increasing within this process (a clock that steps back holds the last
    millisecond and counts on); millisecond-ordered across processes."""
    global _focus_id_last_ms, _focus_id_counter
    with _focus_id_lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _focus_id_last_ms:
            ms = _focus_id_last_ms
            _focus_id_counter += 1
            if _focus_id_counter > 0xFFF:  # the counter wrapped: step the millisecond
                ms += 1
                _focus_id_counter = 0
        else:
            _focus_id_counter = 0
        _focus_id_last_ms = ms
        counter = _focus_id_counter
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (
        ((ms & ((1 << 48) - 1)) << 80)
        | (0x7 << 76)  # version
        | (counter << 64)  # rand_a, used as the monotonic counter (RFC 9562 §6.2 method 1)
        | (0b10 << 62)  # variant
        | rand_b
    )
    return uuid.UUID(int=value)


class ResearchFocusRow(Base):
    """One entry of the owner's research focus stack (docs/DECISIONS.md ADR-0076).

    APPEND-ONLY by design: "which research is the owner pointing at?" is answered by the
    most recent row, "the previous one" by the most recent row naming a DIFFERENT job,
    and an ordinal by counting distinct jobs down that list. Setting focus on a job that
    is already current appends again on purpose — recency is the whole ordering, and a
    row that was updated in place would lose the history "bir önceki" reads.
    """

    __tablename__ = "research_focus"
    __table_args__ = (
        CheckConstraint(
            _in_list("source_of_focus", FOCUS_SOURCES), name="ck_research_focus_source"
        ),
        Index("ix_research_focus_owner_selected_at", "owner_id", "selected_at"),
    )

    #: Time-ordered on purpose (see ``focus_row_id``): the stack's tiebreak key.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=focus_row_id)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default=OWNER_ID)
    #: The research task id — the identity. Never a title: two runs may share one
    #: (the owner's 2026-09-06 record has exactly that pair).
    research_job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    source_of_focus: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Informational only: which voice session (if any) was in the room when the focus
    #: moved. The focus itself is the OWNER's and outlives every session — that is the
    #: point of the table (a page reload used to lose the conversation's only linkage).
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResearchOwnerStateRow(Base):
    """One row, primary key ``'owner'``: the question the server is waiting on.

    ADR-0076: when a reference is genuinely ambiguous the server asks ONE short question
    and remembers what it offered, so the owner's spoken answer ("ikincisi", "20:19'daki")
    has something to land on. In the owner's 2026-09-06 record nothing did, and the same
    clarification was spoken six times in two and a half minutes.
    """

    __tablename__ = "research_owner_state"

    owner_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=OWNER_ID)
    #: {asked_at, question, candidates:[{research_job_id, artifact_id, topic,
    #: completed_at, mode, source_count}]} or NULL. Bounded by a TTL on read
    #: (app.research.focus.PENDING_CLARIFICATION_TTL), never by a sweeper.
    pending_clarification_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONColumn, nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "FOCUS_FOLLOWUP_REFERENCE",
    "FOCUS_OWNER_SELECTED_BY_VOICE",
    "FOCUS_OWNER_SELECTED_IN_UI",
    "FOCUS_RESEARCH_JUST_COMPLETED",
    "FOCUS_RESULT_JUST_SPOKEN",
    "FOCUS_SOURCES",
    "OWNER_ID",
    "STAGES",
    "STAGE_CANCELLED",
    "STAGE_DISCOVERING",
    "STAGE_FAILED",
    "STAGE_FETCHING",
    "STAGE_PERSISTING",
    "STAGE_PLANNED",
    "STAGE_RANKING",
    "STAGE_READY",
    "STAGE_SELECTING_DEVICE",
    "STAGE_SYNTHESIZING",
    "STAGE_WAITING_FOR_OWNER_VERIFICATION",
    "TERMINAL_STAGES",
    "ResearchCandidateRow",
    "ResearchEvidenceRow",
    "ResearchFocusRow",
    "ResearchOwnerStateRow",
    "ResearchReportRow",
    "ResearchRunRow",
]
