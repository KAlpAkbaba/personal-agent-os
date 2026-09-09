"""Ties a configured source to a live decision and persists the durable audit row
(docs/M26_LATEST_NEWS_MODE_SPEC.md §2, §4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.news import sources_service
from app.news.classification import VideoCandidate
from app.news.models import CONTENT_TYPE_ANY_NEWS, NewsResolutionRow, NewsSourceRow
from app.news.provider import ProviderUnavailableError
from app.news.resolve_service import NewsResolveError, resolve_for_source
from app.news.resolver import REASON_NEWEST

REAL_ID = "UCLA_DiR1FfKNvjuUpBHmylQ"


@pytest.fixture()
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    NewsSourceRow.__table__.create(engine)
    NewsResolutionRow.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(session_factory: sessionmaker[Session]) -> Session:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


class _FakeProvider:
    def __init__(self, candidates: list[VideoCandidate], *, answered_by: str = "fixture") -> None:
        self._candidates = candidates
        self._answered_by = answered_by

    def list_recent_uploads(self, channel_id: str) -> tuple[list[VideoCandidate], str]:
        return list(self._candidates), self._answered_by


class _FailingProvider:
    def list_recent_uploads(self, channel_id: str) -> tuple[list[VideoCandidate], str]:
        raise ProviderUnavailableError("network is down")


def _resolved_source(db: Session, **kwargs: object) -> sources_service.NewsSourceView:
    defaults: dict[str, object] = dict(
        news_source_id="nasa",
        display_name="NASA",
        channel_input=f"https://www.youtube.com/channel/{REAL_ID}",
    )
    defaults.update(kwargs)
    return sources_service.create_source(db, **defaults)  # type: ignore[arg-type]


class TestResolveForSource:
    def test_unresolved_identity_refuses_before_touching_any_provider(self, db: Session) -> None:
        source = sources_service.create_source(db, news_source_id="x", display_name="X")
        with pytest.raises(NewsResolveError) as exc:
            resolve_for_source(db, source, provider=_FailingProvider())
        assert exc.value.error_class == "identity_unresolved"

    def test_provider_unavailable_is_never_read_as_no_candidates(self, db: Session) -> None:
        source = _resolved_source(db)
        with pytest.raises(NewsResolveError) as exc:
            resolve_for_source(db, source, provider=_FailingProvider())
        assert exc.value.error_class == "provider_unavailable"
        # Nothing persisted for a run that never got an answer.
        assert db.execute(select(NewsResolutionRow)).scalars().first() is None

    def test_a_successful_resolution_persists_the_audit_row(self, db: Session) -> None:
        source = _resolved_source(db)
        candidate = VideoCandidate(
            video_id="v1",
            title="Ana Haber Bülteni",
            published_at=datetime(2026, 9, 8, tzinfo=UTC),
            channel_id=REAL_ID,
            url="https://www.youtube.com/watch?v=v1",
        )
        outcome = resolve_for_source(
            db, source, content_type=CONTENT_TYPE_ANY_NEWS, provider=_FakeProvider([candidate])
        )
        assert outcome.result.selected is not None
        assert outcome.result.selected.video_id == "v1"
        assert outcome.result.reason == REASON_NEWEST

        row = db.get(NewsResolutionRow, uuid.UUID(outcome.row_id))
        assert row is not None
        assert row.news_source_id == "nasa"
        assert row.channel_id == REAL_ID
        assert row.selected_video_id == "v1"
        assert row.answered_by == "fixture"
        assert len(row.candidates_json) == 1

    def test_content_type_override_wins_over_the_sources_own_default(self, db: Session) -> None:
        from app.news.models import CONTENT_TYPE_FULL_BROADCAST

        source = _resolved_source(db, content_type=CONTENT_TYPE_ANY_NEWS)
        outcome = resolve_for_source(
            db, source, content_type=CONTENT_TYPE_FULL_BROADCAST, provider=_FakeProvider([])
        )
        assert outcome.result.content_type == CONTENT_TYPE_FULL_BROADCAST

    def test_no_eligible_candidates_still_persists_an_honest_none_eligible_row(
        self, db: Session
    ) -> None:
        source = _resolved_source(db)
        outcome = resolve_for_source(db, source, provider=_FakeProvider([]))
        assert outcome.result.selected is None
        row = db.get(NewsResolutionRow, uuid.UUID(outcome.row_id))
        assert row is not None
        assert row.selected_video_id is None


class TestCrossSessionPersistence:
    """Regression (found by the news voice corpus, 2026-09-08; see the identical class
    in test_news_sources_service.py for the full story): the audit row must commit,
    not merely flush, or it is invisible the moment the WRITING session closes."""

    def test_the_audit_row_survives_a_session_close(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        candidate = VideoCandidate(
            video_id="v1",
            title="Ana Haber Bülteni",
            published_at=datetime(2026, 9, 8, tzinfo=UTC),
            channel_id=REAL_ID,
            url="https://www.youtube.com/watch?v=v1",
        )
        with session_factory() as write_session:
            source = _resolved_source(write_session)
            outcome = resolve_for_source(write_session, source, provider=_FakeProvider([candidate]))
            row_id = uuid.UUID(outcome.row_id)
        with session_factory() as read_session:
            row = read_session.get(NewsResolutionRow, row_id)
            assert row is not None
            assert row.selected_video_id == "v1"
