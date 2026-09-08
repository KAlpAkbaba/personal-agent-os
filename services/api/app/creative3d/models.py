"""``scenes``: 3D creation's own durable record (docs/M25_CREATIVE_3D_SPEC.md §2, §4,
ADR-0088). Expand-only migration: ``alembic/versions/20260908_0032_scenes.py``.

One row per (tool, project, scene) the assistant is driving on the owner's machine.
``plan_json`` is the MOST RECENT ``ScenePlan`` applied (canonical JSON,
``app.creative3d.spec.ScenePlan`` — never re-derived from prose); a scene's full history
is the ledger's own ``scene.*`` rows, never re-derived here. ``inspection_json`` is the
tool's own read-back from its last ``scene.inspect`` (never the plan restated — the M25
security lesson stated plainly: what the row believes is true is only ever what a device
call actually reported). ``root_path`` is the device's own answer from
``project.scaffold`` (under the device's 3D root, spec §1) — Cloud Core never invents or
resolves a path itself, the same discipline ``app.appfactory.models.AppProjectRow``
already follows for its own device-facing root.

Portable types throughout (generic ``Uuid``/``JSON`` with a ``JSONB`` variant on
Postgres) so the service layer unit-tests on SQLite, the same discipline every M19-M24
model in this codebase already follows.
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

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

STATE_PLANNED = "planned"
STATE_SCAFFOLDED = "scaffolded"
STATE_APPLIED = "applied"
STATE_RENDERED = "rendered"
STATE_FAILED = "failed"
#: The honest Unity mark (spec §1, §6, ADR-0088 decision 5): the licensing client
#: refused, never a crash, never "done" — kept as its OWN state so a row that landed
#: here is never confused with a genuine ``failed`` (a plan/driver problem).
STATE_DEPENDENCY_UNAVAILABLE = "dependency_unavailable"

SCENE_STATES: tuple[str, ...] = (
    STATE_PLANNED,
    STATE_SCAFFOLDED,
    STATE_APPLIED,
    STATE_RENDERED,
    STATE_FAILED,
    STATE_DEPENDENCY_UNAVAILABLE,
)


class SceneRow(Base):
    """One 3D scene the assistant is driving (spec §2's ``ScenePlan`` identity:
    ``tool``/``project``/``scene``)."""

    __tablename__ = "scenes"
    __table_args__ = (
        Index("ix_scenes_device_id", "device_id"),
        Index("ix_scenes_state", "state"),
        Index("ix_scenes_created_at", "created_at"),
        Index("ix_scenes_project_scene", "project", "scene"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tool: Mapped[str] = mapped_column(String(16), nullable=False)
    project: Mapped[str] = mapped_column(String(64), nullable=False)
    scene: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: "device:<id>" or the broker's own device id as text — the same shape every other
    #: M19-M24 device-facing row in this codebase already uses.
    device_id: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The device's OWN answer from ``project.scaffold`` (under its 3D root); ``None``
    #: until scaffolded.
    root_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default=STATE_PLANNED)
    #: The MOST RECENT ``ScenePlan`` applied (canonical JSON) — module docstring.
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    #: The tool's own read-back from its last ``scene.inspect`` — never the plan
    #: restated (module docstring).
    inspection_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: The last ``app.creative3d.compare.compare()`` result, for the receipt and for
    #: debugging a mismatch — never re-derived from the plan a second time by a caller.
    compare_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: The last render's object-store key (``None`` until a ``render`` operation has
    #: actually produced and stored a PNG), plus its identity — spec §4's "the render
    #: file present and non-trivial", read back independently before storage.
    render_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    render_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    render_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "SCENE_STATES",
    "STATE_APPLIED",
    "STATE_DEPENDENCY_UNAVAILABLE",
    "STATE_FAILED",
    "STATE_PLANNED",
    "STATE_RENDERED",
    "STATE_SCAFFOLDED",
    "SceneRow",
]
