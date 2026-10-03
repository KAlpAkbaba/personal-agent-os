"""ADR-0224 addendum 4's gap, step 2: no confident WRONG reading of a sentence the STT broke.

The STT corpus of 2026-10-03 (main e6682a61) read six renderings as something the owner did
not mean at HIGH - worse than "not understood", because they ACT. Each is reproduced here with
the rule that matched it, and held to the owner's rule: the meant reading, or ONE question.
Never another intent at HIGH or MEDIUM.

The causes, each fixed where it is (the sentences are never listed anywhere):

* the media table's bare-title guess took "Notü Defteri'ni aç." at 1.0 because layer 1 could
  not read a word the STT gave an ending nobody said ("notü": a vowel the stem's harmony
  cannot take) - it now reads it as the word it is;
* a split or a repaired ending let ANOTHER table claim the very words a looser rule had
  claimed as heard (memory_correct's bare "düzelt", the bare repeat's "yeniden"): two
  claimants, neither exact - MEDIUM at most, both named, and the policy asks;
* the relay's adapter gave a repaired WORD the suffix-dropped 0.9 (HIGH) where the router
  gave it the confusion's 0.75.
"""

from __future__ import annotations

import pytest

from app.voice import intents as intents_module
from app.voice.intents import Intent, resolve_intent
from app.voice.understanding import normalize as layer_one
from app.voice.understanding import policy
from app.voice.understanding.combine import MATCH_CONFUSION, RULE_CONFIDENCE, Candidate
from app.voice.understanding.policy import BAND_HIGH, BAND_LOW, BAND_MEDIUM

CONFUSION = RULE_CONFIDENCE[MATCH_CONFUSION]


def _read(text: str) -> tuple[intents_module.ResolvedIntent, policy.Decision]:
    """The relay's own path for one sentence: the router, its adapter, the policy (no layer-2
    engine, as production runs today)."""
    resolved = resolve_intent(text)
    rule = policy.rule_reading(
        resolved.intent.value,
        application=resolved.application,
        route_repair=resolved.route_repair,
    )
    return resolved, policy.read_turn(text, rule=rule, engine=None)


def _assert_meant_or_asks(text: str, meant: Intent, application: str | None = None) -> None:
    resolved, decision = _read(text)
    if decision.band in (BAND_HIGH, BAND_MEDIUM):
        assert resolved.intent is meant, (
            f"{text!r} read as {resolved.intent.value} at {decision.band} "
            f"(matched {resolved.matched!r}); the owner meant {meant.value}"
        )
        if application is not None:
            assert resolved.application == application, text
        return
    assert decision.band == BAND_LOW and decision.question, (text, decision)
    assert decision.question.count("?") == 1, decision.question
    assert not decision.acts


# --- the six renderings of the day ---------------------------------------------------------


def test_an_invented_ending_on_an_application_name_is_not_a_media_title():
    """stt.derived.op.app.1.invented_suffix. Matched: ``_bare_title_media_match`` ("adıyla
    aç": a play verb and two words no table knew) -> media_play at 1.0. Layer 1 could not
    read "notü" (not + an "ü" the stem's harmony cannot take), so no table owned it."""
    _assert_meant_or_asks("Notü Defteri'ni aç.", Intent.APP_OPEN, "notepad")
    resolved, decision = _read("Notü Defteri'ni aç.")
    assert resolved.intent is Intent.APP_OPEN
    assert decision.band != BAND_HIGH  # a word the STT invented is never HIGH


def test_an_invented_ending_on_the_calculator_is_not_a_media_title():
    """stt.derived.op.app.8.invented_suffix. Matched: ``_bare_title_media_match`` ("adıyla
    aç") -> media_play at 1.0, for "Hesapü makinesini aç."."""
    _assert_meant_or_asks("Hesapü makinesini aç.", Intent.APP_OPEN, "calc")
    resolved, decision = _read("Hesapü makinesini aç.")
    assert resolved.intent is Intent.APP_OPEN and decision.band != BAND_HIGH


def test_a_fused_deictic_does_not_hand_the_bug_to_the_memory_correction():
    """stt.derived.selfdev.fix.canonical.fused. Matched: ``_memory_match``'s CORRECT branch
    (the bare stem "düzelt", any object) -> memory_correct at 1.0, because "Şubug'ı" hid the
    defect noun from ``_selfdev_match``. Split, the sentence is selfdev_fix: two claimants of
    the same verb."""
    _assert_meant_or_asks("Şubug'ı kendin düzelt.", Intent.SELFDEV_FIX)


def test_an_invented_ending_on_kendin_does_not_hand_the_bug_to_the_memory_correction():
    """stt.derived.selfdev.fix.canonical.invented_suffix. Matched: ``_memory_match``'s CORRECT
    branch ("düzelt") -> memory_correct at 1.0: "kendinü" hid the self-reference from
    ``_selfdev_match``."""
    _assert_meant_or_asks("Şu bug'ı kendinü düzelt.", Intent.SELFDEV_FIX)


def test_a_fused_object_does_not_turn_a_redraw_into_a_repeat():
    """stt.derived.creative.redraw.canonical.fused. Matched: the bare repeat of
    ``_resolve_intent_rules`` step 6 ("yeniden" anywhere) -> repeat at 1.0, because
    "Buresmi" hid the image noun from ``_creative_redraw_match``."""
    _assert_meant_or_asks("Buresmi Paint'te yeniden çiz.", Intent.CREATIVE_REDRAW)


def test_a_fused_time_is_a_repaired_word_and_never_high():
    """stt.derived.c.collision.alarm_create.fused. Matched: ``_alarm_match`` on the split
    reading (the right intent) at the router's 0.75 - but the relay's adapter mapped any
    repair to the suffix-dropped 0.9, HIGH."""
    resolved, decision = _read("Saat yedibuçukta beni uyandır.")
    assert resolved.intent is Intent.ALARM_CREATE
    assert decision.band == BAND_MEDIUM and decision.confidence == CONFUSION


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ALAN_ISTEGI app/alarms/tr_time.py: the alarm tool parses 'yedibuçukta' as heard "
        "(the router's reading is right; the time is the tool's)"
    ),
)
def test_the_alarm_tool_reads_a_fused_half_hour():
    """stt.derived.c.collision.alarm_create.fused, the tool half: ``parse_when_text`` gets the
    sentence as heard and refuses ``when_unparsed``. Outside this card's area."""
    from datetime import UTC, datetime

    from app.alarms.tr_time import parse_when_text

    now = datetime(2026, 10, 3, 5, 0, tzinfo=UTC)
    parsed = parse_when_text("Saat yedibuçukta beni uyandır.", now=now, timezone="Europe/Istanbul")
    plain = parse_when_text("Saat yedi buçukta beni uyandır.", now=now, timezone="Europe/Istanbul")
    assert parsed == plain


# --- the rule: two claimants, neither exact -------------------------------------------------


def test_two_claimants_of_the_same_words_are_never_high_and_both_are_named():
    resolved = resolve_intent("Şubug'ı kendin düzelt.")
    assert resolved.confidence <= CONFUSION
    assert resolved.route_repair is not None
    assert f"contested:{Intent.SELFDEV_FIX.value}" in resolved.route_repair.split("+")
    _, decision = _read("Şubug'ı kendin düzelt.")
    assert decision.band == BAND_LOW and not decision.acts
    named = {intent for intent, _ in decision.candidate_names()}
    assert {resolved.intent.value, Intent.SELFDEV_FIX.value} <= named


def test_the_policy_asks_between_two_claimants_whatever_the_confidence():
    rule = Candidate("memory_correct", 1.0, source="rule")
    out = policy.decide([rule], rival="selfdev_fix")
    assert out.band == BAND_LOW and not out.acts
    assert out.question is not None and out.question.count("?") == 1
    assert [i for i, _ in out.candidate_names()] == ["memory_correct", "selfdev_fix"]
    assert policy.decide([rule]).band == BAND_HIGH  # alone, the same reading acts


def test_a_word_repair_claiming_other_words_leaves_the_owned_sentence_alone():
    """A table that owns the sentence for words the repair did not touch keeps it, as before:
    the fused word is a second command or dictated content ("Müziği durdur ve ekranlarıkapat")."""
    for said, owner in (
        ("Müziği durdur ve ekranlarıkapat", Intent.MEDIA_STOP),
        ("Şunu hatırla: alarmkur", Intent.MEMORY_REMEMBER),
    ):
        resolved = resolve_intent(said)
        assert resolved.intent is owner, said
        assert resolved == intents_module._resolve_intent_rules(said), said


def test_the_relays_adapter_gives_a_repaired_word_the_confidence_of_a_confusion():
    for repair in ("fused", "invented", "polite+fused"):
        reading = policy.rule_reading("app_open", application="calc", route_repair=repair)
        assert reading is not None and reading.match_kind == MATCH_CONFUSION, repair
    polite = policy.rule_reading("app_open", application="calc", route_repair="polite")
    assert polite is not None and polite.match_kind != MATCH_CONFUSION


# --- the invented ending, read by layer 1 ----------------------------------------------------


def test_layer_one_reads_an_ending_the_stems_harmony_cannot_take():
    reading = layer_one.lemma_reading("Notü Defteri'ni aç.")
    assert reading is not None and reading.text == "Not Defteri'ni aç."
    assert reading.invented == (("notü", "not"),)
    assert layer_one.lemma_reading("Hesapü makinesini aç.").text == "Hesap makinesini aç."
    assert layer_one.lemma_reading("Şu bug'ı kendinü düzelt.").text == "Şu bug'ı kendin düzelt."


@pytest.mark.parametrize(
    "said",
    [
        "Notu aç.",  # not + u: the harmony's own vowel, a real accusative
        "Ofisi aç.",
        "Saati söyle.",  # saat takes a front vowel (lexical harmony)
        "Paint'i aç.",  # an apostrophe ending is the owner's spelling
        "Kapatü.",  # a verb is never given back its stem this way
        "Istanbulü aç.",  # an unknown word stays whole
    ],
)
def test_layer_one_leaves_a_harmonic_or_unknown_ending_alone(said):
    reading = layer_one.lemma_reading(said)
    assert reading is None or reading.invented == (), said


def test_an_owned_sentence_with_an_invented_ending_keeps_its_reading_as_heard():
    """ "Saatü yedi buçukta beni uyandır." was read right as heard: an invented ending the
    same table reads either way changes nothing."""
    said = "Saatü yedi buçukta beni uyandır."
    assert resolve_intent(said) == intents_module._resolve_intent_rules(said)


def test_no_sentence_of_the_owner_utterance_suite_holds_an_invented_ending():
    """The rewrite's precision, held against every sentence the owner corpus knows."""
    from tests.voice_corpus.corpus import all_cases

    cases = all_cases()
    assert len(cases) >= 2754
    invented = {}
    for case in cases:
        reading = layer_one.lemma_reading(case.utterance)
        if reading is not None and reading.invented:
            invented[case.case_id] = reading.invented
    assert invented == {}
