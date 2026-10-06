"""Two devices writing one conversation at once must not lose a line (test team, 2026-10-06).

Round t-manual-20261006d, tester-3: two concurrent ``POST /v1/conversations/{id}/segments``
on one conversation -> one 201, one 409 ``store_conflict``; at four, two lines were lost.
``add_segment`` read ``max(seq)`` and inserted ``max + 1``; the loser hit
``uq_conversation_segments_seq``. The fix locks the conversation row (``SELECT ... FOR
UPDATE``) BEFORE the next number is read, so the writers queue on the row and each reads the
number the previous one committed. SQLite has no row locks: this test proves the ORDER of the
statements (the lock, rendered for PostgreSQL, comes before the ``max(seq)`` read); the real
race is ``tests/integration/test_conversations_segment_race_postgres.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.conversations import service
from app.conversations.models import (
    ConversationRow,
    ConversationSettingRow,
    PersonRow,
    SegmentRow,
)
from app.conversations.service import LiveConversations
from app.voice.crypto import ProfileCipher

NOON = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
CIPHER = ProfileCipher("test-secret")
TABLES = (ConversationRow, SegmentRow, PersonRow, ConversationSettingRow)


@pytest.fixture()
def factory():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.__table__.create(eng)
    yield sessionmaker(bind=eng, expire_on_commit=False)
    eng.dispose()


def _postgres_sql(db: Session, run) -> list[str]:  # noqa: ANN001
    """Every ORM statement ``run`` issues, as PostgreSQL would receive it."""
    seen: list[str] = []

    def record(state) -> None:  # noqa: ANN001
        seen.append(str(state.statement.compile(dialect=postgresql.dialect())))

    event.listen(db, "do_orm_execute", record)
    try:
        run()
    finally:
        event.remove(db, "do_orm_execute", record)
    return seen


@pytest.mark.parametrize("embedding", [None, [1.0, 0.1, 0.0, 0.0]])
def test_the_conversation_row_is_locked_before_the_next_number_is_read(factory, embedding) -> None:
    live = LiveConversations()
    with factory() as db:
        cid = service.start_conversation(db, live, now=NOON).id
        db.commit()
    with factory() as db:
        sql = _postgres_sql(
            db,
            lambda: service.add_segment(
                db, live, CIPHER, cid, text="Merhaba", embedding=embedding, is_owner=None
            ),
        )
        db.commit()
    locks = [
        i
        for i, s in enumerate(sql)
        if "FROM conversations" in s and s.rstrip().endswith("FOR UPDATE")
    ]
    reads = [i for i, s in enumerate(sql) if "max(conversation_segments.seq)" in s]
    assert locks, f"no FOR UPDATE on the conversation row: {sql}"
    assert reads, sql
    assert locks[0] < reads[0], sql


def test_a_closed_conversation_is_still_refused_after_the_lock(factory) -> None:
    live = LiveConversations()
    with factory() as db:
        cid = service.start_conversation(db, live, now=NOON).id
        service.stop_conversation(db, live, cid)
        db.commit()
    with factory() as db, pytest.raises(service.ConversationRefused) as refused:
        service.add_segment(db, live, CIPHER, cid, text="Geç kaldım", is_owner=True)
    assert refused.value.code == "closed"


def test_numbers_stay_gapless_and_in_arrival_order(factory) -> None:
    live = LiveConversations()
    with factory() as db:
        cid = service.start_conversation(db, live, now=NOON).id
        db.commit()
    for n in range(5):
        with factory() as db:
            service.add_segment(db, live, CIPHER, cid, text=f"satır {n}", is_owner=True)
            db.commit()
    with factory() as db:
        lines = service.get_conversation(db, cid).segments
    assert [(s.seq, s.text) for s in lines] == [(n + 1, f"satır {n}") for n in range(5)]
