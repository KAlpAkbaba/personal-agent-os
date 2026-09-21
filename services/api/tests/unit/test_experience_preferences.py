"""A repeated behaviour is a preference, and a preference is what memory is for (ADR-0191).

Production on 2026-09-20, with the heartbeat rows gone: 1381 memories, of which THIRTEEN
were durable and every one of them said "Araştırma tamamlandı: <konu>". The preference,
project and procedural classes were empty - after sixteen days in which the owner asked for
the same subject over and over. The promotion ladder (`PROMOTE_MIN_EVIDENCE` = 3 pieces of
evidence on the SAME key) could never fire, because a one-off event never shares a key with
another one.

So: the subject of a research is a key. Ask for it three times and what the store holds is
not three events - it is one preference with three pieces of evidence behind it, which is
exactly what the ladder was built to promote.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.experience import engine as experience
from app.ledger import service as ledger_service
from app.memory.models import Memory
from app.memory.types import MemoryClass, WriteStage
from tests.unit.test_experience_engine import ALL_TABLES

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    db = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ALL_TABLES:
        table.create(db)
    factory = sessionmaker(bind=db, expire_on_commit=False)
    with factory() as s:
        yield s
    db.dispose()


def _research(session, topic: str, *, minutes: int) -> None:
    task_id = uuid.uuid4()
    ledger_service.record(
        session,
        ledger_service.build_research_completed_event(
            task_id=task_id,
            occurred_at=NOW - timedelta(minutes=minutes),
            report_json={
                "topic": topic,
                "findings": [{"id": "f1"}],
                "sources": [{"id": "e1"}],
                "stats": {"discovered": 10, "fetched": 4, "rejected": 1, "evidence": 1},
            },
            source_ref=f"research_runs:{task_id}:ready",
        ),
    )


def _preferences(session) -> list[Memory]:
    return list(
        session.execute(
            select(Memory).where(Memory.memory_class == MemoryClass.PREFERENCE.value)
        )
        .scalars()
        .all()
    )


def test_the_topic_is_on_the_ledger_event_at_all(session) -> None:
    """It was not: the event carried counts and no subject, so nothing downstream could
    ever know WHAT the owner keeps asking about."""
    _research(session, "yapay zeka haberleri", minutes=1)
    row = session.execute(select(ledger_service.ActivityEventRow)).scalars().one()
    assert row.detail_json.get("topic") == "yapay zeka haberleri"


def test_three_researches_on_one_subject_become_one_preference(session) -> None:
    for i, topic in enumerate(
        ("yapay zeka haberleri", "yapay zeka haberlerini", "yapay zeka ile ilgili haberler")
    ):
        _research(session, topic, minutes=30 * (i + 1))

    experience.ingest(session, now=NOW)

    prefs = _preferences(session)
    assert len(prefs) == 1, [p.text for p in prefs]
    assert "yapay zeka" in prefs[0].text.lower()
    assert prefs[0].evidence_count >= 3, prefs[0].evidence_count


def test_a_preference_with_enough_evidence_becomes_durable(session) -> None:
    """The point of the key: the ladder can finally fire. Thirteen one-off events never
    promoted anything; three on one subject do."""
    for i in range(3):
        _research(session, "yapay zeka haberleri", minutes=30 * (i + 1))

    experience.ingest(session, now=NOW)

    prefs = _preferences(session)
    assert prefs and prefs[0].stage == WriteStage.DURABLE.value, [
        (p.text, p.stage, p.evidence_count, p.confidence) for p in prefs
    ]


def test_asking_twice_is_not_yet_a_preference(session) -> None:
    """Two is a coincidence. The threshold is the same one the ladder already uses."""
    for i in range(2):
        _research(session, "yapay zeka haberleri", minutes=30 * (i + 1))

    experience.ingest(session, now=NOW)

    assert _preferences(session) == []


def test_different_subjects_do_not_merge(session) -> None:
    for i in range(3):
        _research(session, "yapay zeka haberleri", minutes=10 * (i + 1))
    for i in range(3):
        _research(session, "elektrikli otomobil haberleri", minutes=100 + 10 * (i + 1))

    experience.ingest(session, now=NOW)

    texts = sorted(p.text.lower() for p in _preferences(session))
    assert len(texts) == 2, texts
    assert any("yapay zeka" in t for t in texts)
    assert any("elektrikli otomobil" in t for t in texts)


def test_the_preference_is_written_in_the_owners_language(session) -> None:
    for i in range(3):
        _research(session, "yapay zeka haberleri", minutes=30 * (i + 1))

    experience.ingest(session, now=NOW)

    text = _preferences(session)[0].text
    assert "araştır" in text.lower(), text
    assert "research" not in text.lower(), text


def test_a_second_pass_adds_no_new_row(session) -> None:
    """Idempotency, the same rule the episodic and semantic passes keep."""
    for i in range(3):
        _research(session, "yapay zeka haberleri", minutes=30 * (i + 1))

    experience.ingest(session, now=NOW)
    before = len(_preferences(session))
    experience.ingest(session, now=NOW)

    assert len(_preferences(session)) == before == 1
