"""`creative.activity`: the channel is published, and every word it can say is readable.

`test_uistate_contract_halves.py` holds the two VOCABULARIES to each other. This file holds
the PUBLISHER to that vocabulary, which is a different question and the one M25 answered
too late: its two halves agreed on the words while the service sent the database row's own
instead, and the Cockpit could not draw a single successful scene.

There is a second gap this file exists to close, one level up from either. ADR-0094
decision 4 deferred publishing entirely — the Cloud Core emitted ledger rows and left the
bus to the web half. That was right while the halves were separate branches and wrong the
moment they merged: agreeing words that nobody ever says make every posture the panel can
draw unreachable, and no vocabulary guard notices, because both lists are still identical.
So the first test here asserts that the channel is published AT ALL.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.creative import service as creative_service
from app.creative.compare import WIRE_DEFECTS, Mismatch
from app.creative.models import CREATIVE_STATES, wire_step
from app.uistate.contract import CREATIVE_ACTIVITY_STEPS, SUBSYSTEMS, UI_STATES, UiState

_COMPARE = Path(creative_service.__file__).with_name("compare.py")


def test_the_channel_exists_in_the_contract() -> None:
    assert UiState.CREATIVE_ACTIVITY.value == "creative.activity"
    assert "creative.activity" in UI_STATES
    assert "creative" in SUBSYSTEMS


def test_the_service_actually_publishes_it() -> None:
    """The question no vocabulary guard asks: does anything SAY these words?

    Read from the source, because the property is that the publish exists on the run path
    at all — a mocked call would prove only that the test can call it."""
    text = Path(creative_service.__file__).read_text(encoding="utf-8")
    assert "publish_ui_state(" in text, "the creative service publishes nothing at all"
    assert "UiState.CREATIVE_ACTIVITY" in text
    # ...at more than one point, because a twelve-word vocabulary against a seven-state row
    # exists precisely so the owner sees the loop happen rather than only its verdict.
    assert text.count("self._publish(") >= 4, (
        "the loop publishes once or twice - the progress steps the web can draw "
        "(executing, comparing, correcting) are unreachable"
    )


def test_every_row_state_lands_on_a_word_the_channel_can_say() -> None:
    """The 3D family's lesson, applied before it costs anything: the row rests in seven
    states, the channel says twelve, and the mapping has to be total."""
    for state in CREATIVE_STATES:
        assert wire_step(state) in CREATIVE_ACTIVITY_STEPS, state


def test_an_unmapped_row_state_raises_rather_than_guessing() -> None:
    """A silent fallback here is how a new state becomes "failed" on the owner's screen."""
    with pytest.raises(ValueError, match="no wire step"):
        wire_step("a_state_nobody_declared")


# ------------------------------------------------------- the defect the channel names


def _fields_the_comparison_can_produce() -> set[str]:
    """Every `Mismatch` field name constructed anywhere in `compare.py`.

    Read from the source rather than listed here, so a new defect kind cannot be added
    without this test noticing — the same "read the other side" discipline the contract
    halves use, turned on a single file.
    """
    text = _COMPARE.read_text(encoding="utf-8")
    fields = set(re.findall(r'Mismatch\(\s*\n?\s*"[^"]*",\s*\n?\s*"([^"]+)"', text))
    fields |= set(re.findall(r'Mismatch\("[^"]*",\s*"([^"]+)"', text))
    assert fields, "the scan found no mismatch constructions - its regex has gone stale"
    return fields


def test_every_defect_the_comparison_can_produce_has_a_word_the_web_can_read() -> None:
    """`metadata.defect` is a closed four-word set on the wire. The comparison names
    defects far more precisely than that (which object, which field, expected, actual), and
    the precise version stays on the row and the receipt — but the coarse word has to
    exist for every one of them, or a real mismatch reaches the panel as nothing at all."""
    for field in sorted(_fields_the_comparison_can_produce()):
        mismatch = Mismatch("obj", field, "expected", "actual", "detail")
        assert mismatch.wire_defect() in WIRE_DEFECTS, field


def test_an_unmapped_field_raises_rather_than_defaulting() -> None:
    """No silent "colour drift" for a defect nobody classified."""
    with pytest.raises(ValueError, match="no wire defect"):
        Mismatch("obj", "a_field_nobody_mapped", None, None).wire_defect()


def _web_contract() -> Path:
    """Found by WALKING UP, never by a counted `parents[n]`.

    The first version of this test used `parents[3]`, which lands in `services/` rather
    than the repo root, so it skipped - and a skipped cross-file check is exactly the
    vacuous guard this file was written to prevent, in the file written to prevent it.
    `test_uistate_contract_halves.py` already learned this (its own comment cites the M24
    `_REPO_ROOT` lesson); the same walk is used here, and a missing file FAILS.
    """
    relative = Path("apps") / "web" / "app" / "lib" / "uistate" / "contract.ts"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / relative
        if candidate.is_file():
            return candidate
    raise AssertionError(f"the web contract was not found above {__file__}")


def test_the_four_wire_defects_are_the_ones_the_web_declares() -> None:
    """The web's own list, read from its source - the fourth vocabulary in this repo held
    to the other side rather than to a copy of itself."""
    text = _web_contract().read_text(encoding="utf-8")
    match = re.search(r"export const CREATIVE_DEFECTS = \[(.*?)\] as const;", text, re.S)
    assert match is not None, "the web declares no CREATIVE_DEFECTS list to compare against"
    web_words = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert web_words, "CREATIVE_DEFECTS was found but held no words - comparing nothing"
    assert web_words == set(WIRE_DEFECTS), (
        f"core says {sorted(set(WIRE_DEFECTS) - web_words)}, web says "
        f"{sorted(web_words - set(WIRE_DEFECTS))}"
    )
