"""Contract v1.6 item 10: risk markers are whole words, from one file (ADR-0207).

Until v1.6 a marker was a SUBSTRING of the control's name: ``sil`` matched "silver",
``ode`` matched "mode", ``pay`` matched "paylaş". Every marker here has a positive case
and, where one exists in either language, a near miss that must NOT match - the
repository's recurring Turkish defect (a stem match is a guess about the suffix) held
from both sides.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from browser_agent import risk_markers
from browser_agent.policy import ResolvedElement, RiskClass, classify_click
from browser_agent.risk_markers import (
    EXTERNAL_COMMUNICATION,
    HIGH_IMPACT,
    first_marker,
    fold,
    is_external_communication,
    is_high_impact,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
MARKERS_JSON = REPO_ROOT / "packages" / "protocol" / "browser-risk-markers.json"
CLOUD_SOURCES = REPO_ROOT / "services" / "api" / "app"


def _shared() -> dict[str, object]:
    # No skip when the file is missing: a guard that goes green because the contract was
    # deleted is the 2026-09-12 defect (test_contract_falsification).
    return json.loads(MARKERS_JSON.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ one source


def test_the_workers_lists_are_the_shared_file_verbatim() -> None:
    shared = _shared()
    assert list(HIGH_IMPACT) == shared["high_impact"]
    assert list(EXTERNAL_COMMUNICATION) == shared["external_communication"]


def test_no_marker_is_in_both_lists_and_none_is_listed_twice() -> None:
    folded_high = [fold(m) for m in HIGH_IMPACT]
    folded_ext = [fold(m) for m in EXTERNAL_COMMUNICATION]
    assert len(set(folded_high)) == len(folded_high)
    assert len(set(folded_ext)) == len(folded_ext)
    assert not set(folded_high) & set(folded_ext)


def test_the_other_side_reads_the_same_file_and_keeps_no_list_of_its_own() -> None:
    """Contract halves must read each other. The Cloud Core has no consumer of the
    markers until the loop (PR-B); what is held NOW is that nobody there has started a
    second list - the substring list this file replaces began as exactly that."""
    offenders: list[str] = []
    needle = re.compile(r"[\"'](sat[ıi]n al|siparişi tamamla|onayla ve öde)[\"']", re.IGNORECASE)
    for path in CLOUD_SOURCES.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "browser-risk-markers.json" in text:
            continue
        if needle.search(text) and "risk" in text.lower() and "browser" in path.as_posix():
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert not offenders, f"a second list of risk markers, not read from the file: {offenders}"


# ------------------------------------------------------------------ every marker matches


@pytest.mark.parametrize("marker", HIGH_IMPACT)
def test_every_high_impact_marker_matches_as_itself_and_inside_a_sentence(marker: str) -> None:
    assert is_high_impact(marker)
    assert is_high_impact(marker.upper())
    assert is_high_impact(f"  {marker.capitalize()}  ")
    assert is_high_impact(f"Devam: {marker} (1 ürün)")


@pytest.mark.parametrize("marker", EXTERNAL_COMMUNICATION)
def test_every_external_marker_matches_and_is_not_high_impact(marker: str) -> None:
    assert is_external_communication(marker)
    assert is_external_communication(f"Şimdi {marker}!")
    assert not is_high_impact(marker)


SINGLE_WORDS = [m for m in (*HIGH_IMPACT, *EXTERNAL_COMMUNICATION) if " " not in m]


@pytest.mark.parametrize("marker", SINGLE_WORDS)
def test_no_single_word_marker_matches_inside_a_longer_word(marker: str) -> None:
    """Glued to letters or digits on either side, a marker is part of another word."""
    assert first_marker(f"x{marker}x") is None
    assert first_marker(f"{marker}zz") is None
    assert first_marker(f"zz{marker}") is None
    assert first_marker(f"{marker}2") is None


def test_a_phrase_matches_only_as_the_whole_phrase_or_by_a_word_of_its_own() -> None:
    """ "Onayla ve ödeme" is not "onayla ve öde" - but "onayla" is a marker by itself,
    so the name is still one that sends; it is the CLASS that differs."""
    assert first_marker("Siparişi tamamlama rehberi") is None
    assert first_marker("Abone olmadan devam et") is None
    assert not is_high_impact("Onayla ve ödeme geçmişini gör")
    assert is_external_communication("Onayla ve ödeme geçmişini gör")


# ------------------------------------------------------------------ the near misses


@pytest.mark.parametrize(
    "name",
    [
        # The three the owner named.
        "Silver plan",
        "Dark mode",
        "Paylaş",
        # The rest of the family: a marker's letters inside another word.
        "Silgi",
        "Silindir",
        "Silecek",
        "Kodet",
        "Model seç",
        "Moderatör",
        "Episode 4",
        "Payment history",  # "pay" is not "payment"; opening a history pays nothing
        "Paylaşım ayarları",
        "Sending options",
        "Sender name",
        "Removed items",
        "Deleted messages",
        "Buyer protection",
        "Transferred files",
        "Gönderiler",
        "Gönderim seçenekleri",
        "Ödeme geçmişi",
        "Ödemeler",
        "Abonelikler",
        "Kaldırım",
        "Sepete ekle",
        "Devam et",
        "Kapat",
        "İptal",
        "Favorilere ekle",
        "",
    ],
)
def test_a_name_that_only_contains_a_markers_letters_is_not_high_impact(name: str) -> None:
    assert not is_high_impact(name), first_marker(name)


@pytest.mark.parametrize(
    "name",
    [
        "Sil",
        "Hesabı sil",
        "Kalıcı olarak sil",
        "Öde",
        "Hemen öde",
        "Ödeme yap",
        "Ödemeye geç",
        "Satın al",
        "Şimdi satın al",
        "Satın Alın",
        "Siparişi tamamla",
        "Siparişi onayla",
        "Sipariş ver",
        "Onayla ve öde",
        "Alışverişi tamamla",
        "Abone ol",
        "Aboneliği başlat",
        "Aboneliği iptal et",
        "Ücretsiz denemeyi başlat",
        "Gönder",
        "Mesajı gönder",
        "Para gönder",
        "Havale",
        "EFT",
        "Kaldır",
        "Buy",
        "Buy now",
        "Pay",
        "Pay with card",
        "Checkout",
        "Proceed to checkout",
        "Place order",
        "Place your order",
        "Subscribe",
        "Delete account",
        "Remove item",
        "Send message",
        "Transfer",
    ],
)
def test_the_words_on_a_checkout_page_are_high_impact(name: str) -> None:
    assert is_high_impact(name)


@pytest.mark.parametrize(
    ("typed", "meant"),
    [
        ("SATIN AL", "satın al"),
        ("Satin al", "satın al"),
        ("ODEME YAP", "ödeme yap"),
        ("Odeme yap", "ödeme yap"),
        ("SİPARİŞİ TAMAMLA", "siparişi tamamla"),
        ("Siparisi tamamla", "siparişi tamamla"),
        ("GÖNDER", "gönder"),
        ("Gonder", "gönder"),
        ("ABONE OL", "abone ol"),
    ],
)
def test_capitals_and_letters_typed_without_their_marks_are_the_same_word(
    typed: str, meant: str
) -> None:
    assert fold(typed) == fold(meant)
    assert is_high_impact(typed)


def test_the_dotted_capital_i_does_not_break_a_word_in_two() -> None:
    """``"İ".casefold()`` is "i" plus a combining dot, which would put a non-letter in
    the middle of "SİL" and let the boundary rule lose the word."""
    assert fold("SİL") == "sil"
    assert is_high_impact("SİL")
    assert is_high_impact("SİPARİŞİ ONAYLA")


def test_the_longest_marker_is_the_one_reported() -> None:
    assert first_marker("Onayla ve öde") == fold("onayla ve öde")
    assert first_marker("Onayla") == "onayla"
    assert first_marker("Silver plan") is None


def test_punctuation_is_a_boundary_and_a_letter_is_not() -> None:
    for name in ("Sil.", "(Sil)", "Sil!", "Sil / Kaldır", "sil-", "Sil "):
        assert is_high_impact(name), name
    for name in ("Sil2go", "1sil", "silme"):
        assert not is_high_impact(name), name


# ------------------------------------------------------------------ the click classification


@pytest.mark.parametrize(
    ("name", "is_submit", "has_href", "expected"),
    [
        ("Silver plan", False, False, RiskClass.REVERSIBLE_WRITE),
        ("Dark mode", False, False, RiskClass.REVERSIBLE_WRITE),
        ("Paylaş", False, False, RiskClass.EXTERNAL_COMMUNICATION),
        ("Onayla", False, False, RiskClass.EXTERNAL_COMMUNICATION),
        ("Siparişi tamamla", True, False, RiskClass.HIGH_IMPACT),
        ("Onayla ve öde", False, False, RiskClass.HIGH_IMPACT),
        ("Abone ol", False, True, RiskClass.HIGH_IMPACT),
        ("Kaydet", True, False, RiskClass.EXTERNAL_COMMUNICATION),
        ("Kargo bilgisi", False, True, RiskClass.NAVIGATE),
        ("Sepete ekle", False, False, RiskClass.REVERSIBLE_WRITE),
    ],
)
def test_the_click_is_classified_from_whole_words(
    name: str, is_submit: bool, has_href: bool, expected: RiskClass
) -> None:
    element = ResolvedElement(
        tag="a" if has_href else "button",
        role="link" if has_href else "button",
        name=name,
        has_href=has_href,
        is_submit=is_submit,
    )
    assert classify_click(element) == expected


def test_the_module_exports_what_the_policy_uses() -> None:
    for name in ("is_high_impact", "is_external_communication", "fold", "first_marker"):
        assert name in risk_markers.__all__
