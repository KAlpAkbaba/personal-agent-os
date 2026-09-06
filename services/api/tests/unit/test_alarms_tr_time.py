"""Unit tests: app.alarms.tr_time (M18.3 spec §3.8, §6).

The owner's phrases, in the owner's timezone, and the two things this module must never
do: guess, and think in UTC. Pure — no database, no clock of its own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.alarms.tr_time import (
    MAX_RELATIVE_SECONDS,
    UnparsedWhen,
    next_occurrence_after,
    parse_when_struct,
    parse_when_text,
)

IST = ZoneInfo("Europe/Istanbul")

#: A Wednesday, 22:00 local — deliberately in the evening so "yarın sabah" and "07:30"
#: land on different calendar days and a UTC-thinking implementation gets them wrong.
NOW = datetime(2026, 9, 9, 22, 0, tzinfo=IST).astimezone(UTC)


def _local(parsed) -> datetime:
    return parsed.at.astimezone(IST)


def test_yarin_sabah_0730_is_tomorrow_morning_local() -> None:
    parsed = parse_when_text("Yarın sabah 07:30'da beni uyandır.", now=NOW)
    assert _local(parsed).date() == datetime(2026, 9, 10).date()
    assert (_local(parsed).hour, _local(parsed).minute) == (7, 30)
    assert parsed.local_time == "07:30"
    assert parsed.weekdays == ()


def test_yarin_0730da_without_the_daypart_word() -> None:
    parsed = parse_when_text("Yarın 07:30'da uyandır.", now=NOW)
    assert (_local(parsed).hour, _local(parsed).minute) == (7, 30)
    assert _local(parsed).date() == datetime(2026, 9, 10).date()


def test_saat_0800e_takes_the_next_such_moment() -> None:
    parsed = parse_when_text("Saat 08:00'e alarm kur.", now=NOW)
    # 22:00 Wednesday -> the next 08:00 is Thursday morning, not tonight.
    assert _local(parsed).date() == datetime(2026, 9, 10).date()
    assert parsed.local_time == "08:00"


def test_her_hafta_ici_0715_is_recurring_on_weekdays() -> None:
    parsed = parse_when_text("Her hafta içi 07:15'te beni bu şarkıyla uyandır.", now=NOW)
    assert parsed.weekdays == (0, 1, 2, 3, 4)
    assert parsed.local_time == "07:15"
    assert parsed.is_recurring
    assert _local(parsed).weekday() in parsed.weekdays


def test_hafta_sonu_and_named_weekdays() -> None:
    assert parse_when_text("Hafta sonu 09:00'da uyandır.", now=NOW).weekdays == (5, 6)
    assert parse_when_text("Pazartesi 07:00'de uyandır.", now=NOW).weekdays == (0,)


def test_90_saniye_sonra_is_a_relative_offset() -> None:
    parsed = parse_when_text("90 saniye sonra test alarmı kur.", now=NOW)
    assert parsed.relative_seconds == 90
    assert parsed.at == NOW.replace(microsecond=NOW.microsecond) + (parsed.at - NOW)
    assert (parsed.at - NOW).total_seconds() == 90
    assert parsed.weekdays == ()


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("Beş dakika ertele.", 300),
        ("On dakika ertele.", 600),
        ("İki saat sonra uyandır.", 7200),
        ("45 dakika sonra.", 2700),
    ],
)
def test_word_and_digit_offsets(text: str, seconds: int) -> None:
    parsed = parse_when_text(text, now=NOW)
    assert parsed.relative_seconds == seconds


def test_spoken_clock_forms() -> None:
    assert parse_when_text("Yarın sabah yedi buçukta uyandır.", now=NOW).local_time == "07:30"
    assert parse_when_text("Yarın sabah sekizde uyandır.", now=NOW).local_time == "08:00"


def test_aksam_shifts_a_bare_hour_but_never_an_explicit_one() -> None:
    assert parse_when_text("Akşam yedide uyandır.", now=NOW).local_time == "19:00"
    # An explicit 24h time is already unambiguous and must not be shifted twice.
    assert parse_when_text("Akşam 19:00'da uyandır.", now=NOW).local_time == "19:00"


def test_an_unparseable_when_raises_rather_than_guessing() -> None:
    with pytest.raises(UnparsedWhen):
        parse_when_text("Beni bir ara uyandır.", now=NOW)
    with pytest.raises(UnparsedWhen):
        parse_when_text("", now=NOW)


def test_a_relative_offset_beyond_a_day_is_refused() -> None:
    with pytest.raises(UnparsedWhen):
        parse_when_text(f"{MAX_RELATIVE_SECONDS + 60} saniye sonra uyandır.", now=NOW)


def test_the_schedule_is_local_wall_clock_never_utc() -> None:
    """The whole point of spec §3.1's "never UTC for owner-facing times".

    Istanbul is UTC+3, so a naive UTC implementation would place "yarın sabah 07:30" at
    07:30Z — three hours late — and the owner would sleep through it.
    """
    parsed = parse_when_text("Yarın sabah 07:30'da uyandır.", now=NOW)
    assert parsed.at.astimezone(UTC).hour == 4  # 07:30 +03:00 == 04:30Z
    assert parsed.at.astimezone(UTC).minute == 30


def test_dst_safety_uses_wall_clock_arithmetic() -> None:
    """A DST-observing zone must keep the owner's wall-clock time across the transition.

    Berlin moves from +02:00 to +01:00 on 2026-10-25. A fixed-offset implementation
    computed before the change fires an hour early after it; wall-clock arithmetic does
    not. (``app.routines.triggers`` documents the same rule for the schedule trigger.)
    """
    berlin = ZoneInfo("Europe/Berlin")
    # Berlin leaves CEST (+02:00) for CET (+01:00) at 03:00 on 2026-10-25. Both calls ask
    # for "today at 07:30" — one on each side of that boundary.
    before = datetime(2026, 10, 24, 6, 0, tzinfo=berlin).astimezone(UTC)
    after = datetime(2026, 10, 25, 6, 0, tzinfo=berlin).astimezone(UTC)
    first = parse_when_text("Saat 07:30'da uyandır.", now=before, timezone="Europe/Berlin")
    second = parse_when_text("Saat 07:30'da uyandır.", now=after, timezone="Europe/Berlin")
    assert first.at.astimezone(berlin).strftime("%H:%M") == "07:30"
    assert second.at.astimezone(berlin).strftime("%H:%M") == "07:30"
    # ...and the UTC offsets genuinely differ across the boundary, so this is a real test:
    # a fixed-offset implementation would place one of these an hour wrong.
    assert first.at.astimezone(UTC).hour == 5
    assert second.at.astimezone(UTC).hour == 6


# ------------------------------------------------------------------ the struct form


def test_struct_relative_seconds() -> None:
    parsed = parse_when_struct({"relative_seconds": 90}, now=NOW)
    assert parsed.relative_seconds == 90


def test_struct_tomorrow_and_time() -> None:
    parsed = parse_when_struct({"date": "tomorrow", "time": "07:30"}, now=NOW)
    assert _local(parsed).date() == datetime(2026, 9, 10).date()
    assert parsed.local_time == "07:30"


def test_struct_weekdays_are_recurring() -> None:
    parsed = parse_when_struct({"time": "07:15", "weekdays": [0, 1, 2, 3, 4]}, now=NOW)
    assert parsed.weekdays == (0, 1, 2, 3, 4)


def test_struct_refuses_a_bad_time_and_a_past_date() -> None:
    with pytest.raises(UnparsedWhen):
        parse_when_struct({"time": "25:00"}, now=NOW)
    with pytest.raises(UnparsedWhen):
        parse_when_struct({"date": "2020-01-01", "time": "07:00"}, now=NOW)


def test_next_occurrence_after_skips_to_the_next_matching_weekday() -> None:
    # NOW is a Wednesday 22:00; the next weekday 07:15 is Thursday.
    nxt = next_occurrence_after(
        local_time="07:15", weekdays=[0, 1, 2, 3, 4], after=NOW, timezone="Europe/Istanbul"
    )
    local = nxt.astimezone(IST)
    assert local.weekday() == 3  # Thursday
    assert local.strftime("%H:%M") == "07:15"
