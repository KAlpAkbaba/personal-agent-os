"""Conversation search without Turkish letters (conversation-search-turkish-fold).

Test team round t-manual-20261006e (tester-3): ``GET /v1/conversations?q=ISIGI`` did not find
the conversation whose line says 'ışığı'. The owner types on a phone keyboard, often without
Turkish letters or in capitals, so both sides fold the Turkish way: ı/i/İ/I -> i, ş -> s,
ğ -> g, ü -> u, ö -> o, ç -> c. The fold runs the same in Python and in SQL (no new column,
no migration).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, literal, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.conversations import service
from app.conversations.models import (
    ConversationRow,
    ConversationSettingRow,
    PersonRow,
    SegmentRow,
)
from app.conversations.search import search_fold, search_fold_sql
from app.conversations.service import LiveConversations
from app.voice.crypto import ProfileCipher

NOON = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
CIPHER = ProfileCipher("test-secret")
TABLES = (ConversationRow, SegmentRow, PersonRow, ConversationSettingRow)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def _conversation_saying(db, live, *lines: str):
    cid = service.start_conversation(db, live, now=NOON).id
    for at, text in enumerate(lines):
        service.add_segment(
            db, live, CIPHER, cid, text=text, is_owner=True, now=NOON + timedelta(seconds=at)
        )
    service.stop_conversation(db, live, cid, now=NOON + timedelta(minutes=1))
    db.commit()
    return cid


@pytest.mark.parametrize("needle", ["ISIGI", "isigi", "IŞIĞI", "ışığı", "Işığı", "İŞIĞI"])
def test_every_spelling_of_isigi_finds_the_lamp_line(factory, needle) -> None:
    live = LiveConversations()
    with factory() as db:
        cid = _conversation_saying(db, live, "ışığı yak")
        _conversation_saying(db, live, "başka bir şey")
        assert [c.id for c in service.list_conversations(db, q=needle)] == [cid]


@pytest.mark.parametrize(
    ("needle", "text"),
    [
        ("sut", "süt aldım"),
        ("SUT", "Süt aldım"),
        ("cagri", "ÇAĞRI geldi"),
        ("sofor", "şoför geldi"),
        ("goz", "GÖZLÜK nerede"),
        ("istanbul", "İSTANBUL'a gidiyoruz"),
    ],
)
def test_ascii_typing_finds_turkish_letters(factory, needle, text) -> None:
    live = LiveConversations()
    with factory() as db:
        cid = _conversation_saying(db, live, text)
        assert [c.id for c in service.list_conversations(db, q=needle)] == [cid]


@pytest.mark.parametrize("needle", ["olmayan", "kelime yok", "sutlac", "%", "_"])
def test_a_word_that_is_not_there_finds_nothing(factory, needle) -> None:
    live = LiveConversations()
    with factory() as db:
        _conversation_saying(db, live, "ışığı yak", "süt aldım")
        assert service.list_conversations(db, q=needle) == []


@pytest.mark.parametrize(
    "text", ["ışığı yak", "IŞIĞI YAK", "Süt, ÇAY, şeker; GÖZLÜK", "İğneada ılık", "plain ascii"]
)
def test_python_and_sql_fold_agree(engine, text) -> None:
    with engine.connect() as conn:
        sql = conn.execute(select(search_fold_sql(literal(text)))).scalar_one()
    assert sql == search_fold(text)


def test_the_fold_is_plain_ascii_for_turkish_letters() -> None:
    assert search_fold("IŞIĞI İĞNE ÇÖPÜ ışığı iğne çöpü") == "isigi igne copu isigi igne copu"
