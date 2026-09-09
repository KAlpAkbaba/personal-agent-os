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
from app.calendar.models import PROPOSAL_STATES
from app.creative.models import CREATIVE_STATES
from app.creative.models import wire_step as creative_wire_step
from app.creative3d.models import SCENE_STATES, wire_step
from app.genesis.models import GENESIS_STATES
from app.mail.models import DRAFT_STATES
from app.nativefactory.models import NATIVE_BUILD_STATES
from app.nativefactory.models_wire import wire_step as native_wire_step
from app.nativefactory.spec import NATIVE_TARGETS
from app.uistate.contract import (
    CREATIVE_ACTIVITY_STEPS,
    EXECUTIVE_RUN_STATES,
    NATIVE_BUILD_STEPS,
    SCENE_ACTIVITY_STEPS,
    SUBSYSTEMS,
)

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


#: A word in one of the web's closed lists. DIGITS ARE PART OF IT: without them
#: ``creative3d`` - M25's subsystem, and the only word in any of these lists that carries
#: a number - was silently invisible to this guard, so a list containing it compared one
#: word fewer than it appeared to. Found at the M28 merge by adding the subsystem check
#: below, which reported ``creative3d`` as a word the web had never heard of while it was
#: sitting in the file. That is this guard's own recurring failure mode, one level down:
#: a comparison that quietly leaves something out is worse than no comparison, because it
#: reads as coverage.
_WORD = r'"([a-z0-9_.]+)"'


def _web_list(name: str) -> list[str]:
    """One `export const NAME = [...] as const;` list, read as words."""
    text = _WEB_CONTRACT.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", text, re.S)
    assert match is not None, f"{name} not found in {_WEB_CONTRACT}"
    words = re.findall(_WORD, match.group(1))
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
    # M27 (ADR-0093): the 3D family's shape again - the row rests in seven states, the
    # channel says twelve, and `app.creative.models.wire_step` maps every one of the
    # seven onto one of the twelve.
    ("creative_activity", set(CREATIVE_ACTIVITY_STEPS), "CREATIVE_RUN_STATES"),
    # M21, added at the M27 merge by the coverage check below - these two run
    # vocabularies had never been compared with anything since M21 shipped. They agree
    # today (checked before adding them); nothing was holding them there.
    ("mail_drafts", set(DRAFT_STATES), "MAIL_DRAFT_STATES"),
    ("calendar_proposals", set(PROPOSAL_STATES), "CALENDAR_PROPOSAL_STATES"),
    # M28 (ADR-0095): the executive family's shape - the eleven words a `native_builds`
    # row holds ARE the eleven the channel says, because the lifecycle the row records is
    # exactly what the owner watches. `app.nativefactory.models_wire.wire_step` is still an
    # explicit total mapping rather than the identity function, and the test below holds
    # every row state to it, so a twelfth row state fails here instead of reaching the web
    # as a word it cannot read.
    ("native_build", set(NATIVE_BUILD_STEPS), "NATIVE_BUILD_STATES"),
]


def _web_run_vocabularies() -> dict[str, list[str]]:
    """Every list in the web contract that is a RUN vocabulary rather than a list of
    channel NAMES, told apart by content: a channel name carries a dot
    (``"scene.activity"``), a run state does not (``"verified"``).

    Deliberately not a naming convention - `APP_PROJECT_STATES`, `MAIL_DRAFT_STATES` and
    `SCENE_RUN_STATES` are all run vocabularies under three different spellings, and a
    convention would have to be remembered by whoever adds the fourth.
    """
    text = _WEB_CONTRACT.read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for name, body in re.findall(
        r"export const ([A-Z][A-Z0-9_]*_STATES) = \[(.*?)\] as const;", text, re.S
    ):
        words = re.findall(_WORD, body)
        if words and not all("." in w for w in words):
            out[name] = words
    return out


def test_the_guard_knows_about_every_family_the_web_declares() -> None:
    """The blind spot in this guard, found at the M27 merge.

    `FAMILIES` is hand-maintained, so a family added on ONE side is invisible: the web
    landed `CREATIVE_RUN_STATES` and every test in this file still passed, because no row
    named it. A cross-file guard that silently compares nothing is worse than no guard -
    this file's own docstring says so about the M25 drift, and then the file had the same
    shape of hole.

    So the TypeScript is scanned for every `*_STATES` list it exports, and each must have a
    row here. Adding a family to the web without a Cloud Core vocabulary now fails LOUDLY,
    which is the whole point.
    """
    declared = set(_web_run_vocabularies())
    compared = {web_name for _family, _backend, web_name in FAMILIES}
    unwatched = sorted(declared - compared)
    assert not unwatched, (
        f"the web declares the run vocabular{'y' if len(unwatched) == 1 else 'ies'} "
        f"{unwatched}, which this guard never compares against anything - add a FAMILIES "
        f"row, or the two halves can drift with every suite green"
    )


def test_that_scan_would_notice_a_missing_row() -> None:
    """And the detector proves itself before it is trusted: the same discipline the M26
    news review's regression tests were held to."""
    found = _web_run_vocabularies()
    assert len(found) >= len(FAMILIES), (
        f"the scan found {len(found)} run vocabularies but there are {len(FAMILIES)} "
        f"families - its regex has gone stale against the web contract's own shape and is "
        f"now comparing less than it thinks"
    )
    # And it really is separating the two kinds, rather than letting everything through:
    # the channel-name lists must NOT be in the result.
    assert "UI_STATES" not in found
    assert "SCENE_STATES" not in found, "a channel-name list was read as a run vocabulary"


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


def test_the_creative_family_maps_its_row_states_into_that_same_vocabulary() -> None:
    """M27's row states are a smaller, coarser set than the words its channel says (the
    row rests; the channel narrates), so every one of them has to land on a word the web
    declares - the same check the 3D family needs, for the same reason."""
    for state in CREATIVE_STATES:
        assert creative_wire_step(state) in CREATIVE_ACTIVITY_STEPS, state


def test_both_halves_name_the_subsystems_the_same_way() -> None:
    """The third column this guard was missing, found while adding M28's family.

    ``UiStatePublisher`` REFUSES an event whose subsystem is not in ``SUBSYSTEMS``, and
    the web mirrors the list to label a published event in the owner's words. The two
    drifted at M23 and nobody noticed for five milestones: the Cloud Core publishes
    ``appfactory`` (``SUBSYSTEM_APPFACTORY``), the web's mirror and its label table both
    said ``apps``, and a web test asserted ``apps`` — so every ``app.factory`` row in the
    Defter and the durum akışı printed the raw token where the owner's word belongs, with
    both suites green. Exactly the shape this file's docstring describes for state words,
    one column over.

    A name here is only a subset check in one direction on purpose: the web may keep a
    label for a subsystem the API does not publish today (nothing breaks), but a subsystem
    the API DOES publish and the web has never heard of is a row the owner reads as a
    machine token.
    """
    unknown = sorted(set(SUBSYSTEMS) - set(_web_list("SUBSYSTEMS")))
    assert not unknown, (
        f"the Cloud Core publishes under {unknown}, which the web build does not know - "
        f"every event from those subsystems is labelled with its raw token"
    )


def test_the_native_targets_agree_across_the_two_halves() -> None:
    """M28's targets, both directions, for the reason `ios_project` exists as prose and
    not as a value: the spec's §2 lists six, `app.nativefactory.spec` defines five, and a
    web build that offered the sixth would be drawing progress towards an iPhone
    application this machine can never produce (ADR-0095 decision 3)."""
    web = set(_web_list("NATIVE_TARGETS"))
    assert set(NATIVE_TARGETS) == web, (
        f"targets differ: the Cloud Core has {sorted(set(NATIVE_TARGETS) - web)} the web "
        f"does not, and the web draws {sorted(web - set(NATIVE_TARGETS))} nothing sends"
    )
    assert "ios_project" not in web


def test_the_native_family_maps_its_row_states_into_that_same_vocabulary() -> None:
    """M28's row states and its wire words are the same eleven today. That is exactly the
    condition under which a family drifts unnoticed: nothing translates, so nothing looks
    like it could go wrong, and the day a twelfth row state is added the publisher sends a
    word the web build cannot read and draws the build as one still running.

    So every row state is put through the real mapping and has to land on a word the web
    declares - and the mapping RAISES on a state it has never been told about, which is
    what makes this test able to fail rather than absorb the change.
    """
    for state in NATIVE_BUILD_STATES:
        assert native_wire_step(state) in NATIVE_BUILD_STEPS, state


def test_the_native_mapping_refuses_a_state_nobody_taught_it() -> None:
    """And the refusal itself is proven, not assumed: a mapping that quietly returned the
    input would pass the test above for every future state as well, which is the vacuous
    guard this file's docstring is about."""
    with pytest.raises(ValueError, match="no wire step"):
        native_wire_step("signing")


def test_the_3d_family_maps_its_row_states_into_that_same_vocabulary() -> None:
    """The 3D family publishes a step, not a row state, and `/v1/scenes` sends the step
    too — so every state a row can hold has to land on a word the panel knows. Sending the
    row's own word left the panel without controls on every real scene."""
    for state in SCENE_STATES:
        assert wire_step(state, None) in SCENE_ACTIVITY_STEPS, state
