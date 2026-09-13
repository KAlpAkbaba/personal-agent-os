"""B11 req 367/368/377/378/379/380/389: one durable row per notification.

The inbox read the FAKE push provider's in-memory ``deque``. That has two consequences and
both are bad: a restart loses every notification the owner had not seen, and in production
there is nothing to read at all - a real transport hands the message to Apple or Google and
keeps no log, which is exactly why the fake's log was standing in for one.

So the notification stops being a thing a transport remembers and becomes a row this system
owns. It is written BEFORE anything is attempted, every channel that tries to deliver it
updates the same row, and the inbox reads the row rather than a transport's memory. The
ladder (req 389) is then a question about one row - which channel has not been tried yet -
rather than a chain of separate queues that can each lose their own copy.

**The inbox is the floor of the ladder, not a rung.** Recording the notification IS putting
it in the inbox; toast, sound and push are attempts to get the owner's attention sooner. A
notification can therefore never be "lost" by a failing transport - the worst case is that
the owner finds it in the inbox instead of hearing it, which is a degradation and not a loss.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

#: req 378. Three levels, because a fourth invites an argument about which one a thing is.
#: URGENT is the only level that speaks during quiet hours; that is the whole reason the
#: distinction exists, so it is deliberately hard to reach for.
PRIORITY_URGENT: Final[str] = "urgent"
PRIORITY_NORMAL: Final[str] = "normal"
PRIORITY_LOW: Final[str] = "low"
PRIORITIES: Final[tuple[str, ...]] = (PRIORITY_URGENT, PRIORITY_NORMAL, PRIORITY_LOW)

#: req 389, in order. Each rung is a way to reach the owner sooner than the one after it.
#: `inbox` is last and always succeeds, because the row is already there.
CHANNEL_TOAST: Final[str] = "toast"
CHANNEL_SOUND: Final[str] = "sound"
CHANNEL_PUSH: Final[str] = "push"
CHANNEL_INBOX: Final[str] = "inbox"
LADDER: Final[tuple[str, ...]] = (CHANNEL_TOAST, CHANNEL_SOUND, CHANNEL_PUSH, CHANNEL_INBOX)


class NotificationRow(Base):
    """One notification, owned by this system rather than by a transport."""

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    #: What produced it (`artifact_ready`, `briefing`, `alarm`, ...). Not a display string.
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    #: Turkish, and the sentence the owner actually reads (tr-TR first-class, CLAUDE.md).
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PRIORITY_NORMAL, index=True
    )
    #: req 380. Notifications sharing a key are one thing that happened repeatedly; a newer
    #: one supersedes an older UNREAD sibling rather than stacking beside it. Empty means
    #: "this stands alone", which is the safe default: grouping is a claim that two things
    #: are the same thing, and making that claim by accident hides one of them.
    group_key: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)

    #: req 389: which rung actually reached the owner, and when. NULL means no channel has
    #: yet - the row is in the inbox and nothing more.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The rungs already attempted, so the ladder never retries one that failed and never
    #: skips one it has not tried.
    attempted_json: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)

    #: req 379. Set when quiet hours defer a non-urgent notification. It is still IN the
    #: inbox the whole time - deferring is about not making a noise, never about hiding.
    deferred_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: req 368: the owner has seen it. Separate from `delivered_at`, which says a channel
    #: carried it - a toast that appeared while nobody was at the desk was delivered and
    #: not read.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: req 380: superseded by a newer member of its group. Kept rather than deleted, so
    #: "what did it say the first time" is still answerable.
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: B07's bounded-delivery shape, per notification (app.notifications.delivery).
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quarantined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Whether every rung has been tried. The row stays readable in the inbox either way;
    #: this only says the system has stopped trying to interrupt the owner about it.
    ladder_exhausted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


Index("ix_notifications_unread", NotificationRow.read_at, NotificationRow.created_at)


__all__ = [
    "CHANNEL_INBOX",
    "CHANNEL_PUSH",
    "CHANNEL_SOUND",
    "CHANNEL_TOAST",
    "LADDER",
    "PRIORITIES",
    "PRIORITY_LOW",
    "PRIORITY_NORMAL",
    "PRIORITY_URGENT",
    "NotificationRow",
]
