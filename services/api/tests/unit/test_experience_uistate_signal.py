"""Regression: the experience subsystem must actually reach the UI-state bus.

The defect these tests exist for: ``engine.py`` and ``compiler.py`` each carried a private
copy of a publish wrapper that called ``uistate.publish(..., phase=..., **metadata)`` — a
signature ``publish`` has never had. Every call raised ``TypeError`` straight into a bare
``except`` logging at debug level, so ``agent.memory_retrieval`` was never published once,
and the only visible symptom was a Core that showed memory work as permanently idle.

So these tests assert two different things, and both matter:

1. **The event arrives.** Not "the wrapper was called" — the stamped event is read back off
   the real publisher, because a mock would have passed happily against the broken code too.
2. **A wiring bug stays loud.** ``publish_progress`` deliberately has no ``except`` of its
   own; ``uistate.publish`` already guarantees "never raises" for runtime failures, so
   anything thrown here is a wrong call, and a wrong call must fail a test rather than
   quietly turn a subsystem invisible.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.experience import compiler as experience_compiler
from app.experience import engine as experience_engine
from app.experience import signals
from app.experience.models import ExperienceLessonRow
from app.experience.signals import publish_progress
from app.ledger.models import ActivityEventRow
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.selfhealing.models import Incident
from app.uistate import UiState, UiStatePublisher, set_publisher
from app.uistate.publisher import get_publisher

ALL_TABLES = [
    ActivityEventRow.__table__,
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
    ExperienceLessonRow.__table__,
    Incident.__table__,
]


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


@pytest.fixture()
def bus():
    """A fresh publisher whose tail is the assertion surface."""
    previous = get_publisher()
    publisher = UiStatePublisher()
    set_publisher(publisher)
    yield publisher
    set_publisher(previous)


def _states(publisher: UiStatePublisher) -> list[tuple[str, str | None]]:
    return [(e.state.value, e.status) for e in publisher.tail()]


def test_publish_progress_reaches_the_bus(bus):
    publish_progress(phase="ingest_started", events_scanned=12)

    (event,) = bus.tail()
    assert event.state is UiState.MEMORY_RETRIEVAL
    assert event.subsystem == "experience"
    assert event.status == "ingest_started"
    assert event.label == "ingest_started"
    assert event.metadata == {"events_scanned": 12}


def test_the_real_report_dict_is_publishable(bus):
    """The exact kwargs ``ingest()`` passes — the shape that used to raise TypeError."""
    report = experience_engine.IngestReport()
    report.events_scanned = 4
    report.episodic_created = 2
    report.errors.append("evt-1: ValueError")

    publish_progress(phase="ingest_completed", **report.as_dict())

    (event,) = bus.tail()
    assert event.metadata["events_scanned"] == 4
    assert event.metadata["episodic_created"] == 2
    # A list is content-shaped and would be dropped entirely by the publisher; the count
    # is what a renderer can honestly draw, so the wrapper reduces it before sending.
    assert event.metadata["errors"] == 1


def test_metadata_is_reduced_to_what_the_contract_allows(bus):
    publish_progress(
        phase="compile_completed",
        lessons=3,
        since=None,
        ok=True,
        ratio=0.5,
        pattern="generic",
        candidates=["a", "b", "c"],
        opaque=object(),
        api_token="abc",
    )

    (event,) = bus.tail()
    assert event.metadata == {
        "lessons": 3,
        "ok": True,
        "ratio": 0.5,
        "pattern": "generic",
        "candidates": 3,
    }
    assert "since" not in event.metadata  # None carries nothing to draw
    assert "opaque" not in event.metadata  # unbounded objects never leave the wrapper
    assert "api_token" not in event.metadata  # the publisher's forbidden-key rule still bites


def test_a_wrong_call_is_loud_rather_than_swallowed(monkeypatch, bus):
    """The whole point. ``publish_progress`` must not have an ``except`` of its own."""

    def exploding_publish(*args, **kwargs):
        raise TypeError("publish() got an unexpected keyword argument 'phase'")

    monkeypatch.setattr(signals, "publish", exploding_publish)

    with pytest.raises(TypeError):
        publish_progress(phase="ingest_started")


def test_the_wrapper_call_binds_against_the_real_publish_signature():
    """Guards the drift class directly: the wrapper's kwargs must exist on ``publish``."""
    from app.uistate import publish

    inspect.signature(publish).bind(
        UiState.MEMORY_RETRIEVAL,
        subsystem="experience",
        status="ingest_started",
        label="ingest_started",
        metadata={"events_scanned": 1},
    )


def test_ingest_publishes_start_and_completion(session, bus):
    experience_engine.ingest(session, now=datetime(2026, 9, 5, 12, 0, tzinfo=UTC))

    assert _states(bus) == [
        ("agent.memory_retrieval", "ingest_started"),
        ("agent.memory_retrieval", "ingest_completed"),
    ]


def test_compile_lessons_publishes_start_and_completion(session, bus):
    experience_compiler.compile_lessons(session, now=datetime(2026, 9, 5, 12, 0, tzinfo=UTC))

    assert _states(bus) == [
        ("agent.memory_retrieval", "compile_started"),
        ("agent.memory_retrieval", "compile_completed"),
    ]


def test_no_experience_module_swallows_its_own_ui_signal():
    """A second copy of the wrapper is how this bug happened; there must be exactly one."""
    for module in (experience_engine, experience_compiler):
        source = inspect.getsource(module)
        assert "uistate" not in source, (
            f"{module.__name__} publishes UI state directly again; the one wrapper in "
            "app/experience/signals.py exists so the two copies cannot drift apart"
        )
        assert "publish_progress" in source


def test_ingest_report_ids_are_never_published_as_content(session, bus):
    """Ids and counts only: no memory or lesson text may reach the bus."""
    publish_progress(phase="ingest_completed", memory_id=str(uuid.uuid4()))

    (event,) = bus.tail()
    assert list(event.metadata) == ["memory_id"]
