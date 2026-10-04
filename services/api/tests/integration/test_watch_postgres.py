"""The watch tables on the REAL database (migration 0066), not on SQLite.

What SQLite cannot say and PostgreSQL does: the tables are the ones the MIGRATION makes, a
timestamptz comes back aware (the runner compares ``next_due_at`` with an aware clock), the
reading's ``(watch_id, read_at)`` is unique in the database too, and ``downgrade()`` takes
both tables away cleanly. ``watches`` and ``watch_readings`` are emptied around every test;
nothing else writes to them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete, func, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.notifications.models import NotificationRow
from app.watch import compare, runner, service
from app.watch.models import Watch, WatchReading
from app.watch.reader import Observation
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "0065_misheard_utterances"
URL = "https://www.home-assistant.io/blog/"

#: column -> (data_type, character_maximum_length, is_nullable), as information_schema says.
WATCHES = {
    "id": ("uuid", None, "NO"),
    "label": ("character varying", 80, "NO"),
    "url": ("character varying", 2000, "NO"),
    "condition": ("character varying", 240, "NO"),
    "every_hours": ("integer", None, "NO"),
    "selector": ("character varying", 200, "YES"),
    "created_at": ("timestamp with time zone", None, "NO"),
    "next_due_at": ("timestamp with time zone", None, "NO"),
    "last_read_at": ("timestamp with time zone", None, "YES"),
    "last_outcome": ("character varying", 16, "YES"),
    "last_value": ("double precision", None, "YES"),
    "baseline_sha256": ("character varying", 64, "YES"),
    "condition_met": ("boolean", None, "YES"),
    "consecutive_failures": ("integer", None, "NO"),
}
READINGS = {
    "id": ("uuid", None, "NO"),
    "watch_id": ("uuid", None, "NO"),
    "read_at": ("timestamp with time zone", None, "NO"),
    "text_sha256": ("character varying", 64, "YES"),
    "value": ("double precision", None, "YES"),
    "outcome": ("character varying", 16, "NO"),
    "reason": ("character varying", 120, "YES"),
    "notified": ("boolean", None, "NO"),
}


def _alembic() -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.research.destination.resolve_hostname", lambda host: ["93.184.216.34"])


@pytest.fixture()
def factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    command.upgrade(_alembic(), "head")
    engine = build_engine(settings.database_url)
    sessions = build_session_factory(engine)

    def clear() -> None:
        with sessions() as session:
            session.execute(delete(WatchReading))
            session.execute(delete(Watch))
            session.execute(delete(NotificationRow).where(NotificationRow.kind.like("watch.%")))
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


def _page(text: str) -> Observation:
    return Observation(ok=True, text_sha256=compare.text_sha256(text), text=text)


def test_the_migration_makes_both_tables_with_the_contract_columns(factory) -> None:
    with factory() as session:
        assert _columns(session, "watches") == WATCHES
        assert _columns(session, "watch_readings") == READINGS
        indexes = set(
            session.execute(
                sql_text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename IN ('watches', 'watch_readings')"
                )
            ).scalars()
        )
    assert {
        "ix_watches_next_due_at",
        "ix_watch_readings_read_at",
        "uq_watch_readings_watch_read_at",
    } <= indexes


def test_create_read_purge_and_forget_on_postgres(factory) -> None:
    noon = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    with factory() as session:
        view = service.create_watch(
            session, url=URL, condition="number_below:20000", label="Fiyat", now=noon
        )
        session.commit()
    wid = uuid.UUID(view.id)
    with factory() as session:
        watch = session.get(Watch, wid)
        assert watch is not None and watch.next_due_at.tzinfo is not None
        runner.apply_observation(session, watch, _page("21.000 TL"), now=noon - timedelta(days=31))
        watch = session.get(Watch, wid)
        change = runner.apply_observation(session, watch, _page("19.499 TL"), now=noon)
        assert change is not None and change.outcome == "condition_met"
    with factory() as session:
        stored = session.get(Watch, wid)
        assert stored.last_value == pytest.approx(19499)
        assert stored.last_read_at == noon
        assert stored.next_due_at == compare.next_due(noon, wid, 6)
        loop = runner.PurgeLoop(lambda: factory(), clock=lambda: noon)
    assert loop.purge_once() == 1
    with factory() as session:
        assert session.execute(select(func.count()).select_from(WatchReading)).scalar_one() == 1
        assert service.forget_all(session) == 1
        session.commit()
        assert session.execute(select(func.count()).select_from(Watch)).scalar_one() == 0
        assert session.execute(select(func.count()).select_from(WatchReading)).scalar_one() == 0


def test_the_same_reading_twice_is_one_row_in_the_database(factory) -> None:
    noon = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    with factory() as session:
        view = service.create_watch(session, url=URL, condition="changed", label="a", now=noon)
        session.commit()
    with factory() as session:
        session.add(WatchReading(watch_id=uuid.UUID(view.id), read_at=noon, outcome="same"))
        session.commit()
        session.add(WatchReading(watch_id=uuid.UUID(view.id), read_at=noon, outcome="same"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_the_routes_and_one_reading_through_the_real_application(factory, settings) -> None:
    client = owner_client(settings)
    created = client.post(
        "/v1/watches",
        json={"url": URL, "condition": "contains:kararlı sürüm", "label": "HA", "every_hours": 1},
    )
    assert created.status_code == 201, created.text
    wid = created.json()["id"]

    class Reader:
        def read(self, db, *, url, selector, watch_id):
            return _page("Home Assistant 2026.10 KARARLI SÜRÜM")

    run = client.app.state.watch_runner
    run.reader = Reader()
    assert run.run_due(datetime.now(UTC) + timedelta(seconds=1)) == 1
    with factory() as session:
        notes = list(
            session.execute(
                select(NotificationRow).where(NotificationRow.group_key == f"watch:{wid}")
            ).scalars()
        )
    assert [n.kind for n in notes] == ["watch.condition_met"]
    listed = client.get("/v1/watches").json()["items"]
    assert listed[0]["last_outcome"] == "condition_met"
    assert client.delete("/v1/watches").json() == {"deleted": 1}


def test_downgrade_drops_both_tables_and_upgrade_recreates_them(factory) -> None:
    with factory() as session:
        service.create_watch(session, url=URL, condition="changed", label="a")
        session.commit()
    command.downgrade(_alembic(), BEFORE)
    try:
        with factory() as session:
            assert _columns(session, "watches") == {}
            assert _columns(session, "watch_readings") == {}
    finally:
        command.upgrade(_alembic(), "head")
    with factory() as session:
        assert _columns(session, "watches") == WATCHES
        assert session.execute(select(func.count()).select_from(Watch)).scalar_one() == 0
