"""TEAM_STATUS: "ekip ne yapıyor?" reaches the team.status tool and nothing else changes."""

from __future__ import annotations

import pytest

from app.voice.intents import (
    KLASS_QUERY,
    QUERY_TOOL_BY_INTENT,
    Intent,
    klass_for,
    resolve_intent,
)

TEAM_SENTENCES = (
    "ekip ne yapıyor",
    "Ekip ne durumda?",
    "Ofiste kim çalışıyor?",
    "Ajanlar ne yapıyor?",
)


@pytest.mark.parametrize("text", TEAM_SENTENCES)
def test_the_four_sentences_resolve_to_team_status(text):
    resolved = resolve_intent(text)
    assert resolved.intent is Intent.TEAM_STATUS
    assert klass_for(Intent.TEAM_STATUS) == KLASS_QUERY
    assert QUERY_TOOL_BY_INTENT[Intent.TEAM_STATUS] == "team.status"


@pytest.mark.parametrize("text", ["Ofiste ne yaptın?", "Bu hafta ne oldu?"])
def test_the_narrative_neighbours_keep_their_intent(text):
    assert resolve_intent(text).intent is not Intent.TEAM_STATUS


def test_bu_hafta_ne_oldu_is_still_the_narrative():
    resolved = resolve_intent("Bu hafta ne oldu?")
    assert resolved.intent is Intent.EXPLAIN
    assert resolved.query_kind == "narrative"


@pytest.mark.parametrize(
    "text",
    ["Takvimimde ne var?", "Ajandada ne var?", "Ekip toplantısını takvime ekle.", "Ne yapıyorsun?"],
)
def test_neighbouring_words_do_not_fall_into_team_status(text):
    assert resolve_intent(text).intent is not Intent.TEAM_STATUS
