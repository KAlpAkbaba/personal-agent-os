"""The watch's pure half (watch-engine): the hash, the tr-TR number, the conditions, the edge.

Nothing here touches a database, a device or a model. ``judge`` is the one decision a reading
makes: what its outcome is and whether the owner hears of it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.watch import compare
from app.watch.compare import Prior

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


# ------------------------------------------------------------------ the hash


def test_the_same_text_with_different_whitespace_gives_the_same_hash() -> None:
    a = compare.text_sha256("Home  Assistant\n\n 2026.10  \t çıktı ")
    b = compare.text_sha256("Home Assistant 2026.10 çıktı")
    assert a == b
    assert len(a) == 64 and all(ch in "0123456789abcdef" for ch in a)
    assert compare.text_sha256("Home Assistant 2026.11 çıktı") != a


def test_nbsp_counts_as_a_space() -> None:
    assert compare.normalize_text("19.499 TL") == "19.499 TL"
    assert compare.text_sha256("19.499 TL") == compare.text_sha256("19.499 TL")


# ------------------------------------------------------------------ the tr-TR number


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("19.499 TL", 19499.0),
        ("19.499 TL", 19499.0),
        ("19.499,90 TL", 19499.90),
        ("1.299.000", 1299000.0),
        ("39,99", 39.99),
        ("Fiyat: 20000 TL", 20000.0),
        ("₺7.250", 7250.0),
    ],
)
def test_turkish_numbers_are_read_as_written(text: str, value: float) -> None:
    assert compare.parse_tr_number(text) == pytest.approx(value)


@pytest.mark.parametrize(
    "text",
    [
        "19.99",  # ambiguous: a decimal point or a short thousands group - never guessed
        "1,299.00",  # an English number on a Turkish page
        "fiyat yok",
        "",
        "19.499 TL yerine 17.999 TL",  # two numbers: which one is the price is not ours to say
    ],
)
def test_an_ambiguous_or_missing_number_is_none(text: str) -> None:
    assert compare.parse_tr_number(text) is None


def test_no_character_is_stripped_before_parsing() -> None:
    # changedetection #4493: '39,99' became 3999 once the comma was stripped.
    assert compare.parse_tr_number("39,99") != 3999


# ------------------------------------------------------------------ conditions


def test_conditions_parse_and_bad_ones_are_refused() -> None:
    assert compare.parse_condition("changed").kind == "changed"
    below = compare.parse_condition("number_below:20000")
    assert (below.kind, below.number) == ("number_below", 20000.0)
    assert compare.parse_condition("number_above:19.499,90").number == pytest.approx(19499.90)
    assert compare.parse_condition("contains:kararlı sürüm").text == "kararlı sürüm"
    for bad in ("", "sometimes", "number_below:", "number_below:abc", "contains:", "contains:  "):
        with pytest.raises(ValueError):
            compare.parse_condition(bad)


def test_number_below_is_strict() -> None:
    below = compare.parse_condition("number_below:20000")
    assert compare.condition_holds(below, text="", value=20000.0) is False
    assert compare.condition_holds(below, text="", value=19999.0) is True


def test_number_above_is_strict() -> None:
    above = compare.parse_condition("number_above:20000")
    assert compare.condition_holds(above, text="", value=20000.0) is False
    assert compare.condition_holds(above, text="", value=20001.0) is True


def test_a_numeric_condition_without_a_value_is_unknown() -> None:
    below = compare.parse_condition("number_below:20000")
    assert compare.condition_holds(below, text="19.000", value=None) is None


@pytest.mark.parametrize(
    ("page", "needle"),
    [
        ("Yeni KARARLI SÜRÜM yayında", "kararlı sürüm"),
        ("İSTANBUL şubesi açıldı", "istanbul"),
        ("ISPARTA", "ısparta"),
    ],
)
def test_contains_folds_turkish_case(page: str, needle: str) -> None:
    condition = compare.parse_condition(f"contains:{needle}")
    assert compare.condition_holds(condition, text=page, value=None) is True


def test_contains_is_false_when_absent() -> None:
    condition = compare.parse_condition("contains:kararlı sürüm")
    assert compare.condition_holds(condition, text="beta sürüm", value=None) is False


# ------------------------------------------------------------------ the judge (edge-triggered)

SHA_A = "a" * 64
SHA_B = "b" * 64
CHANGED = compare.parse_condition("changed")
BELOW = compare.parse_condition("number_below:20000")


def test_the_first_reading_is_a_baseline_and_never_notifies_on_changed() -> None:
    verdict = compare.judge(CHANGED, Prior(None, None, 0), sha=SHA_A, text="x", value=None)
    assert verdict.outcome == "same"
    assert verdict.notify is False
    assert verdict.baseline is True
    assert verdict.move_baseline is True


def test_an_unchanged_reading_is_same_and_silent() -> None:
    verdict = compare.judge(CHANGED, Prior(SHA_A, None, 0), sha=SHA_A, text="x", value=None)
    assert verdict.outcome == "same"
    assert verdict.notify is False


def test_a_changed_reading_notifies() -> None:
    verdict = compare.judge(CHANGED, Prior(SHA_A, None, 0), sha=SHA_B, text="y", value=None)
    assert verdict.outcome == "changed"
    assert verdict.notify is True
    assert verdict.move_baseline is True


def test_a_condition_met_twice_in_a_row_notifies_once() -> None:
    first = compare.judge(BELOW, Prior(SHA_A, False, 0), sha=SHA_B, text="", value=19999.0)
    assert (first.outcome, first.notify, first.condition_met) == ("condition_met", True, True)
    second = compare.judge(BELOW, Prior(SHA_B, True, 0), sha=SHA_A, text="", value=19500.0)
    assert second.notify is False
    assert second.outcome == "changed"
    assert second.condition_met is True


def test_a_condition_that_turns_false_rearms() -> None:
    off = compare.judge(BELOW, Prior(SHA_A, True, 0), sha=SHA_B, text="", value=21000.0)
    assert (off.notify, off.condition_met) == (False, False)
    on = compare.judge(BELOW, Prior(SHA_B, False, 0), sha=SHA_A, text="", value=19000.0)
    assert on.notify is True


def test_a_condition_met_at_the_baseline_notifies_once() -> None:
    verdict = compare.judge(BELOW, Prior(None, None, 0), sha=SHA_A, text="", value=19000.0)
    assert (verdict.outcome, verdict.notify, verdict.baseline) == ("condition_met", True, True)


def test_a_numeric_condition_without_a_value_is_unreadable_and_keeps_the_baseline() -> None:
    verdict = compare.judge(BELOW, Prior(SHA_A, False, 0), sha=SHA_B, text="", value=None)
    assert verdict.outcome == "unreadable"
    assert verdict.move_baseline is False
    assert verdict.reason_tr == "değer bulunamadı"


def test_three_failures_notify_once_and_a_first_failure_at_once() -> None:
    assert compare.failure_notifies(Prior(SHA_A, None, 0)) is False
    assert compare.failure_notifies(Prior(SHA_A, None, 1)) is False
    assert compare.failure_notifies(Prior(SHA_A, None, 2)) is True  # the third
    assert compare.failure_notifies(Prior(SHA_A, None, 3)) is False  # the fourth
    assert compare.failure_notifies(Prior(None, None, 0)) is True  # no baseline yet
    assert compare.failure_notifies(Prior(None, None, 1)) is False
    assert compare.failure_notifies(Prior(None, None, 2)) is False


# ------------------------------------------------------------------ spreading the checks


def test_each_watch_has_a_fixed_offset_from_its_id() -> None:
    ids = [uuid.UUID(int=n * 7919 + 1) for n in range(40)]
    offsets = [compare.due_offset_seconds(i, 1) for i in ids]
    assert offsets == [compare.due_offset_seconds(i, 1) for i in ids]  # fixed
    assert all(0 <= o < 360 for o in offsets)  # within a tenth of the period
    assert len(set(offsets)) > 20  # spread, not stacked
    assert all(0 <= compare.due_offset_seconds(i, 24) < 24 * 360 for i in ids)


def test_the_next_due_time_counts_from_now_never_from_the_missed_slot() -> None:
    watch_id = uuid.uuid4()
    due = compare.next_due(NOW, watch_id, 1)
    offset = compare.due_offset_seconds(watch_id, 1)
    assert due == NOW + timedelta(hours=1, seconds=offset)
