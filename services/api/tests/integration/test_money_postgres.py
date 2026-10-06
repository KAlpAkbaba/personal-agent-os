"""The money ledger on the REAL database (migration ``money_ledger``), not on SQLite.

What SQLite cannot say and PostgreSQL does: the five tables are the ones the MIGRATION makes
(the model never builds them here), the amounts are BIGINT kuruş, a timestamptz comes back
aware (the match window subtracts it from an aware clock), the bank reference is unique in the
database too (one bank mail can never confirm two rows, nor be booked twice past the service),
the CHECK refuses a zero amount and an unknown status, and ``downgrade()`` takes all five away
cleanly. The tables are emptied around every test; nothing else writes to them.

The revision before this one is read from the migration itself, never written here: its
``down_revision`` is re-pointed at merge when another migration lands first.
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
from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.conversations.models import ConversationRow, SegmentRow
from app.db import build_engine, build_session_factory
from app.mail.models import MailIndexRow
from app.money import ledger, loop, service
from app.money.models import (
    MoneyBalance,
    MoneyBankNotice,
    MoneyEntry,
    MoneyQuestion,
    MoneyScan,
)
from app.notifications.models import NotificationRow
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
REVISION = "0071_money_ledger"
T0 = datetime(2026, 10, 6, 11, 0, tzinfo=UTC)
TABLES = ("money_entries", "money_balances", "money_bank_notices", "money_questions", "money_scans")
BANK_SENDER = "bilgilendirme@garantibbva.com.tr"

ENTRIES = {
    "id": ("uuid", None, "NO"),
    "direction": ("character varying", 4, "NO"),
    "amount_kurus": ("bigint", None, "NO"),
    "status": ("character varying", 12, "NO"),
    "source": ("character varying", 16, "NO"),
    "method": ("character varying", 8, "YES"),
    "category": ("character varying", 24, "YES"),
    "description": ("character varying", 120, "NO"),
    "occurred_at": ("timestamp with time zone", None, "NO"),
    "created_at": ("timestamp with time zone", None, "NO"),
    "updated_at": ("timestamp with time zone", None, "NO"),
    "confirmed_at": ("timestamp with time zone", None, "YES"),
    "cancelled_at": ("timestamp with time zone", None, "YES"),
    "conversation_id": ("uuid", None, "YES"),
    "segment_seq": ("integer", None, "YES"),
    "bank_ref": ("character varying", 600, "YES"),
    "bank": ("character varying", 40, "YES"),
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
    marker: list[uuid.UUID] = []

    def clear() -> None:
        with sessions() as session:
            for model in (MoneyBankNotice, MoneyQuestion, MoneyScan, MoneyBalance, MoneyEntry):
                session.execute(delete(model))
            session.execute(delete(MailIndexRow).where(MailIndexRow.from_email == BANK_SENDER))
            session.execute(
                delete(NotificationRow).where(
                    NotificationRow.kind.in_((loop.KIND_BOOKED, loop.KIND_QUESTION))
                )
            )
            for cid in marker:
                session.execute(delete(SegmentRow).where(SegmentRow.conversation_id == cid))
                session.execute(delete(ConversationRow).where(ConversationRow.id == cid))
            session.commit()

    clear()
    sessions.created_conversations = marker  # type: ignore[attr-defined]
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


def _mail(session: Session, body: str, at: datetime) -> None:
    session.add(
        MailIndexRow(
            id=uuid.uuid4(),
            provider_message_id=f"<{uuid.uuid4()}@garanti>",
            account_key="",
            folder="INBOX",
            from_name="Garanti BBVA",
            from_email=BANK_SENDER,
            to_json=[],
            subject="Kartınızdan harcama yapıldı",
            date=at,
            snippet=body,
            has_attachments=False,
            thread_key="",
            unread=True,
            last_used_at=at,
        )
    )
    session.commit()


def test_the_migration_makes_five_tables_with_the_contract_columns(factory) -> None:
    with factory() as session:
        assert _columns(session, "money_entries") == ENTRIES
        for table in TABLES:
            assert _columns(session, table), table
        assert _columns(session, "money_balances")["balance_kurus"][0] == "bigint"


def test_a_conversation_spend_is_booked_then_confirmed_by_the_bank_mail_once(factory) -> None:
    now = datetime.now(UTC)
    cid = uuid.uuid4()
    factory.created_conversations.append(cid)
    with factory() as session:
        session.add(ConversationRow(id=cid, mode="home", started_at=now, ended_at=None))
        for seq, (owner, said) in enumerate(
            [(True, "Bu ceket ne kadar?"), (False, "Yedi yüz elli lira."), (True, "Tamam alayım.")],
            start=1,
        ):
            session.add(
                SegmentRow(
                    id=uuid.uuid4(),
                    conversation_id=cid,
                    seq=seq,
                    spoken_at=now + timedelta(seconds=seq),
                    text=said,
                    is_owner=owner,
                )
            )
        session.commit()
        assert loop.scan_conversations(session, now=now + timedelta(seconds=20))["booked"] == 1
        assert loop.scan_conversations(session, now=now + timedelta(seconds=40))["booked"] == 0
        _mail(
            session, "LCW MODA işyerinde 750,00 TL harcama yapılmıştır.", now + timedelta(minutes=3)
        )
        assert ledger.ingest_mail(session, now=now + timedelta(minutes=4))["matched"] == 1
        assert ledger.ingest_mail(session, now=now + timedelta(minutes=9))["read"] == 0
    with factory() as session:
        row = session.scalars(select(MoneyEntry)).one()
        assert row.status == service.STATUS_CONFIRMED and row.amount_kurus == 75000
        assert row.occurred_at.tzinfo is not None and row.category == "giyim"
        assert service.spent(session, category="giyim", now=now).total_kurus == 75000


def test_the_bank_reference_is_unique_and_the_checks_hold_in_the_database(factory) -> None:
    with factory() as session:
        first = service.book_spend(
            session,
            1000,
            status=service.STATUS_CONFIRMED,
            source=service.SOURCE_BANK,
            method="card",
            occurred_at=T0,
            now=T0,
            bank_ref=":<x@bank>",
        )
        assert first.id is not None
        session.add(
            MoneyEntry(
                id=uuid.uuid4(),
                amount_kurus=2000,
                status="confirmed",
                source="bank",
                direction="out",
                description="",
                occurred_at=T0,
                created_at=T0,
                updated_at=T0,
                bank_ref=":<x@bank>",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        for bad in ({"amount_kurus": 0, "status": "confirmed"}, {"amount_kurus": 5, "status": "x"}):
            session.add(
                MoneyEntry(
                    id=uuid.uuid4(),
                    source="web",
                    direction="out",
                    description="",
                    occurred_at=T0,
                    created_at=T0,
                    updated_at=T0,
                    **bad,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()


def test_the_routes_through_the_real_application(factory, settings) -> None:
    client = owner_client(settings)
    cash = client.post("/v1/money/cash", json={"amount": "1.234,56", "category": "market"})
    assert cash.status_code == 200, cash.text
    body = client.get("/v1/money").json()
    assert body["entries"][0]["amount_kurus"] == 123456
    assert body["month"]["cash_kurus"] == 123456
    assert "money_spend_loop" in client.get("/v1/system/health").json()["checks"]


def test_downgrade_drops_all_five_and_upgrade_recreates_them(factory) -> None:
    with factory() as session:
        service.book_spend(
            session,
            500,
            status=service.STATUS_CONFIRMED,
            source=service.SOURCE_WEB,
            method="cash",
            occurred_at=T0,
            now=T0,
        )
    command.downgrade(_alembic(), _before())
    try:
        with factory() as session:
            for table in TABLES:
                assert _columns(session, table) == {}, table
    finally:
        command.upgrade(_alembic(), "head")
    with factory() as session:
        assert _columns(session, "money_entries") == ENTRIES
        assert session.scalars(select(MoneyEntry)).all() == []
