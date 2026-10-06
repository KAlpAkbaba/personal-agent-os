"""The house's stock on the REAL database (migration ``household_stock``), not on SQLite.

What SQLite cannot say and PostgreSQL does: the tables are the ones the MIGRATION makes (the
model never builds them here), a timestamptz comes back aware (the rhythm subtracts it from an
aware clock), the folded key is unique in the database too ("süt" and "sütü" cannot be two
rows even past the service), an item's events go with it, and ``downgrade()`` takes both
tables away cleanly. Both tables are emptied around every test; nothing else writes to them.

The revision before this one is read from the migration itself, never written here: the card
says its ``down_revision`` is re-pointed at merge when another migration lands first.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import delete, func, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.household import reminders, service
from app.household.models import HouseholdEvent, HouseholdItem
from app.notifications.models import NotificationRow
from tests.integration.conftest import owner_client
from tests.integration.migration_ids import revision_named

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
REVISION = revision_named("household_stock")
DAY0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

#: column -> (data_type, character_maximum_length, is_nullable), as information_schema says.
ITEMS = {
    "id": ("uuid", None, "NO"),
    "name": ("character varying", 60, "NO"),
    "key": ("character varying", 80, "NO"),
    "level": ("character varying", 8, "YES"),
    "on_list": ("boolean", None, "NO"),
    "list_quantity": ("character varying", 40, "YES"),
    "usual_quantity": ("character varying", 40, "YES"),
    "created_at": ("timestamp with time zone", None, "NO"),
    "updated_at": ("timestamp with time zone", None, "NO"),
    "depleted_at": ("timestamp with time zone", None, "YES"),
    "restocked_at": ("timestamp with time zone", None, "YES"),
    "cycle_days": ("double precision", None, "YES"),
    "reminded_at": ("timestamp with time zone", None, "YES"),
}
EVENTS = {
    "id": ("uuid", None, "NO"),
    "item_id": ("uuid", None, "NO"),
    "kind": ("character varying", 16, "NO"),
    "at": ("timestamp with time zone", None, "NO"),
}


def _alembic() -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


def _before() -> str:
    revision = ScriptDirectory.from_config(_alembic()).get_revision(REVISION)
    assert revision is not None and isinstance(revision.down_revision, str)
    return revision.down_revision


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
            session.execute(
                delete(NotificationRow).where(NotificationRow.kind == reminders.KIND_REMINDER)
            )
            session.commit()

    clear()
    try:
        yield sessions
    finally:
        command.upgrade(_alembic(), "head")
        clear()
        engine.dispose()


def _columns(session: Session, table: str) -> dict[str, tuple[str, int | None, str]]:
    rows = session.execute(
        sql_text(
            "SELECT column_name, data_type, character_maximum_length, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table"
        ),
        {"table": table},
    )
    return {name: (kind, width, nullable) for name, kind, width, nullable in rows}


def test_the_migration_makes_both_tables_with_the_contract_columns(factory) -> None:
    with factory() as session:
        assert _columns(session, "household_items") == ITEMS
        assert _columns(session, "household_events") == EVENTS


def test_levels_the_rhythm_and_the_reminder_on_postgres(factory) -> None:
    starts = [DAY0 + timedelta(days=21 * k) for k in range(3)]
    with factory() as session:
        for at in starts:
            service.set_level(session, "tuvalet kağıdı", "bitti", now=at)
            service.set_level(session, "Tuvalet kağıdını", "var", now=at + timedelta(days=1))
    with factory() as session:
        item = session.scalars(select(HouseholdItem)).one()
        assert item.name == "tuvalet kağıdı"
        assert item.depleted_at is not None and item.depleted_at.tzinfo is not None
        assert item.cycle_days == pytest.approx(21.0)
        due_at = starts[-1] + timedelta(days=19)
        assert reminders.remind_due(session, now=due_at) == 1
        assert reminders.remind_due(session, now=due_at + timedelta(hours=1)) == 0
        notes = session.scalars(
            select(NotificationRow).where(NotificationRow.kind == reminders.KIND_REMINDER)
        ).all()
        assert len(notes) == 1 and "21" in notes[0].body


def test_the_key_is_unique_in_the_database_and_events_go_with_their_item(factory) -> None:
    with factory() as session:
        change = service.set_level(session, "süt", "bitti", now=DAY0)
        item_id = change.item.id
        now = datetime.now(UTC)
        session.add(
            HouseholdItem(
                id=uuid.uuid4(),
                name="sütü",
                key="sut",
                on_list=False,
                created_at=now,
                updated_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    with factory() as session:
        assert service.forget_item(session, item_id) == 1
        assert session.execute(select(func.count()).select_from(HouseholdEvent)).scalar_one() == 0


def test_the_routes_through_the_real_application(factory, settings) -> None:
    client = owner_client(settings)
    low = client.post("/v1/household/items", json={"name": "Tuvalet kağıdı", "level": "azaldı"})
    assert low.status_code == 200, low.text
    client.post("/v1/household/list", json={"name": "makarna", "quantity": "iki paket"})
    body = client.get("/v1/household").json()
    assert [i["name"] for i in body["list"]] == ["tuvalet kağıdı", "makarna"]
    assert body["speech"] == ("Listede 2 şey var: tuvalet kağıdı (azaldı) ve iki paket makarna.")
    assert "household_reminders" in client.get("/v1/system/health").json()["checks"]


def test_downgrade_drops_both_tables_and_upgrade_recreates_them(factory) -> None:
    with factory() as session:
        service.add_to_list(session, "çay", quantity=None, now=DAY0)
    command.downgrade(_alembic(), _before())
    try:
        with factory() as session:
            assert _columns(session, "household_items") == {}
            assert _columns(session, "household_events") == {}
    finally:
        command.upgrade(_alembic(), "head")
    with factory() as session:
        assert _columns(session, "household_items") == ITEMS
        assert session.execute(select(func.count()).select_from(HouseholdItem)).scalar_one() == 0
