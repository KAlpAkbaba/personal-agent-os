"""Narration ORM models (M4).

Canonical schema lives in alembic/versions/20260831_0004_voice.py (authored by
the lead). These models MUST match the ``narration_sessions`` and
``pronunciation_entries`` tables exactly. As in app/artifacts/models.py, types
are portable (generic ``Uuid``, JSON with a JSONB variant on PostgreSQL) so unit
tests run against SQLite while integration tests run against real PostgreSQL.

``voice_profiles`` and ``speaker_profiles`` from the same migration are owned by
app/voice/models.py (voice provider engineer); they are intentionally NOT
redeclared here to avoid a duplicate mapping on the shared metadata.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

# narration_sessions.state values (mirror app.narration.commands.State).
NARRATION_STATE_IDLE = "IDLE"
NARRATION_STATE_READING = "READING"
NARRATION_STATE_PAUSED = "PAUSED"
NARRATION_STATE_EXPLAINING = "EXPLAINING"

NARRATION_STATES = (
    NARRATION_STATE_IDLE,
    NARRATION_STATE_READING,
    NARRATION_STATE_PAUSED,
    NARRATION_STATE_EXPLAINING,
)


class NarrationSession(Base):
    __tablename__ = "narration_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    artifact_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # Semantic cursor is the source of truth (API_AND_PROTOCOLS §7); survives
    # regenerated audio. playback_seconds below is secondary cache metadata.
    semantic_cursor_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    playback_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    speed: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default=NARRATION_STATE_IDLE)
    voice_profile_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PronunciationEntry(Base):
    __tablename__ = "pronunciation_entries"
    __table_args__ = (
        UniqueConstraint("token", "context", name="uq_pronunciation_token_context"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    spoken_form: Mapped[str] = mapped_column(String(512), nullable=False)
    provider_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    context: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Explicit owner corrections carry the highest confidence (VOICE_SPEC §5).
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    explicit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "NarrationSession",
    "PronunciationEntry",
    "NARRATION_STATES",
    "NARRATION_STATE_IDLE",
    "NARRATION_STATE_READING",
    "NARRATION_STATE_PAUSED",
    "NARRATION_STATE_EXPLAINING",
]
