"""Governed playback (docs/M27_LATEST_NEWS_MODE_SPEC.md §5): opens the browser
worker's OWN dedicated ``news`` profile/context and classifies the outcome honestly
from ``browser.media_play``'s own ``verified`` field — never upgraded.

NO BROWSER IS LAUNCHED HERE: a scripted fake stands in for the device port, the same
``DeviceActionPort`` discipline ``tests/alarms_support.py``'s own ``FakeDeviceAction``
uses for the sibling alarm-media surface.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.news import sources_service
from app.news.classification import VideoCandidate
from app.news.models import (
    PLAYBACK_STATUS_CLOSED,
    PLAYBACK_STATUS_FAILED,
    PLAYBACK_STATUS_PLAYING,
    PLAYBACK_STATUS_UNVERIFIED,
    NewsPlaybackContextRow,
    NewsResolutionRow,
    NewsSourceRow,
)
from app.news.playback_service import (
    CAPABILITY_MEDIA_PLAY,
    CAPABILITY_MEDIA_STOP,
    CAPABILITY_SESSION_OPEN,
    NEWS_BROWSER_PROFILE,
    NEWS_BROWSER_SESSION_KIND,
    close_playback,
    open_latest_news,
)
from app.routines.dispatch import DeviceRunResult

REAL_ID = "UCLA_DiR1FfKNvjuUpBHmylQ"


@pytest.fixture()
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        NewsSourceRow.__table__,
        NewsResolutionRow.__table__,
        NewsPlaybackContextRow.__table__,
    ):
        table.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(session_factory: sessionmaker[Session]) -> Session:
    factory = session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


@dataclass
class FakeDeviceAction:
    """Same shape as ``tests/alarms_support.py``'s own fake: scripted per capability,
    records every call so a test can assert exactly what was dispatched (and, just as
    importantly, what was NOT)."""

    results: dict[str, DeviceRunResult | Callable[[dict[str, Any]], DeviceRunResult]] = field(
        default_factory=dict
    )
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def run(
        self, *, capability: str, payload: dict[str, Any], idempotency_key: str, timeout_s: float
    ) -> DeviceRunResult:
        self.calls.append((capability, dict(payload)))
        scripted = self.results.get(capability)
        if scripted is None:
            return DeviceRunResult(True, result={})
        if callable(scripted):
            return scripted(payload)
        return scripted


def _resolved_source(db: Session, **kwargs: object) -> sources_service.NewsSourceView:
    defaults: dict[str, object] = dict(
        news_source_id="nasa",
        display_name="NASA",
        channel_input=f"https://www.youtube.com/channel/{REAL_ID}",
    )
    defaults.update(kwargs)
    return sources_service.create_source(db, **defaults)  # type: ignore[arg-type]


class _FakeProvider:
    def __init__(self, candidates: list[VideoCandidate]) -> None:
        self._candidates = candidates

    def list_recent_uploads(self, channel_id: str) -> tuple[list[VideoCandidate], str]:
        return list(self._candidates), "fixture"


def _one_candidate() -> VideoCandidate:
    return VideoCandidate(
        video_id="v1",
        title="Ana Haber Bülteni",
        published_at=datetime(2026, 9, 8, tzinfo=UTC),
        channel_id=REAL_ID,
        url="https://www.youtube.com/watch?v=v1",
    )


class TestOpenLatestNewsHappyPath:
    def test_verified_playback_is_status_playing_and_ok(self, db: Session) -> None:
        source = _resolved_source(db)
        device = FakeDeviceAction(
            results={
                CAPABILITY_MEDIA_PLAY: DeviceRunResult(
                    True, result={"verified": True, "playing": True, "title": "Ana Haber Bülteni"}
                )
            }
        )
        outcome = open_latest_news(
            db, device, source=source, provider=_FakeProvider([_one_candidate()])
        )
        assert outcome.ok is True
        assert outcome.status == PLAYBACK_STATUS_PLAYING
        assert outcome.video_id == "v1"
        assert outcome.title == "Ana Haber Bülteni"

    def test_uses_the_dedicated_news_profile_never_research_or_alarm(self, db: Session) -> None:
        source = _resolved_source(db)
        device = FakeDeviceAction(
            results={CAPABILITY_MEDIA_PLAY: DeviceRunResult(True, result={"verified": True})}
        )
        open_latest_news(db, device, source=source, provider=_FakeProvider([_one_candidate()]))
        open_call = next(c for c in device.calls if c[0] == CAPABILITY_SESSION_OPEN)
        assert open_call[1]["profile"] == NEWS_BROWSER_PROFILE == "news"
        assert open_call[1]["session_kind"] == NEWS_BROWSER_SESSION_KIND == "media"
        assert open_call[1]["session_id"].startswith("news-")

    def test_media_play_url_is_the_selected_candidates_watch_url(self, db: Session) -> None:
        source = _resolved_source(db)
        device = FakeDeviceAction(
            results={CAPABILITY_MEDIA_PLAY: DeviceRunResult(True, result={"verified": True})}
        )
        open_latest_news(db, device, source=source, provider=_FakeProvider([_one_candidate()]))
        play_call = next(c for c in device.calls if c[0] == CAPABILITY_MEDIA_PLAY)
        assert play_call[1]["url"] == "https://www.youtube.com/watch?v=v1"

    def test_session_open_and_media_play_are_the_only_calls_made(self, db: Session) -> None:
        source = _resolved_source(db)
        device = FakeDeviceAction(
            results={CAPABILITY_MEDIA_PLAY: DeviceRunResult(True, result={"verified": True})}
        )
        open_latest_news(db, device, source=source, provider=_FakeProvider([_one_candidate()]))
        capabilities_called = {c[0] for c in device.calls}
        assert capabilities_called == {CAPABILITY_SESSION_OPEN, CAPABILITY_MEDIA_PLAY}

    def test_a_playback_context_row_is_persisted(self, db: Session) -> None:
        source = _resolved_source(db)
        device = FakeDeviceAction(
            results={CAPABILITY_MEDIA_PLAY: DeviceRunResult(True, result={"verified": True})}
        )
        outcome = open_latest_news(
            db, device, source=source, provider=_FakeProvider([_one_candidate()])
        )
        assert outcome.context_id is not None
        row = db.get(NewsPlaybackContextRow, uuid.UUID(outcome.context_id))
        assert row is not None
        assert row.status == PLAYBACK_STATUS_PLAYING
        assert row.news_source_id == "nasa"
        assert row.video_id == "v1"


class TestOpenLatestNewsHonestClassification:
    def test_session_open_succeeding_is_not_upgraded_to_playing(self, db: Session) -> None:
        """browser.session_open succeeding is NOT proof of playback (task brief §5) -
        only media_play's own verified field decides."""
        source = _resolved_source(db)
        device = FakeDeviceAction(
            results={
                CAPABILITY_SESSION_OPEN: DeviceRunResult(True, result={"created": True}),
                CAPABILITY_MEDIA_PLAY: DeviceRunResult(
                    True,
                    result={"verified": False, "playing": False, "reason": "no_media_element"},
                ),
            }
        )
        outcome = open_latest_news(
            db, device, source=source, provider=_FakeProvider([_one_candidate()])
        )
        assert outcome.ok is False
        assert outcome.status == PLAYBACK_STATUS_UNVERIFIED

    def test_session_open_failure_never_attempts_media_play(self, db: Session) -> None:
        source = _resolved_source(db)
        device = FakeDeviceAction(
            results={
                CAPABILITY_SESSION_OPEN: DeviceRunResult(
                    False, error_class="dependency_unavailable", message="no browser worker"
                )
            }
        )
        outcome = open_latest_news(
            db, device, source=source, provider=_FakeProvider([_one_candidate()])
        )
        assert outcome.ok is False
        assert outcome.status == PLAYBACK_STATUS_FAILED
        assert all(c[0] != CAPABILITY_MEDIA_PLAY for c in device.calls)

    def test_no_device_action_at_all_is_capability_missing(self, db: Session) -> None:
        source = _resolved_source(db)
        outcome = open_latest_news(
            db, None, source=source, provider=_FakeProvider([_one_candidate()])
        )
        assert outcome.ok is False
        assert outcome.error_class == "capability_missing"

    def test_no_eligible_video_never_opens_a_browser_at_all(self, db: Session) -> None:
        """Nothing to play (spec §2's no_eligible_video case) must never touch the
        device at all - refusing before any browser.* command."""
        source = _resolved_source(db)
        device = FakeDeviceAction()
        outcome = open_latest_news(db, device, source=source, provider=_FakeProvider([]))
        assert outcome.ok is False
        assert outcome.error_class == "no_eligible_video"
        assert device.calls == []


class TestClosePlayback:
    def test_close_stops_only_the_named_context(self, db: Session) -> None:
        source = _resolved_source(db)
        device = FakeDeviceAction(
            results={CAPABILITY_MEDIA_PLAY: DeviceRunResult(True, result={"verified": True})}
        )
        outcome = open_latest_news(
            db, device, source=source, provider=_FakeProvider([_one_candidate()])
        )
        context_id = uuid.UUID(outcome.context_id)

        close_device = FakeDeviceAction(
            results={CAPABILITY_MEDIA_STOP: DeviceRunResult(True, result={"stopped": True})}
        )
        close_outcome = close_playback(db, close_device, context_id=context_id)
        assert close_outcome.ok is True
        assert close_outcome.status == PLAYBACK_STATUS_CLOSED
        stop_call = next(c for c in close_device.calls if c[0] == CAPABILITY_MEDIA_STOP)
        assert stop_call[1]["session_id"] == f"news-{context_id}"

        row = db.get(NewsPlaybackContextRow, context_id)
        assert row is not None
        assert row.status == PLAYBACK_STATUS_CLOSED
        assert row.closed_at is not None

    def test_closing_twice_is_idempotent_and_never_calls_the_device_again(
        self, db: Session
    ) -> None:
        source = _resolved_source(db)
        device = FakeDeviceAction(
            results={CAPABILITY_MEDIA_PLAY: DeviceRunResult(True, result={"verified": True})}
        )
        outcome = open_latest_news(
            db, device, source=source, provider=_FakeProvider([_one_candidate()])
        )
        context_id = uuid.UUID(outcome.context_id)

        close_device = FakeDeviceAction(
            results={CAPABILITY_MEDIA_STOP: DeviceRunResult(True, result={"stopped": True})}
        )
        close_playback(db, close_device, context_id=context_id)
        second = close_playback(db, close_device, context_id=context_id)
        assert second.ok is True
        # The FIRST close dispatched exactly one media_stop; the SECOND (against an
        # already-closed context) must not dispatch anything more.
        assert len(close_device.calls) == 1

    def test_closing_an_unknown_context_is_not_found(self, db: Session) -> None:
        outcome = close_playback(db, FakeDeviceAction(), context_id=uuid.uuid4())
        assert outcome.ok is False
        assert outcome.error_class == "not_found"


class TestCrossSessionPersistence:
    """Regression (found by the M27 voice corpus, 2026-09-08; see the identical class
    in test_news_sources_service.py for the full story): the playback context row must
    commit, not merely flush, or a status query in a LATER request (a separate session)
    would see a row that was never really written."""

    def test_the_playback_context_survives_a_session_close(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with session_factory() as write_session:
            source = _resolved_source(write_session)
            device = FakeDeviceAction(
                results={CAPABILITY_MEDIA_PLAY: DeviceRunResult(True, result={"verified": True})}
            )
            outcome = open_latest_news(
                write_session,
                device,
                source=source,
                provider=_FakeProvider([_one_candidate()]),
            )
            context_id = uuid.UUID(outcome.context_id)
        with session_factory() as read_session:
            row = read_session.get(NewsPlaybackContextRow, context_id)
            assert row is not None
            assert row.status == PLAYBACK_STATUS_PLAYING
