"""Two devices say the same NEW item at once (household-item-create-race-500).

The service reads by key, finds nothing, and inserts; between the read and the insert another
device's request committed the same key, so ``uq_household_items_key`` refuses the insert. That
refusal was an unhandled IntegrityError (HTTP 500 on staging). Here the race is made
deterministic on SQLite: the read is told to see nothing while the row already exists, so the
insert MUST conflict - and the request must still land on the one row and apply its change.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.household import service
from app.household.models import HouseholdEvent, HouseholdItem

DAY0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (HouseholdItem.__table__, HouseholdEvent.__table__):
        table.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _other_device_created(factory, name: str) -> uuid.UUID:
    with factory() as other:
        return service.set_level(other, name, "azaldı", now=DAY0).item.id


def _blind_read(monkeypatch) -> None:
    """The read happened before the other device's commit: it saw nothing."""
    real = service.find_item
    calls = {"n": 0}

    def blind(db, name):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(db, name)

    monkeypatch.setattr(service, "find_item", blind)


def test_set_level_lands_on_the_row_another_device_just_created(factory, monkeypatch) -> None:
    existing = _other_device_created(factory, "deterjan")
    _blind_read(monkeypatch)
    with factory() as db:
        change = service.set_level(db, "Deterjan", "bitti", now=DAY0)
    assert change.item.id == existing
    with factory() as db:
        rows = db.scalars(select(HouseholdItem)).all()
        assert [(r.id, r.level, r.on_list) for r in rows] == [(existing, "bitti", True)]


def test_add_to_list_lands_on_the_row_another_device_just_created(factory, monkeypatch) -> None:
    existing = _other_device_created(factory, "çay")
    _blind_read(monkeypatch)
    with factory() as db:
        change = service.add_to_list(db, "çay", quantity="iki paket", now=DAY0)
    assert change.item.id == existing
    with factory() as db:
        assert db.execute(select(func.count()).select_from(HouseholdItem)).scalar_one() == 1
        assert db.get(HouseholdItem, existing).list_quantity == "iki paket"


def test_the_conflict_does_not_undo_what_the_session_already_wrote(factory, monkeypatch) -> None:
    """The recovery rolls back to a savepoint, never the whole transaction."""
    existing = _other_device_created(factory, "süt")
    with factory() as db:
        service.set_level(db, "ekmek", "bitti", now=DAY0)
        db.add(
            HouseholdItem(
                id=uuid.uuid4(),
                name="un",
                key="un",
                on_list=False,
                created_at=DAY0,
                updated_at=DAY0,
            )
        )
        db.flush()
        _blind_read(monkeypatch)
        row = service._get_or_create(db, "süt", DAY0)
        assert row.id == existing
        db.commit()
    with factory() as db:
        assert sorted(r.key for r in db.scalars(select(HouseholdItem))) == ["ekmek", "sut", "un"]
