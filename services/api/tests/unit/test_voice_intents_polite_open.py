"""The polite imperative ("açın") is an open verb, and a named machine survives it.

Owner's trial 2026-09-30 20:11 UTC: "Ofis bilgisayarımdan hesap makinesini aç" reached the
router as "Ofisü bilgisayarında hesap makinesini açın." and resolved to NONE, because
"açın"/"açınız" were in no open-verb table ("açar mısın" was).
"""

from __future__ import annotations

import pytest

from app.devices.aliases import extract_aliases
from app.voice.intents import Intent, resolve_intent
from app.voice.spoken_device import resolve_without_device_phrase


@pytest.mark.parametrize("verb", ["açın", "açınız", "acın", "aciniz"])
def test_polite_open_verb_opens_the_app(verb: str) -> None:
    resolved = resolve_intent(f"Hesap makinesini {verb}.")
    assert resolved.intent == Intent.APP_OPEN
    assert resolved.application == "calc"


def test_named_office_computer_with_polite_verb_binds_ofis() -> None:
    text = "Ofis bilgisayarında hesap makinesini açın."
    intent, _subject, named = resolve_without_device_phrase(text, resolve_intent)
    assert intent.intent == Intent.APP_OPEN
    assert intent.application == "calc"
    assert named == ("ofis",)
    assert extract_aliases(text) == ("ofis",)


@pytest.mark.parametrize("text", ["Neden açın?", "Kapıyı açın."])
def test_polite_verb_in_a_question_or_unknown_thing_is_not_an_open(text: str) -> None:
    assert resolve_intent(text).intent != Intent.APP_OPEN
