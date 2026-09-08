"""The two halves of ``scene.activity`` must speak the same language.

This file exists because they did not. Measured on 2026-09-08 against the real service:
a verified Blender run published ``metadata.state = "applied"`` — the database row's own
word — while the web build could only read
``creating | applying | rendering | inspecting | verified | mismatch | unavailable | failed``.
Five of the seven words the backend could send were unreadable there, including EVERY
successful one and every settled one, so the Cockpit's "3B Sahne" row could never say
"doğrulandı" and never settled; it would have sat in the making posture forever. The Unity
licence path published nothing at all, so the dim ``unavailable`` posture the spec promises
was unreachable even in principle.

Neither suite could see it. The web tests feed the web's own vocabulary; the API tests
assert that a publish happened. Each half was self-consistent and they had never been
driven against each other — the same shape as the two defects the M25 security review
found, one milestone later and one layer up.

So the guard reads the OTHER side's file. A future edit to either list fails here rather
than drifting silently into a Cockpit that cannot read a run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.creative3d.models import SCENE_STATES, wire_step
from app.creative3d.service import SCENE_STEP_TR
from app.uistate.contract import (
    SCENE_ACTIVITY_STEPS,
    SCENE_STEP_MISMATCH,
    SCENE_STEP_UNAVAILABLE,
    SCENE_STEP_UNVERIFIED,
    SCENE_STEP_VERIFIED,
)

#: Found by walking up rather than by a fixed depth. A counted `parents[n]` is what put a
#: container's root directory where a repository root belonged in M24 (ADR-0087 addendum 1);
#: here the cost of being wrong would be a guard that reads nothing and passes.
_RELATIVE = Path("apps") / "web" / "app" / "lib" / "uistate" / "contract.ts"


def _find_web_contract() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _RELATIVE
        if candidate.is_file():
            return candidate
    return Path(__file__).resolve().parents[-1] / _RELATIVE


_WEB_CONTRACT = _find_web_contract()


def _web_scene_run_states() -> list[str]:
    """The words the web build can actually read, taken from its own source."""
    text = _WEB_CONTRACT.read_text(encoding="utf-8")
    match = re.search(r"export const SCENE_RUN_STATES = \[(.*?)\] as const;", text, re.S)
    assert match is not None, "SCENE_RUN_STATES not found in the web contract"
    return re.findall(r'"([a-z_]+)"', match.group(1))


def test_the_web_contract_file_is_where_this_test_thinks_it_is() -> None:
    """A guard that reads another file is worthless if the path goes stale: a missing file
    must fail here rather than quietly stop comparing anything (the M24 lesson — never a
    gate that passes over an empty comparison)."""
    assert _WEB_CONTRACT.is_file(), f"the web contract is not at {_WEB_CONTRACT}"
    assert _web_scene_run_states(), "no scene run states were read from the web contract"


def test_both_halves_carry_the_same_scene_activity_vocabulary_in_the_same_order() -> None:
    """The one contract, read from both sides. Order matters too: each list is documented
    as the loop's own order, and a reader that follows one and not the other would be
    telling the owner the steps in a sequence the other half does not mean."""
    assert list(SCENE_ACTIVITY_STEPS) == _web_scene_run_states()


def test_every_step_the_service_can_publish_is_one_the_web_can_read() -> None:
    """The direction that actually broke: the backend sent words the web could not read.
    Stated separately from the equality above so a failure says which way the drift went."""
    unreadable = sorted(set(SCENE_ACTIVITY_STEPS) - set(_web_scene_run_states()))
    assert not unreadable, (
        f"the service can publish {unreadable}, which the web build cannot read — "
        "the Cockpit would draw those runs as still being made"
    )


def test_the_web_has_no_step_the_service_never_sends() -> None:
    """And the other direction, which is how the drift started: the web grew a vocabulary
    for a publisher that was speaking a different one, and its own tests passed because
    they fed it its own words."""
    unsent = sorted(set(_web_scene_run_states()) - set(SCENE_ACTIVITY_STEPS))
    assert not unsent, f"the web draws {unsent}, which nothing publishes"


@pytest.mark.parametrize(
    "step",
    [SCENE_STEP_VERIFIED, SCENE_STEP_UNVERIFIED, SCENE_STEP_MISMATCH, SCENE_STEP_UNAVAILABLE],
)
def test_the_outcomes_are_four_distinct_words(step: str) -> None:
    """The three ways a run can end plus the tool that could not be driven. They are
    separate words on purpose: "verified" is a claim about a read-back, "unverified" says
    there was nothing to read back, "mismatch" says the read-back disagreed, and
    "unavailable" is a fact about a licence rather than a fault. Rounding any of them into
    another is the defect this whole family exists to refuse."""
    assert step in SCENE_ACTIVITY_STEPS
    assert len({SCENE_STEP_VERIFIED, SCENE_STEP_UNVERIFIED, SCENE_STEP_MISMATCH}) == 3


# ------------------------------------------------- the row the panel reads


@pytest.mark.parametrize("state", SCENE_STATES)
def test_every_database_state_maps_into_the_wire_vocabulary(state: str) -> None:
    """`/v1/scenes` sends the step, and the Cockpit panel reads it through the same closed
    list. It used to send the database's own word, so `rowState()` was null for every real
    scene: no "Render al" chip, no "Sahneyi oku" chip, no row ever marked verified. Every
    state a row can hold must therefore land on a word the panel knows."""
    assert wire_step(state, None) in SCENE_ACTIVITY_STEPS


def test_the_row_step_reads_the_comparison_the_row_kept() -> None:
    """A finished run is not `verified` because it finished. `applied` and `rendered` carry
    the comparison the row stored, and the three outcomes stay three."""
    assert wire_step("applied", {"ok": True}) == SCENE_STEP_VERIFIED
    assert wire_step("rendered", {"ok": True}) == SCENE_STEP_VERIFIED
    assert wire_step("applied", {"ok": False, "reason": "no_constraints"}) == SCENE_STEP_UNVERIFIED
    assert wire_step("applied", {"ok": False, "reason": None}) == SCENE_STEP_MISMATCH
    # And a row with NO comparison at all is never verified: a claim over nothing is the
    # M24 defect, and it does not get a back door here either.
    assert wire_step("applied", None) == SCENE_STEP_UNVERIFIED
    assert wire_step("rendered", None) == SCENE_STEP_UNVERIFIED
    assert wire_step("mismatch", None) == SCENE_STEP_MISMATCH
    assert wire_step("dependency_unavailable", None) == SCENE_STEP_UNAVAILABLE
    # A row that has not finished is a run in progress, never a settled word.
    assert wire_step("planned", None) not in (SCENE_STEP_VERIFIED, SCENE_STEP_MISMATCH)
    assert wire_step("scaffolded", None) not in (SCENE_STEP_VERIFIED, SCENE_STEP_MISMATCH)


def test_every_step_has_a_turkish_word_the_owner_can_hear() -> None:
    """`scene.status` speaks the step, so a step without a word would crash the answer —
    and before this vocabulary existed it spoke the database's English token in the middle
    of a Turkish sentence ("demo sahnesi applied durumunda efendim"), in a product whose
    first rule is Turkish first. The words match the ones the Cockpit draws, so the voice
    and the panel call one run by one name."""
    assert set(SCENE_STEP_TR) == set(SCENE_ACTIVITY_STEPS)
    # No English token leaks into a spoken sentence: every word differs from its step.
    assert all(word != step for step, word in SCENE_STEP_TR.items())
    assert len(set(SCENE_STEP_TR.values())) == len(SCENE_ACTIVITY_STEPS)
    assert SCENE_STEP_TR[SCENE_STEP_VERIFIED] == "doğrulandı"
    # And the one word that must never be reachable from anything but a matching read-back.
    assert [k for k, v in SCENE_STEP_TR.items() if v == "doğrulandı"] == [SCENE_STEP_VERIFIED]
