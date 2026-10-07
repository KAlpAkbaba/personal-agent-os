"""Aktivra's channel on the REAL database (migration ``aktivra_events``).

What SQLite cannot say and PostgreSQL does: the table is the one the MIGRATION makes, the event
id is unique in the database (two copies arriving AT ONCE make one notification), the severity
CHECK refuses a word outside the contract, the foreign key holds the notification, and
``downgrade()`` takes the table away cleanly. Then the route end to end through the real
application object: the same event id twice is 200 with the first notification id and ONE
notifications row.

The revision is read from the tree, never written here (migration_ids). Run it against a
scratch database (``PAGENTOS_DATABASE_URL``): the fixture upgrades whatever it is given to head.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.aktivra import service
from app.aktivra.models import AktivraEventRow
from app.config import Settings
from app.db import build_engine, build_session_factory
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.ledger import vocabulary as v
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.notifications.models import NotificationRow
from tests.identity_support import make_identity_engine
from tests.integration.migration_ids import parent_of

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_FILE = "aktivra_events.py"


def _revision_of_file(name: str) -> str:
    """The revision alembic reads from ``alembic/versions/<name>``. The file carries no
    ``<date>_<NNNN>_`` prefix (the card's area names it so), so migration_ids'
    ``revision_named`` - which matches ``*_<suffix>.py`` - cannot find it; the revision is
    still read from the tree, never typed here."""
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    found = [
        s.revision
        for s in ScriptDirectory.from_config(cfg).walk_revisions()
        if Path(s.path).name == name
    ]
    assert len(found) == 1, f"'{name}' adında tek bir göç dosyası bekleniyordu: {found}"
    return found[0]


REVISION = _revision_of_file(MIGRATION_FILE)
TOKEN = "pagentos_ak_" + "I" * 43
KINDS = (service.KIND_IMPORTANT, service.KIND_INFO)
COLUMNS = {
    "id": ("uuid", None, "NO"),
    "event_id": ("character varying", 64, "NO"),
    "notification_id": ("uuid", None, "YES"),
    "severity": ("character varying", 16, "NO"),
    "received_at": ("timestamp with time zone", None, "NO"),
}


def _alembic() -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    command.upgrade(_alembic(), "head")
    engine = build_engine(Settings().database_url)
    sessions = build_session_factory(engine)

    def clear() -> None:
        with sessions() as session:
            session.execute(delete(AktivraEventRow))
            session.execute(
                delete(ActivityEventRow).where(ActivityEventRow.subsystem == v.SUBSYSTEM_AKTIVRA)
            )
            session.execute(delete(NotificationRow).where(NotificationRow.kind.in_(KINDS)))
            session.commit()

    clear()
    try:
        yield sessions
    finally:
        command.upgrade(_alembic(), "head")
        clear()
        engine.dispose()


def _columns(session: Session) -> dict[str, tuple[str, int | None, str]]:
    rows = session.execute(
        sql_text(
            "SELECT column_name, data_type, character_maximum_length, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'aktivra_events'"
        )
    )
    return {name: (kind, width, nullable) for name, kind, width, nullable in rows}


def _count(factory, model) -> int:
    with factory() as db:
        return db.execute(select(func.count()).select_from(model)).scalar_one()


def test_the_migration_makes_the_contract_table(factory) -> None:
    with factory() as session:
        assert _columns(session) == COLUMNS


def test_the_event_id_is_unique_and_the_severity_closed(factory) -> None:
    now = datetime.now(UTC)
    with factory() as db:
        db.add(AktivraEventRow(event_id="uniq-0000001", severity="info", received_at=now))
        db.commit()
        db.add(AktivraEventRow(event_id="uniq-0000001", severity="info", received_at=now))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(AktivraEventRow(event_id="uniq-0000002", severity="critical", received_at=now))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(
            AktivraEventRow(
                event_id="uniq-0000003",
                severity="info",
                received_at=now,
                notification_id=uuid.uuid4(),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_two_copies_at_once_make_one_notification(factory) -> None:
    def send(_: int):
        with factory() as db:
            return service.accept(
                db, event_id="race-0000001", title="t", summary="", severity="important"
            )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(send, range(4)))
    assert sum(r.created for r in results) == 1
    assert _count(factory, AktivraEventRow) == 1
    with factory() as db:
        kinds = list(db.scalars(select(NotificationRow).where(NotificationRow.kind.in_(KINDS))))
    assert len(kinds) == 1


def test_the_same_event_id_twice_through_the_app_is_200_and_one_row(factory) -> None:
    settings = Settings(aktivra_inbound_token=SecretStr(TOKEN))
    app = create_app(settings)
    runtime = IdentityRuntime(
        settings, engine=make_identity_engine(), root=InMemoryCredentialRoot()
    )
    runtime.service.bootstrap()
    app.state.identity = runtime
    app.state.artifacts = SimpleNamespace(session=factory)
    body = {
        "event_id": "pg-event-000001",
        "title": "Önemli",
        "severity": "important",
        "occurred_at": "2026-10-07T09:00:00Z",
    }
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        first = client.post("/v1/aktivra/events", json=body, headers=headers)
        second = client.post("/v1/aktivra/events", json=body, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["notification_id"] == first.json()["notification_id"]
    with factory() as db:
        rows = list(db.scalars(select(NotificationRow).where(NotificationRow.kind.in_(KINDS))))
        assert [str(r.id) for r in rows] == [first.json()["notification_id"]]
        assert rows[0].priority == "urgent"
        ledger = list(
            db.scalars(
                select(ActivityEventRow).where(ActivityEventRow.subsystem == v.SUBSYSTEM_AKTIVRA)
            )
        )
        assert [r.event_type for r in ledger] == [v.EVENT_TYPE_AKTIVRA_RECEIVED]


def test_down_and_up_again(factory) -> None:
    command.downgrade(_alembic(), parent_of(REVISION))
    with factory() as session:
        assert _columns(session) == {}
    command.upgrade(_alembic(), "head")
    with factory() as session:
        assert _columns(session) == COLUMNS
