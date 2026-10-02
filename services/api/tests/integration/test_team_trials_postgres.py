"""The owner's trials on the REAL database (ADR-0214 addendum 4; owner-trials-api).

The listing, Oldu, Olmadı with its fix task, the duplicate guard and a decision taken while a
cycle holds the lock - through the route, the ledger write and the conditional ``team_state``
writes on the dev stack's PostgreSQL (JSONB, VARCHAR widths and all). The two ledger event
types are the lead's to add to the vocabulary at merge; until then the test adds them.
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
from app.ledger import vocabulary
from app.main import create_app
from app.team import trials
from app.team.models import TeamStateRow
from app.team.store import DbStore, task_problems, utcnow
from tests.integration.conftest import attach_owner

pytestmark = pytest.mark.integration

APPROVALS = "/v1/team/approvals"
DECISION = "/v1/team/trials/decision"
SEEN = "2026-10-02T00:00:00Z"
SHA = "65cd94fffcaaa1028cdfc751729a9238b69102b9"
KEY_PREFIX = "it-trials-"


def _trial(trial_id: str, sentence: str, machine: str) -> dict[str, Any]:
    return {
        "id": trial_id,
        "sentence": sentence,
        "machine": machine,
        "expect": "Hesap makinesi açılır",
        "verdict": None,
        "said": None,
        "at": None,
    }


TRIALS = [
    _trial("hesap", "Hesap makinesini aç", "MAIL"),
    _trial("ofis-hesap", "Ofis bilgisayarımdan hesap makinesini aç", "MAIL -> ofis"),
]


def _task(task_id: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": f"Uzak hesap {task_id}",
        "roadmap_row": "38.4",
        "state": "released",
        "area": ["services/api/app/x"],
        "branch": "",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": {"max_usd": 5},
        "created_at": SEEN,
        "updated_at": SEEN,
        "sha": SHA,
        "owner_trials": TRIALS,
    }


@pytest.fixture()
def task_id() -> str:
    """Fresh ids per run: the ledger is append-only and idempotent on the task's version."""
    return f"{KEY_PREFIX}{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(Settings().database_url)
    made = build_session_factory(engine)

    def clear() -> None:
        with made() as session:
            for pattern in (f"{KEY_PREFIX}%", f"fix-{KEY_PREFIX}%"):
                session.execute(delete(TeamStateRow).where(TeamStateRow.key.like(pattern)))
            session.execute(delete(TeamStateRow).where(TeamStateRow.kind == "lock"))
            session.commit()

    clear()
    try:
        yield made
    finally:
        clear()
        engine.dispose()


@pytest.fixture()
def wired(factory, task_id: str, tmp_path, monkeypatch) -> tuple[TestClient, DbStore]:
    monkeypatch.setattr(
        vocabulary,
        "EVENT_TYPES",
        (*vocabulary.EVENT_TYPES, trials.EVENT_TRIAL_PASSED, trials.EVENT_TRIAL_FAILED),
    )
    settings = Settings()
    app = create_app(settings)
    store = DbStore(factory)
    app.state.team_store = store
    app.state.team_root = tmp_path / "team"
    client = TestClient(app)
    attach_owner(app, client, settings)
    store.put_task(_task(task_id), None)
    return client, store


def _tasks(factory) -> dict[str, dict[str, Any]]:
    # Read back through a fresh store: what is in the rows, not what a session remembers.
    return {t["id"]: t for t in DbStore(factory).read_queue()["tasks"] if KEY_PREFIX in t["id"]}


def test_trials_are_listed_and_decided_while_a_cycle_holds_the_lock_on_postgres(
    wired, factory, task_id: str
) -> None:
    client, store = wired
    taken = store.acquire_lock(
        machine="GMKADIRAKBABA-OFFICE-PC", cycle_id="d20261003", pid=4_194_304, now=utcnow()
    )
    assert taken["acquired"] is True, taken

    listing = client.get(APPROVALS).json()
    mine = [t for t in listing["trials"] if t["task_id"] == task_id]
    assert [t["trial"] for t in mine] == TRIALS
    assert {t["sha"] for t in mine} == {SHA}

    passed = client.post(
        DECISION, json={"task_id": task_id, "trial_id": "hesap", "verdict": "oldu", "said": "oldu"}
    )
    assert passed.status_code == 200, passed.text
    assert passed.json()["cycle_running"] is True

    assert (
        client.post(
            DECISION, json={"task_id": task_id, "trial_id": "ofis-hesap", "verdict": "olmadi"}
        ).status_code
        == 422
    )
    failed = client.post(
        DECISION,
        json={
            "task_id": task_id,
            "trial_id": "ofis-hesap",
            "verdict": "olmadi",
            "said": "ofiste açılmadı",
        },
    )
    assert failed.status_code == 200, failed.text
    fix_id = failed.json()["fix_task_id"]
    assert fix_id == f"fix-{task_id}-1"

    again = client.post(
        DECISION,
        json={"task_id": task_id, "trial_id": "ofis-hesap", "verdict": "olmadi", "said": "x"},
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "already_decided"
    unknown = client.post(DECISION, json={"task_id": task_id, "trial_id": "yok", "verdict": "oldu"})
    assert unknown.status_code == 404

    tasks = _tasks(factory)
    original = tasks[task_id]
    assert original["state"] == "released"
    assert [(t["verdict"], t["said"]) for t in original["owner_trials"]] == [
        ("oldu", "oldu"),
        ("olmadi", "ofiste açılmadı"),
    ]
    assert "reason" not in original  # one trial failed: nothing to claim
    fixes = [t for t in tasks.values() if t["id"].startswith("fix-")]
    assert [t["id"] for t in fixes] == [fix_id]
    fix = fixes[0]
    assert (fix["state"], fix["area"], fix["reason"]) == ("approved", [], "alan: lead belirler")
    assert '"Ofis bilgisayarımdan hesap makinesini aç"' in fix["goal"]
    assert '"ofiste açılmadı"' in fix["goal"] and SHA in fix["goal"]
    assert task_problems(fix) == []
    assert not [t for t in client.get(APPROVALS).json()["trials"] if t["task_id"] == task_id]
    lock = store.read_lock()
    assert lock is not None and lock["held"] is True
