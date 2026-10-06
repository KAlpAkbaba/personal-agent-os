"""The verify mode's table on the REAL database (migration 0067), not on SQLite.

What SQLite cannot say and PostgreSQL does: the table is the one the MIGRATION makes (widths,
nullability, the status check), a timestamptz comes back aware, the JSONB sources round-trip,
and the downgrade really removes the table and the upgrade really brings it back. The store's
own calls (``app.research.verify``) are taken against the dev stack's PostgreSQL after
``alembic upgrade head``.

``claim_verifications`` is emptied around every test: nothing else writes to it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete, inspect
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.research import verify
from app.research.models import ClaimVerificationRow
from tests.integration.migration_ids import parent_of, revision_named

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
TABLE = "claim_verifications"
REVISION = revision_named("claim_verifications")
BEFORE = parent_of(REVISION)
NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)

#: column -> (data_type, character_maximum_length, is_nullable), as information_schema says.
CONTRACT = {
    "id": ("uuid", None, "NO"),
    "created_at": ("timestamp with time zone", None, "NO"),
    "said": ("character varying", 2000, "NO"),
    "claim": ("character varying", 1000, "NO"),
    "status": ("character varying", 16, "NO"),
    "task_id": ("uuid", None, "YES"),
    "verdict": ("character varying", 16, "YES"),
    "confidence": ("double precision", None, "YES"),
    "sources_json": ("jsonb", None, "NO"),
    "counter_json": ("jsonb", None, "YES"),
    "spoken": ("character varying", 1000, "YES"),
    "settled_at": ("timestamp with time zone", None, "YES"),
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
            session.execute(delete(ClaimVerificationRow))
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
            "WHERE table_schema = current_schema() AND table_name = :table"
        ),
        {"table": TABLE},
    )
    return {name: (kind, width, nullable) for name, kind, width, nullable in rows}


def test_the_migration_makes_the_contracted_table(factory) -> None:
    with factory() as session:
        assert _columns(session) == CONTRACT
        indexes = {ix["name"] for ix in inspect(session.bind).get_indexes(TABLE)}
        assert {"ix_claim_verifications_created_at", "ix_claim_verifications_task_id"} <= indexes


def test_downgrade_removes_the_table_and_upgrade_brings_it_back(factory) -> None:
    command.downgrade(_alembic(), BEFORE)
    with factory() as session:
        assert _columns(session) == {}
    command.upgrade(_alembic(), REVISION)
    with factory() as session:
        assert _columns(session) == CONTRACT


def test_a_verification_round_trips_and_is_recalled(factory) -> None:
    with factory() as session:
        row = verify.start_pending(
            session,
            said="bunu doğrula: Ay'ın yüzeyinde su buzu var",
            claim="Ay'ın yüzeyinde su buzu var",
            task_id=None,
            now=NOW,
        )
        payload = verify.settle(
            session,
            row,
            report_json={
                "sources": [
                    {
                        "url": "https://nasa.example/ay",
                        "title": "NASA: Ay'da su",
                        "published_at": "2026-08-01T00:00:00Z",
                        "excerpt": "NASA verilerine göre Ay'ın yüzeyinde su buzu var.",
                    }
                ]
            },
            run_failed=False,
            now=NOW,
        )
        session.commit()
    assert payload["verdict_label"] == "DOĞRU"
    with factory() as session:
        since, until = verify.recall_window("bu hafta", NOW)
        found = verify.search(session, text="ay su", since=since, until=until)
        assert [r.claim for r in found] == ["Ay'ın yüzeyinde su buzu var"]
        kept = found[0]
        assert kept.created_at == NOW  # timestamptz comes back aware
        assert kept.sources_json[0]["title"] == "NASA: Ay'da su"
        assert kept.sources_json[0]["stance"] == "supports"
        assert "Tek kaynak: NASA: Ay'da su" in (kept.spoken or "")


def test_the_status_is_checked_by_the_database(factory) -> None:
    with factory() as session:
        row = verify.start_pending(session, said="x", claim="y", task_id=None, now=NOW)
        row.status = "guessed"
        with pytest.raises(IntegrityError):
            session.commit()
