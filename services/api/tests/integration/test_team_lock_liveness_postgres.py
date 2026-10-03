"""A held lock is alive while its holder writes its status - on the REAL database.

2026-10-02: the cycle d20261002 took the lock at 11:05:28 UTC and still worked at 18:11 UTC;
from 17:05 the Ofis page showed 'koşan ajan 0/6' and the lock route would have handed the lock
to the owner's other machine. The rows here are written by the routes themselves; only the
lock's ``acquired_at`` is moved seven hours back, by REPLACING the row's document through a
fresh session (an in-place mutation of a JSON column is never written), and then everything is
read back through the routes. The rows are removed by the test.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.main import create_app
from app.team.models import TeamStateRow
from app.team.store import DbStore, stamp
from tests.integration.conftest import attach_owner

pytestmark = pytest.mark.integration

LOCK_ROUTE = "/v1/team/queue/lock"
STATUS = "/v1/team/queue/status"
OFFICE = "/v1/team/office"
ANSWER_KEYS = {"acquired", "kind", "holder", "since", "pid"}


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(Settings().database_url)
    made = build_session_factory(engine)

    def clear() -> None:
        with made() as session:
            session.execute(delete(TeamStateRow).where(TeamStateRow.kind.in_(("lock", "status"))))
            session.commit()

    clear()
    try:
        yield made
    finally:
        clear()
        engine.dispose()


@pytest.fixture()
def owner(factory, tmp_path) -> TestClient:
    settings = Settings()
    app = create_app(settings)
    app.state.team_store = DbStore(factory)
    app.state.team_root = tmp_path / "team"
    client = TestClient(app)
    attach_owner(app, client, settings)
    return client


def _acquire(owner: TestClient, machine: str, cycle_id: str, pid: int) -> dict:
    body = {"action": "acquire", "machine": machine, "cycle_id": cycle_id, "pid": pid}
    answer = owner.post(LOCK_ROUTE, json={**body, "takeover_dead": False})
    assert answer.status_code == 200, answer.text
    return answer.json()


def _age_the_lock(factory: sessionmaker[Session], hours: int) -> str:
    """Replace the lock row's document with one taken ``hours`` ago, through a fresh session."""
    with factory() as session:
        row = session.get(TeamStateRow, ("lock", "lock"))
        assert row is not None
        aged = {**row.doc, "acquired_at": stamp(datetime.now(UTC) - timedelta(hours=hours))}
        row.doc = aged
        session.commit()
    with factory() as session:
        assert session.get(TeamStateRow, ("lock", "lock")).doc == aged
    return aged["acquired_at"]


def _cycle_seven_hours_old_writing_its_status(owner: TestClient, factory) -> str:
    assert _acquire(owner, "MAIL", "d1", 10)["acquired"] is True
    now = datetime.now(UTC)
    status = {
        "cycle_id": "d1",
        "machine": "MAIL",
        "pid": 10,
        "started_at": stamp(now - timedelta(hours=7)),
        "runs": [{"task": "busy-task", "role": "worker", "started_at": stamp(now)}],
        "estimated_usd": 3.25,
        "usage_limit": {"state": "ok", "resets_at": None},
        "updated_at": stamp(now - timedelta(minutes=2)),
    }
    put = owner.put(STATUS, json=status)
    assert put.status_code == 200, put.text
    return _age_the_lock(factory, 7)


def test_the_incident_on_postgres_the_office_shows_the_run(owner, factory) -> None:
    _cycle_seven_hours_old_writing_its_status(owner, factory)
    body = owner.get(OFFICE).json()
    assert body["cycle"]["running"] is True
    assert body["cycle"]["running_agents"] == 1
    seat = next(s for s in body["agents"] if s["seat"] == "worker-1")
    assert (seat["state"], seat["task_id"]) == ("working", "busy-task")


def test_the_takeover_on_postgres_is_refused_while_the_cycle_shows_life(owner, factory) -> None:
    since = _cycle_seven_hours_old_writing_its_status(owner, factory)
    other = _acquire(owner, "OFFICE", "d2", 4242)
    assert set(other) == ANSWER_KEYS
    assert other == {"acquired": False, "kind": "held", "holder": "MAIL", "since": since, "pid": 10}
    same = _acquire(owner, "MAIL", "feed-2026-10-02", 11)
    assert (same["acquired"], same["kind"]) == (False, "ours")
    lock = owner.get(LOCK_ROUTE).json()
    assert (lock["machine"], lock["cycle_id"], lock["pid"], lock["acquired_at"]) == (
        "MAIL",
        "d1",
        10,
        since,
    )
