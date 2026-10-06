"""Sixteen devices say the same NEW item at once, on the real database and the real application.

Test team finding (round t-manual-20261006d, staging 72884b71): POST /v1/household/items with
one new name from 16 clients at once answered 500 to 11 of them - ``_get_or_create`` read, saw
nothing, inserted, and ``uq_household_items_key`` raised an unhandled UniqueViolation.

The race is forced, not hoped for: every request waits at a barrier right after its read saw
nothing, so all 16 inserts meet the unique key together. Wanted: every answer 2xx, exactly one
row for the folded key, and each request's change applied to it.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.household import service
from app.household.models import HouseholdEvent, HouseholdItem
from app.main import create_app
from tests.integration.conftest import attach_owner

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
DEVICES = 16
#: Requests forced to insert together; the other eight race as they come.
HELD = 8


def _alembic() -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    command.upgrade(_alembic(), "head")
    engine = build_engine(settings.database_url)
    sessions = build_session_factory(engine)

    def clear() -> None:
        with sessions() as session:
            session.execute(delete(HouseholdEvent))
            session.execute(delete(HouseholdItem))
            session.commit()

    clear()
    try:
        yield sessions
    finally:
        clear()
        engine.dispose()


class _Gate:
    """The first ``parties`` reads that see nothing are held until all of them have read; every
    later read passes. ``parties`` stays under the engine's pool (5 + 10 overflow): a held
    request keeps its connection, and the sixteenth would otherwise wait for one for ever."""

    def __init__(self, parties: int) -> None:
        self.parties = parties
        self.arrived = 0
        self.lock = threading.Lock()
        self.open = threading.Event()

    def met(self) -> bool:
        return self.open.is_set()

    def hold(self) -> None:
        with self.lock:
            if self.arrived >= self.parties:
                return
            self.arrived += 1
            if self.arrived == self.parties:
                self.open.set()
        self.open.wait(timeout=30)


@pytest.fixture()
def all_read_nothing_together(monkeypatch) -> _Gate:
    gate = _Gate(HELD)
    real = service.find_item

    def find_then_wait(db, name):
        row = real(db, name)
        if row is None:
            gate.hold()
        return row

    monkeypatch.setattr(service, "find_item", find_then_wait)
    return gate


def test_sixteen_concurrent_creates_through_the_app_make_one_row(
    factory, settings, all_read_nothing_together
) -> None:
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)
    attach_owner(app, client, settings)

    def say(index: int):
        name = "Bulaşık süngeri" if index % 2 else "bulaşık süngerini"
        return client.post("/v1/household/items", json={"name": name, "level": "bitti"})

    with ThreadPoolExecutor(max_workers=DEVICES) as pool:
        answers = list(pool.map(say, range(DEVICES)))

    assert all_read_nothing_together.met(), "the race was not forced"
    assert [a.status_code for a in answers] == [200] * DEVICES, [a.text for a in answers][:3]
    ids = {a.json()["item"]["id"] for a in answers}
    with factory() as session:
        rows = session.scalars(select(HouseholdItem)).all()
        assert [(r.level, r.on_list) for r in rows] == [("bitti", True)]
        assert ids == {str(rows[0].id)}
        depletions = session.scalars(select(HouseholdEvent)).all()
        assert len(depletions) >= 1 and {e.item_id for e in depletions} == {rows[0].id}


def test_sixteen_concurrent_list_adds_on_the_service_make_one_row(
    factory, all_read_nothing_together
) -> None:
    now = datetime.now(UTC)

    def add(index: int) -> str:
        with factory() as session:
            change = service.add_to_list(session, "makarna", quantity=f"{index} paket", now=now)
            return str(change.item.id)

    with ThreadPoolExecutor(max_workers=DEVICES) as pool:
        ids = set(pool.map(add, range(DEVICES)))

    assert all_read_nothing_together.met()
    with factory() as session:
        rows = session.scalars(select(HouseholdItem)).all()
        assert len(rows) == 1 and rows[0].on_list
        assert ids == {str(rows[0].id)}
        assert rows[0].list_quantity in {f"{i} paket" for i in range(DEVICES)}
