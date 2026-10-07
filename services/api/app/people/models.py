"""The person cards and the follow-ups taken from conversations (conversation-followups).

Canonical schema: ``alembic/versions/conversation_followups.py``.

``people_cards`` is one row per person the owner talked about or with: the name and its folded
key (unique: 'AHMET' and 'Ahmet' are one card), how they relate to him when somebody SAID it,
and the last conversation they were in. ``people_followups`` is one row per item taken from a
conversation - a promise (``direction`` 'owner' = he promised, 'them' = they promised him) or a
date - and every row cites the line it came from (``conversation_id`` + ``segment_seq`` +
``quote``, the quote being a piece of that line's text). A calendar item is ``calendar_state``
'proposed' until the owner says 'tamam' ('written') or no ('declined'); NULL is "not a
calendar item". A follow-up lives as long as its conversation (``ON DELETE CASCADE``): 'unut'
and deleting a conversation leave none of the other side's words in either table, and the card
keeps no line's text - ``last_topic_seq`` points at the line, read while the transcript exists.
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
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

NAME_WIDTH = 80
RELATION_WIDTH = 60
WHAT_WIDTH = 200
QUOTE_WIDTH = 400
KINDS = ("promise", "date")
DIRECTIONS = ("owner", "them")
CALENDAR_STATES = ("proposed", "written", "declined", "failed")


class PersonCardRow(Base):
    __tablename__ = "people_cards"
    __table_args__ = (UniqueConstraint("name_key", name="uq_people_cards_name_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(NAME_WIDTH), nullable=False)
    name_key: Mapped[str] = mapped_column(String(NAME_WIDTH), nullable=False)
    relation: Mapped[str | None] = mapped_column(String(RELATION_WIDTH), nullable=True)
    last_talk_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    #: The first cited line (``seq``) of the last conversation that mentioned them; its text
    #: is read from the transcript, never copied here.
    last_topic_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FollowupRow(Base):
    __tablename__ = "people_followups"
    __table_args__ = (
        CheckConstraint("kind IN ('promise', 'date')", name="ck_people_followups_kind"),
        CheckConstraint(
            "direction IS NULL OR direction IN ('owner', 'them')",
            name="ck_people_followups_direction",
        ),
        CheckConstraint(
            "calendar_state IS NULL OR calendar_state IN "
            "('proposed', 'written', 'declined', 'failed')",
            name="ck_people_followups_calendar_state",
        ),
        CheckConstraint("segment_seq >= 1", name="ck_people_followups_segment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    card_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("people_cards.id", ondelete="CASCADE"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    what: Mapped[str] = mapped_column(String(WHAT_WIDTH), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Whether ``due_at`` carries a time of day ('14:00'), or only the day ('cuma').
    due_has_time: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    segment_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    quote: Mapped[str] = mapped_column(String(QUOTE_WIDTH), nullable=False)
    spoken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    calendar_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    calendar_proposal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
