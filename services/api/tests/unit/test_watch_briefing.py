"""watch-voice: what the owner hears of a watch without asking.

* The morning briefing: one line per change since the last briefing, at most three, and
  nothing at all - not even "değişiklik yok" - when nothing changed.
* ``app.watch.announce.WatchAnnouncer``: a condition that was met is spoken into the live
  session only when the owner is present, a greeting is allowed and it is not quiet hours;
  otherwise it waits for the briefing. Spoken once.
* The narrative: the ledger subsystem ``watch`` is said "nöbet", so a failed reading appears
  in "bu hafta ne oldu" with its reason.

The changes are made by the REAL runner (``apply_observation``) from fake observations, so
the rows are the rows production writes.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.briefing.models import BriefingPreferencesRow
from app.briefing.service import BriefingService, update_preferences
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.narrative.service import tell
from app.notifications.models import NotificationRow
from app.watch import compare, runner, service
from app.watch.models import Watch, WatchReading
from app.watch.reader import Observation

NOON = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)  # 15:00 Istanbul - outside quiet hours
NIGHT = datetime(2026, 10, 4, 23, 30, tzinfo=UTC)  # 02:30 Istanbul - quiet hours


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        Watch.__table__,
        WatchReading.__table__,
        NotificationRow.__table__,
        ActivityEventRow.__table__,
        BriefingPreferencesRow.__table__,
    ):
        table.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.research.destination.resolve_hostname", lambda host: ["93.184.216.34"])


def page(text: str) -> Observation:
    return Observation(ok=True, text_sha256=compare.text_sha256(text), text=text)


def _watch(factory, label: str, condition: str = "changed") -> uuid.UUID:
    with factory() as db:
        view = service.create_watch(
            db, url="https://www.home-assistant.io/blog/", condition=condition, label=label
        )
        db.commit()
        return uuid.UUID(view.id)


def _apply(factory, watch_id, observation, now, *, announcer=None):
    with factory() as db:
        watch = db.get(Watch, watch_id)
        return runner.apply_observation(db, watch, observation, now=now, announcer=announcer)


def _changed(factory, label: str, at: datetime) -> service.WatchChange:
    """A watch whose page changed at ``at`` (its baseline read an hour earlier)."""
    wid = _watch(factory, label)
    _apply(factory, wid, page(f"{label} eski"), at - timedelta(hours=1))
    change = _apply(factory, wid, page(f"{label} yeni"), at)
    assert change is not None and change.outcome == "changed"
    return change


def _brief(factory, now: datetime) -> dict:
    with factory() as db:
        update_preferences(
            db,
            {
                "include_weather": False,
                "include_system_status": False,
                "include_calendar": False,
                "include_mail": False,
                "include_overnight_work": False,
                "include_news_summary": False,
            },
        )
        answer = BriefingService().build(db, settings=Settings(_env_file=None), live={}, now=now)
        db.commit()
        return answer


def _speech(answer: dict) -> str:
    return answer["speech"]


def _sections(answer: dict) -> dict:
    return answer["observed_after"]["server"]["sections"]


# ------------------------------------------------------------------- the briefing


def test_two_changes_give_two_lines(factory) -> None:
    first = _changed(factory, "Fiyat", NOON)
    second = _changed(factory, "Sürüm", NOON + timedelta(hours=2))
    answer = _brief(factory, NOON + timedelta(hours=10))
    speech = _speech(answer)
    assert first.line_tr in speech and second.line_tr in speech
    assert "watches" in _sections(answer)


def test_no_change_gives_no_line_and_no_degisiklik_yok(factory) -> None:
    _watch(factory, "Sessiz")
    answer = _brief(factory, NOON)
    speech = _speech(answer).lower()
    assert "watches" not in _sections(answer)
    assert "değişiklik yok" not in speech
    assert "nöbet" not in speech


def test_at_most_three_lines(factory) -> None:
    changes = [_changed(factory, f"Nöbet {i}", NOON + timedelta(minutes=i)) for i in range(4)]
    speech = _speech(_brief(factory, NOON + timedelta(hours=10)))
    assert sum(change.line_tr in speech for change in changes) == 3


def test_only_the_changes_since_the_last_briefing(factory) -> None:
    before = _changed(factory, "Eski", NOON)
    _brief(factory, NOON + timedelta(hours=1))  # the previous briefing heard "Eski"
    after = _changed(factory, "Yeni", NOON + timedelta(hours=2))
    speech = _speech(_brief(factory, NOON + timedelta(hours=3)))
    assert after.line_tr in speech
    assert before.line_tr not in speech


# ------------------------------------------------------------------- the announcer


class FakeSpeaker:
    def __init__(self, delivered: bool = True) -> None:
        self.delivered = delivered
        self.said: list[str] = []

    def say(self, text: str, briefing_ids=()) -> bool:
        self.said.append(text)
        return self.delivered


def _announcer(speaker, *, present=True, greeting=True, at=NOON):
    from app.watch.announce import WatchAnnouncer

    return WatchAnnouncer(
        speaker,
        owner_present=lambda: present,
        greeting_allowed=lambda db: greeting,
        clock=lambda: at,
    )


def _met(factory) -> service.WatchChange:
    wid = _watch(factory, "Fiyat", "number_below:20000")
    change = _apply(factory, wid, page("19.499 TL"), NOON)
    assert change is not None and change.outcome == "condition_met"
    return change


def test_a_met_condition_is_spoken_once_when_everything_holds(factory) -> None:
    change = _met(factory)
    speaker = FakeSpeaker()
    announcer = _announcer(speaker)
    with factory() as db:
        assert announcer.announce(db, change) is True
        assert announcer.announce(db, change) is False
    assert len(speaker.said) == 1 and change.line_tr in speaker.said[0]


@pytest.mark.parametrize(
    ("present", "greeting", "at"),
    [
        (False, True, NOON),
        (None, True, NOON),
        (True, False, NOON),
        (True, True, NIGHT),
    ],
)
def test_any_one_missing_and_it_is_not_spoken(factory, present, greeting, at) -> None:
    change = _met(factory)
    speaker = FakeSpeaker()
    with factory() as db:
        assert (
            _announcer(speaker, present=present, greeting=greeting, at=at).announce(db, change)
            is False
        )
    assert speaker.said == []


def test_no_live_session_is_not_spoken_and_the_briefing_carries_it(factory) -> None:
    change = _met(factory)
    with factory() as db:
        assert _announcer(FakeSpeaker(delivered=False)).announce(db, change) is False
    assert change.line_tr in _speech(_brief(factory, NOON + timedelta(hours=1)))


def test_a_change_that_is_not_a_met_condition_waits_for_the_briefing(factory) -> None:
    change = _changed(factory, "Sürüm", NOON)
    speaker = FakeSpeaker()
    with factory() as db:
        assert _announcer(speaker).announce(db, change) is False
    assert speaker.said == []


def test_the_runner_calls_the_announcer_it_is_given(factory) -> None:
    speaker = FakeSpeaker()
    wid = _watch(factory, "Fiyat", "number_below:20000")
    _apply(factory, wid, page("19.499 TL"), NOON, announcer=_announcer(speaker))
    assert len(speaker.said) == 1


# ------------------------------------------------------------------- the narrative


def test_a_failed_reading_is_told_as_nobet_with_its_reason(factory) -> None:
    wid = _watch(factory, "Fiyat")
    failed = Observation(ok=False, reason_tr="Sayfa giriş istiyor.", error_class="auth_wall")
    change = _apply(factory, wid, failed, NOON)
    assert change is not None and change.outcome == "unreadable"
    with factory() as db:
        text = tell(db, "bu hafta", now=NOON + timedelta(hours=1))
    assert "nöbet" in text.lower(), text
    assert "Sayfa giriş istiyor" in text, text
