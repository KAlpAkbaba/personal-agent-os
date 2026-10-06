"""Starts at once open ONE conversation, on the real database (test team, 2026-10-06).

Round t-manual-20261006d, tester-3: the phone and the web said 'konuşmayı başlat' at the same
moment - eight concurrent ``POST /v1/conversations`` left four conversations open, where the
design is one open conversation and the rest answered 409. ``start_conversation`` checked for
an open one and then inserted; every racer passed the check before any insert was committed.

The rule now lives in the database: the partial unique index ``uq_conversations_one_open``
(a constant, where ``ended_at IS NULL``) lets one open row exist, and the route turns the
loser's unique violation into the ``409 already_open`` the owner's client already reads.

The races here are deterministic, not timed: the service test holds every racer's commit
until all of them have inserted (or, with the index, until the losers are blocked behind the
winner's insert), and the route test keeps a rival's open row uncommitted while the request
passes its check. The migration test drops and recreates the index, and folds a database that
already has several open conversations (staging after the tester's round) into one.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.conversations import service
from app.conversations.models import (
    ConversationRow,
    ConversationSettingRow,
    PersonRow,
    SegmentRow,
)
from app.conversations.service import LiveConversations
from app.db import build_session_factory
from tests.integration.conftest import owner_client
from tests.integration.migration_ids import parent_of, revision_named

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
NOON = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
STARTERS = 8
INDEX = "uq_conversations_one_open"


def _alembic() -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


def _clear(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        for model in (SegmentRow, ConversationRow, PersonRow, ConversationSettingRow):
            session.execute(delete(model))
        session.commit()


@pytest.fixture()
def factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    command.upgrade(_alembic(), "head")
    engine = create_engine(settings.database_url, pool_size=STARTERS + 2, max_overflow=4)
    sessions = build_session_factory(engine)
    _clear(sessions)
    try:
        yield sessions
    finally:
        command.upgrade(_alembic(), "head")
        _clear(sessions)
        engine.dispose()


def _open_count(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        return int(
            db.execute(
                select(func.count())
                .select_from(ConversationRow)
                .where(ConversationRow.ended_at.is_(None))
            ).scalar_one()
        )


def _index_exists(factory: sessionmaker[Session]) -> bool:
    with factory() as db:
        return (
            db.execute(
                text(
                    "SELECT 1 FROM pg_indexes WHERE tablename = 'conversations' AND indexname = :n"
                ),
                {"n": INDEX},
            ).first()
            is not None
        )


def test_eight_starts_racing_past_the_check_leave_one_open(factory) -> None:
    """Every racer passes the 'is one open?' check before any commit; the database lets one
    insert stand and refuses the other seven with the one-open index."""
    live = LiveConversations()
    gate = threading.Barrier(STARTERS)
    # Held commits: without the index all eight insert and meet here; with it the seven
    # losers are blocked inside their insert, the barrier times out and the winner commits.
    hold = threading.Barrier(STARTERS)

    def start(n: int) -> str:
        with factory() as db:
            db.execute(text("SELECT 1"))
            gate.wait(timeout=60)
            try:
                service.start_conversation(db, live, title=f"başlat {n}", now=NOON)
            except IntegrityError as error:
                db.rollback()
                constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                return f"refused:{constraint}"
            try:
                hold.wait(timeout=3)
            except threading.BrokenBarrierError:
                pass
            db.commit()
        return "opened"

    with ThreadPoolExecutor(max_workers=STARTERS) as pool:
        outcomes = sorted(pool.map(start, range(STARTERS)))

    assert _open_count(factory) == 1, outcomes
    assert outcomes == ["opened"] + [f"refused:{INDEX}"] * (STARTERS - 1), outcomes


def test_a_start_losing_the_race_answers_409_already_open(factory, settings) -> None:
    """A rival's open conversation is inserted but not yet committed while the request runs:
    the route's check sees nothing, its insert waits on the rival, the rival commits, and the
    answer is the 409 ``already_open`` the client reads - not ``store_conflict``, not 500."""
    client = owner_client(settings)
    with factory() as rival:
        rival.add(ConversationRow(mode="manual", title="telefon", started_at=NOON))
        rival.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            answer = pool.submit(client.post, "/v1/conversations", json={"mode": "manual"})
            time.sleep(1.5)  # the request is blocked on the rival's row by now
            assert not answer.done(), "the request did not wait on the rival's insert"
            rival.commit()
            response = answer.result(timeout=60)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "already_open"
    assert "konuşma" in response.json()["detail"]["message"]
    assert _open_count(factory) == 1


def test_eight_posts_at_once_through_the_real_application_one_201_seven_409(
    factory, settings
) -> None:
    client = owner_client(settings)
    gate = threading.Barrier(STARTERS)

    def post(n: int) -> tuple[int, str | None]:
        gate.wait(timeout=60)
        answer = client.post("/v1/conversations", json={"mode": "manual", "title": f"cihaz {n}"})
        code = answer.json().get("detail", {}).get("code") if answer.status_code != 201 else None
        return answer.status_code, code

    with ThreadPoolExecutor(max_workers=STARTERS) as pool:
        answers = sorted(pool.map(post, range(STARTERS)), key=lambda a: a[0])

    assert answers == [(201, None)] + [(409, "already_open")] * (STARTERS - 1), answers
    assert _open_count(factory) == 1
    listed = client.get("/v1/conversations").json()["items"]
    assert sum(1 for c in listed if c["ended_at"] is None) == 1


def test_downgrade_drops_the_index_and_upgrade_folds_open_ones_and_recreates_it(
    factory,
) -> None:
    assert _index_exists(factory)
    command.downgrade(_alembic(), parent_of(revision_named("conversation_one_open")))
    assert not _index_exists(factory)

    # Below the index the old race could leave several open (staging: four): the upgrade
    # keeps the newest open and ends the rest, or it could not build the index at all.
    with factory() as db:
        for n in range(4):
            db.add(
                ConversationRow(
                    mode="manual", title=f"açık {n}", started_at=NOON + timedelta(minutes=n)
                )
            )
        db.add(
            ConversationRow(
                mode="manual",
                title="bitmiş",
                started_at=NOON - timedelta(hours=1),
                ended_at=NOON - timedelta(minutes=30),
            )
        )
        db.commit()
    assert _open_count(factory) == 4

    command.upgrade(_alembic(), revision_named("conversation_one_open"))
    assert _index_exists(factory)
    with factory() as db:
        rows = {r.title: r for r in db.execute(select(ConversationRow)).scalars()}
    assert [t for t, r in rows.items() if r.ended_at is None] == ["açık 3"]
    for n in range(3):
        assert rows[f"açık {n}"].ended_at is not None
    assert rows["bitmiş"].ended_at is not None
    assert len(rows) == 5  # nothing deleted
