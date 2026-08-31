"""Voice ORM models (M4).

Canonical schema is the lead-authored migration
alembic/versions/20260831_0004_voice.py; these models MUST match the
``voice_profiles`` and ``speaker_profiles`` tables it creates. (The other two
M4 tables — ``pronunciation_entries`` and ``narration_sessions`` — belong to the
narration engineer's module.)

Types are portable (generic ``Uuid`` + ``JSON`` with a JSONB variant on
PostgreSQL) exactly like app/artifacts/models.py, so unit tests exercise the
service layer on SQLite while integration tests run the real PostgreSQL schema.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")


class VoiceProfile(Base):
    """Owner voice/narration preferences (VOICE_SPEC §12). Single-owner system:
    one row labelled 'owner' in practice."""

    __tablename__ = "voice_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="owner")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="tr-TR")
    tts_provider_preference_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    narration_settings_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SpeakerProfile(Base):
    """Owner speaker-verification profile (VOICE_SPEC §10). ``embedding_ref``
    points at an encrypted derived-profile blob in the object store — never raw
    audio. ``enrollment_metadata_json`` holds non-audio metadata (sample count,
    dimension, model)."""

    __tablename__ = "speaker_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="owner")
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    enrollment_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["SpeakerProfile", "VoiceProfile"]
