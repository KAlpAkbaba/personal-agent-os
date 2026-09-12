#!/usr/bin/env python3
"""Reconcile ``state/BUILD_STATE.json`` with the qualification record (B01 requirement 23).

BUILD_STATE has always been written by hand, and on 2026-09-12 the audit found it saying
three things at once: ``current_milestone`` claimed "M28_DONE", the ``m28_native_app_factory``
block said "M28 IN PROGRESS", and ``last_completed_milestone`` still named M27. Three readers
of one file got three answers, and none of them was checkable.

The fix is not to write the file more carefully. It is to stop writing the checkable parts by
hand: every milestone block already names the QUALIFICATION stage that proves it, and every
row in that stage already carries a proof mark. That is enough to DERIVE each milestone's
state, and therefore the highest one that is finished.

What is derived (and rewritten by ``--write``):

``milestones``
    one entry per milestone block that names a stage: the stage, its row counts by class,
    and the state those counts imply.
``last_completed_milestone``
    the highest-numbered milestone whose derived state is a closed one.

What is NOT touched: every narrative field. ``status``, the per-milestone prose, the notes -
they are a human record of how the work went, and a generator would flatten them into
something true but worthless. The narrative is only CHECKED, never rewritten: a block whose
prose contradicts its own stage is reported, and a person decides what the prose should say.

    python scripts/core/reconcile_build_state.py            # check; exit 1 on disagreement
    python scripts/core/reconcile_build_state.py --write    # apply the derived fields
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_STATE = REPO_ROOT / "state" / "BUILD_STATE.json"
QUALIFICATION = REPO_ROOT / "docs" / "QUALIFICATION.md"

#: A row that PROVES something.
PROVEN_MARKS = ("PROVEN_REAL", "PROVEN_AUTOMATED", "PROVEN_PROXY")
#: A row that engineering has finished with and a person or a provider now owns. These do not
#: keep a milestone open - the repo's own vocabulary for that state is "ENGINEERING CLOSED".
BOUNDARY_MARKS = (
    "READY_FOR_OWNER_APPROVAL",
    "READY_FOR_OWNER_TEST",
    "READY_FOR_OWNER",
    "PROVIDER_UNAVAILABLE",
    "BLOCKED",
)
#: A row still owed by the engineering.
OPEN_MARKS = ("NOT_YET_PROVEN",)

STATE_CLOSED = "CLOSED"
STATE_ENGINEERING_CLOSED = "ENGINEERING CLOSED"
STATE_IN_PROGRESS = "IN PROGRESS"
CLOSED_STATES = (STATE_CLOSED, STATE_ENGINEERING_CLOSED)

#: How strong a claim each state is. A block may say less than its stage proves; saying more
#: is what this tool exists to catch.
_RANK = {STATE_IN_PROGRESS: 0, STATE_ENGINEERING_CLOSED: 1, STATE_CLOSED: 2}

#: The state a milestone block declares, read from the FRONT of its status line. A substring
#: search over the whole line finds the word "closed" in any sentence that happens to use it -
#: which is how "M28 IN PROGRESS (...)" first read as CLOSED.
_PROSE_STATE = re.compile(
    r"^M[\d._]+\s+(?:\*\*)?(ENGINEERING CLOSED|CLOSED|DONE|IN PROGRESS)\b", re.I
)

_ROW = re.compile(r"^\|\s*`?(\d+)\.(\d+[a-z]?)`?\s*\|")
_STAGE = re.compile(r"\bStage\s+(\d+)\b")
_BLOCK = re.compile(r"^m(\d+)(?:_(\d+))?_")


def stage_rows(text: str) -> dict[int, list[tuple[str, str | None]]]:
    """Every qualification row, by stage, with the proof mark it carries (or ``None``)."""
    rows: dict[int, list[tuple[str, str | None]]] = {}
    for line in text.splitlines():
        head = _ROW.match(line)
        if head is None:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        mark = next(
            (m for m in (*PROVEN_MARKS, *BOUNDARY_MARKS, *OPEN_MARKS) if m in cells[2]),
            None,
        )
        rows.setdefault(int(head.group(1)), []).append((f"{head.group(1)}.{head.group(2)}", mark))
    return rows


def derive_state(marks: list[str | None]) -> str:
    """The state a stage's marks imply. An UNMARKED row counts as open: a row nobody
    classified is not a row that passed."""
    if any(m is None or m in OPEN_MARKS for m in marks):
        return STATE_IN_PROGRESS
    if any(m in BOUNDARY_MARKS for m in marks):
        return STATE_ENGINEERING_CLOSED
    return STATE_CLOSED


def _prose_state(status: str) -> str | None:
    """The state a block's status line declares, or ``None`` when it declares none."""
    match = _PROSE_STATE.match(status.strip())
    if match is None:
        return None
    word = match.group(1).upper()
    return STATE_CLOSED if word == "DONE" else word


def milestone_name(key: str) -> str:
    """``m28_native_app_factory`` -> ``M28_NATIVE_APP_FACTORY``."""
    return key.upper()


def derive(state: dict[str, Any], qualification: str) -> tuple[dict[str, Any], list[str]]:
    """The derived facts, and every disagreement with what the file currently says."""
    rows = stage_rows(qualification)
    milestones: dict[str, Any] = {}
    problems: list[str] = []

    for key, block in state.items():
        if not isinstance(block, dict) or _BLOCK.match(key) is None:
            continue
        prose = str(block.get("status", ""))
        stage_match = _STAGE.search(prose)
        if stage_match is None:
            # Older blocks predate the "QUALIFICATION Stage N" convention. Nothing is guessed
            # for them - they are simply not derivable, and the file says so by omission.
            continue
        stage = int(stage_match.group(1))
        marks = [m for _, m in rows.get(stage, [])]
        if not marks:
            problems.append(f"{key}: names Stage {stage}, which has no rows in QUALIFICATION.md")
            continue
        derived = derive_state(marks)
        milestones[milestone_name(key)] = {
            "stage": stage,
            "rows": len(marks),
            "proven": sum(1 for m in marks if m in PROVEN_MARKS),
            "owner_or_provider": sum(1 for m in marks if m in BOUNDARY_MARKS),
            "open": sum(1 for m in marks if m is None or m in OPEN_MARKS),
            "state": derived,
            "open_rows": [r for r, m in rows.get(stage, []) if m is None or m in OPEN_MARKS],
        }
        # The prose is checked, never rewritten - and only in ONE direction. A block that
        # claims LESS than its stage proves is being conservative (M19/M20/M21 each say
        # "ENGINEERING CLOSED" because of work outside their stage); a block that claims MORE
        # is the defect this check exists for.
        prose_state = _prose_state(prose)
        if prose_state is not None and _RANK[prose_state] > _RANK[derived]:
            entry = milestones[milestone_name(key)]
            problems.append(
                f"{key}: its prose claims {prose_state!r} but Stage {stage} only supports "
                f"{derived!r} ({entry['proven']}/{len(marks)} proven, {entry['open']} open"
                + (f", open rows {entry['open_rows']}" if entry["open_rows"] else "")
                + ")"
            )

    closed = [
        (int(_BLOCK.match(name.lower()).group(1)), name)  # type: ignore[union-attr]
        for name, m in milestones.items()
        if m["state"] in CLOSED_STATES
    ]
    last_completed = max(closed)[1] if closed else None

    derived_fields: dict[str, Any] = {"milestones": milestones}
    if last_completed is not None:
        derived_fields["last_completed_milestone"] = last_completed
        current = str(state.get("current_milestone", ""))
        highest = max(milestones.items(), key=lambda kv: kv[1]["stage"])
        number = _BLOCK.match(highest[0].lower()).group(1)  # type: ignore[union-attr]
        if f"M{number}" not in current:
            problems.append(
                f"current_milestone {current!r} does not name M{number}, the highest milestone "
                f"the qualification record knows about"
            )
        elif highest[1]["state"] not in CLOSED_STATES and re.search(
            r"\b(DONE|CLOSED)\b", current
        ):
            problems.append(
                f"current_milestone {current!r} claims M{number} is finished, but Stage "
                f"{highest[1]['stage']} still has {highest[1]['open']} open row(s): "
                f"{highest[1]['open_rows']}"
            )

    return derived_fields, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply the derived fields")
    args = parser.parse_args(argv)

    state = json.loads(BUILD_STATE.read_text("utf-8"))
    derived, problems = derive(state, QUALIFICATION.read_text("utf-8"))

    stale = {k: v for k, v in derived.items() if state.get(k) != v}
    if args.write:
        if not stale and not problems:
            print("BUILD_STATE already agrees with the qualification record.")
            return 0
        state.update(derived)
        state["last_updated"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        # write_bytes, not write_text: on Windows the text writer translates every "\n" into
        # "\r\n" and a one-field reconciliation arrives as a whole-file line-ending change.
        rendered = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
        BUILD_STATE.write_bytes(rendered.encode("utf-8"))
        print(f"BUILD_STATE reconciled: {', '.join(sorted(stale)) or 'no field changed'}")
        for problem in problems:
            print(f"  STILL DISAGREES (prose, for a person to fix): {problem}")
        return 1 if problems else 0

    for field in sorted(stale):
        print(f"DERIVED {field} disagrees with the file")
    for problem in problems:
        print(f"DISAGREES: {problem}")
    if stale or problems:
        print("\nrun: python scripts/core/reconcile_build_state.py --write")
        return 1
    print("BUILD_STATE agrees with the qualification record.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
