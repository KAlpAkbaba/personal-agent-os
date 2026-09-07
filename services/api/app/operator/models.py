"""The generic durable object focus (docs/M19_DIGITAL_OPERATOR_SPEC.md §4, ADR-0082).

Canonical schema: ``alembic/versions/20260907_0025_object_focus.py``. One table for every
M19-M28 object kind the owner can point at with a deictic word ("bunu kapat", "öndeki
pencere") - ``window`` today, more kinds later, never a second focus table per kind. The
same append-only, most-recent-row-wins discipline ``app.research.models.ResearchFocusRow``
already uses (ADR-0076): setting focus on an object that is already current appends again
on purpose, because recency IS the ordering and an UPDATE would erase exactly the history
"the previous one" reads.

Single-owner system (CLAUDE.md): ``owner_session_id`` is nullable and informational only
(which realtime session, if any, was in the room when the focus moved) - never a tenant
key. Portable types throughout so the service layer unit-tests on SQLite.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

#: Object kinds this table accepts today. Additive: a future kind (``file``, ``tab``, ...)
#: is a new literal here, never a new table (module docstring).
FOCUS_KIND_WINDOW = "window"
FOCUS_KIND_APP = "app"
FOCUS_KINDS: tuple[str, ...] = (FOCUS_KIND_WINDOW, FOCUS_KIND_APP)

#: How many recent rows of ONE kind the stack keeps (bounded, per task brief: "a bounded
#: stack of the last 20 per kind"). Applied on read/prune, never by a CHECK constraint.
FOCUS_STACK_LIMIT = 20


class ObjectFocusRow(Base):
    """One entry of the owner's object focus stack, for one ``kind``."""

    __tablename__ = "object_focus"
    __table_args__ = (
        Index("ix_object_focus_kind_selected_at", "kind", "selected_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: Which realtime session (if any) was in the room when the focus moved. Informational
    #: only - the focus itself is the owner's and outlives any one session.
    owner_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The device's own stable id for the object ("w-<hwnd>-<create tick>" for a window,
    #: an OS pid for an app) - identity, never a title (the same rule ADR-0076 gave
    #: research focus: two windows may share a title).
    object_id: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: Why the focus moved: "operator_observed" (a step re-observed the foreground window),
    #: "operator_launch" (app.launch produced it), "owner_selected_by_voice", ... - a short
    #: machine token, never enumerated by a CHECK constraint (new sources are additive and
    #: this table is not privacy- or safety-critical the way the eye's durable flag is).
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meta_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )


__all__ = [
    "FOCUS_KINDS",
    "FOCUS_KIND_APP",
    "FOCUS_KIND_WINDOW",
    "FOCUS_STACK_LIMIT",
    "ObjectFocusRow",
]
