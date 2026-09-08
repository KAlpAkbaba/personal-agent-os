"""The in-repo iCalendar parser + RRULE expansion, against the fixture calendar and its
computed oracle (docs/M21_MAIL_CALENDAR_SPEC.md §4, ADR-0084)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.calendar.ics import expand_events, parse_calendar
from tests.mail_calendar_support import CALENDAR_PATH, load_truth


def _events():
    return parse_calendar(CALENDAR_PATH.read_text(encoding="utf-8"))


def test_parses_every_vevent() -> None:
    events = _events()
    uids = {e.uid for e in events}
    assert uids == {
        "ev-dis@fixture.example",
        "ev-ekip@fixture.example",
        "ev-bayram@fixture.example",
        "ev-musteri@fixture.example",
        "ev-doktor@fixture.example",
        "ev-spor@fixture.example",
        "ev-ogle@fixture.example",
    }


def test_all_day_event_parses_as_all_day() -> None:
    events = {e.uid: e for e in _events()}
    bayram = events["ev-bayram@fixture.example"]
    assert bayram.all_day is True
    assert bayram.dtstart.date().isoformat() == "2026-09-14"
    assert bayram.dtend.date().isoformat() == "2026-09-15"


def test_rrule_and_exdate_parsed() -> None:
    events = {e.uid: e for e in _events()}
    ekip = events["ev-ekip@fixture.example"]
    assert ekip.rrule == "FREQ=WEEKLY;BYDAY=TU;COUNT=10"
    assert len(ekip.exdates) == 1
    assert ekip.exdates[0].isoformat() == "2026-09-22T10:00:00+03:00"


def test_vtimezone_resolves_the_real_istanbul_offset() -> None:
    events = {e.uid: e for e in _events()}
    spor = events["ev-spor@fixture.example"]
    assert spor.dtstart.isoformat() == "2026-09-08T07:00:00+03:00"


def test_expansion_matches_the_oracle_exactly() -> None:
    truth = load_truth()["calendar"]
    events = _events()
    occs = expand_events(
        events,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2027, 1, 1, tzinfo=UTC),
    )
    got = [o.as_dict() for o in occs]
    assert got == truth["expanded_events"]
    assert len(got) == truth["event_count"]


def test_ekip_occurrence_count_after_exdate() -> None:
    truth = load_truth()["calendar"]
    events = _events()
    occs = expand_events(
        events, start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2027, 1, 1, tzinfo=UTC)
    )
    ekip = [o for o in occs if o.uid == "ev-ekip@fixture.example"]
    assert len(ekip) == truth["ekip_occurrences"]
    exdate_starts = {o.start.isoformat() for o in ekip}
    assert truth["exdate_skipped"] not in exdate_starts


def test_expand_bounds_a_window_without_truncating_an_unbounded_rule() -> None:
    """A window narrower than the fixture's own COUNT-bounded run still returns every
    occurrence that overlaps it — proving the walk stops on the WINDOW, not a hidden cap."""
    events = _events()
    occs = expand_events(
        events,
        start=datetime(2026, 9, 8, tzinfo=UTC),
        end=datetime(2026, 9, 9, tzinfo=UTC),
    )
    uids = {o.uid for o in occs}
    assert "ev-spor@fixture.example" in uids
    assert "ev-ekip@fixture.example" in uids
    assert "ev-ogle@fixture.example" not in uids  # 2026-09-09, outside this window
