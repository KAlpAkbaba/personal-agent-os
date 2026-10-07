"""Dil dayanıklılığı: the test team's findings of 2026-10-06 (rounds t-d20261006, staging
fba299af; card understanding-plural-context-typos, 30 findings merged).

The singular of every sentence below worked; the forms the owner actually says did not:

1. plural / possessive endings: "alarmları kapat", "alarmlarımı sil", "nöbetlerimden birini
   kaldır";
2. a pronoun or "the previous one" that points at the session's last object;
3. long polite sentences around a command;
4. one-letter typos and joined words: "sütt bitti", "sütbitti", "alrmı kapat", "alarmmı kapat";
5. an empty and a 1200-character sentence: a Turkish 422.

Families 1 and 4 are layer 1's WORD repairs (``lemma_reading(..., repair_words=True)``): the
word is read from the module's own suffix grammar and one edit, never from a list of forms,
and the repaired sentence reaches its intent through the very same rule tables. The router
asks for that reading only for a sentence the words as heard left unrouted (the tables read
most plurals as they are: "Ekranları kapatın."), at the repaired-word confidence - MEDIUM,
read back in the receipt, never a second question.

The ``*_router*`` tests and families 2, 3 and 5 need files outside the understanding layer
(``intents.py`` to ask for the reading, the session context, the household parser, the event
route): they are RED until that wiring lands (the card's ALAN_ISTEGI).
"""

from __future__ import annotations

import pytest

from app.voice.intents import Intent, resolve_intent
from app.voice.understanding import normalize as layer_one
from app.voice.understanding import policy

#: The confidence the router gives a reading whose word layer 1 repaired.
REPAIRED_WORD = 0.75

PLURALS = [
    ("alarmları kapat", "alarmı kapat"),
    ("Alarmlarımı sil.", "alarmı sil."),
    ("nöbetlerimden birini kaldır", "nöbeti kaldır"),
]
TYPOS = [
    ("sütt bitti", "süt bitti"),
    ("sütbitti", "süt bitti"),
    ("alrmı kapat", "alarmı kapat"),
    ("alarmmı kapat", "alarmı kapat"),
]


def _routed(plain: str) -> Intent:
    expected = resolve_intent(plain)
    assert expected.intent is not Intent.NONE, plain
    assert expected.confidence == 1.0, plain
    return expected.intent


# --- 1 and 4, layer 1: the word repairs ----------------------------------------------------


@pytest.mark.parametrize(("said", "plain"), PLURALS + TYPOS)
def test_the_word_repair_reads_the_sentence_the_tables_route(said, plain):
    _routed(plain)
    assert resolve_intent(said).intent is Intent.NONE or said == plain  # unrouted as heard
    reading = layer_one.lemma_reading(said, repair_words=True)
    assert reading is not None, said
    assert reading.text == plain, (said, reading)
    assert reading.repaired or reading.splits, reading


def test_the_singular_comes_from_the_suffix_grammar():
    reading = layer_one.lemma_reading("alarmlarımızı kapat", repair_words=True)
    assert reading is not None
    assert reading.text == "alarmı kapat"
    assert reading.repaired == (("alarmlarımızı", "alarmı"),)
    partitive = layer_one.lemma_reading("nöbetlerimden birini kaldır", repair_words=True)
    assert partitive is not None
    assert partitive.repaired == (("nöbetlerimden birini", "nöbeti"),)


def test_a_comma_keeps_the_partitive_out_of_the_noun_phrase():
    reading = layer_one.lemma_reading("nöbetlerimden, birini kaldır", repair_words=True)
    assert reading is not None
    assert reading.repaired == (("nöbetlerimden", "nöbetten"),)


def test_the_repairs_and_the_polite_form_read_together():
    reading = layer_one.lemma_reading("Alarmlarımı silebilir misin?", repair_words=True)
    assert reading is not None
    assert reading.text == "alarmı sil?"
    assert reading.dropped == (("silebilir misin", "sil"),)


def test_without_the_flag_the_reading_is_what_it_was():
    """The tables read most plurals as they are: no sentence of today's reading changes."""
    for said, _ in PLURALS + TYPOS:
        reading = layer_one.lemma_reading(said)
        assert reading is None or not reading.repaired, reading
    reading = layer_one.lemma_reading("Ekranları kapatın.")
    assert reading is not None and reading.text == "Ekranları kapat."


def test_a_plural_with_a_negative_imperative_has_no_reading():
    assert layer_one.lemma_reading("alarmları kapatma", repair_words=True) is None
    assert resolve_intent("alarmları kapatma").intent is not Intent.ALARM_STOP


@pytest.mark.parametrize(
    "said",
    [
        "bu hafta ne var",  # "hafta" is one letter from "hata": a deletion is never a repair
        "takim maçı kaçta",  # "takim" is one consonant from "takvim": only a vowel goes in
        "iptal etti mi",  # "etti" is one letter from "eti" (meat): no bare item name
        "anne geldi",  # a doubled letter is collapsed only into a word this layer knows
        "saat kaç",
    ],
)
def test_a_real_word_near_a_known_one_is_left_as_said(said):
    reading = layer_one.lemma_reading(said, repair_words=True)
    assert reading is None or not (reading.repaired or reading.splits), reading


def test_a_typo_two_words_are_one_edit_from_is_left_whole():
    """ "alarmmı" is "alarmı" with a doubled m, not "alarmımı" with a vowel lost: the doubled
    letter wins; with nothing doubled, two vowel readings are no reading."""
    assert layer_one._typo("alarmmı") == "alarmı"
    assert layer_one._typo("alrmı") == "alarmı"
    assert layer_one._typo("sütt") == "süt"
    assert layer_one._typo("hafta") is None


# --- 1 and 4, the router: asks for the reading when nothing routed the words as heard -------


@pytest.mark.parametrize(("said", "plain"), PLURALS + TYPOS)
def test_the_router_reads_the_word_repair_at_the_repaired_word_confidence(said, plain):
    expected = _routed(plain)
    got = resolve_intent(said)
    assert got.intent is expected, (said, got.intent, got.matched)
    assert got.confidence == REPAIRED_WORD, (said, got.confidence)
    assert policy.band_of(got.confidence) == policy.BAND_MEDIUM, said
    if plain.startswith("süt"):
        assert got.household_item == "süt"


# --- 3. long polite sentences (the router's tables) ---------------------------------------


def test_a_long_polite_sentence_stops_the_ringing_alarm_and_closes_no_window():
    said = "Şu an çalan alarmı kapatır mısın artık, uyandım zaten, teşekkürler"
    got = resolve_intent(said)
    assert got.intent is Intent.ALARM_STOP, (got.intent, got.matched)


def test_a_chatty_sentence_with_two_household_items_names_both():
    said = "Mutfağa baktım da süt de bitmiş ekmek de kalmamış, haberin olsun"
    got = resolve_intent(said)
    assert got.intent is Intent.HOUSEHOLD_LEVEL, (got.intent, got.matched)
    assert {got.household_item, *getattr(got, "household_items", ())} >= {"süt", "ekmek"}


# --- 2. context: the pronoun points at the session's last object ---------------------------

_POINTING = ["onu yedi buçuğa al", "bir öncekini sil", "aynısını yarın için"]


@pytest.mark.parametrize("said", _POINTING)
def test_a_pronoun_with_nothing_before_it_acts_on_nothing(said):
    assert resolve_intent(said).intent is Intent.NONE, said


@pytest.mark.parametrize("said", _POINTING)
def test_a_pronoun_after_an_alarm_reads_the_alarm_through_the_real_relay(
    said, monkeypatch, tmp_path
):
    from tests.unit.test_operator_open_application_fallback import (
        _both_online,
        _bound_session,
        _say,
    )
    from tests.unit.test_understanding_relay import _record

    world = _both_online(monkeypatch, tmp_path)
    sid = _bound_session(world, "MAIL")
    _say(world.client, sid, "Yarın sabah yedide alarm kur.")
    assert _record(world, sid).get("intent") == Intent.ALARM_CREATE.value
    _say(world.client, sid, said)
    assert str(_record(world, sid).get("intent", "")).startswith("alarm_"), said


# --- 5. the validation answers --------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "a" * 1200], ids=["empty", "1200-chars"])
def test_an_empty_or_a_1200_character_sentence_answers_422_in_turkish(text):
    from pydantic import ValidationError

    from app.voice.realtime_sessions.routes import ClientEvent

    with pytest.raises(ValidationError) as caught:
        ClientEvent(kind="utterance", t_ms=0, text=text)
    message = str(caught.value)
    assert any(ch in message for ch in "çğıöşüÇĞİÖŞÜ"), message


def test_the_repaired_label_is_read_back_by_the_policy_adapter():
    """The router's label for a word-repair reading ("repaired") earns the confidence of a
    confusion in the relay's adapter, as "fused" and "invented" do: MEDIUM, read back."""
    from app.voice.understanding.combine import MATCH_CONFUSION, RULE_CONFIDENCE, rule_candidate

    for label in ("repaired", "polite+repaired"):
        candidate = rule_candidate(policy.rule_reading("alarm_stop", route_repair=label))
        assert candidate is not None
        assert candidate.confidence == RULE_CONFIDENCE[MATCH_CONFUSION] == REPAIRED_WORD, label
