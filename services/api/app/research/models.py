"""Browser-research ORM rows (M13 track C).

Canonical schema: alembic/versions/20260903_0012_browser_research.py. Named
with a ``Row`` suffix (mirrors ``app.voice.realtime_sessions.models.
RealtimeSessionRow``) to keep the durable-row types visually distinct from
the domain dataclasses in ``app.research.report``/``app.research.evidence``
that share similar names (``ResearchReport`` the dataclass vs.
``ResearchReportRow`` the table).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
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


__all__ = [
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
    "ResearchReportRow",
    "ResearchRunRow",
]
