"""The connected-accounts tables on the REAL database (migration 0068), not on SQLite.

What PostgreSQL says and SQLite cannot: the tables and columns are the ones the MIGRATION
makes (tokens as ``bytea``, never text), the Turkish-casefold name key is unique in the
database too, one Message-ID may be indexed once per account under the new composite index,
a full connect -> refresh -> disconnect runs against the migrated schema, and ``downgrade()``
takes it all away and ``upgrade`` brings it back. The two account tables and the test's own
``mail_index`` rows are emptied around every test.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.accounts.models import MailAccountPendingRow, MailAccountRow
from app.accounts.service import AccountsService
from app.config import Settings
from app.db import build_engine, build_session_factory
from app.mail.models import MailIndexRow

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "0066_watches"
TAG = "pg-mail-accounts-test"
REFRESH = "1//REFRESH-SECRET-postgres"  # noqa: S105 - test fixture
#: Index rows are keyed by the account's id (``mail_accounts.id`` as text), never its name.
IS_KEY = "6f1d2c3b-0000-4000-8000-000000000001"
KISISEL_KEY = "6f1d2c3b-0000-4000-8000-000000000002"

ACCOUNTS = {
    "id": ("uuid", None, "NO"),
    "name": ("character varying", 40, "NO"),
    "name_key": ("character varying", 40, "NO"),
    "provider": ("character varying", 16, "NO"),
    "address": ("character varying", 320, "NO"),
    "scopes_json": ("jsonb", None, "NO"),
    "refresh_token_enc": ("bytea", None, "NO"),
    "access_token_enc": ("bytea", None, "YES"),
    "access_expires_at": ("timestamp with time zone", None, "YES"),
    "state": ("character varying", 16, "NO"),
    "last_error": ("character varying", 120, "YES"),
    "connected_at": ("timestamp with time zone", None, "NO"),
    "last_sync_at": ("timestamp with time zone", None, "YES"),
}
PENDING = {
    "id": ("uuid", None, "NO"),
    "state_hash": ("character varying", 64, "NO"),
    "provider": ("character varying", 16, "NO"),
    "name": ("character varying", 40, "NO"),
    "code_verifier_enc": ("bytea", None, "NO"),
    "created_at": ("timestamp with time zone", None, "NO"),
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
            session.execute(delete(MailAccountPendingRow))
            session.execute(delete(MailAccountRow))
            session.execute(delete(MailIndexRow).where(MailIndexRow.folder == TAG))
            session.commit()

    clear()
    try:
        yield sessions
    finally:
        clear()
        engine.dispose()


def _columns(session: Session, table: str) -> dict[str, tuple]:
    rows = session.execute(
        sql_text(
            "SELECT column_name, data_type, character_maximum_length, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table"
        ),
        {"table": table},
    ).all()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _tokens(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "/token" in request.url.path:
        return httpx.Response(
            200, json={"access_token": "at-1", "refresh_token": REFRESH, "expires_in": 3600}
        )
    if "profile" in url:
        return httpx.Response(200, json={"emailAddress": "kisisel@gmail.com"})
    if "graph.microsoft.com" in url:
        return httpx.Response(200, json={"mail": "is@aktivra.com.tr"})
    if "revoke" in url:
        return httpx.Response(200, json={})
    return httpx.Response(404)


def _service() -> AccountsService:
    return AccountsService(
        Settings(
            _env_file=None,
            accounts_public_base_url="https://pagentos-core.tail1234.ts.net",
            accounts_token_secret="integration-secret",
            accounts_google_client_id="g",
            accounts_google_client_secret="gs",
            accounts_microsoft_client_id="m",
            accounts_microsoft_client_secret="ms",
        ),
        transport=httpx.MockTransport(_tokens),
    )


def _connect(service: AccountsService, session: Session, provider: str, name: str) -> dict:
    url = service.start(session, provider=provider, name=name)["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    return service.complete(session, state=state, code="c")


def test_the_chain_has_one_head_and_0068_follows_the_base_tip() -> None:
    """Inspector finding 11: 0068 and a sibling's 0067 both revising 0066 would give two
    heads and ``upgrade head`` would refuse."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic())
    # One head (the conversation tables follow 0068 since the 2026-10-06 integration).
    assert len(script.get_heads()) == 1
    assert script.get_revision("0068_mail_accounts").down_revision == BEFORE


def test_the_migration_makes_the_tables_and_columns(factory) -> None:
    with factory() as session:
        assert _columns(session, "mail_accounts") == ACCOUNTS
        assert _columns(session, "mail_account_pending") == PENDING
        assert _columns(session, "mail_index")["account_key"] == ("character varying", 64, "NO")
        assert _columns(session, "mail_drafts")["account_key"] == ("character varying", 64, "YES")


def test_connect_refresh_rename_disconnect_on_postgres(factory) -> None:
    service = _service()
    with factory() as session:
        gmail = _connect(service, session, "gmail", "Kişisel")
        _connect(service, session, "microsoft", "İş")
        assert [a["name"] for a in service.list(session)] == ["Kişisel", "İş"]
        stored = session.execute(
            sql_text("SELECT refresh_token_enc FROM mail_accounts WHERE name = 'Kişisel'")
        ).scalar_one()
        assert REFRESH.encode() not in bytes(stored)
        later = datetime.now(UTC) + timedelta(hours=2)
        assert service.access_token(session, name="kişisel", now=later) == "at-1"
        assert service.rename(session, gmail["id"], "Aile")["name"] == "Aile"
        assert service.disconnect(session, gmail["id"])["revoked"] is True
        assert [a["name"] for a in service.list(session)] == ["İş"]


def test_the_name_key_is_unique_in_the_database(factory) -> None:
    def row(name: str, key: str) -> MailAccountRow:
        return MailAccountRow(
            id=uuid.uuid4(),
            name=name,
            name_key=key,
            provider="gmail",
            address="",
            scopes_json=[],
            refresh_token_enc=b"x",
            state="connected",
            connected_at=datetime.now(UTC),
        )

    with factory() as session:
        session.add(row("İş", "iş"))
        session.commit()
        session.add(row("iş", "iş"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_one_message_is_indexed_once_per_account(factory) -> None:
    now = datetime.now(UTC)
    with factory() as session:
        for account in (IS_KEY, KISISEL_KEY):
            session.add(
                MailIndexRow(
                    id=uuid.uuid4(),
                    account_key=account,
                    provider_message_id="<same@example.com>",
                    folder=TAG,
                    last_used_at=now,
                )
            )
        session.commit()
        session.add(
            MailIndexRow(
                id=uuid.uuid4(),
                account_key=IS_KEY,
                provider_message_id="<same@example.com>",
                folder=TAG,
                last_used_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        count = (
            session.execute(select(MailIndexRow).where(MailIndexRow.folder == TAG)).scalars().all()
        )
        assert len(count) == 2


def test_downgrade_drops_it_all_and_upgrade_brings_it_back(factory) -> None:
    with factory() as session:
        _connect(_service(), session, "gmail", "Kişisel")
    command.downgrade(_alembic(), BEFORE)
    try:
        with factory() as session:
            assert _columns(session, "mail_accounts") == {}
            assert _columns(session, "mail_account_pending") == {}
            assert "account_key" not in _columns(session, "mail_index")
            assert "account_key" not in _columns(session, "mail_drafts")
    finally:
        command.upgrade(_alembic(), "head")
    with factory() as session:
        assert _columns(session, "mail_accounts") == ACCOUNTS
        assert service_list_is_empty(session)


def service_list_is_empty(session: Session) -> bool:
    return session.execute(select(MailAccountRow)).scalars().all() == []
