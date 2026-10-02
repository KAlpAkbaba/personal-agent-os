"""The misheard notebook on the REAL database (migration 0065), not on SQLite.

What SQLite cannot say and PostgreSQL does: a VARCHAR's width is enforced (ADR-0214 addendum
4: 42 characters into VARCHAR(32) was an HTTP 500 in production); one failed statement aborts
the whole transaction unless it ran in a savepoint; a timestamptz comes back aware; the table
is the one the MIGRATION makes, not the one the ORM model would. These tests take the store's
own calls to the dev stack's PostgreSQL after ``alembic upgrade head``.

``misheard_utterances`` is emptied around every test: nothing else writes to it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import structlog
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete, func, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.voice.misheard import service
from app.voice.misheard.models import MisheardUtterance
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
TABLE = "misheard_utterances"
BEFORE = "0064_memory_vocabulary_class"
HEARD = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
SENTENCE = "Ofisü bilgisayarında hesap makinesini açın"

#: column -> (data_type, character_maximum_length, is_nullable): the CONTRACT, as
#: information_schema states it.
CONTRACT = {
    "id": ("uuid", None, "NO"),
    "heard_at": ("timestamp with time zone", None, "NO"),
    "sentence": ("character varying", 2000, "NO"),
    "mode": ("character varying", 8, "NO"),
    "engine": ("character varying", 64, "YES"),
    "device_id": ("uuid", None, "YES"),
    "band": ("character varying", 8, "YES"),
    "confidence": ("double precision", None, "YES"),
    "reason": ("character varying", 16, "NO"),
    "resolved_intent": ("character varying", 64, "YES"),
    "tool": ("character varying", 64, "YES"),
    "session_id": ("uuid", None, "NO"),
    "meant": ("character varying", 2000, "YES"),
    "answered_at": ("timestamp with time zone", None, "YES"),
    "expires_at": ("timestamp with time zone", None, "NO"),
}


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
            session.execute(delete(MisheardUtterance))
            session.commit()

    clear()
    service.reset_hold()
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
            "WHERE table_schema = current_schema() AND table_name = :table"
        ),
        {"table": TABLE},
    )
    return {name: (kind, width, nullable) for name, kind, width, nullable in rows}


def _count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(MisheardUtterance)).scalar_one()


def _record(session: Session, **overrides):
    arguments = {
        "sentence": SENTENCE,
        "mode": "paid",
        "reason": "no_intent",
        "session_id": uuid.uuid4(),
        "heard_at": HEARD,
        "now": HEARD,
    }
    arguments.update(overrides)
    return service.record(session, **arguments)


def test_the_migration_makes_the_table_with_the_contract_columns_and_widths(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        assert _columns(session) == CONTRACT
        indexes = set(
            session.execute(
                sql_text("SELECT indexname FROM pg_indexes WHERE tablename = :table"),
                {"table": TABLE},
            ).scalars()
        )
    assert {
        "misheard_utterances_pkey",
        "uq_misheard_utterances_session_heard",
        "ix_misheard_utterances_expires_at",
    } <= indexes


def test_record_list_answer_forget_all_round_trip_on_postgres(
    factory: sessionmaker[Session],
) -> None:
    session_id, device_id = uuid.uuid4(), uuid.uuid4()
    with factory() as session:
        row = _record(
            session,
            sentence="a" * 2500,
            mode="local",
            engine="e" * 100,
            device_id=device_id,
            band="low",
            confidence=0.41,
            reason="tool_failed",
            resolved_intent="i" * 100,
            # ADR-0214 addendum 4's shape: a name longer than its VARCHAR. Cut, not refused.
            tool="t" * 200,
            session_id=session_id,
        )
        assert row is not None
        _record(session, heard_at=HEARD + timedelta(minutes=1))
        session.commit()
        row_id = row.id

    # Read through a FRESH session: what the database holds, not what this one remembers.
    with factory() as session:
        listed = service.list_items(session, HEARD + timedelta(hours=1))
        assert [item.sentence for item in listed] == [SENTENCE, "a" * 2000]
        stored = listed[1]
        assert stored.id == row_id
        assert (stored.mode, stored.band, stored.reason) == ("local", "low", "tool_failed")
        assert (stored.engine, stored.resolved_intent, stored.tool) == (
            "e" * 64,
            "i" * 64,
            "t" * 64,
        )
        assert stored.device_id == device_id and stored.session_id == session_id
        assert stored.confidence == pytest.approx(0.41)
        # timestamptz: aware, and the same instant that was written.
        assert stored.heard_at.tzinfo is not None and stored.heard_at == HEARD
        assert stored.expires_at == HEARD + timedelta(days=30)
        assert stored.meant is None and stored.answered_at is None

        answered = service.answer(
            session, row_id, "ofis bilgisayarımda hesap makinesini aç", HEARD + timedelta(hours=2)
        )
        assert answered is not None
        session.commit()

    with factory() as session:
        stored = session.get(MisheardUtterance, row_id)
        assert stored is not None
        assert stored.meant == "ofis bilgisayarımda hesap makinesini aç"
        assert stored.answered_at == HEARD + timedelta(hours=2)
        assert stored.sentence == "a" * 2000
        # Day 30 lists, day 31 does not - with no purge having run - and then purge deletes.
        assert len(service.list_items(session, HEARD + timedelta(days=30))) == 1
        assert len(service.list_items(session, HEARD + timedelta(days=29))) == 2
        assert service.list_items(session, HEARD + timedelta(days=31)) == []
        assert _count(session) == 2
        assert service.forget_all(session) == 2
        session.commit()

    with factory() as session:
        assert _count(session) == 0
        assert service.purge(session, HEARD + timedelta(days=31)) == 0


def test_the_same_session_and_heard_at_twice_is_one_row_on_postgres(
    factory: sessionmaker[Session],
) -> None:
    session_id = uuid.uuid4()
    with factory() as session:
        first = _record(session, session_id=session_id, reason="no_intent")
        session.commit()
    with factory() as session:
        second = _record(session, session_id=session_id, reason="objected")
        session.commit()
        assert first is not None and second is not None and second.id == first.id
        assert _count(session) == 1
        assert session.execute(select(MisheardUtterance.reason)).scalar_one() == "no_intent"
        # And the database holds it by itself: a writer that skipped the check is refused.
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                MisheardUtterance(
                    heard_at=HEARD,
                    sentence=SENTENCE,
                    mode="paid",
                    reason="objected",
                    session_id=session_id,
                    expires_at=HEARD + timedelta(days=30),
                )
            )
            session.flush()


def test_a_write_that_fails_leaves_the_callers_transaction_usable(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forced: the engine's cut is lifted, so 100 characters reach a VARCHAR(64) and the
    SERVER refuses the statement. Without the savepoint PostgreSQL answers everything after it
    in this transaction with "current transaction is aborted" (SQLite would carry on)."""
    with factory() as session:
        before = _record(session, sentence="Maillerime bakın")
        assert before is not None

        with monkeypatch.context() as patch, structlog.testing.capture_logs() as events:
            patch.setattr(service, "ENGINE_WIDTH", 500)
            failed = _record(session, engine="e" * 100, heard_at=HEARD + timedelta(minutes=1))
        assert failed is None
        faults = [event for event in events if event["event"] == "misheard_record_failed"]
        assert [fault["error"] for fault in faults] == ["DataError"], events
        assert "e" * 65 not in repr(events) and SENTENCE not in repr(events)

        # The statement after it succeeds, on the same transaction...
        assert session.execute(sql_text("SELECT 1")).scalar_one() == 1
        after = _record(session, sentence="Ekranları kapatın", heard_at=HEARD + timedelta(hours=1))
        assert after is not None
        session.commit()

    # ...and what the caller did before the fault was not lost with it.
    with factory() as session:
        sentences = {item.sentence for item in service.list_items(session, HEARD)}
        assert sentences == {"Maillerime bakın", "Ekranları kapatın"}


def test_the_owner_routes_answer_from_postgres(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    now = datetime.now(UTC)
    with factory() as session:
        row = _record(session, heard_at=now, now=now, reason="asked_question", band="medium")
        assert row is not None
        session.commit()
        row_id = str(row.id)
    client = owner_client(settings)

    body = client.get("/v1/voice/misheard").json()
    assert (body["open"], body["retention_days"]) == (1, 30)
    assert [item["id"] for item in body["items"]] == [row_id]
    item = body["items"][0]
    assert set(item) == set(CONTRACT)
    heard = datetime.fromisoformat(item["heard_at"])
    assert heard == now
    assert datetime.fromisoformat(item["expires_at"]) - heard == timedelta(days=30)

    answered = client.post(f"/v1/voice/misheard/{row_id}/meaning", json={"meant": "postamı oku"})
    assert answered.status_code == 200, answered.text
    assert answered.json()["meant"] == "postamı oku"
    assert client.get("/v1/voice/misheard").json()["open"] == 0

    too_long = client.post(f"/v1/voice/misheard/{row_id}/meaning", json={"meant": "a" * 2001})
    assert too_long.status_code == 422, too_long.text

    assert client.delete("/v1/voice/misheard").json() == {"deleted": 1}
    with factory() as session:
        assert _count(session) == 0


def test_downgrade_drops_the_table_and_upgrade_recreates_it(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        assert _record(session) is not None
        session.commit()

    command.downgrade(_alembic(), BEFORE)
    try:
        with factory() as session:
            assert _columns(session) == {}
            # The old colour of a blue-green release, or a release whose migration has not
            # run yet: the writer answers None and the turn's transaction carries on.
            assert _record(session, heard_at=HEARD + timedelta(minutes=5)) is None
            assert session.execute(sql_text("SELECT 1")).scalar_one() == 1
    finally:
        command.upgrade(_alembic(), "head")

    with factory() as session:
        assert _columns(session) == CONTRACT
        assert _count(session) == 0  # the sentences went with the table
        assert _record(session) is not None
        session.commit()
