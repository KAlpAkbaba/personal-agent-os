"""The misheard notebook's one table.

Canonical schema: ``alembic/versions/20261002_0065_misheard_utterances.py``. One row per
sentence the system did not understand, as the recogniser WROTE it - never audio. The widths
below are the CONTRACT's and the migration's; ``service.record`` cuts to them before the row
is built, because SQLite (the unit suite) does not enforce a VARCHAR's length and PostgreSQL
answers one character too many with an error.

No foreign key on ``session_id`` or ``device_id``, on purpose: the row outlives the session
it was heard in (a swept session must not take the owner's unanswered question with it) and
leaves by its own three doors only - ``expires_at``, "unut", the owner's delete.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

SENTENCE_WIDTH = 2000
MEANT_WIDTH = 2000
ENGINE_WIDTH = 64
INTENT_WIDTH = 64
TOOL_WIDTH = 64


class MisheardUtterance(Base):
    __tablename__ = "misheard_utterances"
    __table_args__ = (
        # What makes the writer idempotent in the database too: two processes that hear of
        # the same sentence at once cannot both keep a row.
        UniqueConstraint("session_id", "heard_at", name="uq_misheard_utterances_session_heard"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    heard_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sentence: Mapped[str] = mapped_column(String(SENTENCE_WIDTH), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    engine: Mapped[str | None] = mapped_column(String(ENGINE_WIDTH), nullable=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    band: Mapped[str | None] = mapped_column(String(8), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    resolved_intent: Mapped[str | None] = mapped_column(String(INTENT_WIDTH), nullable=True)
    tool: Mapped[str | None] = mapped_column(String(TOOL_WIDTH), nullable=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    meant: Mapped[str | None] = mapped_column(String(MEANT_WIDTH), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
