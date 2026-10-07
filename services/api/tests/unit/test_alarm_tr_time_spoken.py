"""Unit tests: app.alarms.tr_time — the spoken shapes the test team found misread on staging.

Round t-d20261006 (staging fba299af), 22 cards merged into one: "çeyrek kala" read as
"çeyrek geçe", "öğleden sonra üçte" as 03:00, "8'i 10 geçe" as 08:00, the article "bir"
inside a long sentence as 01:00, a spoken correction ("yedide değil sekizde") ignored, and
a negated create ("alarm kurma") placed in time. Pure — no database, no clock of its own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.alarms.tr_time import UnparsedWhen, parse_when_text

IST = ZoneInfo("Europe/Istanbul")

#: A Wednesday, 22:00 local: every "yarın" lands on Thursday 2026-09-10.
NOW = datetime(2026, 9, 9, 22, 0, tzinfo=IST).astimezone(UTC)


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        # kala / geçe with çeyrek
        ("Yarın sabah sekize çeyrek kala beni uyandır.", "07:45"),
        ("Yarın akşam dokuzu çeyrek geçe alarm kur.", "21:15"),
        ("Yarın sabah yediyi çeyrek geçe uyandır.", "07:15"),
        ("Yarın akşam dokuza çeyrek kala hatırlat.", "20:45"),
        # kala / geçe with minutes, digits and words
        ("Yarın sabah 8'i 10 geçe uyandır.", "08:10"),
        ("Yarın sabah sekizi on geçe uyandır.", "08:10"),
        ("Yarın sabah sekize on kala uyandır.", "07:50"),
        ("Yarın sabah 8'e 5 kala uyandır.", "07:55"),
        ("Yarın sabah yediyi yirmi beş geçe uyandır.", "07:25"),
        # öğleden sonra / akşam / gece push a 12-hour reading past noon
        ("Yarın öğleden sonra üçte alarm kur.", "15:00"),
        ("Yarın öğleden sonra üç buçukta beni uyandır.", "15:30"),
        ("Yarın ogleden sonra 3'te uyandır.", "15:00"),
        ("Yarın öğlen birde uyandır.", "13:00"),
        ("Yarın gece on birde uyandır.", "23:00"),
        # 'bir' as an article inside a sentence is not an hour
        (
            "Yarın sabah çok önemli bir toplantım var, o yüzden lütfen beni saat altıda uyandır.",
            "06:00",
        ),
        ("Bir şey rica edeceğim, yarın sabah yedide beni uyandır.", "07:00"),
        # the LAST stated time wins after a spoken correction
        ("Yarın yedide değil sekizde uyandır.", "08:00"),
        ("Yarın yedide, yok yok sekizde uyandır.", "08:00"),
        ("Yarın sekizde değil de yedide uyandır.", "07:00"),
        ("Yarın sabah 07:00 değil 07:30'da uyandır.", "07:30"),
        ("Yarın sabah yedide, pardon sekizde uyandır.", "08:00"),
    ],
)
def test_spoken_time_reads_as_the_owner_said_it(spoken: str, expected: str) -> None:
    parsed = parse_when_text(spoken, now=NOW)
    assert parsed.local_time == expected
    local = parsed.at.astimezone(IST)
    assert f"{local.hour:02d}:{local.minute:02d}" == expected
    assert local.date() == datetime(2026, 9, 10).date()


@pytest.mark.parametrize(
    "spoken",
    [
        "Yarın sabah yedide alarm kurma.",
        "Yarın sabah yedide beni uyandırma.",
        "Yarın sekizde alarm kurmayın.",
        "Yarın sabah yedi buçukta alarm kurmasın.",
    ],
)
def test_negated_create_produces_no_time(spoken: str) -> None:
    with pytest.raises(UnparsedWhen):
        parse_when_text(spoken, now=NOW)


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        # guards: forms that already worked keep working
        ("Sekize çeyrek var, uyandır.", "07:45"),
        ("Yarın sabah yedi buçukta beni uyandır.", "07:30"),
        ("Yarın akşam yedide uyandır.", "19:00"),
        ("Yarın 07:30'da uyandır.", "07:30"),
        ("Yarın sabah 7 30 da beni uyandır.", "07:30"),
        ("Yarın sabah on beşte uyandır.", "15:00"),
        # "kurmadan" / "unutma" are not a negated create
        ("Yarın sabah yedide uyandır, unutma.", "07:00"),
        ("Yarın sabah yedide alarm kur, kurmadan önce sorma.", "07:00"),
        ("Yarın sabah yedide beni uyandırmayı unutma.", "07:00"),
        # "uyandırma alarmı" is the noun (corpus a.create.3)
        ("Yarın sabah 07:30 için uyandırma alarmı ayarla.", "07:30"),
        # "onu" is the pronoun here, not ten o'clock
        ("Yarın onu sabah yedide kur.", "07:00"),
    ],
)
def test_existing_spoken_forms_unchanged(spoken: str, expected: str) -> None:
    assert parse_when_text(spoken, now=NOW).local_time == expected


def test_correction_keeps_the_date_and_takes_the_later_weekday() -> None:
    parsed = parse_when_text("Pazartesi değil salı sabah yedide uyandır.", now=NOW)
    assert parsed.local_time == "07:00"
    assert parsed.weekdays == (1,)
    evening = parse_when_text("Yarın sabah yedide değil, akşam yedide uyandır.", now=NOW)
    assert evening.local_time == "19:00"
    assert evening.matched == "tomorrow"


def test_bir_alone_still_names_no_time() -> None:
    with pytest.raises(UnparsedWhen):
        parse_when_text("Beni bir ara uyandır.", now=NOW)
