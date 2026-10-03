"""A held lock is alive while its holder shows life (ADR-0214 addendum, team-lock-heartbeat).

Measured 2026-10-02: the cycle d20261002 took the lock at 11:05:28 UTC and was still working at
18:11 UTC, writing its status every two minutes. From 17:05 UTC - six hours after
``acquired_at`` - the Ofis page showed "koşan ajan 0/6" and nobody working, and the lock route
would have handed the lock to the owner's other machine. The six hours were meant for a holder
that DIED; nothing ever moved ``acquired_at``. The cycle's status (``cycle_id``, ``machine``,
``pid``, ``updated_at``) is its heartbeat: when it is the lock holder's, the lock's age counts
from it. A holder that writes no status (the feeder, an integrate step) keeps the old rule.

The clock is passed in, or ``team_store.utcnow`` is frozen for the routes: nothing sleeps.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.team import approvals, office
from app.team import store as team_store
from app.team.models import TeamStateRow
from tests.identity_support import authenticate, install_identity

NOW = datetime(2026, 10, 2, 18, 11, 0, tzinfo=UTC)
HOURS = team_store.LOCK_STALE_HOURS
LOCK_ROUTE = "/v1/team/queue/lock"
STATUS = "/v1/team/queue/status"
OFFICE = "/v1/team/office"
ANSWER_KEYS = {"acquired", "kind", "holder", "since", "pid"}


def _lock(age: timedelta, *, machine: str = "MAIL", cycle_id: str = "d1", pid: int = 10) -> dict:
    return {
        "held": True,
        "machine": machine,
        "cycle_id": cycle_id,
        "pid": pid,
        "acquired_at": team_store.stamp(NOW - age),
    }


def _status(age: timedelta, *, runs: list[dict] | None = None, **who: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "cycle_id": "d1",
        "machine": "MAIL",
        "pid": 10,
        "started_at": team_store.stamp(NOW - timedelta(hours=7)),
        "runs": runs or [],
        "estimated_usd": 3.25,
        "usage_limit": {"state": "ok", "resets_at": None},
        "updated_at": team_store.stamp(NOW - age),
    }
    doc.update(who)
    return doc


def _decide(lock, status, machine="OFFICE", *, takeover_dead=False, at=NOW) -> dict[str, Any]:
    return team_store.lock_decision(lock, machine, at, takeover_dead=takeover_dead, status=status)


SEVEN_HOURS = timedelta(hours=7)
TWO_MINUTES = timedelta(minutes=2)
RUN = {"task": "busy-task", "role": "worker", "started_at": "2026-10-02T17:30:00Z"}


# ------------------------------------------------------------------ the store's functions


def test_the_incident_a_seven_hour_lock_with_a_two_minute_old_status_is_running():
    lock, status = _lock(SEVEN_HOURS), _status(TWO_MINUTES, runs=[RUN])
    assert team_store.lock_is_running(lock, NOW, status) is True
    view = office.office_view({"version": 1, "tasks": []}, lock, status, [], NOW)
    assert view["cycle"]["running"] is True
    assert view["cycle"]["running_agents"] == 1
    assert next(s for s in view["agents"] if s["seat"] == "worker-1")["task_id"] == "busy-task"


def test_the_takeover_another_machine_is_told_held_and_the_same_machine_another_pid_too():
    lock, status = _lock(SEVEN_HOURS), _status(TWO_MINUTES, runs=[RUN])
    other = _decide(lock, status, "OFFICE")
    assert other == {
        "acquired": False,
        "kind": "held",
        "holder": "MAIL",
        "since": lock["acquired_at"],
        "pid": 10,
    }
    same = _decide(lock, status, "MAIL")
    assert same["acquired"] is False and same["kind"] == "ours"


@pytest.mark.parametrize(
    ("silent_for", "kind", "acquired"),
    [
        (timedelta(hours=HOURS, minutes=1), "stale", True),
        (timedelta(hours=HOURS) - timedelta(minutes=1), "held", False),
    ],
)
def test_a_silent_holder_is_stale_six_hours_after_its_last_status(silent_for, kind, acquired):
    lock, status = _lock(SEVEN_HOURS), _status(silent_for)
    answer = _decide(lock, status)
    assert (answer["kind"], answer["acquired"]) == (kind, acquired)
    assert team_store.lock_is_running(lock, NOW, status) is (not acquired)


@pytest.mark.parametrize(
    "who", [{"cycle_id": "d2"}, {"machine": "OFFICE"}, {"pid": 11}], ids=["cycle", "machine", "pid"]
)
def test_the_status_of_another_cycle_machine_or_pid_does_not_keep_the_lock_alive(who):
    lock, status = _lock(SEVEN_HOURS), _status(TWO_MINUTES, runs=[RUN], **who)
    assert team_store.lock_is_running(lock, NOW, status) is False
    answer = _decide(lock, status)
    assert (answer["kind"], answer["acquired"]) == ("stale", True)


@pytest.mark.parametrize(
    ("age", "kind"),
    [(timedelta(hours=HOURS) - timedelta(minutes=1), "held"), (timedelta(hours=HOURS), "stale")],
)
def test_a_holder_with_no_status_keeps_the_six_hour_rule(age, kind):
    lock = _lock(age, machine="MAIL", cycle_id="feed-2026-10-02", pid=99)
    # No status at all, and the cycle's status that is not this holder's: the same rule.
    for status in (None, _status(TWO_MINUTES, runs=[RUN])):
        assert _decide(lock, status)["kind"] == kind
        assert team_store.lock_is_running(lock, NOW, status) is (kind == "held")


def test_the_future_skew_bound_is_the_office_pages_bound():
    assert team_store.STATUS_FUTURE_SKEW_MINUTES == office.STATUS_FUTURE_SKEW_MINUTES == 2


@pytest.mark.parametrize(
    "ahead", [timedelta(minutes=3), timedelta(days=5)], ids=["three-minutes", "five-days"]
)
def test_a_status_dated_beyond_the_skew_bound_does_not_keep_the_lock_alive(ahead):
    lock, status = _lock(SEVEN_HOURS), _status(-ahead)
    assert team_store.lock_is_running(lock, NOW, status) is False
    assert _decide(lock, status)["kind"] == "stale"


def test_a_status_a_little_ahead_of_the_clock_keeps_the_lock_alive():
    lock, status = _lock(SEVEN_HOURS), _status(-timedelta(minutes=1))
    assert _decide(lock, status)["kind"] == "held"


def test_a_status_older_than_the_lock_does_not_move_its_age():
    """A status left by the previous run of the same cycle id, machine and pid (pid reuse, or
    the cycle restarting itself): the lock taken an hour ago is aged from its own
    ``acquired_at``, not pulled back to the seven-hour-old status and handed away."""
    lock, status = _lock(timedelta(hours=1)), _status(SEVEN_HOURS)
    assert team_store.lock_is_running(lock, NOW, status) is True
    answer = _decide(lock, status)
    assert (answer["acquired"], answer["kind"], answer["holder"]) == (False, "held", "MAIL")


def test_takeover_dead_is_believed_from_the_holders_machine_only_whatever_the_status_says():
    lock, status = _lock(SEVEN_HOURS), _status(TWO_MINUTES, runs=[RUN])
    mine = _decide(lock, status, "mail", takeover_dead=True)
    assert (mine["acquired"], mine["kind"]) == (True, "dead")
    theirs = _decide(lock, status, "OFFICE", takeover_dead=True)
    assert (theirs["acquired"], theirs["kind"]) == (False, "held")


def test_without_a_status_argument_the_old_callers_keep_the_old_rule():
    assert team_store.lock_is_running(_lock(SEVEN_HOURS), NOW) is False
    assert team_store.lock_is_running(_lock(timedelta(hours=1)), NOW) is True


# ------------------------------------------------------------------ the real routes


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


class _Clock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.at = NOW
        monkeypatch.setattr(team_store, "utcnow", lambda: self.at)


@pytest.fixture(params=["file", "db"])
def owner(request, engine, tmp_path, monkeypatch):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    if request.param == "db":
        app.state.team_store = team_store.DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    else:
        app.state.team_store = _file_store(tmp_path)
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    client.clock = _Clock(monkeypatch)
    return client


def _acquire(owner, machine: str, *, pid: int = 10, cycle_id: str = "d1", dead: bool = False):
    body = {
        "action": "acquire",
        "machine": machine,
        "cycle_id": cycle_id,
        "pid": pid,
        "takeover_dead": dead,
    }
    answer = owner.post(LOCK_ROUTE, json=body)
    assert answer.status_code == 200, answer.text
    return answer.json()


def _cycle_running_for_seven_hours(owner, status_age: timedelta = TWO_MINUTES) -> None:
    owner.clock.at = NOW - SEVEN_HOURS
    assert _acquire(owner, "MAIL")["acquired"] is True
    owner.clock.at = NOW
    put = owner.put(STATUS, json=_status(status_age, runs=[RUN]))
    assert put.status_code == 200, put.text


def test_the_incident_through_the_routes_the_office_shows_the_run_on_its_seat(owner):
    _cycle_running_for_seven_hours(owner)
    body = owner.get(OFFICE).json()
    assert body["cycle"]["running"] is True
    assert body["cycle"]["running_agents"] == 1
    seat = next(s for s in body["agents"] if s["seat"] == "worker-1")
    assert (seat["state"], seat["task_id"]) == ("working", "busy-task")
    assert owner.get("/v1/team/approvals").json()["cycle_running"] is True


def test_the_takeover_through_the_lock_route_is_refused_while_the_cycle_shows_life(owner):
    _cycle_running_for_seven_hours(owner)
    other = _acquire(owner, "OFFICE", pid=4242, cycle_id="d2")
    assert set(other) == ANSWER_KEYS
    assert (other["acquired"], other["kind"], other["holder"]) == (False, "held", "MAIL")
    same = _acquire(owner, "MAIL", pid=11, cycle_id="feed-2026-10-02")
    assert (same["acquired"], same["kind"]) == (False, "ours")
    lock = owner.get(LOCK_ROUTE).json()
    assert (lock["machine"], lock["cycle_id"], lock["pid"]) == ("MAIL", "d1", 10)


def test_a_silent_cycle_is_taken_over_through_the_route(owner):
    _cycle_running_for_seven_hours(owner, status_age=timedelta(hours=HOURS, minutes=1))
    other = _acquire(owner, "OFFICE", pid=4242, cycle_id="d2")
    assert (other["acquired"], other["kind"]) == (True, "stale")
    assert owner.get(LOCK_ROUTE).json()["machine"] == "OFFICE"
    assert owner.get(OFFICE).json()["cycle"]["running"] is False


def test_a_young_lock_beside_an_older_status_of_its_holder_is_held_through_the_route(owner):
    """The status the previous run of (MAIL, d1, pid 10) left seven hours ago does not age the
    lock the same holder took an hour ago: another machine is told held."""
    owner.clock.at = NOW
    put = owner.put(STATUS, json=_status(SEVEN_HOURS))
    assert put.status_code == 200, put.text
    owner.clock.at = NOW - timedelta(hours=1)
    assert _acquire(owner, "MAIL")["acquired"] is True
    owner.clock.at = NOW
    other = _acquire(owner, "OFFICE", pid=4242, cycle_id="d2")
    assert set(other) == ANSWER_KEYS
    assert (other["acquired"], other["kind"], other["holder"]) == (False, "held", "MAIL")
    assert owner.get(LOCK_ROUTE).json()["machine"] == "MAIL"


def test_takeover_dead_from_the_holders_machine_still_takes_its_own_lock(owner):
    _cycle_running_for_seven_hours(owner)
    other = _acquire(owner, "OFFICE", pid=4242, cycle_id="d2", dead=True)
    assert (other["acquired"], other["kind"]) == (False, "held")
    mine = _acquire(owner, "MAIL", pid=11, cycle_id="d1", dead=True)
    assert set(mine) == ANSWER_KEYS
    assert (mine["acquired"], mine["kind"]) == (True, "dead")


def test_release_is_unchanged_and_answers_the_same_keys(owner):
    _cycle_running_for_seven_hours(owner)
    refused = owner.post(LOCK_ROUTE, json={"action": "release", "machine": "OFFICE"})
    assert refused.status_code == 409
    released = owner.post(LOCK_ROUTE, json={"action": "release", "machine": "MAIL"})
    assert released.status_code == 200 and released.json() == {"released": True}
    assert owner.get(LOCK_ROUTE).json() == {"held": False}
    free = _acquire(owner, "OFFICE", pid=4242, cycle_id="d2")
    assert set(free) == ANSWER_KEYS and (free["acquired"], free["kind"]) == (True, "free")


# ------------------------------------------------------------------ the decision route


@pytest.fixture()
def decider(tmp_path, monkeypatch):
    """The real application on the database store with a ledger, as the Cloud Core runs it."""
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(eng)
    TeamStateRow.__table__.create(eng)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = eng
    artifacts._session_factory = sessionmaker(bind=eng, expire_on_commit=False)
    app.state.artifacts = artifacts
    app.state.team_root = tmp_path / "team"
    store = team_store.DbStore(sessionmaker(bind=eng, expire_on_commit=False))
    app.state.team_store = store
    store.put_task(
        {
            "id": "fikir-a",
            "title": "Fikir a",
            "roadmap_row": "row",
            "state": "awaiting_owner",
            "area": [],
            "branch": "",
            "worktree": "",
            "assignee": "",
            "reports": [],
            "budget": {"max_usd": 5},
            "created_at": "2026-10-01T00:00:00Z",
            "updated_at": "2026-10-01T00:00:00Z",
            "proposal": "Kısa öneri",
        },
        None,
    )
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    client.clock = _Clock(monkeypatch)
    monkeypatch.setattr(approvals, "_now", lambda: client.clock.at)
    yield client
    eng.dispose()


def test_a_decision_during_a_seven_hour_cycle_that_shows_life_says_the_cycle_runs(decider):
    """The third reader of the lock (approvals.decide): past six hours the decision answered
    cycle_running false - "bir sonraki döngüde uygulanır" - while the cycle ran."""
    _cycle_running_for_seven_hours(decider)
    assert decider.get("/v1/team/approvals").json()["cycle_running"] is True
    answer = decider.post(
        "/v1/team/approvals/decision", json={"task_id": "fikir-a", "decision": "approve"}
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["cycle_running"] is True
