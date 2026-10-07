"""The durable browser task (ADR-0207 b).

Canonical schema: ``alembic/versions/20260929_0062_web_tasks.py``. One row per task the
owner asked for. The loop's whole state - the trail, what it waits for, what it has seen -
lives in ``state_json`` (``app.webtask.loop.TaskState.as_dict``) because it is the task's
own record and nothing queries inside it; the columns that ARE queried (status, the kind
of wait, the round, times) are their own. Single-owner system: ``session_id`` is
informational, never a tenant key.

What this row never holds: a value the loop typed (the trail records its length), and a
credential of any kind.
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

#: The column's default: a task runs in front of the owner (ADR-0207 decision 3). A CLOUD
#: task is the exception and is written ``False`` by ``app.webtask.service.start_task_db``
#: (ADR-0213 addendum, 2026-09-30): the task the owner started may go on after he leaves,
#: because in the cloud it acts only on sites he listed. Nothing routine starts one.
ATTENDED = True


class WebTaskRow(Base):
    __tablename__ = "web_tasks"
    __table_args__ = (Index("ix_web_tasks_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    goal: Mapped[str] = mapped_column(String(600), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    #: Why the task waits for the owner (``app.webtask.types.ASK_KINDS``), or "".
    waiting_for: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    round_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    message: Mapped[str] = mapped_column(String(1200), nullable=False, default="")
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=SOURCE_VOICE)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=ATTENDED)
    #: The read-back the owner heard, and where: what a confirmation is judged against.
    read_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_back_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    read_back_turn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The cancel request lands here first; the round reads it before it does anything.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["ATTENDED", "SOURCE_REST", "SOURCE_VOICE", "WebTaskRow"]
