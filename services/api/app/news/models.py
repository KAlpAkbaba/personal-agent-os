"""Latest News Mode ORM rows (docs/M26_LATEST_NEWS_MODE_SPEC.md §1-§5).

Canonical schema: ``alembic/versions/20260908_0034_news_mode.py``. Same discipline as
``app.research.models`` / ``app.routines.models``: portable column types (generic
``Uuid``, ``JSON`` with a ``JSONB`` variant on Postgres) so the service layer
unit-tests on SQLite, plus ``CheckConstraint``s spelling out the closed vocabularies.

Three tables:

``NewsSourceRow`` — the owner's durable, governed source configuration (spec §1). A
source is identified by its EXACT channel identity, never by display name: ``channel_id``
is nullable and ``identity_status`` starts (and stays) ``needs_identity`` until an
authoritative signal (an owner-given URL, or a provider lookup that returns the
canonical id — ``app.news.identity``) resolves it. A source with no resolved identity
can be listed and edited but the resolver/playback paths refuse it honestly rather than
guessing which channel the display name might mean.

``NewsResolutionRow`` — append-only audit trail of every resolver run (spec §2, §4):
which discovery tier answered, every candidate considered and why each one was accepted
or rejected, and the selection reason. This is what makes "son haber ne zaman
yüklenmiş?" answerable without re-running the resolver, and what the live qualification
(spec §4) leaves a durable record of.

``NewsPlaybackContextRow`` — one row per governed playback (spec §5): the browser
worker's ``news`` profile is opened with ``session_id = "news-<id>"`` (this row's own
id IS the ``news_media_context_id``), so playback state, the video/channel/publish
identity and the playback receipt are all durable and queryable, and "cleanup closes
only that context" has something exact to name.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

# --------------------------------------------------------------------------- #
# vocabularies
# --------------------------------------------------------------------------- #

#: Content policy (spec §2), owner-configurable per source. A Shorts clip or an
#: unrelated promo must never silently satisfy "the latest full bulletin" — only
#: ``latest_any_news`` accepts the newest upload unconditionally.
CONTENT_TYPE_ANY_NEWS = "latest_any_news"
CONTENT_TYPE_FULL_BROADCAST = "latest_full_broadcast"
CONTENT_TYPE_MAIN_NEWS = "latest_main_news"
CONTENT_TYPES = (CONTENT_TYPE_ANY_NEWS, CONTENT_TYPE_FULL_BROADCAST, CONTENT_TYPE_MAIN_NEWS)

#: A source's provider. Only ``youtube`` exists today (spec's own scope); the column
#: is a string, not a bare constant, so a second provider is a data row, not a code fork.
PROVIDER_YOUTUBE = "youtube"
PROVIDERS = (PROVIDER_YOUTUBE,)

#: Identity resolution status (spec §1's central rule: never guess a channel identity
#: from a display name alone).
IDENTITY_RESOLVED = "resolved"
IDENTITY_NEEDS_IDENTITY = "needs_identity"
IDENTITY_STATUSES = (IDENTITY_RESOLVED, IDENTITY_NEEDS_IDENTITY)

#: Which discovery tier answered a resolution (spec §2's own preference order, recorded
#: rather than assumed).
ANSWERED_BY_CHANNEL_FEED = "channel_feed"
ANSWERED_BY_VIDEOS_LISTING = "videos_listing"
ANSWERED_BY_DOM_EXTRACTION = "dom_extraction"
ANSWERED_BY_FIXTURE = "fixture"
ANSWERED_BY_VALUES = (
    ANSWERED_BY_CHANNEL_FEED,
    ANSWERED_BY_VIDEOS_LISTING,
    ANSWERED_BY_DOM_EXTRACTION,
    ANSWERED_BY_FIXTURE,
)

#: Playback context status (spec §5).
PLAYBACK_STATUS_OPENING = "opening"
PLAYBACK_STATUS_PLAYING = "playing"
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


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class NewsSourceRow(Base):
    __tablename__ = "news_sources"
    __table_args__ = (
        CheckConstraint(_in_list("content_type", CONTENT_TYPES), name="ck_news_sources_content"),
        CheckConstraint(_in_list("provider", PROVIDERS), name="ck_news_sources_provider"),
        CheckConstraint(
            _in_list("identity_status", IDENTITY_STATUSES), name="ck_news_sources_identity"
        ),
        Index("ix_news_sources_enabled_priority", "enabled", "priority"),
    )

    #: A short owner/voice-facing slug ("show-ana-haber"), not a UUID: this is the
    #: identifier a spoken "Show haberi aç" and a REST client both name.
    news_source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default=PROVIDER_YOUTUBE)
    #: Exactly what the owner gave us (a URL, a handle, a channel id) — kept verbatim so
    #: a later re-resolution attempt has the original input, never a lossy paraphrase.
    channel_input: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: The CANONICAL channel id (YouTube: "UC" + 22 chars), set ONLY by
    #: ``app.news.identity.resolve_channel_identity`` from an authoritative signal.
    #: NULL means "not established" — the resolver and playback both refuse honestly
    #: rather than guessing.
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    identity_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=IDENTITY_NEEDS_IDENTITY
    )
    #: How the identity was established, for audit ("owner_url" | "provider_lookup"),
    #: NULL while unresolved.
    identity_resolved_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Lower sorts first — the voice default-source pick (spec §6's bare "Haberleri aç")
    #: prefers the lowest priority among enabled, resolved sources.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    content_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CONTENT_TYPE_ANY_NEWS
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NewsResolutionRow(Base):
    __tablename__ = "news_resolutions"
    __table_args__ = (
        CheckConstraint(
            _in_list("answered_by", ANSWERED_BY_VALUES), name="ck_news_resolutions_answered_by"
        ),
        Index("ix_news_resolutions_source_created", "news_source_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    news_source_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("news_sources.news_source_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    answered_by: Mapped[str] = mapped_column(String(24), nullable=False)
    #: Every candidate the resolver considered: [{video_id, title, published_at,
    #: accepted, reason}], newest first — the full accounting behind the pick.
    candidates_json: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    selected_video_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    selected_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    selected_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    #: Why THIS candidate ("bulletin_marker" | "newest" | "newest_non_short_fallback" |
    #: "none_eligible" | ...) — app.news.resolver's own vocabulary.
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NewsPlaybackContextRow(Base):
    __tablename__ = "news_playback_contexts"
    __table_args__ = (
        CheckConstraint(
            _in_list("status", PLAYBACK_STATUSES), name="ck_news_playback_contexts_status"
        ),
    )

    #: This id IS the ``news_media_context_id`` the browser session_id is derived from
    #: ("news-<id>", packages/protocol/BROWSER_CAPABILITIES.md §2 v1.3).
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    news_source_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("news_sources.news_source_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    video_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    video_title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The browser session id this context owns ("news-<id>") — cleanup
    #: (``browser.media_stop``/``session_close``) is scoped to exactly this session,
    #: never another one (spec §5: "cleanup closes only that context").
    session_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PLAYBACK_STATUS_OPENING)
    #: The device worker's own ``media_play``/``media_status``/``media_stop`` result —
    #: the strongest playback evidence the worker actually offers (spec §5); this is
    #: never upgraded by this service, only stored and classified honestly.
    receipt_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "ANSWERED_BY_CHANNEL_FEED",
    "ANSWERED_BY_DOM_EXTRACTION",
    "ANSWERED_BY_FIXTURE",
    "ANSWERED_BY_VALUES",
    "ANSWERED_BY_VIDEOS_LISTING",
    "CONTENT_TYPE_ANY_NEWS",
    "CONTENT_TYPE_FULL_BROADCAST",
    "CONTENT_TYPE_MAIN_NEWS",
    "CONTENT_TYPES",
    "IDENTITY_NEEDS_IDENTITY",
    "IDENTITY_RESOLVED",
    "IDENTITY_STATUSES",
    "PLAYBACK_STATUS_CLOSED",
    "PLAYBACK_STATUS_FAILED",
    "PLAYBACK_STATUS_OPENING",
    "PLAYBACK_STATUS_PLAYING",
    "PLAYBACK_STATUS_UNVERIFIED",
    "PLAYBACK_STATUSES",
    "PROVIDER_YOUTUBE",
    "PROVIDERS",
    "NewsPlaybackContextRow",
    "NewsResolutionRow",
    "NewsSourceRow",
]
