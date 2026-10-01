"""A standing LANGUAGE preference is not a research read and not an answer level
(owner's trial 2026-09-30 20:11 UTC, MAIL, release aa35fcf3).

'Bundan sonra araştırma raporlarını her zaman Türkçe oku' resolved as RESEARCH_OPEN with
answer_level=detail; the model then called research.answer_mode and the durable register
became 'detail' although nothing in the sentence names a level.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import Intent, resolve_intent
from app.voice.realtime_sessions.tools import research_answer_mode

TRIAL = "Bundan sonra araştırma raporlarını her zaman Türkçe oku."
LANGUAGE_SENTENCES = [
    TRIAL,
    "Bundan sonra araştırma raporlarını her zaman İngilizce oku.",
    "Artık raporları hep Almanca anlat.",
]


@pytest.mark.parametrize("text", LANGUAGE_SENTENCES)
def test_a_standing_language_sentence_is_neither_a_read_nor_a_level(text: str) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is Intent.NONE, (text, resolved.intent)
    assert resolved.klass != "action", text
    assert resolved.answer_level is None, text
    assert resolved.exec_shape is None, text


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Bundan sonra raporları ayrıntılı oku.", Intent.DETAIL),
        ("Bundan sonra araştırmaları teknik oku.", Intent.TECHNICAL),
    ],
)
def test_a_standing_read_with_a_level_word_is_a_narration_control(
    text: str, intent: Intent
) -> None:
    """Decided (ADR): "...oku" is not the answer-mode phrase ("anlat/konuş/cevap"), so the
    level word routes the one-off narration control, never a durable register, and never a
    mission."""
    resolved = resolve_intent(text)
    assert resolved.intent is intent
    assert resolved.klass != "action"


@pytest.mark.parametrize(
    ("text", "intent", "level"),
    [
        ("Bundan sonra teknik anlat.", Intent.RESEARCH_ANSWER_MODE, "technical"),
        ("Teknik modu kapat.", Intent.RESEARCH_ANSWER_MODE, "executive"),
        ("Araştırmayı oku.", Intent.RESEARCH_OPEN, "detail"),
    ],
)
def test_the_neighbouring_sentences_keep_their_intent(
    text: str, intent: Intent, level: str
) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is intent
    assert resolved.answer_level == level


def test_answer_mode_refuses_a_turn_without_a_level_word(monkeypatch) -> None:
    """The owner's exact failure: the turn record carries NO level (the router no longer
    invents one), the model passes level=executive -> refuse, durable level untouched."""
    from app.research import focus as focus_module
    from app.voice.realtime_sessions import tools

    set_calls: list[str] = []
    monkeypatch.setattr(
        focus_module, "set_answer_level", lambda db, level, now=None: set_calls.append(level)
    )
    now = datetime(2026, 9, 30, 20, 11, tzinfo=UTC)
    for record in (
        {"intent": "none", "answer_level": None, "at": now.isoformat()},
        {"intent": "research_open", "answer_level": "detail", "at": now.isoformat()},
        None,
    ):
        monkeypatch.setattr(tools, "_turn_record", lambda ctx, record=record: record)
        ctx = type("C", (), {"db": object(), "now": now, "context": {}})()
        with pytest.raises(VoiceError) as caught:
            research_answer_mode(ctx, {"level": "executive"})  # type: ignore[arg-type]
        assert caught.value.error_class is VoiceErrorClass.VALIDATION_ERROR
    assert set_calls == []


def test_answer_mode_still_sets_the_level_the_router_resolved(monkeypatch) -> None:
    from app.research import focus as focus_module
    from app.voice.realtime_sessions import tools

    set_calls: list[str] = []
    monkeypatch.setattr(
        focus_module, "set_answer_level", lambda db, level, now=None: set_calls.append(level)
    )
    now = datetime(2026, 9, 30, 20, 11, tzinfo=UTC)
    record = {"intent": "research_answer_mode", "answer_level": "technical"}
    monkeypatch.setattr(tools, "_turn_record", lambda ctx: record)
    ctx = type("C", (), {"db": object(), "now": now, "context": {}})()
    out = research_answer_mode(ctx, {"level": "executive"})  # type: ignore[arg-type]
    assert out["level"] == "technical"
    assert set_calls == ["technical"]
