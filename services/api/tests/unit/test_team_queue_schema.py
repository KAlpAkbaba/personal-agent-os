"""The agent team's queue against its JSON Schema (docs/TEAM_PROTOCOL.md section 7).

``team/queue.json`` is the state the team's cycle runs from; ``team/queue.schema.json`` says
what it may hold. ``scripts/tests/team-cycle.tests.ps1`` holds the SCRIPT to the schema (one
list of states, one list of required fields). This file holds the QUEUE to the schema, with
a validator for exactly the keywords the schema uses - the repository carries no JSON Schema
library, and a schema nothing validates against is a comment.

A keyword the validator does not know fails the test: the schema cannot grow a rule that
nothing checks.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[4]
QUEUE = REPO / "team" / "queue.json"
SCHEMA = REPO / "team" / "queue.schema.json"
LOCK = REPO / "team" / "lock.json"
PROTOCOL = REPO / "docs" / "TEAM_PROTOCOL.md"
ROLES = REPO / ".claude" / "agents"

KNOWN = {
    "$schema",
    "$id",
    "$defs",
    "$ref",
    "title",
    "type",
    "required",
    "additionalProperties",
    "properties",
    "items",
    "enum",
    "const",
    "pattern",
    "minLength",
    "minimum",
    "maxItems",
}
TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
}


def _load(path: Path) -> Any:
    # No skip when the file is missing: a contract that can be deleted is not one.
    return json.loads(path.read_text(encoding="utf-8"))


def problems(
    value: Any, schema: dict[str, Any], root: dict[str, Any], where: str = "$"
) -> list[str]:
    unknown = set(schema) - KNOWN
    assert not unknown, f"the validator does not know {sorted(unknown)} (at {where})"
    if "$ref" in schema:
        prefix = "#/$defs/"
        assert schema["$ref"].startswith(prefix), schema["$ref"]
        return problems(value, root["$defs"][schema["$ref"][len(prefix) :]], root, where)
    found: list[str] = []
    if "const" in schema and value != schema["const"]:
        found.append(f"{where}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{where}: {value!r} is not one of the allowed values")
    kind = schema.get("type")
    if kind is not None:
        wrong = not isinstance(value, TYPES[kind]) or (
            kind in ("integer", "number") and isinstance(value, bool)
        )
        if wrong:
            return [*found, f"{where}: must be {kind}"]
    if isinstance(value, str):
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            found.append(f"{where}: does not match {schema['pattern']}")
        if len(value) < schema.get("minLength", 0):
            found.append(f"{where}: is too short")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            found.append(f"{where}: is below {schema['minimum']}")
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", len(value)):
            found.append(f"{where}: has more than {schema['maxItems']} items")
        for index, item in enumerate(value):
            if "items" in schema:
                found += problems(item, schema["items"], root, f"{where}[{index}]")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                found.append(f"{where}: '{name}' is missing")
        known = schema.get("properties", {})
        for name, item in value.items():
            if name in known:
                found += problems(item, known[name], root, f"{where}.{name}")
            elif schema.get("additionalProperties") is False:
                found.append(f"{where}: '{name}' is not a field of the protocol")
    return found


def check(queue: Any) -> list[str]:
    schema = _load(SCHEMA)
    return problems(queue, schema, schema)


def task(**changes: Any) -> dict[str, Any]:
    base = {
        "id": "a-task",
        "title": "a task",
        "roadmap_row": "",
        "state": "approved",
        "area": ["services/api/app/team"],
        "branch": "",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": {"max_usd": 5},
        "created_at": "2026-09-30T00:00:00Z",
        "updated_at": "2026-09-30T00:00:00Z",
    }
    base.update(changes)
    return base


# ------------------------------------------------------------------ the files as they are


def test_the_queue_in_the_repository_is_valid() -> None:
    assert check(_load(QUEUE)) == []


def test_the_committed_lock_is_released() -> None:
    assert _load(LOCK) == {"held": False}


def test_no_task_in_the_repository_is_past_a_gate_the_owner_has_not_opened() -> None:
    """Nothing is committed as released or done by a script: those states follow the
    owner's release approval and his real-world evidence."""
    for item in _load(QUEUE)["tasks"]:
        if item["state"] == "done" and item.get("proposal"):
            # A proposal's life ends when the owner approves it and the lead splits it.
            assert "sahip onaylad" in item.get("reason", ""), item["id"]
            continue
        assert item["state"] not in ("released", "done"), item["id"]


# ------------------------------------------------------------------ the schema says no


def test_a_task_as_the_protocol_describes_it_is_valid() -> None:
    assert check({"version": 1, "tasks": [task()]}) == []
    assert check({"version": 1, "tasks": []}) == []


@pytest.mark.parametrize(
    ("changes", "says"),
    [
        ({"state": "finished"}, "is not one of the allowed values"),
        ({"state": "APPROVED"}, "is not one of the allowed values"),
        ({"id": "Bad-Id"}, "does not match"),
        ({"id": "ab"}, "does not match"),
        ({"title": ""}, "is too short"),
        ({"area": "services/api"}, "must be array"),
        ({"area": [""]}, "is too short"),
        ({"budget": {"max_usd": -1}}, "is below 0"),
        ({"budget": {"max_usd": "5"}}, "must be number"),
        ({"budget": {"max_usd": True}}, "must be number"),
        ({"budget": {}}, "'max_usd' is missing"),
        ({"created_at": "2026-09-30 00:00"}, "does not match"),
        ({"sha": "771a9e5"}, "does not match"),
        ({"returns": -1}, "is below 0"),
        ({"approved_by": "somebody"}, "is not a field of the protocol"),
        (
            {"reports": [{"cycle": "c1", "role": "owner", "at": "", "file": "", "summary": []}]},
            "allowed",
        ),
        (
            {
                "reports": [
                    {
                        "cycle": "c1",
                        "role": "worker",
                        "at": "",
                        "file": "",
                        "summary": ["line"] * 41,
                    }
                ]
            },
            "more than 40 items",
        ),
    ],
)
def test_what_the_protocol_does_not_allow_is_refused(changes: dict[str, Any], says: str) -> None:
    found = check({"version": 1, "tasks": [task(**changes)]})
    assert any(says in problem for problem in found), found


@pytest.mark.parametrize(
    "missing",
    [
        "id",
        "title",
        "roadmap_row",
        "state",
        "area",
        "branch",
        "worktree",
        "assignee",
        "reports",
        "budget",
        "created_at",
        "updated_at",
    ],
)
def test_every_field_the_owner_named_is_required(missing: str) -> None:
    item = task()
    del item[missing]
    assert f"$.tasks[0]: '{missing}' is missing" in check({"version": 1, "tasks": [item]})


def test_a_release_the_owner_approved_is_a_flag_on_a_task_that_still_waits() -> None:
    """ADR-0217: approving a release never writes `approved` (the cycle reads that as
    "assign a worker"); it raises a flag and the lead releases."""
    approved = task(
        state="awaiting_release",
        release_approved=True,
        release_approved_at="2026-09-30T07:00:00Z",
        release_approved_by="shell",
    )
    assert check({"version": 1, "tasks": [approved]}) == []
    for changes, says in (
        ({"release_approved": "yes"}, "must be boolean"),
        ({"release_approved_at": "2026-09-30 07:00"}, "does not match"),
        ({"release_approved_by": "someone"}, "allowed"),
    ):
        found = check({"version": 1, "tasks": [task(state="awaiting_release", **changes)]})
        assert any(says in problem for problem in found), (changes, found)


def test_a_queue_of_another_version_or_with_no_tasks_is_refused() -> None:
    assert check({"version": 2, "tasks": []}) == ["$.version: must be 1"]
    assert check({"version": 1}) == ["$: 'tasks' is missing"]
    assert check([]) == ["$: must be object"]


def test_a_rule_the_validator_does_not_know_fails_the_test() -> None:
    schema = copy.deepcopy(_load(SCHEMA))
    schema["$defs"]["task"]["properties"]["title"]["maxLength"] = 10
    with pytest.raises(AssertionError, match="maxLength"):
        problems({"version": 1, "tasks": [task()]}, schema, schema)


# ------------------------------------------------------------------ the protocol's own words


def test_the_states_are_the_thirteen_the_owner_named() -> None:
    states = _load(SCHEMA)["$defs"]["task"]["properties"]["state"]["enum"]
    assert states == [
        "proposed",
        "awaiting_owner",
        "approved",
        "assigned",
        "in_progress",
        "inspecting",
        "returned",
        "merged",
        "awaiting_release",
        "released",
        "awaiting_real_evidence",
        "done",
        "stopped",
    ]


def test_every_role_of_the_protocol_has_its_file_and_the_report_names_no_other() -> None:
    protocol = PROTOCOL.read_text(encoding="utf-8")
    named = re.findall(r"\| `([a-z]+)\.md` \|", protocol)
    assert named == ["lead", "researcher", "integrator", "worker", "inspector"]
    roles = _load(SCHEMA)["$defs"]["report"]["properties"]["role"]["enum"]
    assert roles == named
    for role in named:
        text = (ROLES / f"{role}.md").read_text(encoding="utf-8")
        front = text.split("---")[1]
        assert f"name: {role}\n" in front and "description: " in front and "tools: " in front


def test_who_may_change_code_is_what_the_protocol_says() -> None:
    def tools(role: str) -> set[str]:
        front = (ROLES / f"{role}.md").read_text(encoding="utf-8").split("---")[1]
        line = next(row for row in front.splitlines() if row.startswith("tools:"))
        return {tool.strip() for tool in line.removeprefix("tools:").split(",")}

    assert "Edit" not in tools("inspector")  # "changes no code"
    assert "Edit" not in tools("researcher") and "Bash" not in tools("researcher")
    assert "Edit" not in tools("integrator")  # "never writes feature code"
    assert {"Edit", "Write", "Bash"} <= tools("worker")
