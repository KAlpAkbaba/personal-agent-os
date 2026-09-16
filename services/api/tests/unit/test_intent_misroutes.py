"""B26: the routing measurement, in CI, with the misroute count asserted at zero.

The batch's REAL_PROOF is one line — *103 cümlelik ölçüm seti CI'da; misroute sayısı 0* —
and this file is it. The distinction it keeps is the one the audit drew and the one that
decides how alarming a failure is:

* an utterance that reaches **nothing** leaves the owner to say it differently;
* an utterance that reaches the **wrong** capability *acts*.

Fifty-nine of the audit's 103 sentences reached nothing; requirements 726-735 are about
those and belong to B27. Seven reached the wrong capability, and every one of the seven was
an action: the screen automation went off, a mail went out, text went into whatever window
happened to be focused, a month-long goal became a dated calendar entry.

All seven were reproduced on this machine before anything was changed (see
``MEASURED_MISROUTES``), and the fixes are four narrowings in the router. A narrowing is the
change most likely to take a working sentence with it, so each one is paired with the
sentences it must not have touched — that is what ``SHIELD`` is, and why this file asserts
both halves in the same run.
"""

from __future__ import annotations

import pytest

from app.voice.intents import Intent, normalize_transcript, resolve_intent
from tests.voice_corpus.routing import (
    CRITICAL_VERBS,
    MEASURED_MISROUTES,
    ROUTING_SET,
    SHIELD,
    SOURCE_MEASURED,
    misroutes,
)

#: The audit measured 103 sentences. The set may only grow.
AUDIT_SET_SIZE = 103


def test_the_set_is_at_least_the_size_the_audit_measured() -> None:
    assert len(ROUTING_SET) >= AUDIT_SET_SIZE, (
        f"{len(ROUTING_SET)} cases; the audit measured {AUDIT_SET_SIZE} and this set may "
        "only grow"
    )
    # Every case forbids something. A case with an empty forbidden set and no expectation
    # asserts nothing at all and would pass for a router that resolved everything to
    # `mail.send`.
    for case in ROUTING_SET:
        assert case.forbidden or case.expected is not None, case.utterance
        assert case.why, case.utterance


def test_no_sentence_reaches_the_wrong_capability() -> None:
    """REAL_PROOF: misroute count 0, across the whole set, in one run.

    Reported together rather than one at a time: fixing a router one failure at a time and
    re-running between each is how the seventh gets missed.
    """
    found = misroutes(resolve_intent)
    assert found == [], "\n".join(
        f"  [{case.source}] {case.utterance!r} -> {got} "
        f"(expected {case.expected}, forbidden {case.forbidden}) — {case.why}"
        for case, got in found
    )


@pytest.mark.parametrize("case", MEASURED_MISROUTES, ids=lambda case: case.utterance)
def test_each_measured_misroute_is_closed(case) -> None:
    """Named one at a time as well, so a failure says WHICH of the seven came back."""
    resolved = resolve_intent(case.utterance, **case.state).intent
    assert resolved not in case.forbidden, (
        f"{case.utterance!r} reached {resolved} again — {case.why}"
    )


@pytest.mark.parametrize("case", SHIELD, ids=lambda case: case.utterance)
def test_the_shield_still_holds(case) -> None:
    """req 741: the deterministic router stays the safety shield.

    Every fix in this batch is a narrowing, and these are the sentences each narrowing was
    one word away from taking with it.
    """
    resolved = resolve_intent(case.utterance, **case.state).intent
    assert resolved == case.expected, f"{case.utterance!r} -> {resolved} — {case.why}"


@pytest.mark.parametrize("case", CRITICAL_VERBS, ids=lambda case: case.utterance)
def test_the_critical_verbs_keep_their_meaning(case) -> None:
    """The test plan's five: *dur, gönder, işle, dağıt, kapat*.

    Each appears in the shape where it means what it usually means and in the shape where
    the same letters mean something else — which in Turkish is a suffix away.
    """
    resolved = resolve_intent(case.utterance, **case.state).intent
    assert resolved not in case.forbidden, f"{case.utterance!r} -> {resolved} — {case.why}"
    if case.expected is not None:
        assert resolved == case.expected, f"{case.utterance!r} -> {resolved}"


# ------------------------------------------------------- the four roots, named

def test_otomatik_alone_is_not_a_screen_sentence() -> None:
    """req 738. The gate used to admit any sentence containing "otomatik"."""
    for utterance in (
        "Otomatik güncellemeleri kapat.",
        "Otomatik yedeklemeyi kapat.",
        "Otomatik kaydetmeyi kapat.",
        "Otomatik ödemeyi kapat.",
    ):
        assert resolve_intent(utterance).intent is not Intent.AMBIENT_POLICY_SET, utterance
    # …and the family's own phrase, which has a screen in it, is untouched.
    assert resolve_intent("Ekranları otomatik kapatmayı aç.").intent is Intent.AMBIENT_POLICY_SET


def test_the_policy_writer_is_narrowed_too() -> None:
    """The half that WRITES `auto_off_enabled`, not just the half that routes.

    One call site today; a reader that flips the switch for any sentence containing
    "otomatik" is one call site away from the same defect.
    """
    from app.voice.intents import ambient_policy_changes

    _, tokens, _ = normalize_transcript("Otomatik güncellemeleri kapat.")
    assert ambient_policy_changes(tokens) is None

    _, tokens, _ = normalize_transcript("Ekranları otomatik kapat.")
    assert ambient_policy_changes(tokens) == {"auto_off_enabled": False}


def test_a_confirmation_is_bare() -> None:
    """req 736. "Gönder." confirms a draft; "Dosyayı gönder." is about a file."""
    for utterance in ("Gönder.", "Gönder lütfen.", "Bunu gönder.", "Hadi gönder."):
        assert resolve_intent(utterance).intent is Intent.MAIL_SEND, utterance
    for utterance in (
        "Dosyayı gönder.",
        "Bu dosyayı bana gönder.",
        "Bu belgeyi gönder.",
        "Raporu Ali'ye gönder.",
    ):
        assert resolve_intent(utterance).intent is not Intent.MAIL_SEND, utterance


def test_yazdir_is_print_not_type() -> None:
    """req 737. Turkish builds words by suffix; `yaz` is the first three letters of four
    words that are not the verb."""
    for utterance in ("Bunu yazdır.", "Belgeyi yazdır.", "Şunu yazdırt."):
        assert resolve_intent(utterance).intent is not Intent.TYPE_TEXT, utterance
    for utterance in ("Buraya merhaba yaz.", "Bu kutuya adımı yaz.", "Seçili yere adresi yaz."):
        assert resolve_intent(utterance).intent is Intent.TYPE_TEXT, utterance


def test_the_payload_reader_agrees_about_the_verb() -> None:
    """The two halves of req 737: the matcher and the reader that cuts the utterance at the
    verb. Two lists of the same words is how halves drift, so the reader uses a lookahead
    over the same non-verbs."""
    from app.voice.intents import _WRITE_VERB_RE

    assert _WRITE_VERB_RE.search("bunu yazdır") is None
    assert _WRITE_VERB_RE.search("yazıcıyı aç") is None
    assert _WRITE_VERB_RE.search("buraya merhaba yaz") is not None


def test_ekle_needs_a_calendar_anchor() -> None:
    """req 739. Everything gets added to something; only a time or an appointment makes the
    sentence the calendar's."""
    for utterance in (
        "Bir hedef ekle: bu ay kitabı bitir.",
        "Listeye süt ekle.",
        "Bir not ekle.",
    ):
        assert resolve_intent(utterance).intent is not Intent.CALENDAR_PROPOSE, utterance
    for utterance in (
        "Perşembe 15'e diş hekimi ekle.",
        "Yarın 10'a toplantı ekle.",
        "Duruşmayı takvime ekle.",
    ):
        assert resolve_intent(utterance).intent is Intent.CALENDAR_PROPOSE, utterance


def test_the_measured_seven_are_still_named() -> None:
    """The audit's own seven, kept as a list that may only grow.

    A regression corpus that can shrink is a corpus that will: the one sentence somebody
    cannot make pass is the one sentence that gets removed.
    """
    assert len(MEASURED_MISROUTES) >= 7
    assert all(case.source == SOURCE_MEASURED for case in MEASURED_MISROUTES)
    utterances = {case.utterance for case in MEASURED_MISROUTES}
    for audited in (
        "Otomatik güncellemeleri kapat.",
        "Dosyayı gönder.",
        "Bunu yazdır.",
        "Bir hedef ekle: bu ay kitabı bitir.",
    ):
        assert audited in utterances, audited
