"""Durable, owner-governed news-source configuration (docs/M26_LATEST_NEWS_MODE_SPEC.md
§1): CRUD plus the identity-resolution rule — a source's channel_id is set ONLY from
an authoritative signal, never guessed from its display_name.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.news import sources_service
from app.news.models import (
    CONTENT_TYPE_FULL_BROADCAST,
    IDENTITY_NEEDS_IDENTITY,
    IDENTITY_RESOLVED,
    NewsSourceRow,
)

REAL_ID = "UCLA_DiR1FfKNvjuUpBHmylQ"


@pytest.fixture()
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    NewsSourceRow.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(session_factory: sessionmaker[Session]) -> Session:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


class TestCrossSessionPersistence:
    """Regression (found by the news voice corpus, 2026-09-08): every write here MUST
    commit, not merely flush — a route or a voice-tool call opens its OWN session per
    request (``app.artifacts.runtime.ArtifactRuntime.session()``'s own docstring: yield
    then close, no commit), so a write left only flushed is invisible — silently rolled
    back — the moment that session closes. The corpus caught this the hard way: a
    seeded source resolved fine inside seed()'s own session and then answered
    "no_news_source" from the very next request, because closing without committing at
    the WRITE was exactly the same as never having written it. Every test above passes
    with a single, long-lived session (flush alone is enough to read your own write in
    the SAME transaction) — masking the defect completely; only a genuinely separate
    session per step exposes it."""

    def test_create_survives_a_session_close(self, session_factory: sessionmaker[Session]) -> None:
        with session_factory() as write_session:
            sources_service.create_source(write_session, news_source_id="nasa", display_name="NASA")
        with session_factory() as read_session:
            assert sources_service.get_source(read_session, "nasa") is not None

    def test_update_survives_a_session_close(self, session_factory: sessionmaker[Session]) -> None:
        with session_factory() as s1:
            sources_service.create_source(s1, news_source_id="nasa", display_name="NASA")
        with session_factory() as s2:
            sources_service.update_source(s2, "nasa", priority=5)
        with session_factory() as s3:
            view = sources_service.get_source(s3, "nasa")
            assert view is not None
            assert view.priority == 5

    def test_delete_survives_a_session_close(self, session_factory: sessionmaker[Session]) -> None:
        with session_factory() as s1:
            sources_service.create_source(s1, news_source_id="nasa", display_name="NASA")
        with session_factory() as s2:
            assert sources_service.delete_source(s2, "nasa") is True
        with session_factory() as s3:
            assert sources_service.get_source(s3, "nasa") is None


class TestCreateSource:
    def test_create_with_no_channel_input_stays_needs_identity(self, db: Session) -> None:
        view = sources_service.create_source(
            db, news_source_id="show-ana-haber", display_name="Show Ana Haber"
        )
        assert view.identity_status == IDENTITY_NEEDS_IDENTITY
        assert view.channel_id is None

    def test_create_with_a_bare_display_name_as_channel_input_stays_unresolved(
        self, db: Session
    ) -> None:
        """The central rule (task brief §1): a display name alone must never resolve
        a channel identity, even if the owner pastes it into channel_input."""
        view = sources_service.create_source(
            db,
            news_source_id="show-ana-haber",
            display_name="Show Ana Haber",
            channel_input="Show Ana Haber",
        )
        assert view.identity_status == IDENTITY_NEEDS_IDENTITY
        assert view.channel_id is None

    def test_create_with_an_authoritative_channel_url_resolves(self, db: Session) -> None:
        view = sources_service.create_source(
            db,
            news_source_id="nasa",
            display_name="NASA",
            channel_input=f"https://www.youtube.com/channel/{REAL_ID}",
        )
        assert view.identity_status == IDENTITY_RESOLVED
        assert view.channel_id == REAL_ID
        assert view.identity_resolved_by == "channel_url"

    def test_duplicate_slug_is_refused(self, db: Session) -> None:
        sources_service.create_source(db, news_source_id="x", display_name="X")
        with pytest.raises(sources_service.NewsSourceError) as exc:
            sources_service.create_source(db, news_source_id="x", display_name="X again")
        assert exc.value.error_class == sources_service.ERROR_ALREADY_EXISTS

    def test_invalid_slug_is_refused(self, db: Session) -> None:
        with pytest.raises(sources_service.NewsSourceError) as exc:
            sources_service.create_source(db, news_source_id="Not A Slug!", display_name="X")
        assert exc.value.error_class == sources_service.ERROR_INVALID_SLUG

    def test_invalid_content_type_is_refused(self, db: Session) -> None:
        with pytest.raises(sources_service.NewsSourceError) as exc:
            sources_service.create_source(
                db, news_source_id="x", display_name="X", content_type="not_a_policy"
            )
        assert exc.value.error_class == sources_service.ERROR_INVALID_CONTENT_TYPE


class TestUpdateSource:
    def test_giving_a_new_channel_input_re_resolves(self, db: Session) -> None:
        sources_service.create_source(db, news_source_id="nasa", display_name="NASA")
        view = sources_service.update_source(
            db, "nasa", channel_input=f"https://www.youtube.com/channel/{REAL_ID}"
        )
        assert view.identity_status == IDENTITY_RESOLVED
        assert view.channel_id == REAL_ID

    def test_an_unresolvable_new_channel_input_clears_the_old_resolution(self, db: Session) -> None:
        """Never keep a stale, possibly-wrong channel_id once the owner has changed
        their mind about the input — a failed re-resolution moves back to
        needs_identity rather than silently keeping the old id."""
        sources_service.create_source(
            db,
            news_source_id="nasa",
            display_name="NASA",
            channel_input=f"https://www.youtube.com/channel/{REAL_ID}",
        )
        view = sources_service.update_source(db, "nasa", channel_input="just a display name")
        assert view.identity_status == IDENTITY_NEEDS_IDENTITY
        assert view.channel_id is None

    def test_update_not_found_is_refused(self, db: Session) -> None:
        with pytest.raises(sources_service.NewsSourceError) as exc:
            sources_service.update_source(db, "nope", display_name="X")
        assert exc.value.error_class == sources_service.ERROR_NOT_FOUND

    def test_content_type_and_priority_and_enabled_update(self, db: Session) -> None:
        sources_service.create_source(db, news_source_id="x", display_name="X")
        view = sources_service.update_source(
            db, "x", content_type=CONTENT_TYPE_FULL_BROADCAST, priority=5, enabled=False
        )
        assert view.content_type == CONTENT_TYPE_FULL_BROADCAST
        assert view.priority == 5
        assert view.enabled is False


class TestListGetDelete:
    def test_list_orders_by_priority_then_id(self, db: Session) -> None:
        sources_service.create_source(db, news_source_id="b", display_name="B", priority=5)
        sources_service.create_source(db, news_source_id="a", display_name="A", priority=1)
        views = sources_service.list_sources(db)
        assert [v.news_source_id for v in views] == ["a", "b"]

    def test_enabled_only_filters_disabled(self, db: Session) -> None:
        sources_service.create_source(db, news_source_id="a", display_name="A", enabled=True)
        sources_service.create_source(db, news_source_id="b", display_name="B", enabled=False)
        views = sources_service.list_sources(db, enabled_only=True)
        assert [v.news_source_id for v in views] == ["a"]

    def test_get_missing_returns_none(self, db: Session) -> None:
        assert sources_service.get_source(db, "nope") is None

    def test_delete_returns_true_then_false(self, db: Session) -> None:
        sources_service.create_source(db, news_source_id="x", display_name="X")
        assert sources_service.delete_source(db, "x") is True
        assert sources_service.delete_source(db, "x") is False


class TestDefaultSource:
    def test_default_source_needs_resolved_identity(self, db: Session) -> None:
        sources_service.create_source(db, news_source_id="unresolved", display_name="Unresolved")
        sources_service.create_source(
            db,
            news_source_id="nasa",
            display_name="NASA",
            channel_input=f"https://www.youtube.com/channel/{REAL_ID}",
            priority=50,
        )
        default = sources_service.default_source(db)
        assert default is not None
        assert default.news_source_id == "nasa"

    def test_default_source_needs_enabled(self, db: Session) -> None:
        sources_service.create_source(
            db,
            news_source_id="nasa",
            display_name="NASA",
            channel_input=f"https://www.youtube.com/channel/{REAL_ID}",
            enabled=False,
        )
        assert sources_service.default_source(db) is None

    def test_no_sources_at_all_returns_none(self, db: Session) -> None:
        assert sources_service.default_source(db) is None

    def test_lowest_priority_wins_among_resolved_enabled(self, db: Session) -> None:
        sources_service.create_source(
            db,
            news_source_id="low",
            display_name="Low",
            channel_input=f"https://www.youtube.com/channel/{REAL_ID}",
            priority=10,
        )
        sources_service.create_source(
            db,
            news_source_id="high",
            display_name="High",
            channel_input=f"https://www.youtube.com/channel/{REAL_ID}",
            priority=1,
        )
        default = sources_service.default_source(db)
        assert default is not None
        assert default.news_source_id == "high"
