"""Owner media playback ORM row (ADR-0112).

Canonical schema: ``alembic/versions/20260910_0039_owner_media_playbacks.py``.
Same discipline as ``app.news.models``: portable column types so the service
layer unit-tests on SQLite, and a ``CheckConstraint`` spelling out the closed
status vocabulary.

One table, and it exists for one reason: **a feature that can start something it
cannot stop is not finished.** "Durdur" has to name the exact browser session it
opened -- never "the media session", of which there may be an alarm's and a news
video's at the same time -- so the session id is durable, and it is derived from
this row's own id (``owner-media-<id>``) exactly as Latest News Mode derives
``news-<id>``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

#: The session was opened and the video handed over; nothing proven yet.
PLAYBACK_STATUS_OPENING = "opening"
#: ``browser.media_play`` reported ``verified: true`` -- the element's own
#: ``currentTime`` really advanced.
PLAYBACK_STATUS_PLAYING = "playing"
#: It opened and the worker could NOT prove it is playing. Deliberately its own
#: status rather than folded into either neighbour: telling the owner "çalıyor"
#: on this evidence is the exact dishonesty the news spec forbids, and telling
#: them "olmadı" when a video may well be on screen is no better.
PLAYBACK_STATUS_UNVERIFIED = "unverified"
PLAYBACK_STATUS_FAILED = "failed"
PLAYBACK_STATUS_CLOSED = "closed"

PLAYBACK_STATUSES = (
    PLAYBACK_STATUS_OPENING,
    PLAYBACK_STATUS_PLAYING,
    PLAYBACK_STATUS_UNVERIFIED,
    PLAYBACK_STATUS_FAILED,
    PLAYBACK_STATUS_CLOSED,
)

#: Statuses where something may still be on the owner's screen, so "durdur" has
#: something to stop and a second "aç" must close this one first.
PLAYBACK_LIVE_STATUSES = (
    PLAYBACK_STATUS_OPENING,
    PLAYBACK_STATUS_PLAYING,
    PLAYBACK_STATUS_UNVERIFIED,
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class OwnerMediaPlaybackRow(Base):
    """One thing the owner asked to be played, and what became of it."""

    __tablename__ = "owner_media_playbacks"
    __table_args__ = (
        CheckConstraint(
            _in_list("status", PLAYBACK_STATUSES), name="ck_owner_media_playbacks_status"
        ),
        Index("ix_owner_media_playbacks_status", "status"),
        Index("ix_owner_media_playbacks_created_at", "created_at"),
    )

    #: The browser session id is ``owner-media-<this id>``.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: What the owner actually said, kept verbatim so "neyi açmıştın?" is
    #: answerable from a row rather than from a reconstruction.
    request_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: What was handed to ``browser.search``.
    query: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    video_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    video_title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PLAYBACK_STATUS_OPENING)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The worker's own ``media_play``/``media_stop`` result, stored as it came
    #: and never upgraded -- the strongest evidence there is about this playback.
    receipt_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "PLAYBACK_LIVE_STATUSES",
    "PLAYBACK_STATUSES",
    "PLAYBACK_STATUS_CLOSED",
    "PLAYBACK_STATUS_FAILED",
    "PLAYBACK_STATUS_OPENING",
    "PLAYBACK_STATUS_PLAYING",
    "PLAYBACK_STATUS_UNVERIFIED",
    "OwnerMediaPlaybackRow",
]
