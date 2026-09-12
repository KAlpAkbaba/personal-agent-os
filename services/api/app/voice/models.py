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

from sqlalchemy import JSON, Boolean, DateTime, Float, String, Uuid, func
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


class SpeakerVerdictRow(Base):
    """The last speaker verdict reached for an owner session (B05 req 245/247/665).

    Before this the verdict was computed by one route, returned to the caller and then
    forgotten - "advisory" in the most literal sense, since nothing could read it even if it
    wanted to. A sensitive voice action needs to ask "who is speaking, how sure are we, and
    how long ago did we last check?", and none of those three had an answer that outlived
    the HTTP response.

    What is stored is the VERDICT, never the probe: a score, a decision, the device-trust
    input that produced it and when. An embedding is biometric material and belongs in the
    encrypted profile blob (``speaker_profiles.embedding_ref``) or nowhere.
    """

    __tablename__ = "speaker_verdicts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: The owner session the probe was presented on. One row per session, replaced in place:
    #: the question is always "how sure are we RIGHT NOW", never "what is the history".
    owner_session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    #: The device-trust input, recorded so a verdict can never be re-read as if it had been
    #: reached under different conditions than it was.
    device_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_accept: Mapped[float] = mapped_column(Float, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["SpeakerProfile", "SpeakerVerdictRow", "VoiceProfile"]
