"""The team's state on the Cloud Core (ADR-0214 addendum, pilot-02).

Canonical schema: ``alembic/versions/20260930_0063_team_state.py``. One table, three kinds of
row, keyed ``(kind, key)``: a ``task`` (key = the task id, ``doc`` = the task as
``team/queue.schema.json`` states it), the one ``lock`` row (``doc`` = machine, cycle_id, pid,
acquired_at), and a ``report`` (key = the file name, ``doc`` = its text). ``updated_at`` is the
string the writer stamped (``2026-09-30T12:00:00Z``): it is what a write's precondition is
compared against, so it is kept exactly as written.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

KIND_TASK = "task"
KIND_LOCK = "lock"
KIND_REPORT = "report"


class TeamStateRow(Base):
    __tablename__ = "team_state"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    doc: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
