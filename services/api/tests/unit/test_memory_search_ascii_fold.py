"""Unit tests: the memory's word leg finds a Turkish word typed in ASCII (card
memory-search-ascii-fold).

Test team round t-w10070808 on staging b1f8ef94: a memory search for 'sukru' did not find the
memory about 'Şükrü'. Both sides fold the way the conversation search does
(``app.conversations.search.search_fold``): ş->s, ü->u, ğ->g, ı/İ->i, ç->c, ö->o, case.
Both word-leg modes: ``like`` (the default, what staging runs) and ``trgm`` (SQLite half).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.memory import lexical, service
from app.memory.embedding import DeterministicEmbedder
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.retrieval import RetrievalFilters, keyword_candidates
from app.memory.service import MemoryLinks
from app.memory.types import MemoryClass

MEMORY_TABLES = [
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
]

EMBEDDER = DeterministicEmbedder()


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    for table in MEMORY_TABLES:
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(params=["like", "trgm"])
def mode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "like":
        monkeypatch.delenv(lexical.MODE_ENV, raising=False)
    else:
        monkeypatch.setenv(lexical.MODE_ENV, request.param)
    return request.param


def _remember(db: Session, text: str, conversation_id: uuid.UUID) -> uuid.UUID:
    return service.remember_explicit(
        db,
        EMBEDDER,
        text=text,
        memory_class=MemoryClass.SEMANTIC,
        links=MemoryLinks(conversation_id=conversation_id),
    ).memory_id


def _found(db: Session, query: str, conversation: uuid.UUID) -> list[uuid.UUID]:
    return [
        m.id for m in keyword_candidates(db, query, RetrievalFilters(conversation_id=conversation))
    ]


def test_ascii_sukru_finds_turkish_sukru(db: Session, mode: str) -> None:
    conversation = uuid.uuid4()
    target = _remember(db, "Şükrü cumartesi matkabı geri getirecek.", conversation)
    _remember(db, "Ankara'daki toplantı ertelendi.", conversation)
    assert _found(db, "sukru", conversation) == [target]


def test_capital_sukru_finds_lower_case_sukru(db: Session, mode: str) -> None:
    conversation = uuid.uuid4()
    target = _remember(db, "şükrü'nün doğum günü salı.", conversation)
    _remember(db, "Kombi bakımı yapılacak.", conversation)
    assert _found(db, "ŞÜKRÜ", conversation) == [target]


def test_ascii_query_folds_every_turkish_letter(db: Session, mode: str) -> None:
    conversation = uuid.uuid4()
    target = _remember(db, "Çiğdem Gökçe ışığı söndürmeyi unuttu.", conversation)
    assert _found(db, "cigdem gokce isigi", conversation) == [target]


def test_turkish_query_finds_ascii_memory(db: Session, mode: str) -> None:
    conversation = uuid.uuid4()
    target = _remember(db, "Sukru aradi, yarin gelecek.", conversation)
    assert _found(db, "Şükrü", conversation) == [target]


def test_a_different_word_still_does_not_match(db: Session, mode: str) -> None:
    conversation = uuid.uuid4()
    _remember(db, "Şükrü cumartesi matkabı geri getirecek.", conversation)
    assert _found(db, "mehmet", conversation) == []
    assert _found(db, "sakir", conversation) == []
