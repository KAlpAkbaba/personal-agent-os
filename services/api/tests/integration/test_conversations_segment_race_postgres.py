"""Concurrent lines on ONE conversation all land, on the real database (test team, 2026-10-06).

Round t-manual-20261006d, tester-3: the phone and the web capturing the same talk posted
lines at once; of two one came back 409 ``store_conflict``, of four two were lost. Each
writer read ``max(seq)`` and inserted ``max + 1``; ``uq_conversation_segments_seq`` refused
the loser. Only PostgreSQL has the row lock that queues the writers, so the race is proven
here: sixteen at once through the service on sixteen connections, and sixteen through the
real application's route - every one lands, ``seq`` is 1..16 with no gap and no duplicate.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, delete, select
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
from app.voice.crypto import ProfileCipher
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
NOON = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
CIPHER = ProfileCipher("integration-secret")
WRITERS = 16


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
    # Every writer holds its own connection while it waits on the lock.
    engine = create_engine(settings.database_url, pool_size=WRITERS + 2, max_overflow=4)
    sessions = build_session_factory(engine)
    _clear(sessions)
    try:
        yield sessions
    finally:
        _clear(sessions)
        engine.dispose()


def _seqs_and_texts(factory: sessionmaker[Session], cid) -> list[tuple[int, str]]:  # noqa: ANN001
    with factory() as db:
        rows = db.execute(
            select(SegmentRow.seq, SegmentRow.text)
            .where(SegmentRow.conversation_id == cid)
            .order_by(SegmentRow.seq)
        ).all()
    return [(int(seq), text) for seq, text in rows]


def test_sixteen_writers_at_once_through_the_service_all_land(factory) -> None:
    live = LiveConversations()
    with factory() as db:
        cid = service.start_conversation(db, live, now=NOON).id
        db.commit()
    gate = threading.Barrier(WRITERS)

    def write(n: int) -> str:
        with factory() as db:
            # Open the transaction and read the conversation first, then start together:
            # every writer reaches the next-number step at the same moment.
            db.get(ConversationRow, cid)
            gate.wait(timeout=60)
            service.add_segment(db, live, CIPHER, cid, text=f"satır {n:02d}", is_owner=True)
            db.commit()
        return f"satır {n:02d}"

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        futures = [pool.submit(write, n) for n in range(WRITERS)]
        errors = [type(e).__name__ for f in futures if (e := f.exception(timeout=120))]
    assert errors == [], errors

    landed = _seqs_and_texts(factory, cid)
    assert [seq for seq, _ in landed] == list(range(1, WRITERS + 1))
    assert sorted(text for _, text in landed) == [f"satır {n:02d}" for n in range(WRITERS)]


def test_sixteen_posts_at_once_through_the_real_application_all_answer_201(
    factory, settings
) -> None:
    client = owner_client(settings)
    cid = client.post("/v1/conversations", json={"mode": "manual"}).json()["id"]
    gate = threading.Barrier(WRITERS)

    def post(n: int) -> int:
        gate.wait(timeout=60)
        answer = client.post(
            f"/v1/conversations/{cid}/segments", json={"content": f"cümle {n:02d}"}
        )
        return answer.status_code

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        codes = list(pool.map(post, range(WRITERS)))
    assert codes == [201] * WRITERS, codes

    shown = client.get(f"/v1/conversations/{cid}").json()["segments"]
    assert [s["seq"] for s in shown] == list(range(1, WRITERS + 1))
    assert sorted(s["text"] for s in shown) == [f"cümle {n:02d}" for n in range(WRITERS)]
