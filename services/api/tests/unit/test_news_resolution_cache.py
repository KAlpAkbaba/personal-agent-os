"""The resolution table was written on every call and never read back.

So "Haberleri aç.", "Son haber ne zaman yüklenmiş?" and the morning briefing's news section
were three separate live requests to a third party — for one answer. Against YouTube, which
rate-limits per IP, that is not a performance question: measured 2026-09-09 while
configuring the owner's own source, the feed refused NINE consecutive honest requests once
the limit was reached, from the owner's machine and from the Cloud Core alike, having
answered perfectly minutes before.

A capability that re-fetches on every question cannot survive the owner using it. These
tests hold the cache to three properties: it answers without touching the provider while
fresh, it does NOT answer from a stale row, and a stale row is never used to paper over a
provider that failed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.news.classification import VideoCandidate
from app.news.models import (
    CONTENT_TYPE_ANY_NEWS,
    NewsResolutionRow,
    NewsSourceRow,
)
from app.news.provider import ProviderUnavailableError
from app.news.resolve_service import RESOLUTION_TTL_S, NewsResolveError, resolve_for_source
from app.news.sources_service import NewsSourceView

CHANNEL = "UCaaaaaaaaaaaaaaaaaaaaaa"
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


@pytest.fixture()
def db(tmp_path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'news.db'}")
    NewsSourceRow.__table__.create(engine)
    NewsResolutionRow.__table__.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def _source() -> NewsSourceView:
    return NewsSourceView(
        news_source_id="show",
        display_name="Show",
        provider="youtube",
        channel_id=CHANNEL,
        identity_status="resolved",
        identity_resolved_by="channel_url",
        channel_input=f"https://www.youtube.com/channel/{CHANNEL}",
        content_type=CONTENT_TYPE_ANY_NEWS,
        priority=100,
        enabled=True,
    )


class _CountingProvider:
    """Counts what the cache is supposed to avoid."""

    def __init__(self, candidates: list[VideoCandidate] | None = None, fail: bool = False) -> None:
        self.calls = 0
        self._candidates = candidates or []
        self._fail = fail

    def list_recent_uploads(self, channel_id: str) -> tuple[list[VideoCandidate], str]:
        self.calls += 1
        if self._fail:
            raise ProviderUnavailableError("channel feed request failed: 404 Not Found")
        return list(self._candidates), "channel_feed"


def _candidate(video_id: str = "vid00000001", *, when: datetime = NOW) -> VideoCandidate:
    return VideoCandidate(
        video_id=video_id,
        title="Ana Haber 9 Eylül 2026",
        published_at=when,
        channel_id=CHANNEL,
        url=f"https://www.youtube.com/watch?v={video_id}",
        description="",
    )


def test_a_second_question_inside_the_window_does_not_touch_the_provider(db: Session) -> None:
    """THE regression. Three questions in one conversation used to be three requests."""
    provider = _CountingProvider([_candidate()])

    first = resolve_for_source(db, _source(), provider=provider, now=NOW)
    second = resolve_for_source(db, _source(), provider=provider, now=NOW + timedelta(seconds=30))
    third = resolve_for_source(db, _source(), provider=provider, now=NOW + timedelta(seconds=120))

    assert provider.calls == 1, f"{provider.calls} live requests for one answer"
    assert first.result.selected is not None
    for outcome in (second, third):
        assert outcome.result.selected is not None
        assert outcome.result.selected.video_id == first.result.selected.video_id
        assert outcome.result.reason == first.result.reason


def test_the_answer_rebuilt_from_the_row_carries_what_the_owner_is_told(db: Session) -> None:
    """A cached answer is only worth having if it says the same things: the selection, its
    real publish time, the reason, and the rejections the receipt names."""
    newest = _candidate("newest00001", when=NOW)
    older = _candidate("older000001", when=NOW - timedelta(hours=3))
    provider = _CountingProvider([older, newest])

    live = resolve_for_source(db, _source(), provider=provider, now=NOW)
    cached = resolve_for_source(db, _source(), provider=provider, now=NOW + timedelta(seconds=10))

    assert cached.result.selected is not None and live.result.selected is not None
    assert cached.result.selected.video_id == live.result.selected.video_id
    assert cached.result.selected.published_at == live.result.selected.published_at
    assert cached.result.selected.url == live.result.selected.url
    assert cached.result.reason == live.result.reason
    assert cached.result.content_type == live.result.content_type
    assert cached.result.answered_by == live.result.answered_by


def test_a_stale_row_is_not_an_answer(db: Session) -> None:
    """The other side of the bargain. "The latest bulletin" from an hour ago may not be the
    latest bulletin, and the cache exists to spare the endpoint, never to mislead."""
    provider = _CountingProvider([_candidate()])

    resolve_for_source(db, _source(), provider=provider, now=NOW)
    later = NOW + timedelta(seconds=RESOLUTION_TTL_S + 1)
    resolve_for_source(db, _source(), provider=provider, now=later)

    assert provider.calls == 2, "an expired cache must go back to the provider"


def test_a_stale_row_never_papers_over_a_provider_failure(db: Session) -> None:
    """If the cache has expired and the fetch fails, that is a failure. Serving the old row
    would be claiming "the latest" over something we know might not be."""
    good = _CountingProvider([_candidate()])
    resolve_for_source(db, _source(), provider=good, now=NOW)

    broken = _CountingProvider(fail=True)
    with pytest.raises(NewsResolveError):
        resolve_for_source(
            db, _source(), provider=broken, now=NOW + timedelta(seconds=RESOLUTION_TTL_S + 1)
        )


def test_a_forced_refresh_skips_the_cache(db: Session) -> None:
    """The owner asking again, explicitly, is a different question from asking again."""
    provider = _CountingProvider([_candidate()])
    resolve_for_source(db, _source(), provider=provider, now=NOW)
    resolve_for_source(
        db, _source(), provider=provider, now=NOW + timedelta(seconds=5), force_refresh=True
    )
    assert provider.calls == 2


def test_a_different_content_policy_is_a_different_question(db: Session) -> None:
    """`latest_any_news` and `latest_main_news` can select different videos from the same
    feed, so one may not answer for the other."""
    provider = _CountingProvider([_candidate()])
    resolve_for_source(db, _source(), provider=provider, now=NOW)
    resolve_for_source(
        db,
        _source(),
        provider=provider,
        content_type="latest_main_news",
        now=NOW + timedelta(seconds=5),
    )
    assert provider.calls == 2


def test_the_ttl_is_short_enough_to_still_mean_latest() -> None:
    """A number with a reason: a bulletin channel publishes on the order of minutes, so the
    window has to be minutes, not hours. This test is where a future 'just make it an hour'
    has to argue with that."""
    assert 60 <= RESOLUTION_TTL_S <= 900
