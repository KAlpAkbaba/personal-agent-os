"""Is a run working or stuck? (pm-stuck-run-check) - the Ofis side.

The cycle measures signs of life per run (``scripts/lib/TeamLiveness.ps1``) and writes, per run in
the live status, ``last_activity_at`` and ``idle_minutes`` (and the tool processes that sit idle,
``stuck_children``); ``run_idle_minutes`` is the bound it used. The Ofis answer carries them on the
run and the seat, with ``stuck`` true once a run (or a child of it) showed no life for the bound,
so the page can show "takılmış olabilir - 34 dk iz yok" instead of a tired face. A cycle older
than this sends none of it and is read as before.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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

NOW = datetime(2026, 10, 4, 14, 3, 0, tzinfo=UTC)
LOCK = {"held": True, "machine": "MAIL", "cycle_id": "c1", "pid": 7, "acquired_at": "x"}
STATUS = "/v1/team/queue/status"
OFFICE = "/v1/team/office"
stamp = team_store.stamp


def _task(task_id: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": f"Başlık {task_id}",
        "roadmap_row": "row",
        "state": "in_progress",
        "area": ["a"],
        "branch": f"team/c/{task_id}",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": {"max_usd": 5},
        "created_at": "2026-10-04T00:00:00Z",
        "updated_at": "2026-10-04T00:00:00Z",
    }


def _status(runs: list[dict[str, Any]], *, at: datetime = NOW, **extra: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "cycle_id": "c1",
        "machine": "MAIL",
        "pid": 7,
        "started_at": stamp(at - timedelta(hours=3)),
        "runs": runs,
        "estimated_usd": 1.5,
        "usage_limit": {"state": "ok", "resets_at": None},
        "updated_at": stamp(at),
    }
    doc.update(extra)
    return doc


def _run(task: str, *, at: datetime = NOW, idle: int | None = None, **extra: Any) -> dict:
    run: dict[str, Any] = {
        "task": task,
        "role": "worker",
        "started_at": stamp(at - timedelta(hours=2)),
    }
    if idle is not None:
        run["last_activity_at"] = stamp(at - timedelta(minutes=idle))
        run["idle_minutes"] = idle
    run.update(extra)
    return run


def _seat(view: dict, name: str) -> dict:
    return next(s for s in view["agents"] if s["seat"] == name)


def _view(status: dict, now: datetime = NOW) -> dict:
    tasks = [_task(r["task"]) for r in status["runs"]]
    return office.office_view({"version": 1, "tasks": tasks}, LOCK, status, [], now)


def test_a_run_that_showed_life_is_not_stuck_however_long_it_runs():
    view = _view(_status([_run("long-task", idle=1)]))
    seat = _seat(view, "worker-1")
    assert seat["state"] == "working"
    assert seat["stuck"] is False
    assert seat["idle_minutes"] == 1
    assert seat["runs"][0]["last_activity_at"] == stamp(NOW - timedelta(minutes=1))


def test_a_run_with_no_life_for_the_bound_is_stuck_and_not_a_minute_before():
    assert _seat(_view(_status([_run("t", idle=29)])), "worker-1")["stuck"] is False
    seat = _seat(_view(_status([_run("t", idle=30)])), "worker-1")
    assert (seat["stuck"], seat["idle_minutes"]) == (True, 30)
    assert seat["runs"][0]["stuck"] is True


def test_the_idle_minutes_count_from_last_activity_to_the_clock_of_the_answer():
    status = _status([_run("t", idle=25)])  # written at NOW, read six minutes later
    seat = _seat(_view(status, NOW + timedelta(minutes=6)), "worker-1")
    assert (seat["idle_minutes"], seat["stuck"]) == (31, True)


def test_the_bound_is_the_status_run_idle_minutes_when_it_names_one():
    seat = _seat(_view(_status([_run("t", idle=40)], run_idle_minutes=45)), "worker-1")
    assert seat["stuck"] is False
    seat = _seat(_view(_status([_run("t", idle=45)], run_idle_minutes=45)), "worker-1")
    assert seat["stuck"] is True


def test_a_stuck_child_makes_the_seat_stuck_though_the_run_writes():
    child = {"pid": 300, "name": "python.exe", "idle_minutes": 140}
    seat = _seat(_view(_status([_run("gate-faster", idle=0, stuck_children=[child])])), "worker-1")
    assert seat["stuck"] is True
    assert seat["idle_minutes"] == 0
    assert seat["runs"][0]["stuck_children"] == [child]


def test_an_older_cycle_without_the_fields_is_read_as_before():
    seat = _seat(_view(_status([_run("t")])), "worker-1")
    assert seat["state"] == "working"
    assert "stuck" not in seat and "idle_minutes" not in seat
    assert "last_activity_at" not in seat["runs"][0]


def test_a_broken_liveness_field_is_ignored_not_trusted():
    for extra in (
        {"last_activity_at": "yesterday"},
        {"idle_minutes": -4},
        {"idle_minutes": True},
        {"stuck_children": "python"},
    ):
        seat = _seat(_view(_status([_run("t", **extra)])), "worker-1")
        assert "stuck" not in seat, extra


# ------------------------------------------------------------------ through the application


@pytest.fixture()
def owner():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    TeamStateRow.__table__.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.team_store = team_store.DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    client.team_store = app.state.team_store
    yield client
    engine.dispose()


def test_the_liveness_fields_round_trip_and_the_office_shows_the_stuck_seat(owner):
    now = team_store.utcnow()
    for name in ("live-task", "hung-task"):
        put = owner.put(
            f"/v1/team/queue/tasks/{name}", json={"task": _task(name), "expected_updated_at": None}
        )
        assert put.status_code == 200, put.text
    owner.team_store.acquire_lock(machine="MAIL", cycle_id="c1", pid=7)
    child = {"pid": 300, "name": "python.exe", "idle_minutes": 34}
    doc = _status(
        [
            _run("live-task", at=now, idle=0),
            _run("hung-task", at=now, idle=34, stuck_children=[child]),
        ],
        at=now,
        run_idle_minutes=30,
    )
    put = owner.put(STATUS, json=doc)
    assert put.status_code == 200, put.text
    assert owner.get(STATUS).json() == doc
    body = owner.get(OFFICE).json()
    assert _seat(body, "worker-1")["stuck"] is False
    hung = _seat(body, "worker-2")
    assert (hung["task_id"], hung["stuck"], hung["idle_minutes"]) == ("hung-task", True, 34)


def test_an_older_cycles_status_without_the_fields_is_still_accepted(owner):
    owner.team_store.acquire_lock(machine="MAIL", cycle_id="c1", pid=7)
    doc = _status([_run("old-task", at=team_store.utcnow())], at=team_store.utcnow())
    assert owner.put(STATUS, json=doc).status_code == 200
    assert "stuck" not in _seat(owner.get(OFFICE).json(), "worker-1")


def test_a_wrong_liveness_field_in_the_status_is_a_422(owner):
    now = team_store.utcnow()
    for extra in (
        {"idle_minutes": -1},
        {"idle_minutes": "34"},
        {"last_activity_at": "2026-10-04T14:03:00.000000000000000000000Z"},
        {"stuck_children": [{"pid": 1, "name": "x"}]},
    ):
        doc = _status([_run("t", at=now, **extra)], at=now)
        assert owner.put(STATUS, json=doc).status_code == 422, extra
    assert owner.put(STATUS, json=_status([], at=now, run_idle_minutes=0)).status_code == 422
