"""The owner's trials, wired end to end (ADR-0258 addendum, task owner-trials-wiring).

The inspector writes a READY_FOR_OWNER claim as a fixed four-line block; ``trials.
parse_inspector_trials`` turns it into an ``owner_trials`` object the queue schema accepts and
skips a block that is not whole, without raising. The role file is READ here, so the role text
and the reader cannot drift. ``trials.released_state`` decides, for a task being marked
released, whether it waits for the owner's real-device proof - and writes nothing.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

from app.ledger import vocabulary
from app.team import store as team_store
from app.team import trials

REPO_ROOT = Path(__file__).resolve().parents[4]
INSPECTOR_ROLE = REPO_ROOT / ".claude" / "agents" / "inspector.md"
ADR_TEXT = (
    REPO_ROOT / "docs" / "DECISIONS.md"
)  # ADR-0258 addendum 1, numbered from team/plans/owner-trials-wiring-adr.md

WHOLE = """Bulgular ...
deneme: ses-saat
cumle: Saat kaç?
makine: ev PC
beklenen: saati Türkçe söyler
APPROVE
"""


def _task(task_id: str, owner_trials: list[Any] | None) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": task_id,
        "title": f"the task {task_id}",
        "roadmap_row": "row",
        "state": "merged",
        "area": ["src/area"],
        "branch": "",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": {"max_usd": 5},
        "created_at": "2026-10-03T00:00:00Z",
        "updated_at": "2026-10-03T00:00:00Z",
    }
    if owner_trials is not None:
        task["owner_trials"] = owner_trials
    return task


def _trial(trial_id: str, verdict: str | None) -> dict[str, Any]:
    return {
        "id": trial_id,
        "sentence": "Saat kaç?",
        "machine": "ev PC",
        "expect": "saati söyler",
        "verdict": verdict,
        "said": None if verdict != trials.FAILED else "ses yok",
        "at": None if verdict is None else "2026-10-03T10:00:00Z",
    }


def _fenced_trial_blocks(text: str) -> list[str]:
    blocks = re.findall(r"```\n(.*?)```", text, flags=re.DOTALL)
    return [b for b in blocks if b.lstrip().startswith("deneme:")]


# ------------------------------------------------------------------ the inspector's form


def test_a_whole_block_becomes_a_trial_the_schema_accepts() -> None:
    found = trials.parse_inspector_trials(WHOLE)
    assert found == [
        {
            "id": "ses-saat",
            "sentence": "Saat kaç?",
            "machine": "ev PC",
            "expect": "saati Türkçe söyler",
            "verdict": None,
            "said": None,
            "at": None,
        }
    ]
    assert team_store.task_problems(_task("trial-task", found)) == []


def test_a_block_without_a_machine_is_skipped_without_raising() -> None:
    broken = WHOLE.replace("makine: ev PC\n", "")
    assert trials.parse_inspector_trials(broken) == []
    # the next whole block is still read
    assert len(trials.parse_inspector_trials(broken + WHOLE.replace("ses-saat", "iki"))) == 1


def test_a_sentence_longer_than_the_bound_is_skipped() -> None:
    long = "a" * (trials.SENTENCE_MAX_CHARS + 1)
    assert trials.parse_inspector_trials(WHOLE.replace("Saat kaç?", long)) == []
    edge = "a" * trials.SENTENCE_MAX_CHARS
    assert len(trials.parse_inspector_trials(WHOLE.replace("Saat kaç?", edge))) == 1


@pytest.mark.parametrize(
    "report",
    [
        "",
        "cumle: yalnız\nmakine: x\nbeklenen: y\n",  # no deneme line
        WHOLE.replace("deneme: ses-saat", "deneme: Büyük Harf"),  # an id the schema refuses
        WHOLE.replace("beklenen: saati Türkçe söyler", "beklenen:"),  # an empty line
        WHOLE.replace("makine: ev PC", "makine: ev PC\nmakine: iş PC"),  # a line twice
    ],
)
def test_a_malformed_block_is_skipped_never_raised(report: str) -> None:
    assert trials.parse_inspector_trials(report) == []


def test_the_adr_paragraph_example_parses() -> None:
    """The paragraph the lead pastes into the role file (see the xfail below) is checked here."""
    decisions = ADR_TEXT.read_text(encoding="utf-8")
    start = decisions.index("### ADR-0258 addendum 1")
    end = decisions.find("\n### ", start + 1)
    blocks = _fenced_trial_blocks(decisions[start : end if end != -1 else None])
    assert blocks, "the ADR carries the role paragraph's example block"
    for block in blocks:
        found = trials.parse_inspector_trials(block)
        assert len(found) == 1, block
        assert team_store.task_problems(_task("trial-task", found)) == []


def test_the_role_file_example_block_parses() -> None:
    blocks = _fenced_trial_blocks(INSPECTOR_ROLE.read_text(encoding="utf-8"))
    assert blocks, "inspector.md carries the trial form's example block"
    for block in blocks:
        assert len(trials.parse_inspector_trials(block)) == 1, block


# ------------------------------------------------------------------ released with open trials


@pytest.mark.parametrize(
    ("owner_trials", "expected"),
    [
        (None, "released"),
        ([], "released"),
        ([_trial("a", "oldu"), _trial("b", "oldu")], "released"),
        ([_trial("a", "oldu"), _trial("b", None)], "awaiting_real_evidence"),
        ([_trial("a", "olmadi")], "awaiting_real_evidence"),
        (["Telefonda Ofis'i aç"], "awaiting_real_evidence"),  # the old form has no verdict
    ],
)
def test_released_state(owner_trials: list[Any] | None, expected: str) -> None:
    assert trials.released_state(_task("t", owner_trials)) == expected


def test_released_state_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "team"
    root.mkdir()
    (root / "queue.json").write_text('{"version": 1, "tasks": []}\n', encoding="utf-8")
    (root / "lock.json").write_text('{"held": false}\n', encoding="utf-8")
    store = team_store.FileStore(root)
    store.put_task(_task("open-one", [_trial("a", None)]), None)
    store.put_task(_task("closed-one", [_trial("a", "oldu")]), None)

    def digest() -> dict[str, str]:
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.iterdir())}

    before = digest()
    queue = store.read_queue()
    states = {t["id"]: trials.released_state(t) for t in queue["tasks"]}
    assert states == {"open-one": "awaiting_real_evidence", "closed-one": "released"}
    assert [t["state"] for t in queue["tasks"]] == ["merged", "merged"]  # its input untouched
    assert digest() == before


# ------------------------------------------------------------------ the ledger's vocabulary


def test_the_trial_events_are_in_the_ledger_vocabulary() -> None:
    assert trials.EVENT_TRIAL_PASSED in vocabulary.EVENT_TYPES
    assert trials.EVENT_TRIAL_FAILED in vocabulary.EVENT_TYPES
