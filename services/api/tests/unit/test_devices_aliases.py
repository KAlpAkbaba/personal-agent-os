"""Turkish device-alias phrase parsing (app.devices.aliases)."""

import pytest

from app.devices.aliases import (
    ALIAS_EV,
    ALIAS_IS,
    ALIAS_LAPTOP,
    ALIAS_OFIS,
    alias_matches,
    extract_alias,
)


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


# --- ADR-0205: the second device is "ofis" --------------------------------------------
#
# 2026-09-28: GMKADIRAKBABA enrolled and the owner calls it "ofis". The parser knew ev / iş /
# laptop; "ofis" selected the device only as the bare word, through the free-form equality
# branch, so the sentence a person actually says - "ofis bilgisayarımda aç" - selected
# nothing.


@pytest.mark.parametrize(
    "phrase",
    [
        "ofis",
        "Ofis",
        "ofis bilgisayarı",
        "ofis bilgisayarımda aç",
        "ofisteki bilgisayarda araştır",
        "ofiste aç",
        "ofisimde çalıştır",
        "ofisimdeki bilgisayarda devam et",
    ],
)
def test_extract_alias_recognizes_the_office(phrase: str) -> None:
    assert extract_alias(phrase) == ALIAS_OFIS


def test_the_office_sentence_selects_a_device_aliased_ofis() -> None:
    """The owner's device carries ["ofis", "iş"]: either word, in a sentence, finds it."""
    configured = ["ofis", "iş"]
    assert alias_matches(configured, "ofis bilgisayarımda aç") is True
    assert alias_matches(configured, "iş bilgisayarında aç") is True
    assert alias_matches(configured, "ev bilgisayarımda aç") is False
    assert alias_matches(["ev"], "ofis bilgisayarımda aç") is False


@pytest.mark.parametrize(
    "phrase",
    [
        # "ofis" inside another word is not the office.
        "ofisiyal bir belge aç",
        "profisyonel",
        # Microsoft Office is a program, not a place.
        "office programını aç",
    ],
)
def test_the_office_is_not_found_inside_other_words(phrase: str) -> None:
    assert extract_alias(phrase) is None


@pytest.mark.parametrize(
    "phrase",
    [
        # `i[şs]te\w*` read every form of "istemek" as "at work". Harmless while one
        # device existed; with a second device aliased "iş" it is a wrong machine.
        "istediğim videoyu aç",
        "istemiyorum",
        "istersen aç",
        "isterim",
        "istek listesini aç",
        "istanbul haberlerini aç",
        "işlem yap",
        "işletim sistemini güncelle",
    ],
)
def test_wanting_something_is_not_being_at_work(phrase: str) -> None:
    assert extract_alias(phrase) is None


@pytest.mark.parametrize("phrase", ["işte aç", "iste aç", "işteyken aç", "işteki bilgisayarda aç"])
def test_at_work_is_still_at_work(phrase: str) -> None:
    assert extract_alias(phrase) == ALIAS_IS


@pytest.mark.parametrize(
    "phrase", ["evdeki bilgisayarda aç", "evde aç", "evimde aç", "evdeyken aç"]
)
def test_at_home_is_still_at_home(phrase: str) -> None:
    assert extract_alias(phrase) == ALIAS_EV


@pytest.mark.parametrize("phrase", ["evden çıkınca kapat", "evet aç", "evrak aç", "evlilik"])
def test_home_is_not_found_inside_other_words(phrase: str) -> None:
    assert extract_alias(phrase) is None
