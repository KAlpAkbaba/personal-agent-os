"""A song per wake alarm on the REAL database (migration 0067), not on SQLite.

What SQLite cannot say: the column the MIGRATION makes (jsonb, nullable) rather than the one
the ORM model would; a Turkish title round-tripping through jsonb; the downgrade really
dropping the column and the upgrade really bringing it back, with the alarm rows intact.
Runs against the dev stack's PostgreSQL after ``alembic upgrade head``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from app.alarms import service as alarms_service
from app.alarms.models import WakeAlarm
from app.alarms.tr_time import parse_when_struct
from app.config import Settings
from app.db import build_engine, build_session_factory

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "0066_watches"
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
SONG = {"url": "https://www.youtube.com/watch?v=SimarikTarka", "title": "Tarkan - Şımarık"}


def _alembic() -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    command.upgrade(_alembic(), "head")
    engine = build_engine(Settings().database_url)
    sessions = build_session_factory(engine)
    made: list = []
    try:
        yield sessions, made
    finally:
        command.upgrade(_alembic(), "head")
        with sessions() as session:
            for alarm_id in made:
                row = session.get(WakeAlarm, alarm_id)
                if row is not None:
                    alarms_service.cancel_alarm(session, alarm_id)
            session.commit()
        engine.dispose()


def _song_column(session: Session) -> tuple[str, str] | None:
    row = session.execute(
        sql_text(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'wake_alarms' "
            "AND column_name = 'song'"
        )
    ).first()
    return (row[0], row[1]) if row else None


def test_the_migration_adds_a_nullable_jsonb_song_column(factory) -> None:
    sessions, _ = factory
    with sessions() as session:
        assert _song_column(session) == ("jsonb", "YES")


def test_an_alarm_song_round_trips_through_postgres(factory) -> None:
    sessions, made = factory
    with sessions() as session:
        alarm = alarms_service.create_alarm(
            session,
            when=parse_when_struct({"relative_seconds": 3600}, now=NOW),
            song=SONG,
            is_test=True,
        )
        made.append(alarm.id)
        plain = alarms_service.create_alarm(
            session, when=parse_when_struct({"relative_seconds": 3600}, now=NOW), is_test=True
        )
        made.append(plain.id)
        alarm_id, plain_id = alarm.id, plain.id

    with sessions() as session:
        assert session.get(WakeAlarm, alarm_id).song == SONG
        assert session.get(WakeAlarm, plain_id).song is None
        alarms_service.set_alarm_song(
            session, plain_id, url="https://www.youtube.com/watch?v=GulpembeBM1", title="Gülpembe"
        )

    with sessions() as session:
        assert session.get(WakeAlarm, plain_id).song == {
            "url": "https://www.youtube.com/watch?v=GulpembeBM1",
            "title": "Gülpembe",
        }


def test_downgrade_drops_the_column_and_upgrade_brings_it_back(factory) -> None:
    sessions, made = factory
    with sessions() as session:
        alarm = alarms_service.create_alarm(
            session,
            when=parse_when_struct({"relative_seconds": 3600}, now=NOW),
            song=SONG,
            is_test=True,
        )
        made.append(alarm.id)
        alarm_id = alarm.id

    command.downgrade(_alembic(), BEFORE)
    try:
        with sessions() as session:
            assert _song_column(session) is None
            # The row itself survives: only the column went.
            count = session.execute(
                sql_text("SELECT count(*) FROM wake_alarms WHERE id = :id"), {"id": alarm_id}
            ).scalar_one()
            assert count == 1
    finally:
        command.upgrade(_alembic(), "head")

    with sessions() as session:
        assert _song_column(session) == ("jsonb", "YES")
        # Expand-only: the song came back empty, and the alarm rings the global song.
        assert session.get(WakeAlarm, alarm_id).song is None
