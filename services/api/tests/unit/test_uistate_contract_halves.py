"""Every UI-state family's two halves must speak the same language.

The Cloud Core publishes ``metadata.state`` for each family; the web build declares a
CLOSED list of the words it can read and draws a posture from each. A word outside that
list is one the build cannot read, and every family's reader falls back the same way — to
"something is happening" — so a drift does not show as an error anywhere. It shows as a
finished run drawn forever as one still running.

Both suites stay green through it, because each side's tests feed that side its own
vocabulary. That is how it went unnoticed twice:

* M25 (2026-09-08): `scene.activity` published the database row's word, so five of the
  seven tokens were unreadable — every successful run and every settled one. The Cockpit's
  "3B Sahne" row could never say "doğrulandı", showed no controls at all, and the Core drew
  every finished scene as one still being made.
* M24, found the same day by asking the same question of the earlier families: a
  `GenesisRun` reaches `cancelled` whenever the owner says "Vazgeç", `_transition`
  publishes it, and the web's list had no such word — so a run the owner had given up on
  was drawn as one still being built. A web test even asserted that "cancelled" was NOT a
  genesis state: a fact written to hold a belief the publisher had already contradicted.

So this file reads the OTHER side's source. It is deliberately dumb — a regex over the
TypeScript — because anything cleverer would be a second implementation of the thing it is
checking. Add a family here the moment one gains a state vocabulary.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.appfactory.models import APP_PROJECT_STATES
from app.creative3d.models import SCENE_STATES, wire_step
from app.genesis.models import GENESIS_STATES
from app.uistate.contract import EXECUTIVE_RUN_STATES, SCENE_ACTIVITY_STEPS

#: Found by walking up rather than by a counted `parents[n]`: a wrong depth would make this
#: guard read nothing and pass, which is worse than not having it (the M24 `_REPO_ROOT`
#: lesson, ADR-0087 addendum 1).
_RELATIVE = Path("apps") / "web" / "app" / "lib" / "uistate" / "contract.ts"


def _find_web_contract() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _RELATIVE
        if candidate.is_file():
            return candidate
    return Path(__file__).resolve().parents[-1] / _RELATIVE


_WEB_CONTRACT = _find_web_contract()


def _web_list(name: str) -> list[str]:
    """One `export const NAME = [...] as const;` list, read as words."""
    text = _WEB_CONTRACT.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", text, re.S)
    assert match is not None, f"{name} not found in {_WEB_CONTRACT}"
    words = re.findall(r'"([a-z_.]+)"', match.group(1))
    assert words, f"{name} was found but held no words — the guard would compare nothing"
    return words


#: (family, what the Cloud Core can publish, the web's list name). The 3D family's
#: publisher sends a STEP rather than a row state, so its own vocabulary is the one to
#: compare; `test_scene_activity_vocabulary.py` additionally holds every scene ROW state to
#: a step through ``wire_step``.
FAMILIES: list[tuple[str, set[str], str]] = [
    ("app_projects", set(APP_PROJECT_STATES), "APP_PROJECT_STATES"),
    ("genesis_runs", set(GENESIS_STATES), "GENESIS_RUN_STATES"),
    ("scene_activity", set(SCENE_ACTIVITY_STEPS), "SCENE_RUN_STATES"),
    # M26 (ADR-0089): the executive family's row states ARE the wire words (one
    # vocabulary, app.uistate.contract's own comment on EXECUTIVE_STEP_* explains
    # why there is no second, richer set to translate down from here). Expected to
    # FAIL until the web track lands its own EXECUTIVE_RUN_STATES list — that is the
    # correct, catchable state this guard exists to produce (module docstring).
    ("executive_run", set(EXECUTIVE_RUN_STATES), "EXECUTIVE_RUN_STATES"),
]


def test_the_web_contract_is_where_this_guard_thinks_it_is() -> None:
    assert _WEB_CONTRACT.is_file(), f"the web contract is not at {_WEB_CONTRACT}"


@pytest.mark.parametrize(("family", "backend", "web_name"), FAMILIES, ids=lambda v: str(v)[:24])
def test_every_state_the_core_publishes_is_one_the_web_can_read(
    family: str, backend: set[str], web_name: str
) -> None:
    """The direction that broke twice. A word the build cannot read is not an error there:
    it falls through to "still working", so the owner is shown a run that never ends."""
    unreadable = sorted(backend - set(_web_list(web_name)))
    assert not unreadable, (
        f"{family}: the Cloud Core can publish {unreadable}, which the web build cannot "
        f"read — those runs would be drawn as still in progress forever"
    )


@pytest.mark.parametrize(("family", "backend", "web_name"), FAMILIES, ids=lambda v: str(v)[:24])
def test_the_web_draws_no_state_the_core_never_sends(
    family: str, backend: set[str], web_name: str
) -> None:
    """And the other direction: a posture nothing can reach is dead code that reads like a
    promise. M25's `mismatch` posture sat unreachable from the day it was written."""
    unsent = sorted(set(_web_list(web_name)) - backend)
    assert not unsent, f"{family}: the web draws {unsent}, which nothing publishes"


def test_the_3d_family_maps_its_row_states_into_that_same_vocabulary() -> None:
    """The 3D family publishes a step, not a row state, and `/v1/scenes` sends the step
    too — so every state a row can hold has to land on a word the panel knows. Sending the
    row's own word left the panel without controls on every real scene."""
    for state in SCENE_STATES:
        assert wire_step(state, None) in SCENE_ACTIVITY_STEPS, state
