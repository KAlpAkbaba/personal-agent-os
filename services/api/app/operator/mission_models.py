"""The durable operator mission (B39 req 128).

Canonical schema: ``alembic/versions/20260915_0051_operator_missions.py``. One row per
mission the owner asked for; the steps, the trail and the escalation live in JSON columns
because they are the mission's own record (``app.operator.mission.Mission.as_dict``) and
nothing queries inside them - the columns that ARE queried (status, session, times) are
their own. Single-owner system: ``session_id`` is informational, never a tenant key.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

SOURCE_VOICE = "voice"
SOURCE_REST = "rest"


class OperatorMissionRow(Base):
    __tablename__ = "operator_missions"
    __table_args__ = (Index("ix_operator_missions_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    goal: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    preview: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=SOURCE_VOICE)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_class: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    message: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    #: ``Mission.as_dict()`` - the whole record, the trail included.
    mission_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    #: The workflow's cancel/pause requests land here first; the activity reads them
    #: before every round, so a request between two rounds is honoured by the next.
    pause_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["OperatorMissionRow", "SOURCE_REST", "SOURCE_VOICE"]
