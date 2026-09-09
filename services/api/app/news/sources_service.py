"""Durable, owner-governed news-source configuration (docs/M26_LATEST_NEWS_MODE_SPEC.md
§1). CRUD plus the one rule that matters more than any of it: a source's ``channel_id``
is set ONLY from an authoritative signal (``app.news.identity``), never from its
``display_name`` — a source that cannot be resolved this way is persisted as
``NEEDS_NEWS_SOURCE_IDENTITY`` and stays usable for editing, never for resolution or
playback until it is resolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.news.identity import resolve_channel_identity
from app.news.models import (
    CONTENT_TYPE_ANY_NEWS,
    CONTENT_TYPES,
    IDENTITY_NEEDS_IDENTITY,
    IDENTITY_RESOLVED,
    PROVIDER_YOUTUBE,
    NewsSourceRow,
)

ERROR_INVALID_SLUG = "invalid_news_source_id"
ERROR_ALREADY_EXISTS = "already_exists"
ERROR_NOT_FOUND = "not_found"
ERROR_INVALID_CONTENT_TYPE = "invalid_content_type"
ERROR_IDENTITY_UNRESOLVED = "identity_unresolved"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")


class NewsSourceError(ValueError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


@dataclass(frozen=True, slots=True)
class NewsSourceView:
    news_source_id: str
    display_name: str
    provider: str
    channel_input: str | None
    channel_id: str | None
    identity_status: str
    identity_resolved_by: str | None
    enabled: bool
    priority: int
    content_type: str

    @classmethod
    def from_row(cls, row: NewsSourceRow) -> NewsSourceView:
        return cls(
            news_source_id=row.news_source_id,
            display_name=row.display_name,
            provider=row.provider,
            channel_input=row.channel_input,
            channel_id=row.channel_id,
            identity_status=row.identity_status,
            identity_resolved_by=row.identity_resolved_by,
            enabled=row.enabled,
            priority=row.priority,
            content_type=row.content_type,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "news_source_id": self.news_source_id,
            "display_name": self.display_name,
            "provider": self.provider,
            "channel_input": self.channel_input,
            "channel_id": self.channel_id,
            "identity_status": self.identity_status,
            "identity_resolved_by": self.identity_resolved_by,
            "enabled": self.enabled,
            "priority": self.priority,
            "content_type": self.content_type,
        }


def _validate_slug(news_source_id: str) -> None:
    if not _SLUG_RE.match(news_source_id):
        raise NewsSourceError(
            ERROR_INVALID_SLUG,
            "news_source_id must be lowercase letters, digits and hyphens (1-64 chars)",
        )


def create_source(
    db: Session,
    *,
    news_source_id: str,
    display_name: str,
    channel_input: str | None = None,
    provider: str = PROVIDER_YOUTUBE,
    content_type: str = CONTENT_TYPE_ANY_NEWS,
    priority: int = 100,
    enabled: bool = True,
    fetch_page: object | None = None,
) -> NewsSourceView:
    """Create a source. When ``channel_input`` is given, identity resolution is
    attempted immediately (never deferred silently) — an input that cannot be
    authoritatively resolved (a bare display name, an unresolvable handle with no
    ``fetch_page``) leaves the source ``needs_identity`` rather than refusing the
    whole create: the row is still useful for the owner to see and correct."""
    _validate_slug(news_source_id)
    if content_type not in CONTENT_TYPES:
        raise NewsSourceError(ERROR_INVALID_CONTENT_TYPE, f"unknown content_type: {content_type!r}")
    existing = db.get(NewsSourceRow, news_source_id)
    if existing is not None:
        raise NewsSourceError(ERROR_ALREADY_EXISTS, f"{news_source_id} already exists")

    channel_id: str | None = None
    identity_status = IDENTITY_NEEDS_IDENTITY
    identity_resolved_by: str | None = None
    if channel_input:
        identity = resolve_channel_identity(channel_input, fetch_page=fetch_page)
        if identity is not None:
            channel_id = identity.channel_id
            identity_status = IDENTITY_RESOLVED
            identity_resolved_by = identity.resolved_by

    now = datetime.now(UTC)
    row = NewsSourceRow(
        news_source_id=news_source_id,
        display_name=display_name,
        provider=provider,
        channel_input=channel_input,
        channel_id=channel_id,
        identity_status=identity_status,
        identity_resolved_by=identity_resolved_by,
        enabled=enabled,
        priority=priority,
        content_type=content_type,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    return NewsSourceView.from_row(row)


def update_source(
    db: Session,
    news_source_id: str,
    *,
    display_name: str | None = None,
    channel_input: str | None = None,
    content_type: str | None = None,
    priority: int | None = None,
    enabled: bool | None = None,
    fetch_page: object | None = None,
) -> NewsSourceView:
    """Update a source. Giving ``channel_input`` ALWAYS re-attempts identity
    resolution from that new input (never keeps a stale, possibly-wrong prior
    resolution) — success moves the source to ``resolved``; failure moves it back to
    ``needs_identity`` rather than silently keeping an old (and now unconfirmed)
    channel_id."""
    row = db.get(NewsSourceRow, news_source_id)
    if row is None:
        raise NewsSourceError(ERROR_NOT_FOUND, f"{news_source_id} not found")
    if display_name is not None:
        row.display_name = display_name
    if content_type is not None:
        if content_type not in CONTENT_TYPES:
            raise NewsSourceError(
                ERROR_INVALID_CONTENT_TYPE, f"unknown content_type: {content_type!r}"
            )
        row.content_type = content_type
    if priority is not None:
        row.priority = priority
    if enabled is not None:
        row.enabled = enabled
    if channel_input is not None:
        row.channel_input = channel_input
        identity = resolve_channel_identity(channel_input, fetch_page=fetch_page)
        if identity is not None:
            row.channel_id = identity.channel_id
            row.identity_status = IDENTITY_RESOLVED
            row.identity_resolved_by = identity.resolved_by
        else:
            row.channel_id = None
            row.identity_status = IDENTITY_NEEDS_IDENTITY
            row.identity_resolved_by = None
    row.updated_at = datetime.now(UTC)
    db.commit()
    return NewsSourceView.from_row(row)


def get_source(db: Session, news_source_id: str) -> NewsSourceView | None:
    row = db.get(NewsSourceRow, news_source_id)
    return NewsSourceView.from_row(row) if row is not None else None


def list_sources(db: Session, *, enabled_only: bool = False) -> list[NewsSourceView]:
    stmt = select(NewsSourceRow).order_by(NewsSourceRow.priority, NewsSourceRow.news_source_id)
    if enabled_only:
        stmt = stmt.where(NewsSourceRow.enabled.is_(True))
    return [NewsSourceView.from_row(r) for r in db.execute(stmt).scalars().all()]


def delete_source(db: Session, news_source_id: str) -> bool:
    row = db.get(NewsSourceRow, news_source_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def default_source(db: Session) -> NewsSourceView | None:
    """The source a bare "Haberleri aç." (no named source) opens: the lowest-priority
    ENABLED source whose identity is RESOLVED — a source stuck on
    ``needs_identity`` can never be silently picked as "the" news source."""
    stmt = (
        select(NewsSourceRow)
        .where(NewsSourceRow.enabled.is_(True))
        .where(NewsSourceRow.identity_status == IDENTITY_RESOLVED)
        .order_by(NewsSourceRow.priority, NewsSourceRow.news_source_id)
        .limit(1)
    )
    row = db.execute(stmt).scalars().first()
    return NewsSourceView.from_row(row) if row is not None else None


__all__ = [
    "ERROR_ALREADY_EXISTS",
    "ERROR_IDENTITY_UNRESOLVED",
    "ERROR_INVALID_CONTENT_TYPE",
    "ERROR_INVALID_SLUG",
    "ERROR_NOT_FOUND",
    "NewsSourceError",
    "NewsSourceView",
    "create_source",
    "default_source",
    "delete_source",
    "get_source",
    "list_sources",
    "update_source",
]
