"""Turkish relative-date-window parsing (M13)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.research.dates import default_window, parse_recency_window

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def test_son_uc_gun_yields_three_day_window() -> None:
    window = parse_recency_window("Son üç gündeki gelişmeler", now=NOW)
    assert window is not None
    assert window.unit == "day"
    assert window.amount == 3
    assert window.end == NOW
    assert window.start == NOW - timedelta(days=3)


def test_son_uc_gun_ascii_fallback_also_matches() -> None:
    window = parse_recency_window("son uc gun ozet", now=NOW)
    assert window is not None
    assert window.amount == 3
    assert window.unit == "day"


def test_son_with_digit_amount() -> None:
    window = parse_recency_window("son 5 gün", now=NOW)
    assert window is not None
    assert window.amount == 5
    assert window.start == NOW - timedelta(days=5)


def test_son_saat_yields_hour_window() -> None:
    window = parse_recency_window("son 24 saat içindeki haberler", now=NOW)
    assert window is not None
    assert window.unit == "hour"
    assert window.amount == 24
    assert window.start == NOW - timedelta(hours=24)


def test_son_hafta_word_number() -> None:
    window = parse_recency_window("son bir hafta", now=NOW)
    assert window is not None
    assert window.unit == "week"
    assert window.amount == 1
    assert window.start == NOW - timedelta(weeks=1)


def test_son_ay_word_number() -> None:
    window = parse_recency_window("son iki ay", now=NOW)
    assert window is not None
    assert window.unit == "month"
    assert window.amount == 2


def test_bugun() -> None:
    window = parse_recency_window("bugün neler oldu", now=NOW)
    assert window is not None
    assert window.label == "bugün"
    assert window.start == NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    assert window.end == NOW


def test_dun() -> None:
    window = parse_recency_window("dün ne oldu", now=NOW)
    assert window is not None
    assert window.label == "dün"
    yesterday = NOW - timedelta(days=1)
    assert window.start == yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    assert window.start.date() == window.end.date()


@pytest.mark.parametrize(
    "text",
    [
        "yapay zeka ajanlarındaki gelişmeler",  # no recency phrase at all
        "",
        "   ",
        "son x gün",  # unrecognized number word
        "son 0 gün",  # non-positive amount
    ],
)
def test_unrecognized_or_invalid_phrases_return_none(text: str) -> None:
    assert parse_recency_window(text, now=NOW) is None


def test_default_window_is_three_days_labelled_as_default() -> None:
    window = default_window(NOW)
    assert window.amount == 3
    assert window.unit == "day"
    assert "varsayılan" in window.label
    assert window.start == NOW - timedelta(days=3)


def test_default_window_days_is_configurable() -> None:
    window = default_window(NOW, days=7)
    assert window.amount == 7
    assert window.start == NOW - timedelta(days=7)


def test_parse_recency_window_is_deterministic() -> None:
    a = parse_recency_window("Son üç gündeki gelişmeler", now=NOW)
    b = parse_recency_window("Son üç gündeki gelişmeler", now=NOW)
    assert a == b
