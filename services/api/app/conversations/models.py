"""The conversation tables (conversation-transcripts).

Canonical schema: ``alembic/versions/conversation_transcripts.py``. A conversation is TEXT:
``conversation_segments`` holds one line each - what was said, when, whether it was the owner,
the voice's number in that conversation and, when the voice belongs to a consenting named
person, that person. No table has a column for audio. The only bytes column is
``conversation_people.profile_sealed``: the person's DERIVED voice embedding, encrypted, and
the database refuses it unless ``consent_at`` is set (KVKK: a voiceprint is biometric data).

The label a line shows is not stored: it is read from ``is_owner`` / the person's name /
``speaker_no`` each time, so deleting a person turns every line of theirs back into
'Konuşmacı N' without rewriting a transcript.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

TEXT_WIDTH = 4000
NAME_WIDTH = 80
NOTE_WIDTH = 200
TITLE_WIDTH = 120
MODEL_WIDTH = 80
MODES = ("manual", "home")


class ConversationRow(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("mode IN ('manual', 'home')", name="ck_conversations_mode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str | None] = mapped_column(String(TITLE_WIDTH), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PersonRow(Base):
    __tablename__ = "conversation_people"
    __table_args__ = (
        # No voice profile without the person's recorded consent - in the database too.
        CheckConstraint(
            "profile_sealed IS NULL OR consent_at IS NOT NULL",
            name="ck_conversation_people_profile_needs_consent",
        ),
        UniqueConstraint("name_key", name="uq_conversation_people_name_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(NAME_WIDTH), nullable=False)
    #: ``name`` case-folded the Turkish way: 'bu ahmet' and 'bu Ahmet' are one person.
    name_key: Mapped[str] = mapped_column(String(NAME_WIDTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_note: Mapped[str | None] = mapped_column(String(NOTE_WIDTH), nullable=True)
    profile_sealed: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    profile_model: Mapped[str | None] = mapped_column(String(MODEL_WIDTH), nullable=True)


class SegmentRow(Base):
    __tablename__ = "conversation_segments"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_conversation_segments_seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    spoken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    text: Mapped[str] = mapped_column(String(TEXT_WIDTH), nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    speaker_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversation_people.id", ondelete="SET NULL"), nullable=True, index=True
    )


class ConversationSettingRow(Base):
    """One row (id 1): the standing 'evde dinle' switch."""

    __tablename__ = "conversation_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    home_listen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
