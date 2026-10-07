"""Person cards: written from a conversation's follow-ups, read back with the date.

'Ahmet'e ne söz vermiştim' is :func:`recall_promises`; 'Ayşe ile en son ne konuştuk' is
:func:`last_talk`. Both answer only from the cards: what was said, by whom, when, and the line
it was said in. A name nobody has a card for is said so, never guessed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.conversations.models import SegmentRow
from app.conversations.service import name_key
from app.people.models import (
    NAME_WIDTH,
    RELATION_WIDTH,
    FollowupRow,
    PersonCardRow,
)

ISTANBUL = ZoneInfo("Europe/Istanbul")
MONTHS = (
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)
WEEKDAYS = ("pazartesi", "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar")


def local(value: datetime) -> datetime:
    """SQLite hands a timestamptz back naive; every stored value is UTC."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(ISTANBUL)


def say_day(value: datetime, *, year: bool = False, weekday: bool = False) -> str:
    day = local(value)
    text = f"{day.day} {MONTHS[day.month - 1]}"
    if year:
        text += f" {day.year}"
    if weekday:
        text += f" {WEEKDAYS[day.weekday()]}"
    return text


def say_due(row: FollowupRow) -> str:
    if row.due_at is None:
        return ""
    text = say_day(row.due_at, weekday=True)
    if row.due_has_time:
        text += " " + local(row.due_at).strftime("%H:%M")
    return text


def card_for(
    db: Session,
    name: str,
    *,
    now: datetime,
    relation: str | None = None,
    conversation_id: uuid.UUID | None = None,
    talked_at: datetime | None = None,
    topic_seq: int | None = None,
) -> PersonCardRow:
    """The card for ``name`` (made when missing); a newer talk moves 'last talk' forward."""
    clean = " ".join(name.split())[:NAME_WIDTH]
    key = name_key(clean)
    row = db.execute(
        select(PersonCardRow).where(PersonCardRow.name_key == key)
    ).scalar_one_or_none()
    if row is None:
        row = PersonCardRow(name=clean, name_key=key, created_at=now, updated_at=now)
        db.add(row)
    if relation and not row.relation:
        row.relation = relation[:RELATION_WIDTH]
    if talked_at is not None and (
        row.last_talk_at is None or local(talked_at) >= local(row.last_talk_at)
    ):
        if row.last_conversation_id != conversation_id or row.last_topic_seq is None:
            row.last_topic_seq = topic_seq  # the first line of that conversation that named them
        row.last_talk_at = talked_at
        row.last_conversation_id = conversation_id
    row.updated_at = now
    db.flush()
    return row


def _card(db: Session, name: str) -> PersonCardRow | None:
    key = name_key(name or "")
    if not key:
        return None
    return db.execute(
        select(PersonCardRow).where(PersonCardRow.name_key == key)
    ).scalar_one_or_none()


@dataclass(frozen=True, slots=True)
class PromiseView:
    id: uuid.UUID
    direction: str
    what: str
    quote: str
    spoken_at: datetime
    due_at: datetime | None
    conversation_id: uuid.UUID | None
    segment_seq: int


@dataclass(frozen=True, slots=True)
class RecallView:
    name: str
    found: bool
    speech: str
    promises: list[PromiseView] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TalkView:
    name: str
    found: bool
    speech: str
    at: datetime | None = None
    conversation_id: uuid.UUID | None = None
    topic: str | None = None


def recall_promises(db: Session, name: str) -> RecallView:
    card = _card(db, name)
    if card is None:
        return RecallView(name, False, f"{name} ile ilgili bir kaydım yok.")
    rows = db.execute(
        select(FollowupRow).where(
            FollowupRow.card_id == card.id,
            FollowupRow.kind == "promise",
            FollowupRow.done.is_(False),
        )
    ).scalars()
    ordered = sorted(rows, key=lambda r: (r.direction != "owner", local(r.spoken_at)))
    views = [
        PromiseView(
            r.id,
            r.direction or "owner",
            r.what,
            r.quote,
            r.spoken_at,
            r.due_at,
            r.conversation_id,
            r.segment_seq,
        )
        for r in ordered
    ]
    if not views:
        return RecallView(card.name, True, f"{card.name} ile aranızda açık bir söz yok.")

    def line(r: FollowupRow) -> str:
        due = say_due(r)
        return f"{say_day(r.spoken_at)} günü: {r.what}" + (f" ({due})" if due else "")

    mine = [line(r) for r in ordered if r.direction == "owner"]
    theirs = [line(r) for r in ordered if r.direction == "them"]
    parts = []
    if mine:
        parts.append(f"{card.name} - senin sözlerin: " + "; ".join(mine) + ".")
    if theirs:
        parts.append(f"{card.name} - onun sözleri: " + "; ".join(theirs) + ".")
    return RecallView(card.name, True, " ".join(parts), views)


def last_talk(db: Session, name: str) -> TalkView:
    card = _card(db, name)
    if card is None or card.last_talk_at is None:
        return TalkView(name, False, f"{name} ile konuştuğunuza dair bir kaydım yok.")
    when = say_day(card.last_talk_at, year=True) + ", " + local(card.last_talk_at).strftime("%H:%M")
    speech = f"{card.name} ile en son konuşma: {when}."
    topic = None
    if card.last_conversation_id is not None and card.last_topic_seq is not None:
        # Read from the transcript: once it is deleted ('unut') there is no topic to say.
        topic = db.execute(
            select(SegmentRow.text).where(
                SegmentRow.conversation_id == card.last_conversation_id,
                SegmentRow.seq == card.last_topic_seq,
            )
        ).scalar_one_or_none()
    if topic:
        speech += f" Konu: '{topic}'."
    return TalkView(card.name, True, speech, card.last_talk_at, card.last_conversation_id, topic)


def list_cards(db: Session) -> list[PersonCardRow]:
    return list(db.execute(select(PersonCardRow).order_by(PersonCardRow.name_key)).scalars())


def open_promises(db: Session, card_id: uuid.UUID) -> list[FollowupRow]:
    return list(
        db.execute(
            select(FollowupRow)
            .where(
                FollowupRow.card_id == card_id,
                FollowupRow.kind == "promise",
                FollowupRow.done.is_(False),
            )
            .order_by(FollowupRow.spoken_at)
        ).scalars()
    )
