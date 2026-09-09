"""Ties a configured source to a live decision: fetch candidates from its provider,
run the pure resolver, and persist the answer as a durable, queryable
``NewsResolutionRow`` (docs/M26_LATEST_NEWS_MODE_SPEC.md §2, §4) — what "son haber ne
zaman yüklenmiş?" and "şu an hangi haber videosunu açacaksın?" both read, and what
governed playback resolves against before it ever opens a browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.news.classification import VideoCandidate
from app.news.models import PROVIDER_YOUTUBE, NewsResolutionRow
from app.news.provider import NewsUploadProvider, ProviderUnavailableError, YouTubeFeedProvider
from app.news.resolver import ResolverResult, audit_candidates, resolve_latest
from app.news.sources_service import NewsSourceView

logger = get_logger("app.news.resolve_service")

ERROR_IDENTITY_UNRESOLVED = "identity_unresolved"
ERROR_PROVIDER_UNAVAILABLE = "provider_unavailable"
ERROR_UNSUPPORTED_PROVIDER = "unsupported_provider"


def _aware(value: datetime) -> datetime:
    """A row read back from SQLite loses its tzinfo; Postgres keeps it. The cache
    compares ages, so a naive datetime here would raise rather than expire."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class NewsResolveError(RuntimeError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


@dataclass(frozen=True, slots=True)
class ResolutionOutcome:
    result: ResolverResult
    row_id: str  # str(uuid) of the persisted NewsResolutionRow


def _provider_for(
    source: NewsSourceView, override: NewsUploadProvider | None
) -> NewsUploadProvider:
    if override is not None:
        return override
    if source.provider == PROVIDER_YOUTUBE:
        return YouTubeFeedProvider()
    raise NewsResolveError(ERROR_UNSUPPORTED_PROVIDER, f"unsupported provider: {source.provider!r}")


#: How long a resolution answers for. A bulletin channel publishes on the order of
#: minutes and the row records exactly when it was made, so a five-minute-old answer to
#: "what is the latest bulletin" is still that bulletin. Short enough to be true; long
#: enough that asking three times in a conversation is one request, not three.
RESOLUTION_TTL_S: Final = 300


def _fresh_resolution(
    db: Session, source: NewsSourceView, content_type: str, now: datetime
) -> NewsResolutionRow | None:
    """The most recent resolution for this source and policy, if it is still fresh.

    The audit table has always held this; nothing ever read it back. Reading it is what
    turns three questions in one conversation into one request to a third party that
    rate-limits per IP.
    """
    cutoff = now - timedelta(seconds=RESOLUTION_TTL_S)
    return (
        db.execute(
            select(NewsResolutionRow)
            .where(
                NewsResolutionRow.news_source_id == source.news_source_id,
                NewsResolutionRow.channel_id == source.channel_id,
                NewsResolutionRow.content_type == content_type,
                NewsResolutionRow.created_at >= cutoff,
            )
            .order_by(NewsResolutionRow.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def _result_from_row(row: NewsResolutionRow) -> ResolverResult:
    """Rebuild the resolver's own answer from the row that recorded it."""
    selected: VideoCandidate | None = None
    if row.selected_video_id is not None and row.selected_published_at is not None:
        published = row.selected_published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        selected = VideoCandidate(
            video_id=row.selected_video_id,
            title=row.selected_title or "",
            published_at=published,
            channel_id=row.channel_id,
            url=row.selected_url or "",
            description="",
        )
    rejected = tuple(
        (str(entry.get("video_id")), str(entry.get("reason")))
        for entry in (row.candidates_json or [])
        if isinstance(entry, dict) and not entry.get("accepted")
    )
    return ResolverResult(
        selected,
        row.reason,
        row.content_type,
        row.answered_by,
        len(row.candidates_json or []),
        rejected,
        ambiguous=bool(row.ambiguous),
    )


def resolve_for_source(
    db: Session,
    source: NewsSourceView,
    *,
    content_type: str | None = None,
    provider: NewsUploadProvider | None = None,
    force_refresh: bool = False,
    now: datetime | None = None,
) -> ResolutionOutcome:
    """Resolve "the latest" for ``source`` and persist the audit row. Raises
    :class:`NewsResolveError` for the two things that make "latest" unanswerable:
    an unresolved channel identity, or a provider that could not answer at all
    (never claim "latest" over nothing — task brief §4)."""
    if source.channel_id is None:
        raise NewsResolveError(
            ERROR_IDENTITY_UNRESOLVED,
            f"{source.news_source_id} has no resolved channel identity",
        )
    upload_provider = _provider_for(source, provider)
    effective_content_type = content_type or source.content_type
    moment = now or datetime.now(UTC)

    if not force_refresh:
        cached = _fresh_resolution(db, source, effective_content_type, moment)
        if cached is not None:
            logger.info(
                "news_resolution_cache_hit",
                source=source.news_source_id,
                age_s=round((moment - _aware(cached.created_at)).total_seconds(), 1),
            )
            return ResolutionOutcome(result=_result_from_row(cached), row_id=str(cached.id))

    try:
        candidates, answered_by = upload_provider.list_recent_uploads(source.channel_id)
    except ProviderUnavailableError as exc:
        raise NewsResolveError(ERROR_PROVIDER_UNAVAILABLE, str(exc)) from exc

    result = resolve_latest(
        candidates, content_type=effective_content_type, answered_by=answered_by
    )

    row = NewsResolutionRow(
        news_source_id=source.news_source_id,
        channel_id=source.channel_id,
        content_type=effective_content_type,
        answered_by=answered_by,
        candidates_json=audit_candidates(candidates, result),
        selected_video_id=result.selected.video_id if result.selected else None,
        selected_title=result.selected.title if result.selected else None,
        selected_published_at=result.selected.published_at if result.selected else None,
        selected_url=result.selected.url if result.selected else None,
        reason=result.reason,
        ambiguous=result.ambiguous,
        created_at=moment,
    )
    db.add(row)
    db.commit()
    return ResolutionOutcome(result=result, row_id=str(row.id))


__all__ = [
    "ERROR_IDENTITY_UNRESOLVED",
    "ERROR_PROVIDER_UNAVAILABLE",
    "ERROR_UNSUPPORTED_PROVIDER",
    "NewsResolveError",
    "ResolutionOutcome",
    "resolve_for_source",
]
