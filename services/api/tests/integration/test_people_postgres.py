"""Person cards and conversation follow-ups on the REAL database (migration
``conversation_followups``), not on SQLite.

What SQLite cannot say and PostgreSQL does: the tables are the ones the MIGRATION makes (the
model never builds them here), the foreign keys to ``conversations`` behave (a deleted
conversation leaves its follow-ups with their quotes, a deleted card takes its follow-ups), the
folded name is unique and the CHECKs refuse an unknown kind or calendar state in the database
too, a timestamptz comes back aware, and ``downgrade()`` takes both tables away cleanly.

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

from app.calendar.service import CalendarService
from app.config import Settings
from app.conversations import followups, service
from app.conversations.models import ConversationRow
from app.conversations.service import LiveConversations
from app.db import build_engine, build_session_factory
from app.people import service as people
from app.people.models import FollowupRow, PersonCardRow
from tests.mail_calendar_support import (
    build_fake_calendar_provider,
    build_fake_calendar_writer,
)

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
REVISION = "0071_conversation_followups"
MARK = "followups-postgres-test"
NOON = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

LINES = [
    (True, "Ahmet, raporu cuma sana göndereceğim."),
    (False, "Tamam, ben de yarın seni ararım."),
]
ITEMS = [
    {
        "kind": "promise",
        "person": "Ahmet",
        "relation": "",
        "direction": "owner",
        "what": "raporu göndermek",
        "due": "2026-10-09",
        "segment": 1,
        "quote": "raporu cuma sana göndereceğim",
    },
    {
        "kind": "promise",
        "person": "Ahmet",
        "relation": "",
        "direction": "them",
        "what": "seni arayacak",
        "due": "",
        "segment": 2,
        "quote": "yarın seni ararım",
    },
    {  # invented
        "kind": "promise",
        "person": "Ahmet",
        "relation": "",
        "direction": "owner",
        "what": "araba almak",
        "due": "",
        "segment": 2,
        "quote": "araba alacağım",
    },
]

CARDS = {
    "id": ("uuid", None, "NO"),
    "name": ("character varying", 80, "NO"),
    "name_key": ("character varying", 80, "NO"),
    "relation": ("character varying", 60, "YES"),
    "last_talk_at": ("timestamp with time zone", None, "YES"),
    "last_conversation_id": ("uuid", None, "YES"),
    "last_topic": ("character varying", 400, "YES"),
    "created_at": ("timestamp with time zone", None, "NO"),
    "updated_at": ("timestamp with time zone", None, "NO"),
}
FOLLOWUPS = {
    "id": ("uuid", None, "NO"),
    "card_id": ("uuid", None, "YES"),
    "conversation_id": ("uuid", None, "YES"),
    "kind": ("character varying", 16, "NO"),
    "direction": ("character varying", 8, "YES"),
    "what": ("character varying", 200, "NO"),
    "due_at": ("timestamp with time zone", None, "YES"),
    "due_has_time": ("boolean", None, "NO"),
    "segment_seq": ("integer", None, "NO"),
    "quote": ("character varying", 400, "NO"),
    "spoken_at": ("timestamp with time zone", None, "NO"),
    "done": ("boolean", None, "NO"),
    "calendar_state": ("character varying", 16, "YES"),
    "calendar_proposal_id": ("uuid", None, "YES"),
    "created_at": ("timestamp with time zone", None, "NO"),
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
            session.execute(delete(FollowupRow))
            session.execute(delete(PersonCardRow))
            session.execute(delete(ConversationRow).where(ConversationRow.title == MARK))
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


def _conversation(session: Session) -> uuid.UUID:
    live = LiveConversations()
    # The dev database may hold an open conversation of the owner's own: never touch it.
    open_one = session.execute(
        select(ConversationRow.id).where(ConversationRow.ended_at.is_(None))
    ).scalar_one_or_none()
    if open_one is not None:
        pytest.skip("an open conversation on this database; this test does not close it")
    view = service.start_conversation(session, live, title=MARK, now=NOON)
    for i, (owner, text) in enumerate(LINES):
        service.add_segment(
            session,
            live,
            None,  # type: ignore[arg-type] - no embeddings, the cipher is never read
            view.id,
            text=text,
            is_owner=owner,
            now=NOON + timedelta(seconds=10 * i),
        )
    service.stop_conversation(session, live, view.id, now=NOON + timedelta(minutes=5))
    session.commit()
    return view.id


def test_the_migration_makes_both_tables_with_the_contract_columns(factory) -> None:
    with factory() as session:
        assert _columns(session, "people_cards") == CARDS
        assert _columns(session, "people_followups") == FOLLOWUPS


def test_followups_recall_and_tamam_on_postgres(factory) -> None:
    calendar = CalendarService(build_fake_calendar_provider(), build_fake_calendar_writer())
    with factory() as session:
        cid = _conversation(session)
        batch = followups.process_conversation(
            session, cid, followups.FakeFollowupExtractor(ITEMS), now=NOON + timedelta(minutes=6)
        )
        session.commit()
        assert (batch.created, batch.proposed, batch.dropped) == (2, 1, 1)
        assert calendar._writer.created == []  # type: ignore[attr-defined]
    with factory() as session:
        row = session.scalars(select(FollowupRow).where(FollowupRow.direction == "owner")).one()
        assert row.due_at is not None and row.due_at.tzinfo is not None
        assert row.spoken_at.tzinfo is not None
        recall = people.recall_promises(session, "AHMET")
        assert "raporu göndermek" in recall.speech and "9 Ekim" in recall.speech
        assert "5 Ekim 2026" in people.last_talk(session, "ahmet").speech
        answer = followups.answer_followups(
            session, cid, accepted=True, calendar=calendar, host_flag_enabled=True, session_id="s"
        )
        session.commit()
        assert answer.written == 1
        assert [p.summary for p in calendar._writer.created] == ["raporu göndermek"]  # type: ignore[attr-defined]


def test_keys_checks_and_foreign_keys_hold_in_the_database(factory) -> None:
    now = datetime.now(UTC)
    with factory() as session:
        cid = _conversation(session)
        followups.process_conversation(session, cid, followups.FakeFollowupExtractor(ITEMS))
        session.commit()
        session.add(PersonCardRow(name="AHMET", name_key="ahmet", created_at=now, updated_at=now))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        card = session.scalars(select(PersonCardRow)).one()
        session.add(
            FollowupRow(
                card_id=card.id,
                kind="wish",
                what="x",
                segment_seq=1,
                quote="x",
                spoken_at=now,
                created_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    with factory() as session:
        # A deleted conversation leaves the follow-ups, quotes and all.
        assert service.delete_conversation(session, LiveConversations(), cid)
        session.commit()
        rows = session.scalars(select(FollowupRow)).all()
        assert len(rows) == 2 and all(r.conversation_id is None for r in rows)
        assert all(r.quote for r in rows)
        # A deleted card takes its follow-ups.
        session.execute(delete(PersonCardRow))
        session.commit()
        assert session.execute(select(func.count(FollowupRow.id))).scalar_one() == 0


def test_downgrade_drops_both_tables_and_upgrade_recreates_them(factory) -> None:
    command.downgrade(_alembic(), _before())
    try:
        with factory() as session:
            assert _columns(session, "people_cards") == {}
            assert _columns(session, "people_followups") == {}
    finally:
        command.upgrade(_alembic(), "head")
    with factory() as session:
        assert _columns(session, "people_cards") == CARDS
        assert _columns(session, "people_followups") == FOLLOWUPS
