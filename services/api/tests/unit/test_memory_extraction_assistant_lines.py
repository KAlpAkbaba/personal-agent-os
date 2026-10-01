"""The assistant's own reply is never a memory candidate (owner's trial 2026-09-30 20:11 UTC).

The summary carries speaker-prefixed lines; the splitter used to treat every sentence as
the owner's, so '| Asistan: Bundan sonra araştırmaları ayrıntılı anlatacağım efendim.' was
filed as a preference candidate 39 s after the owner's own sentence.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.memory.embedding import DeterministicEmbedder
from app.memory.extraction import extract_from_summary
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)

EMBEDDER = DeterministicEmbedder()
_TABLES = [
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
]
OWNER_LINE = "Bundan sonra raporları kısa tutmanı istiyorum."
ASSISTANT_LINE = "Bundan sonra araştırmaları ayrıntılı anlatacağım efendim."


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in _TABLES:
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def _texts(db: Session) -> list[str]:
    return [m.text for m in db.execute(select(Memory)).scalars().all()]


@pytest.mark.parametrize(
    "summary",
    [
        f"| Sahip: {OWNER_LINE} | Asistan: {ASSISTANT_LINE}",
        f"| Owner: {OWNER_LINE} | Assistant: {ASSISTANT_LINE}",
        f"Sahip: {OWNER_LINE}\nAsistan: {ASSISTANT_LINE}",
        f"| Sahip: {OWNER_LINE}\n| Asistan: {ASSISTANT_LINE}",
    ],
)
def test_only_the_owners_sentence_is_filed(db: Session, summary: str) -> None:
    result = extract_from_summary(db, EMBEDDER, summary)
    texts = _texts(db)
    assert len(texts) == 1, texts
    assert "kısa tutmanı" in texts[0]
    assert "ayrıntılı anlatacağım" not in " ".join(texts)
    assert result.skipped >= 1


def test_an_assistant_only_summary_files_nothing_and_counts_skipped(db: Session) -> None:
    result = extract_from_summary(db, EMBEDDER, f"| Asistan: {ASSISTANT_LINE}")
    assert _texts(db) == []
    assert result.written == []
    assert result.skipped == 1
