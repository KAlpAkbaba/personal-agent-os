"""The team's store on the REAL database (ADR-0222), not on SQLite.

Production, 2026-10-01: the first cycle in database mode died on its first call -
``POST /v1/team/queue/lock`` answered 500, ``value too long for type character varying(32)``.
The lock row's version was ``<stamp>|<machine>|<cycle>|<pid>`` (42 characters for
``MAIL`` / ``adr0224-02``); every unit test ran on SQLite, which does not enforce a
VARCHAR's length, and the worker had written NOT_RUN beside "DbStore on Postgres".
These tests take the same calls to the dev stack's PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.team.models import TeamStateRow
from app.team.store import DbStore

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 1, 11, 0, tzinfo=UTC)
#: Longer than anything a real machine or cycle is called, so the version cannot fit by luck.
MACHINE = "GMKADIRAKBABA-OFFICE-PC"
CYCLE = "cycle-2026-10-01-a-long-cycle-id"


@pytest.fixture()
def store() -> Iterator[DbStore]:
    engine = build_engine(Settings().database_url)
    factory = build_session_factory(engine)

    def clear() -> None:
        with factory() as session:
            session.execute(
                delete(TeamStateRow).where(TeamStateRow.kind.in_(("lock", "status", "report")))
            )
            session.commit()

    clear()
    try:
        yield DbStore(factory)
    finally:
        clear()
        engine.dispose()


def test_the_lock_is_taken_and_released_on_postgres(store: DbStore) -> None:
    taken = store.acquire_lock(machine=MACHINE, cycle_id=CYCLE, pid=4_194_304, now=NOW)
    assert taken["acquired"] is True, taken
    lock = store.read_lock()
    assert lock is not None and lock["held"] is True and lock["machine"] == MACHINE

    other = store.acquire_lock(machine="MAIL", cycle_id="c2", pid=7, now=NOW)
    assert other["acquired"] is False and other["kind"] == "held"

    store.release_lock(machine=MACHINE, cycle_id=CYCLE, now=NOW + timedelta(minutes=1))
    assert store.read_lock() == {"held": False}

    again = store.acquire_lock(machine="MAIL", cycle_id="c2", pid=7, now=NOW + timedelta(minutes=2))
    assert again["acquired"] is True, again


def test_two_takers_in_the_same_second_cannot_both_win_on_postgres(store: DbStore) -> None:
    """The version separates writers inside one second - that is what it was made long for."""
    first = store.acquire_lock(machine=MACHINE, cycle_id=CYCLE, pid=1, now=NOW)
    second = store.acquire_lock(machine=MACHINE, cycle_id=CYCLE + "-b", pid=2, now=NOW)
    assert first["acquired"] is True
    assert second["acquired"] is False


def test_the_live_status_and_a_report_are_written_on_postgres(store: DbStore) -> None:
    status = {
        "cycle_id": CYCLE,
        "machine": MACHINE,
        "pid": 4_194_304,
        "started_at": "2026-10-01T11:00:00Z",
        "runs": [{"task": "a-task-id", "role": "worker", "started_at": "2026-10-01T11:00:05Z"}],
        "estimated_usd": 1.25,
        "usage_limit": {"state": "ok", "resets_at": None},
        "updated_at": "2026-10-01T11:00:05Z",
    }
    store.put_status(status)
    assert store.read_status() == status
    store.put_status({**status, "runs": [], "updated_at": "2026-10-01T11:05:00Z"})
    assert store.read_status()["runs"] == []

    store.put_report(f"{CYCLE}.md", "# Döngü raporu\n\nYok.\n", now=NOW)
    newest = store.newest_report()
    assert newest is not None and "Döngü raporu" in newest["text"]
