"""Ties a configured source to a live decision: fetch candidates from its provider,
run the pure resolver, and persist the answer as a durable, queryable
``NewsResolutionRow`` (docs/M27_LATEST_NEWS_MODE_SPEC.md §2, §4) — what "son haber ne
zaman yüklenmiş?" and "şu an hangi haber videosunu açacaksın?" both read, and what
governed playback resolves against before it ever opens a browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.news.models import PROVIDER_YOUTUBE, NewsResolutionRow
from app.news.provider import NewsUploadProvider, ProviderUnavailableError, YouTubeFeedProvider
from app.news.resolver import ResolverResult, audit_candidates, resolve_latest
from app.news.sources_service import NewsSourceView

ERROR_IDENTITY_UNRESOLVED = "identity_unresolved"
ERROR_PROVIDER_UNAVAILABLE = "provider_unavailable"
ERROR_UNSUPPORTED_PROVIDER = "unsupported_provider"


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


def resolve_for_source(
    db: Session,
    source: NewsSourceView,
    *,
    content_type: str | None = None,
    provider: NewsUploadProvider | None = None,
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
        created_at=datetime.now(UTC),
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
