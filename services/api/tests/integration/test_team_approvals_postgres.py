"""The owner decides while a cycle holds the lock - on the REAL database (ADR-0214 addendum 4).

Owner, 2026-10-01: "neden onaylayamıyorum?" - a cycle was running, as it now does all day, and
the Onay Merkezi refused every decision. On the database store a decision is one conditional
``UPDATE ... WHERE updated_at = :seen`` on one ``team_state`` row, so it is taken while the
cycle's lock row is held, and a task the cycle changed meanwhile is a 409, not an overwrite.
These tests take the route, the ledger write and that UPDATE to the dev stack's PostgreSQL.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.main import create_app
from app.team.models import TeamStateRow
from app.team.store import DbStore, Stale, utcnow
from tests.integration.conftest import attach_owner

pytestmark = pytest.mark.integration

BASE = "/v1/team/approvals"
SEEN = "2026-10-01T00:00:00Z"
MACHINE = "GMKADIRAKBABA-OFFICE-PC"
CYCLE = "cycle-2026-10-01-a-long-cycle-id"


def _task(task_id: str, state: str = "awaiting_owner", **extra: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": task_id,
        "title": f"Fikir {task_id}",
        "roadmap_row": "",
        "state": state,
        "area": [],
        "branch": "",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": {"max_usd": 5},
        "created_at": SEEN,
        "updated_at": SEEN,
        "proposal": "Kısa öneri",
    }
    task.update(extra)
    return task


@pytest.fixture()
def prefix() -> str:
    """Fresh ids per run: a decision is a ledger event, idempotent on the task's id and
    version, and the ledger is append-only - a rerun must not meet its own earlier events."""
    return f"it-approvals-{uuid.uuid4().hex[:10]}-"


@pytest.fixture()
def factory(prefix: str) -> Iterator[sessionmaker[Session]]:
    engine = build_engine(Settings().database_url)
    made = build_session_factory(engine)

    def clear() -> None:
        with made() as session:
            session.execute(delete(TeamStateRow).where(TeamStateRow.key.like("it-approvals-%")))
            session.execute(delete(TeamStateRow).where(TeamStateRow.kind == "lock"))
            session.commit()

    clear()
    try:
        yield made
    finally:
        clear()
        engine.dispose()


@pytest.fixture()
def wired(factory, prefix: str, tmp_path) -> tuple[TestClient, DbStore]:
    settings = Settings()
    app = create_app(settings)
    store = DbStore(factory)
    app.state.team_store = store
    app.state.team_root = tmp_path / "team"
    client = TestClient(app)
    attach_owner(app, client, settings)
    for name in ("a", "b", "c"):
        store.put_task(_task(f"{prefix}{name}"), None)
    return client, store


def _held_as_a_running_cycle_would(store: DbStore) -> None:
    taken = store.acquire_lock(machine=MACHINE, cycle_id=CYCLE, pid=4_194_304, now=utcnow())
    assert taken["acquired"] is True, taken


def _tasks(store: DbStore, prefix: str) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in store.read_queue()["tasks"] if t["id"].startswith(prefix)}


def test_an_idea_is_approved_and_another_rejected_while_the_lock_is_held_on_postgres(
    wired, prefix: str
) -> None:
    client, store = wired
    _held_as_a_running_cycle_would(store)

    listing = client.get(BASE).json()
    assert (listing["decisions_open"], listing["cycle_running"]) == (True, True)
    assert {f"{prefix}a", f"{prefix}b"} <= {a["task_id"] for a in listing["approvals"]}

    approved = client.post(
        f"{BASE}/decision", json={"task_id": f"{prefix}a", "decision": "approve"}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["applied"] == "next_cycle"
    assert approved.json()["cycle_running"] is True
    rejected = client.post(
        f"{BASE}/decision",
        json={"task_id": f"{prefix}b", "decision": "reject", "reason": "şimdi değil"},
    )
    assert rejected.status_code == 200, rejected.text

    tasks = _tasks(DbStore(wired[1]._factory), prefix)  # read back through a fresh store
    assert tasks[f"{prefix}a"]["state"] == "approved"
    assert (tasks[f"{prefix}b"]["state"], tasks[f"{prefix}b"]["reason"]) == (
        "stopped",
        "Sahip reddetti: şimdi değil",
    )
    assert tasks[f"{prefix}c"] == _task(f"{prefix}c")  # the idea nobody decided: untouched
    lock = store.read_lock()
    assert lock is not None and (lock["held"], lock["machine"]) == (True, MACHINE)

    # The cycle read the idea before the decision: its write over it is refused.
    with pytest.raises(Stale):
        store.put_task(_task(f"{prefix}a", title="döngünün eski kopyası"), SEEN)
    assert _tasks(store, prefix)[f"{prefix}a"]["state"] == "approved"


def test_a_decision_on_a_task_the_cycle_changed_meanwhile_is_a_409_on_postgres(
    wired, prefix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = wired
    _held_as_a_running_cycle_would(store)
    task_id = f"{prefix}a"
    original = store.put_task

    def the_cycle_writes_first(task: dict[str, Any], expected: str | None) -> dict[str, Any]:
        monkeypatch.setattr(store, "put_task", original)
        original(_task(task_id, title="döngü yazdı", updated_at="2026-10-01T00:30:00Z"), SEEN)
        return original(task, expected)

    monkeypatch.setattr(store, "put_task", the_cycle_writes_first)
    refused = client.post(f"{BASE}/decision", json={"task_id": task_id, "decision": "approve"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "stale_write"
    task = _tasks(store, prefix)[task_id]
    assert (task["state"], task["title"]) == ("awaiting_owner", "döngü yazdı")

    # What the page does next: it reloads and the same decision is taken.
    again = client.post(f"{BASE}/decision", json={"task_id": task_id, "decision": "approve"})
    assert again.status_code == 200, again.text
    assert _tasks(store, prefix)[task_id]["state"] == "approved"
