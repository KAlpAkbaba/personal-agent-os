"""Turkish device-alias phrase parsing (app.devices.aliases)."""

import pytest

from app.devices.aliases import ALIAS_EV, ALIAS_IS, ALIAS_LAPTOP, alias_matches, extract_alias


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("ev", ALIAS_EV),
        ("ev bilgisayarı", ALIAS_EV),
        ("ev bilgisayarımda aç", ALIAS_EV),
        ("evdeki bilgisayarda araştır", ALIAS_EV),
        ("iş", ALIAS_IS),
        ("iş bilgisayarı", ALIAS_IS),
        ("işteki bilgisayarda çalıştır", ALIAS_IS),
        ("laptop", ALIAS_LAPTOP),
        ("laptopta devam et", ALIAS_LAPTOP),
        ("dizüstünde araştır", ALIAS_LAPTOP),
    ],
)
def test_extract_alias_recognizes_canonical_phrases(phrase: str, expected: str) -> None:
    assert extract_alias(phrase) == expected


def test_extract_alias_unknown_phrase_returns_none() -> None:
    assert extract_alias("bilinmeyen bir cihaz") is None


def test_extract_alias_case_insensitive() -> None:
    assert extract_alias("EV BILGISAYARINDA AC") == ALIAS_EV


def test_alias_matches_configured_alias() -> None:
    assert alias_matches(["ev"], "ev bilgisayarımda aç") is True


def test_alias_matches_free_form_configured_alias() -> None:
    assert alias_matches(["eski masaüstü"], "eski masaüstü") is True


def test_alias_matches_false_when_no_configured_alias() -> None:
    assert alias_matches([], "ev bilgisayarımda aç") is False


def test_alias_matches_false_when_configured_alias_differs() -> None:
    assert alias_matches(["iş"], "ev bilgisayarımda aç") is False
