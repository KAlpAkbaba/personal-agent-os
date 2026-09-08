"""The in-repo iCalendar parser + RRULE expansion, against the fixture calendar and its
computed oracle (docs/M21_MAIL_CALENDAR_SPEC.md §4, ADR-0084)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.calendar.ics import (
    MAX_OCCURRENCES_PER_EVENT,
    MAX_WINDOW_DAYS,
    VEvent,
    clamp_window,
    expand_events,
    expand_events_report,
    parse_calendar,
)
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


# --------------------------------------------------------------- M2 (security review)


def _secondly_since_2000() -> list[VEvent]:
    """The exact pathological shape M2's own security review named: a ``FREQ=SECONDLY``
    event anchored decades before any window a real agenda/find_slot call would ever
    ask for - the DoS ``expand_events`` used to be open to (verified live)."""
    tz = ZoneInfo("Europe/Istanbul")
    return [
        VEvent(
            uid="ev-poison@fixture.example",
            summary="Poison",
            dtstart=datetime(2000, 1, 1, 0, 0, 0, tzinfo=tz),
            dtend=datetime(2000, 1, 1, 0, 0, 1, tzinfo=tz),
            all_day=False,
            rrule="FREQ=SECONDLY",
        )
    ]


def test_freq_secondly_since_2000_finishes_in_bounded_time() -> None:
    events = _secondly_since_2000()
    started = time.monotonic()
    occs = expand_events(
        events,
        start=datetime(2026, 9, 9, tzinfo=UTC),
        end=datetime(2026, 9, 10, tzinfo=UTC),
    )
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"took {elapsed:.2f}s - the raw-scan cap did not bite"
    # The event's own DTSTART (2000) is so far before the window that the raw-scan cap
    # (MAX_RRULE_RAW_SCAN) exhausts itself before ever reaching 2026 - the honestly
    # bounded answer is "nothing found", never a hang and never a silent lie that the
    # rule was fully expanded.
    assert occs == []


def test_freq_secondly_caps_occurrences_within_the_window_itself() -> None:
    """A SECONDLY rule anchored INSIDE the window (no raw-scan gap to cross) still must
    not produce one row per second unbounded - MAX_OCCURRENCES_PER_EVENT bites."""
    tz = ZoneInfo("Europe/Istanbul")
    events = [
        VEvent(
            uid="ev-poison2@fixture.example",
            summary="Poison",
            dtstart=datetime(2026, 9, 9, 0, 0, 0, tzinfo=tz),
            dtend=datetime(2026, 9, 9, 0, 0, 1, tzinfo=tz),
            all_day=False,
            rrule="FREQ=SECONDLY",
        )
    ]
    started = time.monotonic()
    occs, truncated = expand_events_report(
        events,
        start=datetime(2026, 9, 9, tzinfo=UTC),
        end=datetime(2026, 9, 10, tzinfo=UTC),  # a whole day of seconds, uncapped >> cap
    )
    elapsed = time.monotonic() - started
    assert elapsed < 2.0, f"took {elapsed:.2f}s"
    assert truncated is True
    assert len(occs) == MAX_OCCURRENCES_PER_EVENT


def test_clamp_window_narrows_a_window_wider_than_the_cap() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2027, 1, 1, tzinfo=UTC)  # a whole year >> MAX_WINDOW_DAYS
    clamped_start, clamped_end, clamped = clamp_window(start, end)
    assert clamped is True
    assert clamped_start == start
    assert (clamped_end - start).days == MAX_WINDOW_DAYS


def test_clamp_window_leaves_a_narrow_window_alone() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = datetime(2026, 9, 8, tzinfo=UTC)
    clamped_start, clamped_end, clamped = clamp_window(start, end)
    assert clamped is False
    assert (clamped_start, clamped_end) == (start, end)


def test_expand_events_report_names_truncation_for_a_normal_calendar_as_false() -> None:
    """The fixture calendar's own legitimate rules (COUNT=10, COUNT=5, ...) never trip
    any cap — ``truncated`` stays false for the everyday case."""
    events = _events()
    occs, truncated = expand_events_report(
        events,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert truncated is False
    assert len(occs) == load_truth()["calendar"]["event_count"]
