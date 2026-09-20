"""Forgetting the heartbeat rows, selected by provenance and never by their words.

ADR-0190, owner-authorised on 2026-09-20 after seeing the rows ("Uygula"). 765 of 2316
episodic memories were voice-session lifecycle, presence changes and "the owner used the
keyboard" - the ledger keeps every one of them, and the memory store should not.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.experience.cleanup import event_id_of, select_telemetry_memories
from app.experience.engine import EPISODIC_KEY_PREFIX
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_PRESENCE_STATE_CHANGED,
    EVENT_TYPE_RESEARCH_COMPLETED,
    EVENT_TYPE_VOICE_SESSION_CREATED,
    SUBSYSTEM_PRESENCE,
    SUBSYSTEM_RESEARCH,
    SUBSYSTEM_VOICE,
)
from app.memory.models import Memory
from app.memory.types import MemoryClass, MemoryStatus, WriteStage
from tests.unit.test_experience_engine import ALL_TABLES

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ALL_TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


def _event(session, event_type: str, subsystem: str, summary: str):
    return ledger_service.record(
        session,
        ledger_service.ActivityEvent(
            event_type=event_type,
            subsystem=subsystem,
            action="x",
            factual_summary=summary,
            occurred_at=NOW,
            source="test",
            source_ref=f"test:{uuid.uuid4()}",
        ),
    )


def _memory(session, *, key: str, text: str, explicit: bool = False, pinned: bool = False):
    row = Memory(
        id=uuid.uuid4(),
        memory_class=MemoryClass.EPISODIC.value,
        key=key,
        text=text,
        value_json={},
        stage=WriteStage.CANDIDATE.value,
        status=MemoryStatus.ACTIVE.value,
        explicit=explicit,
        pinned=pinned,
        confidence=0.5,
        evidence_count=1,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(row)
    session.commit()
    return row


def test_a_heartbeat_row_is_selected(session) -> None:
    event = _event(
        session, EVENT_TYPE_PRESENCE_STATE_CHANGED, SUBSYSTEM_PRESENCE, "owner presence changed"
    )
    _memory(session, key=f"{EPISODIC_KEY_PREFIX}:{event.event_id}", text="owner presence changed")

    selected = select_telemetry_memories(session)

    assert [m.text for m in selected] == ["owner presence changed"]


def test_what_the_owner_did_is_never_selected(session) -> None:
    event = _event(
        session,
        EVENT_TYPE_RESEARCH_COMPLETED,
        SUBSYSTEM_RESEARCH,
        "Araştırma tamamlandı: yapay zeka",
    )
    _memory(
        session,
        key=f"{EPISODIC_KEY_PREFIX}:{event.event_id}",
        text="Araştırma tamamlandı: yapay zeka",
    )

    assert select_telemetry_memories(session) == []


def test_a_row_the_owner_pinned_or_wrote_is_never_selected(session) -> None:
    """Forgetting is the one irreversible operation; the owner's own is not ours."""
    for flag in ("explicit", "pinned"):
        event = _event(
            session, EVENT_TYPE_VOICE_SESSION_CREATED, SUBSYSTEM_VOICE, "Sesli oturum oluşturuldu."
        )
        _memory(
            session,
            key=f"{EPISODIC_KEY_PREFIX}:{event.event_id}",
            text="Sesli oturum oluşturuldu.",
            **{flag: True},
        )

    assert select_telemetry_memories(session) == []


def test_a_memory_that_did_not_come_from_the_ledger_is_untouched(session) -> None:
    """A row written by any other path has no episodic key, so it is not even looked at -
    the selection is by PROVENANCE, never by what the sentence says."""
    _event(
        session, EVENT_TYPE_PRESENCE_STATE_CHANGED, SUBSYSTEM_PRESENCE, "owner presence changed"
    )
    _memory(session, key="preference.tone", text="owner presence changed")

    assert select_telemetry_memories(session) == []


def test_the_key_names_the_event_it_came_from(session) -> None:
    event = _event(
        session, EVENT_TYPE_PRESENCE_STATE_CHANGED, SUBSYSTEM_PRESENCE, "owner presence changed"
    )
    memory = _memory(
        session, key=f"{EPISODIC_KEY_PREFIX}:{event.event_id}", text="owner presence changed"
    )

    assert event_id_of(memory) == str(event.event_id)
    assert event_id_of(_memory(session, key="x", text="y")) == ""
