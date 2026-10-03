"""A widened task's two new fields on the REAL database (ADR-0253, card area-widen-cycle-wiring).

The cycle writes ``area_widenings`` and ``area_history`` onto a task when a role's report asks
for files outside the card's area (``scripts/team/cycle.ps1``). They reach the Cloud Core through
``PUT /v1/team/queue/tasks/{id}``, and the store validates every task against
``queue.schema.json`` (no field it does not know). This test puts such a task through the real
route onto the dev stack's PostgreSQL (``team_store = db``, TEAM_PROTOCOL 9a), reads it back
through a FRESH store - what is in the row, not what a session remembers - and holds the
conditional write to its rule: a stale ``expected_updated_at`` is a 409 that changes nothing.
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
from app.team.store import DbStore
from tests.integration.conftest import attach_owner

pytestmark = pytest.mark.integration

KEY_PREFIX = "it-area-"
SEEN = "2026-10-03T10:00:00Z"
LATER = "2026-10-03T10:05:00Z"

HISTORY = [
    {
        "at": "2026-10-03T10:01:00Z",
        "by": "inspector",
        "why": "Çakışma yok; kartın alanı genişletildi: docs/ölçüm.md.",
        "files": ["docs/ölçüm.md"],
    },
    {
        "at": "2026-10-03T10:02:00Z",
        "by": "worker",
        "why": (
            "İstenen dosya başka kartın alanında (it-area-holder); alan değişmedi, "
            "kart o iş main'e girene kadar bekler."
        ),
        "files": ["services/api/app/voice/intents.py"],
        "waits_for": ["it-area-holder"],
    },
]


def _task(task_id: str, updated_at: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": "Alanı genişletilen kart",
        "roadmap_row": "Repairs and improves itself",
        "state": "returned",
        "area": ["scripts/team/cycle.ps1", "docs/ölçüm.md"],
        "branch": f"team/d20261003/worker-{task_id}",
        "worktree": "",
        "assignee": "worker",
        "reports": [],
        "budget": {"max_usd": 5},
        "created_at": SEEN,
        "updated_at": updated_at,
        "returns": 0,
        "depends_on": ["it-area-holder"],
        "area_widenings": 1,
        "area_history": HISTORY,
    }


@pytest.fixture()
def task_id() -> str:
    return f"{KEY_PREFIX}{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(Settings().database_url)
    made = build_session_factory(engine)

    def clear() -> None:
        with made() as session:
            session.execute(delete(TeamStateRow).where(TeamStateRow.key.like(f"{KEY_PREFIX}%")))
            session.commit()

    clear()
    try:
        yield made
    finally:
        clear()
        engine.dispose()


@pytest.fixture()
def client(factory, tmp_path) -> TestClient:
    settings = Settings()
    app = create_app(settings)
    app.state.team_store = DbStore(factory)
    app.state.team_root = tmp_path / "team"
    made = TestClient(app)
    attach_owner(app, made, settings)
    return made


def _stored(factory, task_id: str) -> dict[str, Any]:
    tasks = [t for t in DbStore(factory).read_queue()["tasks"] if t["id"] == task_id]
    assert len(tasks) == 1, tasks
    return tasks[0]


def test_a_widened_task_is_stored_whole_and_a_stale_write_leaves_it_as_it_was(
    client: TestClient, factory, task_id: str
) -> None:
    # The holder the wait names exists: the queue's rule for depends_on.
    holder = _task("it-area-holder", SEEN)
    for name in ("area_widenings", "area_history", "depends_on"):
        holder.pop(name)
    holder.update(state="in_progress", area=["services/api/app/voice"], branch="team/x/worker-h")
    made = client.put(
        "/v1/team/queue/tasks/it-area-holder", json={"task": holder, "expected_updated_at": None}
    )
    assert made.status_code == 200, made.text

    put = client.put(
        f"/v1/team/queue/tasks/{task_id}",
        json={"task": _task(task_id, SEEN), "expected_updated_at": None},
    )
    assert put.status_code == 200, put.text

    stored = _stored(factory, task_id)
    assert stored["area_widenings"] == 1
    assert stored["area"] == ["scripts/team/cycle.ps1", "docs/ölçüm.md"]
    assert stored["area_history"] == HISTORY  # the keys, the order, the Turkish text
    assert list(stored["area_history"][1].keys()) == ["at", "by", "why", "files", "waits_for"]

    # A cycle that read the task before this version: refused, and nothing of it is written.
    stale = _task(task_id, LATER)
    stale["area_widenings"] = 2
    stale["area_history"] = [*HISTORY, {**HISTORY[0], "files": ["docs/b.md"]}]
    refused = client.put(
        f"/v1/team/queue/tasks/{task_id}",
        json={"task": stale, "expected_updated_at": "2026-10-03T09:00:00Z"},
    )
    assert refused.status_code == 409, refused.text
    again = _stored(factory, task_id)
    assert again["area_widenings"] == 1
    assert again["area_history"] == HISTORY
    assert again["updated_at"] == SEEN
