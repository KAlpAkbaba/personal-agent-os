"""``app_projects``: the App Factory's own durable record (docs/M23_APP_FACTORY_SPEC.md
§1, ADR-0086). Expand-only migration: ``alembic/versions/20260908_0030_app_projects.py``.

One row per project the assistant scaffolded on the owner's machine. ``spec_json`` is the
``AppSpec`` the project was generated from (canonical JSON, ``app.appfactory.spec.AppSpec``
— never re-derived from prose); ``root_path`` is the device's own answer from
``project.scaffold`` (under the device's ``Projects`` authorised root, spec §1) — Cloud
Core never invents or resolves a path itself. Portable types throughout (generic
``Uuid``/``JSON`` with a ``JSONB`` variant on Postgres) so the service layer unit-tests on
SQLite, the same discipline every M19-M22 model in this codebase already follows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

STATE_PLANNED = "planned"
STATE_SCAFFOLDED = "scaffolded"
STATE_RUNNING = "running"
STATE_TESTED = "tested"
STATE_FAILED = "failed"
STATE_STOPPED = "stopped"

APP_PROJECT_STATES: tuple[str, ...] = (
    STATE_PLANNED,
    STATE_SCAFFOLDED,
    STATE_RUNNING,
    STATE_TESTED,
    STATE_FAILED,
    STATE_STOPPED,
)


class AppProjectRow(Base):
    """One App Factory project (spec §1's table, verbatim)."""

    __tablename__ = "app_projects"
    __table_args__ = (
        Index("ix_app_projects_device_id", "device_id"),
        Index("ix_app_projects_state", "state"),
        Index("ix_app_projects_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    template: Mapped[str] = mapped_column(String(64), nullable=False)
    spec_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    #: "device:<id>" or the broker's own device id as text — the same shape every other
    #: M19-M22 device-facing row in this codebase already uses.
    device_id: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The device's OWN answer from ``project.scaffold`` (under its ``Projects`` root);
    #: ``None`` until scaffolded.
    root_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=STATE_PLANNED)
    run_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: A pointer to the run's captured stdout/stderr (the device's own bounded log path
    #: or ledger evidence ref) — never the log body itself in this row.
    last_run_log_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    test_report_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "APP_PROJECT_STATES",
    "STATE_FAILED",
    "STATE_PLANNED",
    "STATE_RUNNING",
    "STATE_SCAFFOLDED",
    "STATE_STOPPED",
    "STATE_TESTED",
    "AppProjectRow",
]
