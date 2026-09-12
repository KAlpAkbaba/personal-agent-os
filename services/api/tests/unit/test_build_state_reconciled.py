"""B01 req 23: BUILD_STATE must not be able to contradict the qualification record.

On 2026-09-12 one file said three things at once - ``current_milestone`` claimed M28 was
done, the M28 block said "IN PROGRESS", and ``last_completed_milestone`` still named M27.
Nothing noticed, because nothing could: the fields were written by hand and checked by
reading. These tests hold the derived fields to the record, and hold the DIRECTION of the
rule: a block may claim less than its stage proves, never more.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOL = REPO_ROOT / "scripts" / "core" / "reconcile_build_state.py"


def _load():
    spec = importlib.util.spec_from_file_location("reconcile_build_state", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reconcile = _load()


def test_the_committed_build_state_agrees_with_the_qualification_record() -> None:
    """The gate itself. A hand edit that contradicts the record fails here."""
    assert reconcile.main([]) == 0


def test_every_derived_milestone_is_backed_by_rows_that_exist() -> None:
    state = json.loads(reconcile.BUILD_STATE.read_text("utf-8"))
    rows = reconcile.stage_rows(reconcile.QUALIFICATION.read_text("utf-8"))
    milestones = state["milestones"]
    assert milestones, "no milestone was derived at all"
    for name, entry in milestones.items():
        stage_rows = rows[entry["stage"]]
        assert entry["rows"] == len(stage_rows), name
        assert entry["proven"] + entry["owner_or_provider"] + entry["open"] == entry["rows"], name
        assert entry["state"] in (
            reconcile.STATE_CLOSED,
            reconcile.STATE_ENGINEERING_CLOSED,
            reconcile.STATE_IN_PROGRESS,
        ), name


def test_last_completed_is_the_highest_milestone_the_record_closes() -> None:
    state = json.loads(reconcile.BUILD_STATE.read_text("utf-8"))
    closed = {
        name: entry
        for name, entry in state["milestones"].items()
        if entry["state"] in reconcile.CLOSED_STATES
    }
    assert state["last_completed_milestone"] in closed
    highest = max(closed.items(), key=lambda kv: kv[1]["stage"])[0]
    assert state["last_completed_milestone"] == highest


@pytest.mark.parametrize(
    ("marks", "expected"),
    [
        (["PROVEN_REAL", "PROVEN_AUTOMATED"], reconcile.STATE_CLOSED),
        (["PROVEN_REAL", "PROVIDER_UNAVAILABLE"], reconcile.STATE_ENGINEERING_CLOSED),
        (["PROVEN_REAL", "READY_FOR_OWNER"], reconcile.STATE_ENGINEERING_CLOSED),
        (["PROVEN_REAL", "NOT_YET_PROVEN"], reconcile.STATE_IN_PROGRESS),
        # A row nobody classified is not a row that passed.
        (["PROVEN_REAL", None], reconcile.STATE_IN_PROGRESS),
    ],
)
def test_a_stage_is_only_as_finished_as_its_weakest_row(marks, expected) -> None:
    assert reconcile.derive_state(marks) == expected


def test_a_block_claiming_more_than_its_stage_proves_is_reported() -> None:
    qualification = "| 99.1 | a claim | `NOT_YET_PROVEN` | nothing yet |\n"
    state = {
        "current_milestone": "M99 DONE",
        "m99_invented": {"status": "M99 CLOSED (QUALIFICATION Stage 99 rows 99.1-99.1)"},
    }
    derived, problems = reconcile.derive(state, qualification)
    assert derived["milestones"]["M99_INVENTED"]["state"] == reconcile.STATE_IN_PROGRESS
    assert any("claims 'CLOSED'" in p and "99.1" in p for p in problems), problems


def test_a_block_claiming_less_than_its_stage_proves_is_left_alone() -> None:
    """M19/M20/M21 each say "ENGINEERING CLOSED" for work outside their own stage. Being
    conservative is not a defect, and a checker that nags about it gets switched off."""
    qualification = "| 99.1 | a claim | `PROVEN_REAL` | a real run |\n"
    state = {
        "current_milestone": "M99 in progress",
        "m99_invented": {"status": "M99 ENGINEERING CLOSED (QUALIFICATION Stage 99 rows 99.1)"},
    }
    derived, problems = reconcile.derive(state, qualification)
    assert derived["milestones"]["M99_INVENTED"]["state"] == reconcile.STATE_CLOSED
    assert problems == []


def test_the_state_is_read_from_the_front_of_the_status_line() -> None:
    """"M28 IN PROGRESS (... nothing here is CLOSED yet ...)" is IN PROGRESS. A substring
    search over the whole sentence read it as CLOSED, which is how the contradiction hid."""
    assert reconcile._prose_state("M28 IN PROGRESS (later this line says CLOSED)") == "IN PROGRESS"
    assert reconcile._prose_state("M27 CLOSED 2026-09-09") == "CLOSED"
    assert reconcile._prose_state("M19 ENGINEERING CLOSED") == "ENGINEERING CLOSED"
    assert reconcile._prose_state("M28_DONE_2026_09_12") is None


def test_the_narrative_is_never_rewritten() -> None:
    """Only the derived fields are the tool's to write."""
    state = json.loads(reconcile.BUILD_STATE.read_text("utf-8"))
    derived, _ = reconcile.derive(state, reconcile.QUALIFICATION.read_text("utf-8"))
    assert set(derived) <= {"milestones", "last_completed_milestone"}
    assert "status" not in derived
    for key in state:
        if reconcile._BLOCK.match(key) and isinstance(state[key], dict):
            assert key not in derived, f"{key} is narrative; the tool must not own it"


def test_the_file_keeps_its_line_endings(tmp_path: Path, monkeypatch) -> None:
    """A one-field reconciliation must not arrive as a whole-file line-ending change - which
    is exactly what the first version of this tool did on Windows."""
    target = tmp_path / "BUILD_STATE.json"
    target.write_bytes(b'{\n  "schema_version": 1,\n  "current_milestone": "M99"\n}\n')
    qual = tmp_path / "QUALIFICATION.md"
    qual.write_text("| 99.1 | a claim | `PROVEN_REAL` | a real run |\n", encoding="utf-8")
    monkeypatch.setattr(reconcile, "BUILD_STATE", target)
    monkeypatch.setattr(reconcile, "QUALIFICATION", qual)
    reconcile.main(["--write"])
    assert b"\r\n" not in target.read_bytes()
