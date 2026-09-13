"""B16 req 33/34: the write policy's trigger patterns, finally fed a real sentence.

`app.memory.policy` has carried them since M5 — "hatırla", "bundan sonra", "tercih
ederim", "her zaman", "karar" — and the feature matrix's note on requirement 33 was exact:
*kod var, girdi yok*. The decision table, the ladder, the evidence chain and the promotion
rule were all complete, and the only text that had ever reached them arrived through
`POST /v1/memory/observe`.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.memory.embedding import DeterministicEmbedder
from app.memory.extraction import (
    MAX_PER_SUMMARY,
    classify,
    extract_from_summary,
    sentence_key,
)
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.types import Actor, MemoryClass, WriteStage

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

PREFERENCE_SUMMARY = (
    "Sahip günaydın dedi. "
    "Bundan sonra raporları kısa tutmamı istedi. "
    "Hava durumunu sordu."
)


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in _TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


def _rows(db: Session) -> list[Memory]:
    return list(db.execute(select(Memory)).scalars().all())


def _extract(db: Session, summary: str, **kwargs: Any):
    return extract_from_summary(db, EMBEDDER, summary, **kwargs)


# ------------------------------------------------------------------- what it catches


def test_a_preference_said_in_conversation_becomes_a_memory(db):
    """req 33. The first time in this product's history that something the owner SAID
    reaches the write policy."""
    result = _extract(db, PREFERENCE_SUMMARY)

    rows = _rows(db)
    assert len(rows) == 1
    assert "raporları kısa" in rows[0].text
    assert len(result.written) == 1


def test_the_greeting_and_the_question_are_not_memories(db):
    """The other half, and the one that keeps this from being noise: a summary is mostly
    sentences nobody should have to forget later."""
    _extract(db, PREFERENCE_SUMMARY)

    texts = [row.text for row in _rows(db)]
    assert not any("günaydın" in t.lower() for t in texts)
    assert not any("hava durumunu" in t.lower() for t in texts)


def test_a_project_sentence_lands_on_the_project_shelf(db):
    """req 34. `MemoryClass.PROJECT` has existed since M5 and nothing that reads
    conversation ever wrote one."""
    _extract(db, "Bu projede sürüm numaralarını her zaman biz veriyoruz.")

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0].memory_class == MemoryClass.PROJECT.value


def test_classification_is_about_the_shelf_and_not_about_whether_to_write():
    assert classify("Kahveyi sade severim.") is MemoryClass.PREFERENCE
    assert classify("Bu repoda testleri önce yazıyoruz.") is MemoryClass.PROJECT


# ----------------------------------------------------------- what it is NOT allowed to do


def test_a_conversation_never_mints_an_owner_memory(db):
    """M5 review #4, and the reason this module is allowed to exist at all.

    `policy.decide()` grants `Actor.OWNER` on a caller-asserted flag and never on a phrase,
    precisely so that text arriving through an ingestion path cannot promote itself by
    containing the words "bundan sonra". A summary is such a path. The sentence below
    carries the strongest explicit phrase in the table and still lands as a candidate.
    """
    _extract(db, "Bundan sonra raporları kısa tut.")

    row = _rows(db)[0]
    assert row.explicit is False
    assert row.stage == WriteStage.CANDIDATE.value
    assert row.confidence <= 0.4
    assert row.provenance_json["origin"] == "observation"


def test_a_credential_in_a_summary_is_refused_and_not_stored(db):
    """The summariser is a model, and a model summarising a conversation about a server
    can put a password in the summary. The secrets guard is the same one `/v1/memory/observe`
    runs, because this goes through `policy.decide()` like everything else."""
    result = _extract(db, "Sunucu için password: hunter2-correct-horse kullanıyoruz.")

    assert result.refused == 1
    assert _rows(db) == []


def test_a_refused_sentence_is_still_marked_seen(db):
    """So a secret in a summary is refused ONCE rather than re-offered to the guard on
    every later summary event for the rest of the session."""
    result = _extract(db, "Sunucu için password: hunter2-correct-horse kullanıyoruz.")

    assert len(result.seen) == 1


def test_one_summary_cannot_write_an_unbounded_number_of_rows(db):
    long_summary = " ".join(
        f"Bundan sonra {i} numaralı raporu kısa tut." for i in range(20)
    )

    result = _extract(db, long_summary)

    assert len(result.written) == MAX_PER_SUMMARY


# ------------------------------------------------------- a summary re-sent is not evidence


def test_the_same_sentence_in_a_later_summary_is_not_a_second_observation(db):
    """The trap this module is shaped around.

    `transcript_summary` is REPLACED as the conversation grows and each new summary tends
    to contain what the last one said. Extracting blindly would let one thing the owner
    mentioned ONCE corroborate itself up the promotion ladder to durable - the ladder being
    fed its own output, and an inferred guess arriving at the confidence of a stated fact.
    """
    first = _extract(db, PREFERENCE_SUMMARY)
    seen = set(first.seen)

    second = _extract(db, PREFERENCE_SUMMARY + " Sonra teşekkür etti.", already=seen)

    assert second.written == []
    assert len(_rows(db)) == 1
    evidence = db.execute(select(MemoryEvidence)).scalars().all()
    assert len(evidence) == 1, "the same sentence corroborated itself"


def test_the_key_is_a_hash_and_survives_whitespace(db):
    """It travels into `context_json`, which a reattaching client receives in full, so the
    summary's own words have no business being copied there."""
    key = sentence_key("  Bundan   sonra raporları kısa tut. ")

    assert key == sentence_key("bundan sonra raporları kısa tut.")
    assert "rapor" not in key
    assert len(key) == 16


# -------------------------------------------------------------------------- the wiring


def test_the_summary_event_is_what_feeds_it():
    """The guard, and it drives the REAL function the route calls.

    An extractor nothing calls is the defect this repository keeps paying for, and here it
    would look exactly like a memory that never learns anything from being spoken to. So
    this posts an actual `summary` client event through `record_client_events` rather than
    calling the helper: a test that called the helper would still pass on the day somebody
    deletes the one line in the summary branch that reaches it.
    """
    from datetime import UTC, datetime, timedelta

    from app.broker.models import AuditEvent
    from app.identity.service import SessionContext
    from app.ledger.models import ActivityEventRow
    from app.voice.realtime_sessions import service as realtime
    from app.voice.realtime_sessions.models import (
        REALTIME_STATE_ACTIVE,
        RealtimeSessionRow,
        RealtimeToolCall,
    )

    class _Runtime:
        embedder = EMBEDDER

    engine = create_engine("sqlite://")
    for table in (
        *_TABLES,
        RealtimeSessionRow.__table__,
        RealtimeToolCall.__table__,
        ActivityEventRow.__table__,
        AuditEvent.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session_id, owner_session_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    with factory() as db:
        db.add(
            RealtimeSessionRow(
                id=session_id,
                provider="openai",
                transport="webrtc",
                client_kind="web",
                device_id=None,
                owner_session_id=owner_session_id,
                state=REALTIME_STATE_ACTIVE,
                expires_at=None,
            )
        )
        db.commit()

    with factory() as db:
        realtime.record_client_events(
            db,
            db.get(RealtimeSessionRow, session_id),
            owner=SessionContext(
                session_id=owner_session_id,
                client_kind="web",
                client_label="test",
                device_id=None,
                scopes=(),
                created_at=now,
                expires_at=now + timedelta(hours=1),
                last_seen_at=None,
            ),
            events=[{"kind": "summary", "text": PREFERENCE_SUMMARY}],
            memory_runtime=_Runtime(),
        )
        db.commit()

    with factory() as db:
        rows = _rows(db)
        assert len(rows) == 1
        assert "raporları kısa" in rows[0].text
        row = db.get(RealtimeSessionRow, session_id)
        assert row.context_json["memory_extracted"], (
            "the session must remember what it already extracted, or the next summary "
            "corroborates the same sentence"
        )
    engine.dispose()


def test_a_process_with_no_memory_runtime_says_so_rather_than_going_quiet(db):
    """"Extraction found nothing" and "extraction never ran" look identical from outside
    and mean opposite things. ADR-0078's defect was exactly this shape: the tests injected
    a runtime and the route did not, and every answer was a plausible-looking nothing."""
    from app.voice.realtime_sessions import service as realtime

    class _Row:
        id = uuid.uuid4()

    meta = realtime._extract_memories(db, {}, PREFERENCE_SUMMARY, None, row=_Row())

    assert meta == {"memory_extraction": "no_runtime"}


def test_the_route_hands_the_recorder_the_memory_runtime():
    """The other half, and the one a unit test cannot fake: the real route reads the live
    source `create_app` registers."""
    import inspect

    from app.voice.realtime_sessions import routes

    source = inspect.getsource(routes.report_events)

    assert "memory_runtime=" in source
    assert 'live_sources().get("memory_runtime")' in source


def test_an_extracted_memory_is_never_attributed_to_the_owner(db):
    """Belt and braces on the security rule, asserted through the AUDIT trail rather than
    the row: `Actor.OWNER` on a row nobody stated is the failure that would matter."""
    _extract(db, "Bundan sonra raporları kısa tut.")

    actors = {
        e.actor for e in db.execute(select(MemoryAuditEvent)).scalars().all()
    }
    assert Actor.OWNER.value not in actors
