"""The watch's two tables.

Canonical schema: ``alembic/versions/20261003_0066_watches.py``. ``watches`` is the standing
subscription and its state (the baseline hash, whether the condition holds, the failure
count); ``watch_readings`` is one row per reading, kept 30 days. Neither table has a column
for page text: a reading leaves a hash and at most one number behind.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

LABEL_WIDTH = 80
URL_WIDTH = 2000
CONDITION_WIDTH = 240
SELECTOR_WIDTH = 200
REASON_WIDTH = 120


class Watch(Base):
    __tablename__ = "watches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(LABEL_WIDTH), nullable=False)
    url: Mapped[str] = mapped_column(String(URL_WIDTH), nullable=False)
    condition: Mapped[str] = mapped_column(String(CONDITION_WIDTH), nullable=False)
    every_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    selector: Mapped[str | None] = mapped_column(String(SELECTOR_WIDTH), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    condition_met: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WatchReading(Base):
    __tablename__ = "watch_readings"
    __table_args__ = (
        # The same reading processed twice is one row, in the database too.
        UniqueConstraint("watch_id", "read_at", name="uq_watch_readings_watch_read_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    watch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("watches.id", ondelete="CASCADE"), nullable=False
    )
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(REASON_WIDTH), nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
