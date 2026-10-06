"""The alarm's receipts on the REAL database (migration ``urgent_alert_receipts``).

What SQLite cannot say and PostgreSQL does: the table is the one the MIGRATION makes (the
model never builds it here), the receipt id is unique in the database, the outcome/source
CHECKs refuse a word outside the contract, a timestamptz comes back aware, the foreign key
holds the notification, the partial index exists, and ``downgrade()`` takes the table away
cleanly. Then the loop end to end on that table with a fake Pushover: acknowledged ->
``alert.seen``, rang out -> ``alert.unseen``, nothing open -> no request, a new process keeps
asking, read in the inbox -> cancelled.

The revision before this one is read from the tree, never written here (migration_ids).
Run it against a scratch database (``PAGENTOS_DATABASE_URL``): the session fixture upgrades
whatever database it is given to head.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.ledger import vocabulary as v
from app.ledger.models import ActivityEventRow
from app.notifications import service as notifications
from app.notifications.models import PRIORITY_URGENT, NotificationRow
from app.telephony import policy
from app.urgent_alert import wiring
from app.urgent_alert.models import UrgentAlertReceiptRow
from tests.integration.migration_ids import parent_of, revision_named

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
REVISION = revision_named("urgent_alert_receipts")
RECEIPT = "rReceiptIdIntegration000000001"
LINK_BASE = "https://home-pc.tail1234.ts.net"
KIND = policy.KIND_SECURITY_CRITICAL
COLUMNS = {
    "id": ("uuid", None, "NO"),
    "notification_id": ("uuid", None, "NO"),
    "receipt_id": ("character varying", 64, "NO"),
    "sent_at": ("timestamp with time zone", None, "NO"),
    "expires_at": ("timestamp with time zone", None, "NO"),
    "closed_at": ("timestamp with time zone", None, "YES"),
    "outcome": ("character varying", 16, "YES"),
    "seen_at": ("timestamp with time zone", None, "YES"),
    "source": ("character varying", 16, "NO"),
}


def _alembic() -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


class FakePushover:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.receipt: dict = {"status": 1, "acknowledged": 0, "expired": 0}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path.endswith("/messages.json"):
            return httpx.Response(200, json={"status": 1, "request": "q", "receipt": RECEIPT})
        if request.url.path.endswith("/cancel.json"):
            return httpx.Response(200, json={"status": 1, "request": "q"})
        return httpx.Response(200, json=self.receipt)


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    command.upgrade(_alembic(), "head")
    engine = build_engine(Settings().database_url)
    sessions = build_session_factory(engine)

    def clear() -> None:
        with sessions() as session:
            session.execute(delete(UrgentAlertReceiptRow))
            session.execute(
                delete(ActivityEventRow).where(
                    ActivityEventRow.subsystem == v.SUBSYSTEM_URGENT_ALERT
                )
            )
            session.execute(
                delete(NotificationRow).where(
                    NotificationRow.kind.in_((KIND, policy.KIND_URGENT_ALERT_TEST))
                )
            )
            session.commit()

    clear()
    try:
        yield sessions
    finally:
        command.upgrade(_alembic(), "head")
        clear()
        engine.dispose()


def _settings() -> Settings:
    return Settings(
        urgent_alert_pushover_app_token=SecretStr("aAppTokenIntegration0000000000"),
        urgent_alert_pushover_user_key=SecretStr("uUserKeyIntegration00000000000"),
        urgent_alert_link_base=LINK_BASE,
    )


def _world(factory, server: FakePushover, now: datetime):  # noqa: ANN202
    rung = wiring.build_alarm_rung(
        _settings(),
        factory,
        client=httpx.Client(transport=httpx.MockTransport(server)),
        clock=lambda: now,
    )
    assert rung is not None
    return rung, wiring.build_receipt_loop(_settings(), factory, rung, clock=lambda: now)


def _ring(factory, server: FakePushover, now: datetime) -> NotificationRow:
    rung, _ = _world(factory, server, now)
    with factory() as db:
        row = notifications.record(
            db, kind=KIND, title="Güvenlik", body="Acil.", priority=PRIORITY_URGENT, now=now
        )
    assert rung.deliver(row) is True
    server.paths.clear()
    return row


def _columns(session: Session) -> dict[str, tuple[str, int | None, str]]:
    rows = session.execute(
        sql_text(
            "SELECT column_name, data_type, character_maximum_length, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'urgent_alert_receipts'"
        )
    )
    return {name: (kind, width, nullable) for name, kind, width, nullable in rows}


def _events(factory, event_type: str) -> list[ActivityEventRow]:
    with factory() as db:
        return list(
            db.scalars(select(ActivityEventRow).where(ActivityEventRow.event_type == event_type))
        )


def test_the_migration_makes_the_contract_table(factory) -> None:
    with factory() as session:
        assert _columns(session) == COLUMNS
        index = session.execute(
            sql_text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_urgent_alert_receipts_open'"
            )
        ).scalar_one()
        assert "closed_at IS NULL" in index


def test_the_receipt_id_is_unique_and_the_words_are_closed(factory) -> None:
    now = datetime.now(UTC)
    with factory() as db:
        row = notifications.record(db, kind=KIND, priority=PRIORITY_URGENT, now=now)
        base = {
            "notification_id": row.id,
            "receipt_id": RECEIPT,
            "sent_at": now,
            "expires_at": now + timedelta(hours=3),
            "source": "pushover",
        }
        db.add(UrgentAlertReceiptRow(**base))
        db.commit()
        db.add(UrgentAlertReceiptRow(**base))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(UrgentAlertReceiptRow(**{**base, "receipt_id": "other1", "source": "sms"}))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(
            UrgentAlertReceiptRow(
                **{**base, "notification_id": uuid.uuid4(), "receipt_id": "other2"}
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_down_and_up_again(factory) -> None:
    command.downgrade(_alembic(), parent_of(REVISION))
    with factory() as session:
        assert _columns(session) == {}
    command.upgrade(_alembic(), "head")
    with factory() as session:
        assert _columns(session) == COLUMNS


def test_acknowledged_is_seen_with_its_time(factory) -> None:
    server = FakePushover()
    now = datetime.now(UTC).replace(microsecond=0)
    row = _ring(factory, server, now)
    ack = now + timedelta(minutes=2)
    server.receipt = {
        "status": 1,
        "acknowledged": 1,
        "acknowledged_at": int(ack.timestamp()),
        "expired": 0,
    }
    _, loop = _world(factory, server, now + timedelta(minutes=3))

    assert loop.run_once()["seen"] == 1
    with factory() as db:
        receipt = db.scalars(select(UrgentAlertReceiptRow)).one()
    assert receipt.outcome == "seen" and receipt.seen_at == ack and receipt.seen_at.tzinfo
    assert [e.source_ref for e in _events(factory, v.EVENT_TYPE_ALERT_SEEN)] == [
        f"notification:{row.id}"
    ]
    assert len(_events(factory, v.EVENT_TYPE_ALERT_SENT)) == 1


def test_rang_out_is_unseen(factory) -> None:
    server = FakePushover()
    now = datetime.now(UTC)
    _ring(factory, server, now)
    server.receipt = {"status": 1, "acknowledged": 0, "expired": 1}
    _, loop = _world(factory, server, now + timedelta(hours=3))
    assert loop.run_once()["unseen"] == 1
    assert len(_events(factory, v.EVENT_TYPE_ALERT_UNSEEN)) == 1


def test_nothing_open_means_no_request_and_a_restart_keeps_asking(factory) -> None:
    server = FakePushover()
    now = datetime.now(UTC)
    _, idle = _world(factory, server, now)
    idle.run_once()
    assert server.paths == []

    _ring(factory, server, now)
    _, fresh = _world(factory, server, now + timedelta(minutes=1))  # a new process
    fresh.run_once()
    assert server.paths == [f"/1/receipts/{RECEIPT}.json"]


def test_read_in_the_inbox_cancels_the_ringing(factory) -> None:
    server = FakePushover()
    now = datetime.now(UTC).replace(microsecond=0)
    row = _ring(factory, server, now)
    with factory() as db:
        notifications.mark_read(db, row.id, now=now + timedelta(minutes=1))
    _, loop = _world(factory, server, now + timedelta(minutes=2))

    assert loop.run_once()["cancelled"] == 1
    assert server.paths == [f"/1/receipts/{RECEIPT}/cancel.json"]
    with factory() as db:
        receipt = db.scalars(select(UrgentAlertReceiptRow)).one()
    assert (receipt.outcome, receipt.source) == ("cancelled", "inbox")
    seen = _events(factory, v.EVENT_TYPE_ALERT_SEEN)
    assert [e.detail_json["source"] for e in seen] == ["inbox"]
