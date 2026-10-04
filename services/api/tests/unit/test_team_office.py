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

REPO_ROOT = Path(__file__).resolve().parents[4]

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
SEATS = [
    "lead",
    "researcher",
    "integrator",
    "worker-1",
    "worker-2",
    "worker-3",
    "worker-4",
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
        # Stale: seven hours old and its holder (pid 8) is not the status' writer. With the
        # writer's own pid this was the 2026-10-02 incident, and it is running now (the last case).
        {**LOCK, "pid": 8, "acquired_at": "2026-10-01T05:00:00Z"},
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


def _workers(view):
    return [s for s in view["agents"] if s["role"] == "worker"]


def _order(view):
    """The seat ids, with the worker block checked and folded into one name."""
    seats = [s["seat"] for s in view["agents"]]
    workers = [s for s in seats if s.startswith("worker-")]
    assert workers == [f"worker-{n}" for n in range(1, len(workers) + 1)]
    first = seats.index("worker-1")
    assert seats[first : first + len(workers)] == workers  # one unbroken block
    return seats[:first] + ["workers"] + seats[first + len(workers) :]


ORDER = ["lead", "researcher", "integrator", "workers", "inspector", "owner"]


def test_with_no_cycle_there_are_exactly_four_worker_seats_all_waiting():
    view = _view([], None, lock=None)
    assert [s["seat"] for s in _workers(view)] == ["worker-1", "worker-2", "worker-3", "worker-4"]
    assert {s["state"] for s in _workers(view)} == {"waiting"}
    assert all(s["runs"] == [] for s in view["agents"])
    assert _order(view) == ORDER
    assert (view["cycle"]["running_agents"], view["cycle"]["capacity"]) == (0, 6)


def test_four_live_worker_runs_are_four_working_seats_with_their_tasks():
    names = ["a-task", "b-task", "c-task", "d-task"]
    view = _view([_task(n) for n in names], _status([(n, "worker") for n in names]))
    assert [(s["seat"], s["state"], s["task_id"]) for s in _workers(view)] == [
        (f"worker-{i + 1}", "working", n) for i, n in enumerate(names)
    ]
    fourth = _seat(view, "worker-4")
    assert fourth["task_title"] == "Başlık d-task"
    assert fourth["runs"] == [
        {"task_id": "d-task", "task_title": "Başlık d-task", "since": fourth["since"]}
    ]
    assert view["cycle"]["running_agents"] == 4
    assert _order(view) == ORDER


@pytest.mark.parametrize(("live", "seats"), [(0, 4), (3, 4), (4, 4), (5, 5), (6, 6)])
def test_the_worker_seats_are_four_or_as_many_as_the_live_worker_runs(live, seats):
    names = [f"task-{n}" for n in range(live)]
    view = _view([_task(n) for n in names], _status([(n, "worker") for n in names]))
    assert len(_workers(view)) == seats
    assert [s["state"] for s in _workers(view)] == ["working"] * live + ["waiting"] * (seats - live)
    assert _order(view) == ORDER
    assert len({s["seat"] for s in view["agents"]}) == len(view["agents"])


def test_one_inspector_run_and_four_worker_runs_are_five_running_of_six():
    names = ["a-task", "b-task", "c-task", "d-task"]
    runs = [(n, "worker") for n in names] + [("e-task", "inspector")]
    view = _view([_task(n) for n in [*names, "e-task"]], _status(runs))
    assert (view["cycle"]["running_agents"], view["cycle"]["capacity"]) == (5, 6)
    assert _seat(view, "inspector")["task_id"] == "e-task"
    assert _seat(view, "worker-4")["task_id"] == "d-task"
    assert _order(view) == ORDER


def test_three_live_inspector_runs_are_one_working_seat_listing_all_three_in_start_order():
    status = _status([("a-task", "inspector"), ("b-task", "inspector"), ("c-task", "inspector")])
    status["runs"].reverse()  # the store's order is not the start order
    view = _view([_task(n, "inspecting") for n in ("a-task", "b-task", "c-task")], status)
    seat = _seat(view, "inspector")
    assert seat["state"] == "working"
    assert [r["task_id"] for r in seat["runs"]] == ["a-task", "b-task", "c-task"]
    assert [r["task_title"] for r in seat["runs"]] == [f"Başlık {n}-task" for n in "abc"]
    assert [r["since"] for r in seat["runs"]] == sorted(r["since"] for r in seat["runs"])
    assert len({r["since"] for r in seat["runs"]}) == 3
    # an old-shape reader's fields are the first run's
    assert (seat["task_id"], seat["task_title"], seat["since"]) == (
        "a-task",
        "Başlık a-task",
        seat["runs"][0]["since"],
    )
    assert set(seat["runs"][0]) == {"task_id", "task_title", "since"}
    assert view["cycle"]["running_agents"] == 3
    assert view["cycle"]["capacity"] == 6
    assert {s["state"] for s in _workers(view)} == {"waiting"}
    assert _order(view) == ORDER


@pytest.mark.parametrize("role", ["lead", "researcher", "integrator"])
def test_every_live_run_of_a_single_seat_role_is_listed_on_its_seat(role):
    view = _view([_task("a-task"), _task("b-task")], _status([("a-task", role), ("b-task", role)]))
    assert [r["task_id"] for r in _seat(view, role)["runs"]] == ["a-task", "b-task"]
    assert view["cycle"]["running_agents"] == 2


def test_seven_live_runs_raise_the_capacity_to_seven():
    names = [f"task-{n}" for n in range(5)]
    runs = [(n, "worker") for n in names] + [("x-task", "inspector"), ("y-task", "inspector")]
    view = _view([_task(n) for n in [*names, "x-task", "y-task"]], _status(runs))
    assert (view["cycle"]["running_agents"], view["cycle"]["capacity"]) == (7, 7)
    assert len(_workers(view)) == 5
    assert _order(view) == ORDER


def test_a_run_whose_task_left_the_queue_is_still_a_run_with_its_id():
    view = _view([], _status([("gone-task", "worker"), ("gone-too", "inspector")]))
    assert _seat(view, "worker-1")["runs"] == [
        {"task_id": "gone-task", "task_title": None, "since": _seat(view, "worker-1")["since"]}
    ]
    assert view["cycle"]["running_agents"] == 2


def test_a_stale_status_with_six_worker_runs_is_four_waiting_seats_and_nothing_running():
    names = [f"task-{n}" for n in range(6)]
    old = _status([(n, "worker") for n in names], at=NOW - timedelta(minutes=11))
    view = _view([_task(n) for n in names], old)
    assert len(_workers(view)) == 4
    assert (view["cycle"]["running_agents"], view["cycle"]["capacity"]) == (0, 6)
    assert all(s["runs"] == [] for s in view["agents"])


def test_returned_worker_tasks_fill_the_free_seats_up_to_the_fourth():
    stamps = ["2026-10-01T07:00:00Z", "2026-10-01T08:00:00Z", "2026-10-01T09:00:00Z"]
    tasks = [_task("run-task")] + [
        _task(f"old-{i}", "returned", reports=[_report("worker", at)])
        for i, at in enumerate(stamps)
    ]
    view = _view(tasks, _status([("run-task", "worker")]))
    assert [(s["state"], s["task_id"]) for s in _workers(view)] == [
        ("working", "run-task"),
        ("returned", "old-2"),
        ("returned", "old-1"),
        ("returned", "old-0"),
    ]
    assert _seat(view, "worker-4")["runs"] == []
    assert view["cycle"]["running_agents"] == 1


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


def test_the_office_route_shows_every_run_of_a_cycle_wider_than_its_seats(owner):
    owner.team_store.acquire_lock(machine="MAIL", cycle_id="c1", pid=7)
    runs = [(f"task-{n}", "worker") for n in range(5)] + [("x-task", "inspector")] * 2
    assert owner.put(STATUS, json=_live_status(runs)).status_code == 200
    body = owner.get(OFFICE).json()
    workers = [s for s in body["agents"] if s["role"] == "worker"]
    assert [(s["seat"], s["task_id"]) for s in workers] == [
        (f"worker-{n + 1}", f"task-{n}") for n in range(5)
    ]
    assert [r["task_id"] for r in _seat(body, "inspector")["runs"]] == ["x-task", "x-task"]
    assert (body["cycle"]["running_agents"], body["cycle"]["capacity"]) == (7, 7)
    assert [s["seat"] for s in body["agents"]][-2:] == ["inspector", "owner"]


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
    assert [s["state"] for s in body["agents"]] == ["waiting"] * 9
    assert body["tasks"] == {}


def test_the_file_store_keeps_the_status_beside_the_queue(tmp_path):
    store = _file_store(tmp_path)
    assert store.read_status() is None
    store.put_status({"cycle_id": "c1"})
    on_disk = json.loads((store.root / "status.json").read_text(encoding="utf-8"))
    assert on_disk == {"cycle_id": "c1"}
    assert store.read_status() == {"cycle_id": "c1"}


def test_a_returned_worker_task_is_shown_while_another_worker_runs():
    tasks = [
        _task("run-task"),
        _task("old-task", "returned", reports=[_report("worker")]),
    ]
    view = _view(tasks, _status([("run-task", "worker")]))
    assert (_seat(view, "worker-1")["state"], _seat(view, "worker-1")["task_id"]) == (
        "working",
        "run-task",
    )
    w2 = _seat(view, "worker-2")
    assert (w2["state"], w2["task_id"]) == ("returned", "old-task")
    assert _seat(view, "worker-3")["state"] == "waiting"


def test_two_returned_tasks_fill_the_free_seats_newest_first_around_a_running_one():
    tasks = [
        _task("run-task"),
        _task("old-a", "returned", reports=[_report("worker", "2026-10-01T09:00:00Z")]),
        _task("old-b", "stopped", reports=[_report("worker", "2026-10-01T10:00:00Z")]),
    ]
    view = _view(tasks, _status([("run-task", "worker")]))
    assert [_seat(view, f"worker-{n}")["task_id"] for n in (1, 2, 3)] == [
        "run-task",
        "old-b",
        "old-a",
    ]


@pytest.mark.parametrize(
    "updated_at",
    [
        "2026-10-01T12:00:00",  # no timezone: not the contract's UTC 'Z' form
        "2026-10-01T15:00:00+03:00",  # an offset is not accepted either
        "2026-10-01T12:30:00Z",  # 30 minutes in the future: clock skew, not liveness
    ],
)
def test_an_updated_at_that_is_not_utc_z_or_is_far_in_the_future_is_not_live(updated_at):
    status = _status([("alpha-task", "worker")], updated_at=updated_at)
    view = _view([_task("alpha-task")], status)
    assert view["cycle"]["running"] is False
    assert _seat(view, "worker-1")["state"] != "working"


def test_an_updated_at_a_little_ahead_of_the_clock_is_still_live():
    status = _status(
        [("alpha-task", "worker")], updated_at=team_store.stamp(NOW + timedelta(seconds=30))
    )
    assert _view([_task("alpha-task")], status)["cycle"]["running"] is True


def test_the_status_names_the_claude_account_and_the_office_shows_it(owner):
    """The owner switches the team between Claude accounts (2026-10-04) and wants to SEE which
    one is working on the Ofis. The cycle sends the account's folder name, never an e-mail."""
    owner.team_store.acquire_lock(machine="MAIL", cycle_id="c1", pid=7)
    doc = {**_live_status([("alpha-task", "worker")]), "account": ".claude-hesap3"}
    assert owner.put(STATUS, json=doc).status_code == 200
    assert owner.get(OFFICE).json()["cycle"]["account"] == ".claude-hesap3"
    older = _live_status([("alpha-task", "worker")])
    assert owner.put(STATUS, json=older).status_code == 200, (
        "a cycle without the field still writes"
    )
    assert owner.get(OFFICE).json()["cycle"]["account"] is None
    for bad in ("someone@example.com", "x" * 65, "a b", ""):
        assert owner.put(STATUS, json={**older, "account": bad}).status_code == 422, bad


def test_the_cycle_script_writes_the_account_it_runs_under():
    """The other half of the contract above: the cycle's status document carries the account
    the team wrapper chose (CLAUDE_CONFIG_DIR's folder name), or 'varsayilan'."""
    script = (REPO_ROOT / "scripts" / "team" / "cycle.ps1").read_text(encoding="utf-8")
    body = script[
        script.index("function New-CycleStatus") : script.index("function Write-CycleStatus")
    ]
    assert '$document["account"]' in body
    assert "CLAUDE_CONFIG_DIR" in body


def test_a_cycle_seven_hours_old_with_a_two_minute_old_status_is_running_with_its_run():
    """2026-10-02: from six hours after ``acquired_at`` the page showed 'koşan ajan 0/6' for a
    cycle that wrote its status every two minutes. Its status is its heartbeat."""
    lock = {**LOCK, "acquired_at": team_store.stamp(NOW - timedelta(hours=7))}
    status = _status(
        [("alpha-task", "worker")], updated_at=team_store.stamp(NOW - timedelta(minutes=2))
    )
    view = _view([_task("alpha-task")], status, lock=lock)
    assert view["cycle"]["running"] is True
    assert view["cycle"]["running_agents"] == 1
    assert (_seat(view, "worker-1")["state"], _seat(view, "worker-1")["task_id"]) == (
        "working",
        "alpha-task",
    )
