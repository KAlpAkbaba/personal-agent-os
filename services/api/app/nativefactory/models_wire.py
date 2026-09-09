"""What ``native.build`` says for a ``native_builds`` row (M28 spec §4, §6, ADR-0095).

A module of its own rather than a function in :mod:`app.nativefactory.models`, because the
two halves it joins are owned by different files: the row's states are the table's, the
wire words are ``app.uistate.contract``'s, and the MAPPING between them is the thing that
has drifted in this repository three milestones running. Keeping it apart makes it
importable by the guard that reads both sides without dragging the ORM into that guard.

The shape is M27's ``app.creative.models.wire_step``: an EXPLICIT, TOTAL dict, and a
lookup that RAISES rather than guessing. Here the two vocabularies happen to be the same
eleven words — the lifecycle the row records is exactly what the owner is watching, so
there is no coarser row vocabulary to translate down from, as there is for the creative
and 3D families — and that is precisely why the identity function would have been the
wrong thing to write. An identity mapping silently absorbs a twelfth row state the day
someone adds one, publishes a word the web build cannot read, and the owner is shown a
finished build as one still running with both suites green. That is not a hypothetical:
it is what M25 shipped (`scene.activity` published the row's own word; five of seven
tokens were unreadable) and what M24 shipped before it. An explicit dict fails at the
first call instead, and ``test_uistate_contract_halves.py`` fails before that.
"""

from __future__ import annotations

from typing import Final

from app.nativefactory.models import (
    STATE_BUILDING,
    STATE_FAILED,
    STATE_GENERATING,
    STATE_MISMATCH,
    STATE_PACKAGING,
    STATE_PLANNED,
    STATE_TESTING,
    STATE_UNAVAILABLE,
    STATE_UNVERIFIED,
    STATE_VALIDATING,
    STATE_VERIFIED,
)
from app.uistate.contract import (
    NATIVE_STEP_BUILDING,
    NATIVE_STEP_FAILED,
    NATIVE_STEP_GENERATING,
    NATIVE_STEP_MISMATCH,
    NATIVE_STEP_PACKAGING,
    NATIVE_STEP_PLANNED,
    NATIVE_STEP_TESTING,
    NATIVE_STEP_UNAVAILABLE,
    NATIVE_STEP_UNVERIFIED,
    NATIVE_STEP_VALIDATING,
    NATIVE_STEP_VERIFIED,
)

#: Every row state, mapped onto the word the CHANNEL says. Total by construction and held
#: total by the guard: a row state with no entry here is a contract change someone made
#: without finishing it.
_WIRE_STEP: Final[dict[str, str]] = {
    STATE_PLANNED: NATIVE_STEP_PLANNED,
    STATE_GENERATING: NATIVE_STEP_GENERATING,
    STATE_BUILDING: NATIVE_STEP_BUILDING,
    STATE_TESTING: NATIVE_STEP_TESTING,
    STATE_PACKAGING: NATIVE_STEP_PACKAGING,
    STATE_VALIDATING: NATIVE_STEP_VALIDATING,
    STATE_VERIFIED: NATIVE_STEP_VERIFIED,
    STATE_UNVERIFIED: NATIVE_STEP_UNVERIFIED,
    STATE_MISMATCH: NATIVE_STEP_MISMATCH,
    STATE_UNAVAILABLE: NATIVE_STEP_UNAVAILABLE,
    STATE_FAILED: NATIVE_STEP_FAILED,
}


def wire_step(state: str) -> str:
    """The word ``native.build`` says for a row in ``state``.

    Raises rather than guessing. A silent fallback here would publish a word the web build
    cannot read, and an unreadable word does not show up as an error anywhere — it shows
    up as a finished build drawn forever as one still being made.
    """
    try:
        return _WIRE_STEP[state]
    except KeyError:
        raise ValueError(f"native build row state {state!r} has no wire step") from None


__all__ = ["wire_step"]
