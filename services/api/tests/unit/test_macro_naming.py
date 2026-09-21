"""ADR-0196: a macro's name - how it is kept and how a sentence is recognised as one.

Pure tests for ``app.macros.naming`` and the recording state machine in
``app.macros.service`` (no database, no session). The relay flow is in
``test_voice_macros.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.macros import service as macros_service
from app.macros.naming import fold, match_stored_name, name_key, spoken_name
from app.voice.intents import normalize_transcript

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def _tokens(text: str) -> tuple[str, ...]:
    return normalize_transcript(text)[1]


# ------------------------------------------------------------------ name_key


@pytest.mark.parametrize(
    ("spoken", "key"),
    [
        ("Yeni Mail Sekmesi", "yeni mail sekmesi"),
        ("yeni mail sekmesi.", "yeni mail sekmesi"),
        ("YENİ MAİL SEKMESİ", "yeni mail sekmesi"),
        ("yeni maıl sekmesı", "yeni mail sekmesi"),
        ("Adı yeni mail sekmesi olsun", "yeni mail sekmesi"),
        ("yeni mail sekmesi hareketi", "yeni mail sekmesi"),
        ("Sağ tuş", "sag tus"),
        ("   ", ""),
        ("hareketi", ""),
    ],
)
def test_one_name_has_one_key(spoken: str, key: str) -> None:
    assert name_key(spoken) == key


@pytest.mark.parametrize("word", ["Dur.", "kes", "Sus!", "Yeter", "tamam dur", "Devam", "vazgeç"])
def test_a_stop_or_control_word_is_never_a_name(word: str) -> None:
    """Security review 2026-09-21: a recording answered with "dur" is a STOP, and a stored
    macro called "dur" would run in place of every later emergency stop."""
    assert name_key(word) == ""
    assert spoken_name(word) == ""
    assert match_stored_name(_tokens(word), (name_key(word) or word.lower().rstrip(".!"),)) is None


def test_a_secret_shaped_step_is_not_kept() -> None:
    ctx: dict = {}
    macros_service.begin_recording(ctx, now=NOW)
    assert not macros_service.capture_step(
        ctx,
        tool="operator.type",
        arguments={},
        turn={"text_to_type": "şifremi yaz 1234"},
        now=NOW,
    )
    state = macros_service.recording_state(ctx)
    assert state is not None and state["steps"] == [] and state["dropped_secret"] == 1
    assert not macros_service.is_recordable("research.start")


def test_a_key_is_bounded() -> None:
    long = " ".join(f"kelime{n}" for n in range(20))
    assert len(name_key(long).split()) == 8


def test_spoken_name_keeps_the_owners_casing_and_drops_the_frame() -> None:
    assert spoken_name("Adı Yeni Mail Sekmesi olsun.") == "Yeni Mail Sekmesi"
    assert spoken_name("Yeni mail sekmesi hareketi.") == "Yeni mail sekmesi"
    assert spoken_name("hareketi") == ""


def test_fold_reads_turkish_i_both_ways() -> None:
    assert fold("İSTANBUL") == "istanbul"
    assert fold("ISPARTA") == "isparta"


# -------------------------------------------------------------- match_stored_name


@pytest.mark.parametrize(
    "sentence",
    [
        "Yeni mail sekmesi aç.",
        "yeni mail sekmesini aç",
        "Yeni mail sekmesi.",
        "Yeni mail sekmesi hareketini yap.",
        "Yeni mail sekmesi hareketini çalıştır lütfen.",
        "Hadi yeni mail sekmesi.",
    ],
)
def test_a_sentence_that_is_the_name_plus_a_run_word_matches(sentence: str) -> None:
    assert match_stored_name(_tokens(sentence), ("yeni mail sekmesi",)) == "yeni mail sekmesi"


@pytest.mark.parametrize(
    "sentence",
    [
        # The name buried in a longer sentence is not a request to run it.
        "Yeni mail sekmesi açıp bir şey yaz.",
        "Yeni mail sekmesine git.",
        # "sağ tuşuna bas" must stay the arrow key even with a macro called "sağ tuş":
        # "tuşuna" is not "tuş" plus a listed ending.
        "sağ tuşuna bas",
        "Mail sekmesi aç.",
        "",
    ],
)
def test_anything_else_does_not_match(sentence: str) -> None:
    assert match_stored_name(_tokens(sentence), ("yeni mail sekmesi", "sag tus")) is None


def test_the_longest_name_wins() -> None:
    keys = ("yeni mail", "yeni mail sekmesi")
    assert match_stored_name(_tokens("yeni mail sekmesi aç"), keys) == "yeni mail sekmesi"
    assert match_stored_name(_tokens("yeni mail aç"), keys) == "yeni mail"


# ------------------------------------------------------------ recording state


def test_recording_keeps_the_calls_that_were_made_and_never_the_macro_words() -> None:
    ctx: dict = {}
    assert macros_service.recording_state(ctx) is None
    assert not macros_service.capture_step(
        ctx, tool="operator.key", arguments={}, turn={"key_press": "enter"}, now=NOW
    )
    macros_service.begin_recording(ctx, now=NOW)
    assert macros_service.is_recording(ctx)
    assert macros_service.capture_step(
        ctx,
        tool="operator.key",
        arguments={"count": 2},
        turn={"key_press": "enter", "at": "x", "turn": 3, "route_source": "rules"},
        now=NOW,
    )
    for tool in ("macro.record_end", "macro.run", "assistant.chat", "voice.intent"):
        assert not macros_service.capture_step(ctx, tool=tool, arguments={}, turn=None, now=NOW)
    steps = ctx[macros_service.RECORDING_KEY]["steps"]
    assert [s["tool"] for s in steps] == ["operator.key"]
    assert steps[0]["arguments"] == {"count": 2}
    # The route's own bookkeeping is not kept; what the tool reads is.
    assert steps[0]["turn"] == {"key_press": "enter"}


def test_the_recording_is_bounded_and_counts_what_it_dropped() -> None:
    ctx: dict = {}
    macros_service.begin_recording(ctx, now=NOW)
    for _ in range(macros_service.MAX_MACRO_STEPS + 3):
        macros_service.capture_step(ctx, tool="operator.key", arguments={}, turn={}, now=NOW)
    state = macros_service.recording_state(ctx)
    assert state is not None
    assert len(state["steps"]) == macros_service.MAX_MACRO_STEPS
    assert state["dropped"] == 3


def test_end_asks_for_a_name_only_when_something_was_kept() -> None:
    ctx: dict = {}
    assert macros_service.end_recording(ctx) is None
    macros_service.begin_recording(ctx, now=NOW)
    empty = macros_service.end_recording(ctx)
    assert empty is not None and empty["steps"] == []
    assert macros_service.recording_state(ctx) is None  # closed, nothing to name
    macros_service.begin_recording(ctx, now=NOW)
    macros_service.capture_step(ctx, tool="operator.key", arguments={}, turn={}, now=NOW)
    ended = macros_service.end_recording(ctx)
    assert ended is not None and ended["status"] == macros_service.STATUS_AWAITING_NAME
    assert macros_service.is_awaiting_name(ctx) and not macros_service.is_recording(ctx)
    # While a name is awaited nothing is captured (the recording is over).
    assert not macros_service.capture_step(ctx, tool="operator.key", arguments={}, turn={}, now=NOW)
    pending = macros_service.take_pending(ctx)
    assert pending is not None and len(pending) == 1
    assert macros_service.recording_state(ctx) is None


def test_cancel_drops_a_recording_or_a_pending_name() -> None:
    ctx: dict = {}
    assert macros_service.cancel_recording(ctx) is None
    macros_service.begin_recording(ctx, now=NOW)
    assert macros_service.cancel_recording(ctx) is not None
    assert macros_service.recording_state(ctx) is None
