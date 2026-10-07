"""A created alarm is always in the list (test team round t-w10070808, case 't3-invisible').

On staging b1f8ef94 the test team had left 767 alarms waiting, then ``POST /v1/alarms``
(2026-10-10 08:00, label ``tj3-gorunmez``) answered 201 and ``GET /v1/alarms?limit=200`` did
not have it: the list is ordered by ``scheduled_for`` and stops at 200 rows, and nothing kept
the number of open alarms at or under 200. The same 200-row read feeds the tick, the next
alarm and the ringing check, so an alarm past the 200th was not only invisible: it was never
armed.

These tests take the improvised steps to the dev stack's PostgreSQL through the REST surface
(the fillers through ``app.alarms.service.create_alarm``, the production write): an alarm the
API accepted is in the list, with and without ``limit``; one past the open-alarm bound is
refused in Turkish and leaves no row; concurrent creates at the bound do not overshoot it.

The rows are namespaced with a per-test label and deleted when the test ends; the ledger rows
stay (append-only). Every filler is a month ahead, so nothing here can ring.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.alarms import routes as alarm_routes
from app.alarms import service as alarms_service
from app.alarms.models import ALARM_TERMINAL_STATES, WakeAlarm
from app.alarms.tr_time import ParsedWhen
from app.config import Settings
from app.db import build_engine, build_session_factory
from app.routines.models import Routine
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

#: The bound the list can show in one page; an open alarm past it would be invisible.
BOUND = alarm_routes.MAX_LIST_LIMIT


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def db(settings: Settings) -> Iterator[sessionmaker[Session]]:
    engine = build_engine(settings.database_url)
    assert engine.dialect.name == "postgresql"
    try:
        yield build_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture()
def made(db: sessionmaker[Session]) -> Iterator[SimpleNamespace]:
    created = SimpleNamespace(token=f"tj3-{uuid.uuid4().hex[:8]}")
    try:
        yield created
    finally:
        with db() as session:
            ids = list(
                session.execute(
                    select(WakeAlarm.id).where(WakeAlarm.label.like(f"{created.token}%"))
                ).scalars()
            )
            if ids:
                refs = [Routine.source_ref.like(f"alarm:{alarm_id}%") for alarm_id in ids]
                for start in range(0, len(refs), 100):
                    session.execute(delete(Routine).where(or_(*refs[start : start + 100])))
                session.execute(delete(WakeAlarm).where(WakeAlarm.id.in_(ids)))
            session.commit()


def _open_count(db: sessionmaker[Session]) -> int:
    with db() as session:
        return int(
            session.execute(
                select(func.count())
                .select_from(WakeAlarm)
                .where(WakeAlarm.state.not_in(tuple(sorted(ALARM_TERMINAL_STATES))))
            ).scalar_one()
        )


def _fill(db: sessionmaker[Session], token: str, count: int) -> None:
    """``count`` open alarms a month ahead - all EARLIER than the alarm under test, so the
    ``scheduled_for`` order puts them in front of it exactly as on staging."""
    start = datetime.now(UTC) + timedelta(days=30)
    with db() as session:
        for index in range(count):
            at = start + timedelta(minutes=index)
            alarms_service.create_alarm(
                session,
                when=ParsedWhen(at=at, local_time=at.strftime("%H:%M"), timezone="UTC"),
                is_test=True,
                label=f"{token}-dolgu-{index}",
            )


def _late_body(label: str) -> dict:
    when = (datetime.now(UTC) + timedelta(days=60)).date().isoformat()
    return {"when": {"date": when, "time": "08:00"}, "label": label}


def _labels(payload: dict) -> list[str]:
    return [row["label"] for row in payload["alarms"]]


def test_created_alarm_is_listed_and_the_one_past_the_bound_is_refused(
    db: sessionmaker[Session], settings: Settings, made: SimpleNamespace
) -> None:
    existing = _open_count(db)
    assert existing < BOUND, f"the dev database already holds {existing} open alarms"
    _fill(db, made.token, BOUND - 1 - existing)

    client = owner_client(settings)
    late = f"{made.token}-gorunmez"
    created = client.post("/v1/alarms", json=_late_body(late))
    assert created.status_code == 201, created.text

    listed = client.get(f"/v1/alarms?limit={BOUND}")
    assert listed.status_code == 200
    assert late in _labels(listed.json())
    # The default page is the whole bound too: "alarmlarımı göster" shows every open alarm.
    assert late in _labels(client.get("/v1/alarms").json())
    assert listed.json()["total"] == BOUND

    # The 201st open alarm would sit past the page and past the tick: refused, nothing kept.
    over = f"{made.token}-fazla"
    refused = client.post("/v1/alarms", json=_late_body(over))
    assert refused.status_code == 409, refused.text
    assert "alarm" in refused.json()["detail"]["message"].lower()
    assert _open_count(db) == BOUND
    with db() as session:
        assert (
            session.execute(
                select(func.count()).select_from(WakeAlarm).where(WakeAlarm.label == over)
            ).scalar_one()
            == 0
        )


def test_concurrent_creates_at_the_bound_do_not_overshoot_it(
    db: sessionmaker[Session], made: SimpleNamespace
) -> None:
    existing = _open_count(db)
    assert existing < BOUND - 3, f"the dev database already holds {existing} open alarms"
    _fill(db, made.token, BOUND - 3 - existing)

    outcomes: list[str] = []
    gate = threading.Barrier(8)

    def create(index: int) -> None:
        at = datetime.now(UTC) + timedelta(days=61, minutes=index)
        gate.wait()
        with db() as session:
            try:
                alarms_service.create_alarm(
                    session,
                    when=ParsedWhen(at=at, local_time=at.strftime("%H:%M"), timezone="UTC"),
                    is_test=True,
                    label=f"{made.token}-yaris-{index}",
                )
                outcomes.append("created")
            except alarms_service.AlarmLimitReached:
                outcomes.append("refused")

    threads = [threading.Thread(target=create, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert sorted(outcomes) == ["created"] * 3 + ["refused"] * 5
    assert _open_count(db) == BOUND
