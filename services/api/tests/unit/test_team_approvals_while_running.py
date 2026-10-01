"""The owner decides while a cycle runs - on the database store.

Owner, 2026-10-01, three ideas waiting and a cycle running: "neden onaylayamıyorum?" The page
said "Bir döngü çalışıyor; bitene kadar karar verilemez" and every button was disabled; since
the same day the cycle runs all day (ADR-0214 addendum 6), so the owner could almost never
decide.

The refusal was made for the FILE store, where the cycle rewrites the whole queue file at its
end and would overwrite a decision. On the DATABASE store (ADR-0222) the cycle writes back only
the tasks it changed, each conditional on the ``updated_at`` it read, and it does not touch a
task waiting at one of the owner's gates: the owner's decision is safe, and a task that did
change meanwhile is a 409 ``stale_write``, never an overwrite. With the file store the refusal
stays exactly as it was. The same calls go to real PostgreSQL in
``tests/integration/test_team_approvals_postgres.py``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.team import approvals
from app.team import store as team_store
from app.team.models import TeamStateRow
from tests.identity_support import authenticate, install_identity

BASE = "/v1/team/approvals"
SEEN = "2026-10-01T00:00:00Z"


def _task(task_id: str, state: str, **extra: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": task_id,
        "title": f"Görev {task_id}",
        "roadmap_row": "row",
        "state": state,
        "area": ["services/api/app/x"],
        "branch": "",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": {"max_usd": 5},
        "created_at": SEEN,
        "updated_at": SEEN,
    }
    task.update(extra)
    return task


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(eng)
    TeamStateRow.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def team_root(tmp_path: Path) -> Path:
    root = tmp_path / "team"
    root.mkdir()
    (root / "queue.json").write_text('{"version": 1, "tasks": []}\n', encoding="utf-8")
    (root / "lock.json").write_text('{"held": false}\n', encoding="utf-8")
    return root


def _wired(kind: str, engine, team_root: Path) -> tuple[TestClient, team_store.TeamStore]:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    app.state.team_root = team_root
    if kind == "db":
        store: team_store.TeamStore = team_store.DbStore(
            sessionmaker(bind=engine, expire_on_commit=False)
        )
    else:
        store = team_store.FileStore(team_root)
    app.state.team_store = store
    for task in (
        _task("fikir-a", "awaiting_owner", proposal="Kısa öneri"),
        _task("fikir-b", "awaiting_owner", proposal="Başka öneri"),
        _task("yayin-c", "awaiting_release", sha="a" * 40),
        _task("calisan-d", "in_progress"),
    ):
        store.put_task(task, None)
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    return client, store


@pytest.fixture()
def db(engine, team_root):
    return _wired("db", engine, team_root)


@pytest.fixture()
def files(engine, team_root):
    return _wired("file", engine, team_root)


@pytest.fixture(params=["file", "db"])
def both(request, engine, team_root):
    return _wired(request.param, engine, team_root)


def _cycle_takes_the_lock(store, *, hours_ago: float = 0.5) -> None:
    at = datetime.now(UTC) - timedelta(hours=hours_ago)
    taken = store.acquire_lock(machine="MAIL", cycle_id="d20261001", pid=4242, now=at)
    assert taken["acquired"] is True


def _tasks(store) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in store.read_queue()["tasks"]}


def _decide(client: TestClient, **body: Any):
    return client.post(f"{BASE}/decision", json=body)


def _events(engine) -> list[ActivityEventRow]:
    with sessionmaker(bind=engine)() as session:
        return list(session.execute(select(ActivityEventRow)).scalars())


# ------------------------------------------------------------------ the database store


def test_on_the_database_store_an_idea_is_approved_while_a_cycle_holds_the_lock(db, engine):
    client, store = db
    _cycle_takes_the_lock(store)
    response = _decide(client, task_id="fikir-a", decision="approve")
    assert response.status_code == 200, response.text
    tasks = _tasks(store)
    assert tasks["fikir-a"]["state"] == "approved"
    assert tasks["fikir-a"]["updated_at"] != SEEN
    assert tasks["fikir-b"] == _task("fikir-b", "awaiting_owner", proposal="Başka öneri")
    # The cycle's lock is the cycle's: the decision neither took nor released it.
    lock = store.read_lock()
    assert (lock["held"], lock["machine"], lock["pid"]) == (True, "MAIL", 4242)
    assert [e.event_type for e in _events(engine)] == [approvals.EVENT_TASK_APPROVED]


def test_on_the_database_store_an_idea_is_rejected_with_its_reason_while_a_cycle_runs(db):
    client, store = db
    _cycle_takes_the_lock(store)
    assert _decide(client, task_id="fikir-b", decision="reject").status_code == 422
    assert _tasks(store)["fikir-b"]["state"] == "awaiting_owner"
    response = _decide(client, task_id="fikir-b", decision="reject", reason="şimdi değil")
    assert response.status_code == 200, response.text
    task = _tasks(store)["fikir-b"]
    assert (task["state"], task["reason"]) == ("stopped", "şimdi değil")


def test_on_the_database_store_a_release_is_approved_while_a_cycle_runs(db):
    client, store = db
    _cycle_takes_the_lock(store)
    response = _decide(client, task_id="yayin-c", decision="approve", gate="yayin")
    assert response.status_code == 200, response.text
    task = _tasks(store)["yayin-c"]
    assert (task["state"], task["release_approved"]) == ("awaiting_release", True)


def test_a_running_cycle_opens_no_decision_on_a_task_that_is_not_at_a_gate(db):
    client, store = db
    _cycle_takes_the_lock(store)
    refused = _decide(client, task_id="calisan-d", decision="approve")
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "not_at_a_gate"
    assert _tasks(store)["calisan-d"] == _task("calisan-d", "in_progress")


def test_the_cycle_that_read_the_task_before_the_decision_cannot_write_over_it(db):
    """The other direction of the same race: the cycle holds the version it read at its start,
    the owner decides, and the cycle's write is refused - the decision stands."""
    client, store = db
    _cycle_takes_the_lock(store)
    as_the_cycle_read_it = _tasks(store)["fikir-a"]
    assert _decide(client, task_id="fikir-a", decision="approve").status_code == 200
    as_the_cycle_read_it["title"] = "döngünün eski kopyası"
    with pytest.raises(team_store.Stale):
        store.put_task(as_the_cycle_read_it, SEEN)
    assert _tasks(store)["fikir-a"]["state"] == "approved"


def test_the_listing_says_decisions_are_open_on_the_database_store_cycle_or_not(db):
    client, store = db
    idle = client.get(BASE).json()
    assert (idle["decisions_open"], idle["cycle_running"]) == (True, False)
    _cycle_takes_the_lock(store)
    running = client.get(BASE).json()
    assert (running["decisions_open"], running["cycle_running"]) == (True, True)


# ------------------------------------------------------------------ the file store: unchanged


def test_on_the_file_store_a_decision_is_still_refused_while_a_cycle_holds_the_lock(
    files, team_root, engine
):
    client, store = files
    _cycle_takes_the_lock(store)
    before = hashlib.sha256((team_root / "queue.json").read_bytes()).hexdigest()
    for body in (
        {"task_id": "fikir-a", "decision": "approve"},
        {"task_id": "fikir-b", "decision": "reject", "reason": "şimdi değil"},
        {"task_id": "yayin-c", "decision": "approve"},
    ):
        refused = _decide(client, **body)
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "cycle_running"
    assert hashlib.sha256((team_root / "queue.json").read_bytes()).hexdigest() == before
    assert _events(engine) == []


def test_the_listing_says_decisions_are_closed_on_the_file_store_only_while_a_cycle_runs(files):
    client, store = files
    idle = client.get(BASE).json()
    assert (idle["decisions_open"], idle["cycle_running"]) == (True, False)
    _cycle_takes_the_lock(store)
    running = client.get(BASE).json()
    assert (running["decisions_open"], running["cycle_running"]) == (False, True)


def test_on_the_file_store_a_stale_lock_closes_nothing(files):
    client, store = files
    _cycle_takes_the_lock(store, hours_ago=team_store.LOCK_STALE_HOURS + 1)
    listing = client.get(BASE).json()
    assert (listing["decisions_open"], listing["cycle_running"]) == (True, False)
    assert _decide(client, task_id="fikir-a", decision="approve").status_code == 200
    assert _tasks(store)["fikir-a"]["state"] == "approved"


def test_what_the_listing_says_is_what_the_decision_does(both):
    """One rule, read twice: a page told ``decisions_open`` is never answered cycle_running."""
    client, store = both
    _cycle_takes_the_lock(store)
    told_open = client.get(BASE).json()["decisions_open"]
    answer = _decide(client, task_id="fikir-a", decision="approve")
    assert (answer.status_code == 200) is told_open
    if not told_open:
        assert answer.json()["detail"]["code"] == "cycle_running"


# ------------------------------------------------------------------ both stores


def test_a_task_that_moved_since_it_was_read_is_a_stale_write_on_both_stores(
    both, monkeypatch, engine
):
    """The cycle changed the task between the decision's read and its write. On the database
    store this is what a RUNNING cycle can do, so the lock is held there; the file store
    refuses earlier while it is, so it is shown free."""
    client, store = both
    if store.kind == "db":
        _cycle_takes_the_lock(store)
    original = store.put_task

    def the_cycle_writes_first(task: dict[str, Any], expected: str | None) -> dict[str, Any]:
        monkeypatch.setattr(store, "put_task", original)
        moved = _task("fikir-a", "awaiting_owner", proposal="Kısa öneri", title="döngü yazdı")
        moved["updated_at"] = "2026-10-01T00:30:00Z"
        original(moved, SEEN)
        return original(task, expected)

    monkeypatch.setattr(store, "put_task", the_cycle_writes_first)
    refused = _decide(client, task_id="fikir-a", decision="approve")
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "stale_write"
    task = _tasks(store)["fikir-a"]
    assert (task["state"], task["title"]) == ("awaiting_owner", "döngü yazdı")
    # The same decision on what the page shows after it reloads is taken.
    assert _decide(client, task_id="fikir-a", decision="approve").status_code == 200
    assert _tasks(store)["fikir-a"]["state"] == "approved"


def test_the_answer_says_the_decision_takes_effect_in_the_next_cycle(both):
    client, _ = both
    body = _decide(client, task_id="fikir-a", decision="approve").json()
    assert body["applied"] == "next_cycle"
    assert "sonraki döngüde" in body["message"]
    rejected = _decide(client, task_id="fikir-b", decision="reject", reason="gerek yok").json()
    assert "sonraki döngüde" in rejected["message"]


def test_the_answer_says_so_when_a_cycle_is_running_now(db):
    client, store = db
    quiet = _decide(client, task_id="fikir-a", decision="approve").json()
    _cycle_takes_the_lock(store)
    during = _decide(client, task_id="fikir-b", decision="approve").json()
    assert (quiet["cycle_running"], during["cycle_running"]) == (False, True)
    assert "çalışan döngü" in during["message"] and "çalışan döngü" not in quiet["message"]
