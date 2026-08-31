"""Unit tests: narration command state machine.

Covers the acceptance-critical behaviours:
- "dur" always wins (from every state, never errors);
- "oku / devam / tekrar" work;
- "bu ne demek?" -> explanation mode -> return to the EXACT saved cursor;
- jump commands (sonraki bölüm / N. maddeye geç);
- Turkish utterance parsing.
"""

import pytest

from app.narration import commands
from app.narration.commands import Command, State
from app.narration.engine import Cursor, build_plan

BODY = """# Giriş

Birinci cümle. İkinci cümle. Üçüncü cümle.

# İkinci Bölüm

Dördüncü cümle. Beşinci cümle.
"""


@pytest.fixture()
def plan():
    return build_plan(BODY, artifact_id="a1", version=1)


# --------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("oku", Command.OKU),
        ("okumaya başla", Command.OKU),
        ("dur", Command.DUR),
        ("devam", Command.DEVAM),
        ("devam et", Command.DEVAM),
        ("burayı tekrar oku", Command.TEKRAR),
        ("bu ne demek?", Command.ACIKLA),
        ("sonraki bölüm", Command.SONRAKI_BOLUM),
    ],
)
def test_parse_utterance(utterance: str, expected: Command) -> None:
    parsed = commands.parse_utterance(utterance)
    assert parsed is not None
    assert parsed.command == expected


def test_parse_maddeye_gec_target() -> None:
    assert commands.parse_utterance("ikinci maddeye geç").target_index == 2
    assert commands.parse_utterance("3. maddeye geç").target_index == 3


def test_parse_dur_wins_even_when_other_words_present() -> None:
    # "durdur" contains the stop token and must parse as DUR, not OKU.
    assert commands.parse_utterance("okumayı durdur").command == Command.DUR


def test_parse_speed() -> None:
    assert commands.parse_utterance("hızı 1,5 yap").command == Command.HIZ
    assert commands.parse_utterance("daha yavaş").command == Command.HIZ


# --------------------------------------------------------------- transitions


def test_oku_starts_at_first_chunk(plan) -> None:
    state = commands.NarrationState()
    res = commands.apply(state, commands.ParsedCommand(Command.OKU), plan)
    assert res.state.state == State.READING
    assert res.state.cursor == plan.chunks[0].cursor


def test_dur_always_wins_from_every_state(plan) -> None:
    cursor = Cursor("s2", "p2", 1)
    for start_state in State:
        state = commands.NarrationState(state=start_state, cursor=cursor)
        res = commands.apply(state, commands.ParsedCommand(Command.DUR), plan)
        assert res.ok is True
        assert res.state.state == State.PAUSED
        # cursor is preserved exactly on stop
        assert res.state.cursor == cursor


def test_devam_resumes_from_saved_cursor(plan) -> None:
    cursor = Cursor("s2", "p2", 2)
    state = commands.NarrationState(state=State.PAUSED, cursor=cursor)
    res = commands.apply(state, commands.ParsedCommand(Command.DEVAM), plan)
    assert res.state.state == State.READING
    assert res.state.cursor == cursor


def test_explain_then_return_to_exact_cursor(plan) -> None:
    cursor = Cursor("s2", "p2", 1)
    reading = commands.NarrationState(state=State.READING, cursor=cursor)

    # "bu ne demek?" enters EXPLAINING and saves the EXACT cursor.
    explaining = commands.apply(reading, commands.ParsedCommand(Command.ACIKLA), plan).state
    assert explaining.state == State.EXPLAINING
    assert explaining.saved_cursor == cursor
    assert explaining.cursor == cursor  # explanation does not advance position

    # explanation finished -> back to READING at the EXACT saved cursor.
    resumed = commands.apply(explaining, commands.ParsedCommand(Command.ACIKLA_BITTI), plan).state
    assert resumed.state == State.READING
    assert resumed.cursor == cursor
    assert resumed.saved_cursor is None


def test_dur_during_explanation_still_returns_to_saved_cursor(plan) -> None:
    cursor = Cursor("s2", "p2", 1)
    reading = commands.NarrationState(state=State.READING, cursor=cursor)
    explaining = commands.apply(reading, commands.ParsedCommand(Command.ACIKLA), plan).state
    # dur wins even mid-explanation; the saved cursor is preserved for devam.
    paused = commands.apply(explaining, commands.ParsedCommand(Command.DUR), plan).state
    assert paused.state == State.PAUSED
    resumed = commands.apply(paused, commands.ParsedCommand(Command.DEVAM), plan).state
    assert resumed.cursor == cursor


def test_tekrar_repeats_current_paragraph_from_its_start(plan) -> None:
    # Sitting on the 3rd sentence of paragraph p2.
    anchor = Cursor("s2", "p2", 0)
    state = commands.NarrationState(
        state=State.READING, cursor=Cursor("s2", "p2", 2), paragraph_anchor=anchor
    )
    res = commands.apply(state, commands.ParsedCommand(Command.TEKRAR), plan)
    assert res.state.cursor == anchor


def test_sonraki_bolum_jumps_to_next_section(plan) -> None:
    state = commands.NarrationState(state=State.READING, cursor=Cursor("s2", "p2", 0))
    res = commands.apply(state, commands.ParsedCommand(Command.SONRAKI_BOLUM), plan)
    assert res.state.cursor.section_id == "s3"


def test_maddeye_gec_jumps_to_nth_paragraph(plan) -> None:
    state = commands.NarrationState(state=State.READING, cursor=plan.chunks[0].cursor)
    res = commands.apply(
        state, commands.ParsedCommand(Command.MADDEYE_GEC, target_index=2), plan
    )
    # 2nd content paragraph is p2 (p1 is the "Giriş" heading).
    assert res.state.cursor == Cursor("s2", "p2", 0)


def test_speed_change_is_clamped(plan) -> None:
    state = commands.NarrationState()
    res = commands.apply(state, commands.ParsedCommand(Command.HIZ, speed=5.0), plan)
    assert res.state.speed == 3.0  # clamped to max
    res2 = commands.apply(state, commands.ParsedCommand(Command.HIZ, speed=0.1), plan)
    assert res2.state.speed == 0.5  # clamped to min


def test_jump_past_last_section_reports_gracefully(plan) -> None:
    state = commands.NarrationState(state=State.READING, cursor=Cursor("s3", "p4", 1))
    res = commands.apply(state, commands.ParsedCommand(Command.SONRAKI_BOLUM), plan)
    assert res.ok is False
    assert res.state.state == State.PAUSED
