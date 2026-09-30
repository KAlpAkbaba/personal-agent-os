"""The team's queue and lock on the Cloud Core (ADR-0214 addendum, pilot-02).

The queue and the lock live where either machine can reach them: one row per task, one lock
row, in ``team_state``. ``FileStore`` is the same contract over ``team/queue.json`` and
``team/lock.json`` (the home PC without the API keeps working), so every rule below runs on
both stores. ``scripts/lib/TeamQueue.ps1`` holds the same lock rules; a test reads its source.
"""

from __future__ import annotations

import copy
import json
import re
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
from app.team import store as team_store
from app.team.models import TeamStateRow
from tests.identity_support import authenticate, install_identity
from tests.unit import test_team_queue_schema as schema_test

REPO = Path(__file__).resolve().parents[4]
QUEUE_API = "/v1/team/queue"
NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def _task(task_id: str = "task-one", state: str = "approved", **extra: Any) -> dict[str, Any]:
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
        "created_at": "2026-09-30T00:00:00Z",
        "updated_at": "2026-09-30T00:00:00Z",
    }
    task.update(extra)
    return task


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    TeamStateRow.__table__.create(eng)
    yield eng
    eng.dispose()


def _db_store(engine) -> team_store.DbStore:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return team_store.DbStore(factory)


@pytest.fixture(params=["file", "db"])
def store(request, tmp_path: Path, engine) -> team_store.TeamStore:
    if request.param == "db":
        return _db_store(engine)
    root = tmp_path / "team"
    (root / "reports").mkdir(parents=True)
    (root / "queue.json").write_text('{"version": 1, "tasks": []}\n', encoding="utf-8")
    (root / "lock.json").write_text('{"held": false}\n', encoding="utf-8")
    return team_store.FileStore(root)


# ------------------------------------------------------------------ the queue


def test_a_valid_queue_round_trips_through_the_store(store):
    store.put_task(_task("task-one"), None)
    store.put_task(_task("task-two", "awaiting_owner", proposal="team/proposals/x.md"), None)
    queue = store.read_queue()
    assert queue["version"] == 1
    assert {t["id"]: t["state"] for t in queue["tasks"]} == {
        "task-one": "approved",
        "task-two": "awaiting_owner",
    }
    again = next(t for t in queue["tasks"] if t["id"] == "task-two")
    assert again["proposal"] == "team/proposals/x.md"
    assert again["title"] == "Görev task-two"


@pytest.mark.parametrize(
    "broken",
    [
        {"state": "finished"},
        {"id": "Bad-Id"},
        {"unknown_field": 1},
        {"title": ""},
        {"budget": {"max_usd": -1}},
        {"updated_at": "yesterday"},
    ],
)
def test_a_task_that_breaks_the_schema_is_refused_and_nothing_is_written(store, broken):
    with pytest.raises(team_store.Invalid) as refused:
        store.put_task(_task(**broken), None)
    assert refused.value.problems
    assert store.read_queue()["tasks"] == []


def test_a_task_missing_one_required_field_is_refused_and_the_same_task_with_it_is_kept(store):
    whole = _task()
    partial = {k: v for k, v in whole.items() if k != "assignee"}
    with pytest.raises(team_store.Invalid):
        store.put_task(partial, None)
    store.put_task(whole, None)
    assert [t["id"] for t in store.read_queue()["tasks"]] == ["task-one"]


def test_the_store_and_the_schema_agree_on_what_a_valid_task_is(store):
    schema = json.loads((REPO / "team" / "queue.schema.json").read_text(encoding="utf-8"))
    cases = [
        _task(),
        _task(state="finished"),
        _task(unknown_field=1),
        _task(sha="a" * 40),
        _task(sha="a" * 39),
        _task(returns=-1),
        _task(release_approved_by="voice"),
        _task(release_approved_by="nobody"),
        _task(budget={"max_usd": 0}),
        _task(budget={}),
    ]
    for case in cases:
        by_schema = not schema_test.problems(case, schema["$defs"]["task"], schema)
        assert team_store.task_problems(case) == [] or not by_schema, case
        assert (team_store.task_problems(case) == []) == by_schema, case


def test_a_write_that_saw_the_current_version_lands_and_one_that_did_not_is_stale(store):
    store.put_task(_task(), None)
    changed = _task(state="assigned", updated_at="2026-09-30T01:00:00Z")
    store.put_task(changed, "2026-09-30T00:00:00Z")
    assert store.read_queue()["tasks"][0]["state"] == "assigned"
    older = _task(state="stopped", updated_at="2026-09-30T02:00:00Z")
    with pytest.raises(team_store.Stale):
        store.put_task(older, "2026-09-30T00:00:00Z")
    assert store.read_queue()["tasks"][0]["state"] == "assigned"


def test_creating_a_task_that_exists_and_updating_one_that_does_not_are_both_stale(store):
    store.put_task(_task(), None)
    with pytest.raises(team_store.Stale):
        store.put_task(_task(state="stopped"), None)
    with pytest.raises(team_store.Stale):
        store.put_task(_task("ghost-task"), "2026-09-30T00:00:00Z")
    assert [t["id"] for t in store.read_queue()["tasks"]] == ["task-one"]


# ------------------------------------------------------------------ the lock


def _acquire(store, machine="MAIL", *, at=NOW, cycle="c1", pid=10, takeover_dead=False):
    return store.acquire_lock(
        machine=machine, cycle_id=cycle, pid=pid, takeover_dead=takeover_dead, now=at
    )


def test_a_free_lock_is_taken_and_records_machine_pid_and_time(store):
    result = _acquire(store, pid=4242)
    assert (result["acquired"], result["kind"]) == (True, "free")
    lock = store.read_lock()
    assert lock["held"] is True
    assert (lock["machine"], lock["pid"], lock["cycle_id"]) == ("MAIL", 4242, "c1")
    assert lock["acquired_at"] == "2026-09-30T12:00:00Z"


def test_the_other_machines_fresh_lock_stops_us_and_is_left_untouched(store):
    _acquire(store, "OFFICE")
    result = _acquire(store, "MAIL", at=NOW + timedelta(minutes=5))
    assert (result["acquired"], result["kind"], result["holder"]) == (False, "held", "OFFICE")
    assert store.read_lock()["machine"] == "OFFICE"


def test_a_lock_is_stale_at_six_hours_and_not_a_minute_before(store):
    _acquire(store, "OFFICE")
    early = _acquire(store, "MAIL", at=NOW + timedelta(hours=5, minutes=59))
    assert (early["acquired"], early["kind"]) == (False, "held")
    late = _acquire(store, "MAIL", at=NOW + timedelta(hours=6))
    assert (late["acquired"], late["kind"], late["holder"]) == (True, "stale", "OFFICE")
    assert store.read_lock()["machine"] == "MAIL"


def test_our_own_fresh_lock_is_ours_in_any_letter_case_and_is_taken_over_only_when_it_is_dead(
    store,
):
    _acquire(store, "MAIL", pid=1)
    alive = _acquire(store, "mail", pid=2, at=NOW + timedelta(minutes=1))
    assert (alive["acquired"], alive["kind"], alive["pid"]) == (False, "ours", 1)
    dead = _acquire(store, "mail", pid=2, at=NOW + timedelta(minutes=1), takeover_dead=True)
    assert (dead["acquired"], dead["kind"]) == (True, "dead")
    assert store.read_lock()["pid"] == 2


def test_a_dead_holder_claim_is_not_believed_for_the_other_machines_lock(store):
    _acquire(store, "OFFICE")
    result = _acquire(store, "MAIL", at=NOW + timedelta(minutes=1), takeover_dead=True)
    assert (result["acquired"], result["kind"]) == (False, "held")


def test_only_the_holder_releases_and_a_release_frees_the_lock(store):
    _acquire(store, "MAIL")
    with pytest.raises(team_store.Stale):
        store.release_lock(machine="OFFICE", cycle_id="c1", now=NOW)
    assert store.read_lock()["held"] is True
    store.release_lock(machine="MAIL", cycle_id="c1", now=NOW)
    assert store.read_lock()["held"] is False
    assert _acquire(store, "OFFICE")["acquired"] is True


def test_a_lock_that_does_not_say_when_it_was_taken_is_running_and_a_released_one_is_not():
    assert team_store.lock_is_running({"held": False}, NOW) is False
    assert team_store.lock_is_running(None, NOW) is False
    assert team_store.lock_is_running({"held": True}, NOW) is True
    fresh = {"held": True, "acquired_at": "2026-09-30T11:00:00Z"}
    assert team_store.lock_is_running(fresh, NOW) is True
    old = {"held": True, "acquired_at": "2026-09-30T05:59:59Z"}
    assert team_store.lock_is_running(old, NOW) is False


def test_the_staleness_constant_is_the_one_the_powershell_script_holds():
    source = (REPO / "scripts" / "lib" / "TeamQueue.ps1").read_text(encoding="utf-8")
    match = re.search(r"\$script:TeamLockStaleHours\s*=\s*(\d+)", source)
    assert match, "TeamQueue.ps1 lost its staleness constant"
    assert int(match.group(1)) == team_store.LOCK_STALE_HOURS


# ------------------------------------------------------------------ reports


def test_a_report_is_kept_as_text_and_the_newest_is_the_one_shown(store):
    assert store.newest_report() is None
    store.put_report("pilot-01.md", "# eski\n", now=NOW)
    store.put_report("pilot-02.md", "# yeni\nTürkçe: ığüşöç\n", now=NOW + timedelta(hours=1))
    newest = store.newest_report()
    assert newest["file"] == "pilot-02.md"
    assert "ığüşöç" in newest["text"]


def test_a_report_name_that_leaves_the_folder_is_refused(store):
    store.put_report("pilot-03.md", "ok\n", now=NOW)
    for bad in ("../x.md", "a/b.md", "x.txt", ""):
        with pytest.raises(team_store.Invalid):
            store.put_report(bad, "no\n", now=NOW)


# ------------------------------------------------------------------ the HTTP surface


@pytest.fixture()
def api(engine, tmp_path: Path):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.team_store = _db_store(engine)
    client = TestClient(app)
    return app, client, settings


@pytest.fixture()
def owner(api) -> TestClient:
    app, client, settings = api
    authenticate(app, client, settings=settings)
    return client


def test_the_queue_api_is_closed_to_anyone_without_an_owner_session(api):
    _, client, _ = api
    assert client.get(QUEUE_API).status_code == 401
    assert client.put(f"{QUEUE_API}/tasks/task-one", json={"task": _task()}).status_code == 401
    assert client.post(f"{QUEUE_API}/lock", json={"action": "release"}).status_code == 401


def test_the_whole_queue_is_read_with_one_get_and_a_task_is_written_with_one_put(owner):
    put = owner.put(
        f"{QUEUE_API}/tasks/task-one", json={"task": _task(), "expected_updated_at": None}
    )
    assert put.status_code == 200, put.text
    body = owner.get(QUEUE_API).json()
    assert body["version"] == 1
    assert [t["id"] for t in body["tasks"]] == ["task-one"]


def test_a_stale_put_is_a_409_and_the_fresh_put_beside_it_is_a_200(owner):
    owner.put(f"{QUEUE_API}/tasks/task-one", json={"task": _task(), "expected_updated_at": None})
    changed = _task(state="assigned", updated_at="2026-09-30T01:00:00Z")
    ok = owner.put(
        f"{QUEUE_API}/tasks/task-one",
        json={"task": changed, "expected_updated_at": "2026-09-30T00:00:00Z"},
    )
    assert ok.status_code == 200
    stale = owner.put(
        f"{QUEUE_API}/tasks/task-one",
        json={"task": _task(state="stopped"), "expected_updated_at": "2026-09-30T00:00:00Z"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_write"
    assert owner.get(QUEUE_API).json()["tasks"][0]["state"] == "assigned"


def test_an_invalid_task_is_a_422_with_its_problems_and_a_path_that_names_another_task_too(owner):
    bad = owner.put(
        f"{QUEUE_API}/tasks/task-one",
        json={"task": _task(state="finished"), "expected_updated_at": None},
    )
    assert bad.status_code == 422
    assert "problems" in bad.json()["detail"]
    other = owner.put(
        f"{QUEUE_API}/tasks/task-two", json={"task": _task("task-one"), "expected_updated_at": None}
    )
    assert other.status_code == 422
    assert owner.get(QUEUE_API).json()["tasks"] == []


def test_the_lock_is_acquired_and_released_through_the_api_and_the_other_machine_is_told(owner):
    first = owner.post(
        f"{QUEUE_API}/lock",
        json={"action": "acquire", "machine": "MAIL", "cycle_id": "c1", "pid": 7},
    )
    assert first.status_code == 200 and first.json()["acquired"] is True
    other = owner.post(
        f"{QUEUE_API}/lock",
        json={"action": "acquire", "machine": "OFFICE", "cycle_id": "c2", "pid": 8},
    )
    assert other.status_code == 200
    assert (other.json()["acquired"], other.json()["holder"]) == (False, "MAIL")
    assert owner.get(f"{QUEUE_API}/lock").json()["machine"] == "MAIL"
    wrong = owner.post(
        f"{QUEUE_API}/lock", json={"action": "release", "machine": "OFFICE", "cycle_id": "c2"}
    )
    assert wrong.status_code == 409
    right = owner.post(
        f"{QUEUE_API}/lock", json={"action": "release", "machine": "MAIL", "cycle_id": "c1"}
    )
    assert right.status_code == 200
    assert owner.get(f"{QUEUE_API}/lock").json()["held"] is False


def test_a_report_posted_as_text_is_shown_by_the_onay_merkezi_listing(owner):
    posted = owner.post(
        f"{QUEUE_API}/reports", json={"name": "pilot-02.md", "text": "# Döngü raporu\n"}
    )
    assert posted.status_code == 200
    listing = owner.get("/v1/team/approvals").json()
    assert listing["cycle_report"]["file"] == "pilot-02.md"
    bad = owner.post(f"{QUEUE_API}/reports", json={"name": "../evil.md", "text": "x"})
    assert bad.status_code == 422


def test_a_copy_of_a_task_is_not_shared_with_the_store(store):
    original = _task()
    store.put_task(original, None)
    original["state"] = "stopped"
    assert store.read_queue()["tasks"][0]["state"] == "approved"
    read = store.read_queue()
    read["tasks"][0]["state"] = "stopped"
    assert copy.deepcopy(store.read_queue())["tasks"][0]["state"] == "approved"


def test_the_schema_the_store_validates_with_is_the_schema_the_protocol_holds():
    served = team_store.SCHEMA_PATH.read_bytes()
    protocol = (REPO / "team" / "queue.schema.json").read_bytes()
    assert served == protocol, "copy team/queue.schema.json over app/team/queue.schema.json"


def test_every_route_and_body_field_the_powershell_client_uses_is_one_the_server_has():
    """The two halves read each other: TeamQueue.ps1's client against routes.py's surface."""
    from app.team import routes

    source = (REPO / "scripts" / "lib" / "TeamQueue.ps1").read_text(encoding="utf-8")
    # The app wraps an included router, so the router's own routes are read; the HTTP tests
    # above are what prove the app includes it.
    served = {
        (method, route.path)
        for route in routes.router.routes
        if route.path.startswith(QUEUE_API)
        for method in route.methods
    }
    used = set()
    for method, path in re.findall(
        r'-Method "(GET|PUT|POST)" -Path "(/v1/team/queue[^"]*)"', source
    ):
        used.add((method, re.sub(r"/tasks/\$id$", "/tasks/{task_id}", path)))
    assert used, "the client's calls were not found: did the call shape change?"
    assert used <= served, f"the client calls what the server does not serve: {used - served}"
    assert len(used) == len(served), f"a served route the client never calls: {served - used}"

    fields = {
        "expected_updated_at": routes.PutTaskRequest,
        "takeover_dead": routes.LockRequest,
        "cycle_id": routes.LockRequest,
        "machine": routes.LockRequest,
        "pid": routes.LockRequest,
        "action": routes.LockRequest,
        "name": routes.ReportRequest,
        "text": routes.ReportRequest,
    }
    for name, model in fields.items():
        assert name in source, f"the client no longer sends {name}"
        assert name in model.model_fields, f"the server does not take {name}"
    assert "Authorization" in source and "Bearer" in source
