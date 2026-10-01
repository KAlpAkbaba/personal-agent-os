"""The Ofis page's data: ``GET /v1/team/office`` and the live status behind it.

The pure function ``office_view`` holds the seat rules and the staleness rule; the routes carry
it over a FileStore on ``tmp_path`` and over the DbStore the other team tests use.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.team import office
from app.team import store as team_store
from app.team.models import TeamStateRow
from tests.identity_support import authenticate, install_identity

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
SEATS = [
    "lead",
    "researcher",
    "integrator",
    "worker-1",
    "worker-2",
    "worker-3",
    "inspector",
    "owner",
]
OFFICE = "/v1/team/office"
STATUS = "/v1/team/queue/status"
LOCK = {
    "held": True,
    "machine": "MAIL",
    "cycle_id": "c1",
    "pid": 7,
    "acquired_at": "2026-10-01T11:00:00Z",
}


def _task(task_id: str, state: str = "in_progress", **extra: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": task_id,
        "title": f"Başlık {task_id}",
        "roadmap_row": "row",
        "state": state,
        "area": ["a"],
        "branch": f"team/c/{task_id}",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": {"max_usd": 5},
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
    }
    task.update(extra)
    return task


def _report(role: str, at: str = "2026-10-01T10:00:00Z", lines: int = 3) -> dict[str, Any]:
    return {
        "cycle": "c",
        "role": role,
        "at": at,
        "file": "team/reports/x.md",
        "outcome": "tamam",
        "summary": [f"satır {i}" for i in range(lines)],
    }


def _status(runs: list[tuple[str, str]], *, at: datetime = NOW, **extra: Any) -> dict[str, Any]:
    stamp = team_store.stamp
    doc: dict[str, Any] = {
        "cycle_id": "c1",
        "machine": "MAIL",
        "pid": 7,
        "started_at": stamp(at - timedelta(hours=1)),
        "runs": [
            {"task": t, "role": r, "started_at": stamp(at - timedelta(minutes=30 - i))}
            for i, (t, r) in enumerate(runs)
        ],
        "estimated_usd": 1.5,
        "usage_limit": {"state": "ok", "resets_at": None},
        "updated_at": stamp(at),
    }
    doc.update(extra)
    return doc


def _view(tasks, status, lock=LOCK, approvals=None, now=NOW):
    return office.office_view({"version": 1, "tasks": tasks}, lock, status, approvals or [], now)


def _seat(view, name):
    return next(s for s in view["agents"] if s["seat"] == name)


# ------------------------------------------------------------------ the pure function


def test_two_worker_runs_take_worker_1_and_2_and_the_third_waits():
    tasks = [_task("alpha-task"), _task("beta-task")]
    view = _view(tasks, _status([("alpha-task", "worker"), ("beta-task", "worker")]))
    assert [s["seat"] for s in view["agents"]] == SEATS
    w1, w2, w3 = (_seat(view, f"worker-{n}") for n in (1, 2, 3))
    assert (w1["state"], w1["task_id"], w1["task_title"]) == (
        "working",
        "alpha-task",
        "Başlık alpha-task",
    )
    assert (w2["state"], w2["task_id"]) == ("working", "beta-task")
    assert (w3["state"], w3["task_id"]) == ("waiting", None)
    assert view["cycle"]["running"] is True
    assert view["cycle"]["running_agents"] == 2
    assert view["cycle"]["capacity"] == 6
    assert view["cycle"]["cycle_id"] == "c1"
    assert view["cycle"]["estimated_usd"] == 1.5


def test_an_inspector_run_makes_the_inspector_seat_working():
    view = _view([_task("alpha-task", "inspecting")], _status([("alpha-task", "inspector")]))
    assert _seat(view, "inspector")["state"] == "working"
    assert _seat(view, "worker-1")["state"] == "waiting"


@pytest.mark.parametrize("state", ["returned", "stopped"])
def test_a_role_whose_newest_task_is_returned_or_stopped_is_returned(state):
    tasks = [
        _task("old-task", "merged", reports=[_report("inspector", "2026-10-01T08:00:00Z")]),
        _task("new-task", state, reports=[_report("inspector", "2026-10-01T09:00:00Z")]),
    ]
    view = _view(tasks, _status([]))
    seat = _seat(view, "inspector")
    assert (seat["state"], seat["task_id"]) == ("returned", "new-task")
    assert _seat(view, "lead")["state"] == "waiting"


def test_a_stale_status_means_not_running_and_nobody_working():
    old = _status([("alpha-task", "worker")], at=NOW - timedelta(minutes=11))
    view = _view([_task("alpha-task")], old)
    assert view["cycle"]["running"] is False
    assert view["cycle"]["running_agents"] == 0
    assert all(s["state"] != "working" for s in view["agents"])
    fresh = _status([("alpha-task", "worker")], at=NOW - timedelta(minutes=9))
    assert _view([_task("alpha-task")], fresh)["cycle"]["running"] is True


@pytest.mark.parametrize(
    "lock",
    [
        None,
        {"held": False},
        {**LOCK, "cycle_id": "other"},
        {**LOCK, "acquired_at": "2026-10-01T05:00:00Z"},
    ],
)
def test_a_lock_that_is_released_or_not_the_cycles_means_not_running(lock):
    view = _view([_task("alpha-task")], _status([("alpha-task", "worker")]), lock=lock)
    assert view["cycle"]["running"] is False
    assert all(s["state"] != "working" for s in view["agents"])


def test_a_summary_longer_than_forty_lines_is_cut_to_forty():
    task = _task("alpha-task", "returned", reports=[_report("worker", lines=55)])
    report = _view([task], None)["tasks"]["alpha-task"]["report"]
    assert len(report["summary"]) == 40
    assert report["summary"][0] == "satır 0"
    assert set(report) == {"role", "at", "outcome", "summary"}


def test_the_owner_seat_always_waits_with_no_task_and_tasks_exclude_released_and_done():
    tasks = [_task("a-task", "released"), _task("b-task", "done"), _task("c-task", "approved")]
    view = _view(tasks, None, approvals=[{"task_id": "x"}])
    owner_seat = _seat(view, "owner")
    assert (owner_seat["state"], owner_seat["task_id"]) == ("waiting", None)
    assert list(view["tasks"]) == ["c-task"]
    assert view["tasks"]["c-task"]["report"] is None
    assert view["approvals"] == [{"task_id": "x"}]


def test_no_status_is_the_empty_office():
    view = _view([], None, lock=None)
    assert [s["seat"] for s in view["agents"]] == SEATS
    assert {s["state"] for s in view["agents"]} == {"waiting"}
    assert view["cycle"]["running"] is False
    assert view["cycle"]["usage_limit"] == {"state": "ok", "resets_at": None}
    assert view["tasks"] == {}


# ------------------------------------------------------------------ the routes


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    TeamStateRow.__table__.create(eng)
    yield eng
    eng.dispose()


def _file_store(tmp_path: Path) -> team_store.FileStore:
    root = tmp_path / "team"
    (root / "reports").mkdir(parents=True)
    (root / "queue.json").write_text('{"version": 1, "tasks": []}\n', encoding="utf-8")
    (root / "lock.json").write_text('{"held": false}\n', encoding="utf-8")
    return team_store.FileStore(root)


@pytest.fixture(params=["file", "db"])
def owner(request, engine, tmp_path):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    if request.param == "db":
        app.state.team_store = team_store.DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    else:
        app.state.team_store = _file_store(tmp_path)
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    client.team_store = app.state.team_store
    return client


def _live_status(runs, **extra):
    return _status(runs, at=team_store.utcnow(), **extra)


def test_the_status_round_trips_and_the_office_shows_the_runs(owner):
    for name in ("alpha-task", "beta-task"):
        put = owner.put(
            f"/v1/team/queue/tasks/{name}", json={"task": _task(name), "expected_updated_at": None}
        )
        assert put.status_code == 200, put.text
    owner.team_store.acquire_lock(machine="MAIL", cycle_id="c1", pid=7)
    doc = _live_status([("alpha-task", "worker"), ("beta-task", "worker")])
    assert owner.put(STATUS, json=doc).status_code == 200
    assert owner.get(STATUS).json() == doc
    body = owner.get(OFFICE).json()
    assert [s["seat"] for s in body["agents"]] == SEATS
    assert body["cycle"]["running_agents"] == 2
    assert _seat(body, "worker-2")["task_id"] == "beta-task"
    assert body["approvals"] == owner.get("/v1/team/approvals").json()["approvals"]


def test_a_status_with_an_unknown_key_or_a_wrong_type_is_a_422(owner):
    good = _live_status([("alpha-task", "worker")])
    assert owner.put(STATUS, json={**good, "surprise": 1}).status_code == 422
    assert owner.put(STATUS, json={**good, "pid": "seven"}).status_code == 422
    bad_limit = {"state": "bored", "resets_at": None}
    assert owner.put(STATUS, json={**good, "usage_limit": bad_limit}).status_code == 422
    bad_run = [{"task": "a-task", "role": "king", "started_at": "x"}]
    assert owner.put(STATUS, json={**good, "runs": bad_run}).status_code == 422
    assert (
        owner.put(STATUS, json={k: v for k, v in good.items() if k != "cycle_id"}).status_code
        == 422
    )
    assert not owner.get(STATUS).json().get("cycle_id")


def test_the_status_routes_are_closed_without_an_owner_session(engine):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.team_store = team_store.DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    client = TestClient(app)
    assert client.get(OFFICE).status_code == 401
    assert client.get(STATUS).status_code == 401
    assert client.put(STATUS, json={}).status_code == 401


def test_with_no_store_and_no_team_root_the_office_is_empty_not_a_500(tmp_path):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.team_store = None
    app.state.team_root = tmp_path / "nowhere"
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    response = client.get(OFFICE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cycle"]["running"] is False
    assert [s["state"] for s in body["agents"]] == ["waiting"] * 8
    assert body["tasks"] == {}


def test_the_file_store_keeps_the_status_beside_the_queue(tmp_path):
    store = _file_store(tmp_path)
    assert store.read_status() is None
    store.put_status({"cycle_id": "c1"})
    on_disk = json.loads((store.root / "status.json").read_text(encoding="utf-8"))
    assert on_disk == {"cycle_id": "c1"}
    assert store.read_status() == {"cycle_id": "c1"}
