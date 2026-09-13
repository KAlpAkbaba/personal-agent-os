"""B18 req 71-73: the Experience Engine, on a clock, working through its own backlog.

`app.experience.engine.ingest` has been complete since it was written and the only thing
that ever called it was `POST /v1/experience/ingest`. The feature matrix measured the
result exactly: **1441 activity events, 0 memories.** A system that keeps a durable record
of everything it does and never reads it back is keeping a diary it cannot remember.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.experience.scheduler import (
    DEFAULT_INTERVAL_S,
    MIN_INTERVAL_S,
    ExperienceScheduler,
    read_cursor,
)
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_EXPERIENCE_INGESTED,
    EVENT_TYPE_RESEARCH_COMPLETED,
    SUBSYSTEM_RESEARCH,
)
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
from app.memory.types import MemoryClass

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
EMBEDDER = DeterministicEmbedder()

_TABLES = [
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
    ActivityEventRow.__table__,
]


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


def _event(db: Session, *, at: datetime, n: int) -> None:
    """One real ledger row through the real writer, so the engine reads what it would read
    in production rather than a hand-built object."""
    ledger_service.record(
        db,
        ledger_service.ActivityEvent(
            event_type=EVENT_TYPE_RESEARCH_COMPLETED,
            subsystem=SUBSYSTEM_RESEARCH,
            action="research.complete",
            status="completed",
            factual_summary=f"{n} numaralı araştırma tamamlandı ve raporu hazırlandı efendim.",
            source="live",
            source_ref=f"research:{n}",
            occurred_at=at,
            evidence_refs=[{"kind": "report", "ref": f"r{n}"}],
            detail_json={"findings": n},
        ),
    )
    db.commit()


def _episodic(db: Session) -> list[Memory]:
    return list(
        db.execute(
            select(Memory).where(Memory.memory_class == MemoryClass.EPISODIC.value)
        )
        .scalars()
        .all()
    )


def _scheduler(**kwargs: Any) -> ExperienceScheduler:
    scheduler = ExperienceScheduler(interval_s=MIN_INTERVAL_S, limit=5, **kwargs)
    scheduler.bind_embedder(EMBEDDER)
    return scheduler


# ------------------------------------------------------------------- it runs at all


def test_a_pass_turns_activity_into_memory(db):
    """req 71/72/73. The whole point, and the thing that had never happened: an activity
    event becomes something the system remembers, without anybody posting to a REST route.
    """
    for n in range(3):
        _event(db, at=NOW - timedelta(minutes=10 + n), n=n)

    result = _scheduler().tick(db, now=NOW)

    assert result.ran
    assert result.episodic_created == 3
    assert len(_episodic(db)) == 3
    # Four rows READ for three events: the backlog half starts at the forward half's oldest
    # row and `until` is inclusive, so that one row is read twice on purpose. An exclusive
    # boundary would be an off-by-one that skips an event for ever, and the engine is
    # idempotent per event, so a re-read costs a lookup. `episodic_created` is the exact
    # number and is what the owner-facing receipt leads with.
    assert result.events_scanned == 4


def test_a_second_pass_over_the_same_events_writes_nothing_new(db):
    """The engine's idempotency, exercised through the SCHEDULER, which is the caller that
    will actually re-read overlapping windows for the rest of this system's life."""
    _event(db, at=NOW - timedelta(minutes=5), n=1)
    scheduler = _scheduler()
    scheduler.tick(db, now=NOW)

    scheduler.last_run_at = None  # due again
    second = scheduler.tick(db, now=NOW + timedelta(seconds=1))

    assert second.episodic_created == 0
    assert len(_episodic(db)) == 1


def test_it_does_nothing_when_it_is_not_due(db):
    _event(db, at=NOW - timedelta(minutes=5), n=1)
    scheduler = _scheduler()
    scheduler.tick(db, now=NOW)

    again = scheduler.tick(db, now=NOW + timedelta(seconds=5))

    assert again.reason == "not_due"
    assert not again.ran


def test_the_interval_has_a_floor(db):
    """A misconfiguration must not turn this into a busy loop over the whole ledger."""
    assert ExperienceScheduler(interval_s=0.001).interval_s == MIN_INTERVAL_S
    assert DEFAULT_INTERVAL_S >= MIN_INTERVAL_S


def test_a_disabled_scheduler_is_never_due(db):
    _event(db, at=NOW - timedelta(minutes=5), n=1)

    result = _scheduler(enabled=False).tick(db, now=NOW)

    assert result.reason == "not_due"
    assert _episodic(db) == []


# ------------------------------------------------------------------- the backlog


def test_it_reaches_history_older_than_one_page(db):
    """The reason a forward-only scheduler would not have closed this requirement.

    `ledger.query` is newest-first and capped, so "everything since my last pass" reaches
    today and never reaches what was already there. Twelve events, a page of five: three
    passes and the whole history is in memory.
    """
    for n in range(12):
        _event(db, at=NOW - timedelta(hours=n + 1), n=n)
    scheduler = _scheduler()

    for pass_number in range(4):
        scheduler.last_run_at = None
        scheduler.tick(db, now=NOW + timedelta(minutes=pass_number))

    assert len(_episodic(db)) == 12
    assert scheduler.backlog_done


def test_the_backlog_stops_when_there_is_nothing_older(db):
    for n in range(2):
        _event(db, at=NOW - timedelta(hours=n + 1), n=n)
    scheduler = _scheduler()

    scheduler.tick(db, now=NOW)

    assert scheduler.backlog_done, "two events do not fill a page; there is no more history"


def test_the_backlog_cursor_moves_on_what_was_SCANNED_not_on_what_was_written(db):
    """The stall this design exists to avoid.

    An event whose summary the write policy ignores produces no memory row. A cursor
    derived from the memories would not move past a page of those, and the pass would
    re-read the same page for ever. The cursor is the window the pass READ.
    """
    ledger_service.record(
        db,
        ledger_service.ActivityEvent(
            event_type=EVENT_TYPE_RESEARCH_COMPLETED,
            subsystem=SUBSYSTEM_RESEARCH,
            action="research.complete",
            status="completed",
            factual_summary="Tamam.",  # chatty: the write policy ignores it
            source="live",
            source_ref="research:chatty",
            occurred_at=NOW - timedelta(hours=3),
        ),
    )
    db.commit()
    scheduler = _scheduler()

    scheduler.tick(db, now=NOW)

    assert _episodic(db) == [], "the fixture must actually be ignored, or this proves nothing"
    cursor = read_cursor(db)
    assert cursor.oldest_scanned is not None, "the cursor moved on a page that wrote nothing"


# ------------------------------------------------------- the cursor is a ledger event


def test_each_pass_records_where_it_got_to(db):
    _event(db, at=NOW - timedelta(minutes=5), n=1)

    _scheduler().tick(db, now=NOW)

    rows = ledger_service.query(db, event_types=(EVENT_TYPE_EXPERIENCE_INGESTED,), limit=5)
    assert len(rows) == 1
    assert rows[0].detail_json["episodic_created"] == 1
    assert rows[0].detail_json["newest_scanned"]
    # The sentence the owner would hear leads with the exact number, not with the row count
    # that carries the deliberate boundary re-read.
    assert rows[0].factual_summary.startswith("Deneyim motoru 1 yeni bellek yazdı")


def test_the_engine_never_learns_from_the_record_of_its_own_learning(db):
    """A system that ingested its own `experience.ingested` events would corroborate
    itself: every pass would write a row saying it ran, and the next pass would remember
    that it ran, for ever."""
    _event(db, at=NOW - timedelta(minutes=5), n=1)
    scheduler = _scheduler()
    scheduler.tick(db, now=NOW)

    scheduler.last_run_at = None
    scheduler.tick(db, now=NOW + timedelta(minutes=1))

    texts = [m.text for m in _episodic(db)]
    assert not any("Deneyim motoru" in t for t in texts)


def test_a_fresh_process_picks_up_where_the_last_one_stopped(db):
    """The cursor is durable, not in-memory: a restart must not re-read the whole ledger,
    and must not skip what arrived while the process was down."""
    _event(db, at=NOW - timedelta(minutes=30), n=1)
    _scheduler().tick(db, now=NOW)

    _event(db, at=NOW + timedelta(minutes=1), n=2)
    fresh = _scheduler()  # a different object, as after a restart
    result = fresh.tick(db, now=NOW + timedelta(minutes=2))

    assert result.episodic_created == 1, "only the new event"
    assert len(_episodic(db)) == 2


# -------------------------------------------------------------------------- the wiring


def test_the_clock_drives_it_and_the_health_endpoint_reports_it():
    """The guard. A scheduler nothing calls is this repository's most-paid-for defect, and
    here it would look exactly like the state the matrix already measured: a complete
    engine, 1441 events, and nothing ever learned."""
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None))

    assert app.state.experience_scheduler is not None
    assert "experience" in [name for name, _ in app.state.routine_clock._sub_ticks()]
    health = app.state.experience_scheduler.health_check()
    assert health["enabled"] is True
    assert health["interval_s"] >= MIN_INTERVAL_S


def test_one_setting_turns_it_off_again():
    """B18's own rollback plan: "zamanlayıcı kapatılır"."""
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None, experience_ingest_enabled=False))

    assert app.state.experience_scheduler.enabled is False


def test_it_uses_the_memory_runtime_s_own_embedder():
    """ADR-0078's lesson, third time in three batches: vectors from a different model in
    the one embeddings table make every semantic search quietly worse."""
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None))

    assert app.state.experience_scheduler._embedder is app.state.memory.embedder


def test_a_failing_pass_never_stops_the_clock(db, monkeypatch):
    """This rides the clock that fires alarms. A memory derivation that raised would be a
    morning the owner was not woken because the system was busy learning."""
    from app.experience import scheduler as scheduler_module

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the ledger went away")

    monkeypatch.setattr(scheduler_module, "ingest", _boom)
    scheduler = _scheduler()

    result = scheduler.tick(db, now=NOW)

    assert result.reason.startswith("error:")
    assert scheduler.last_error is not None
    assert scheduler.health_check()["last_error"]


def test_the_event_type_is_enumerated_by_the_ledger():
    """`app.ledger.vocabulary`'s own rule: every event type the ledger accepts is
    enumerated, never discovered from a caller's payload."""
    from app.ledger.vocabulary import EVENT_TYPES

    assert EVENT_TYPE_EXPERIENCE_INGESTED in EVENT_TYPES


def test_the_ingest_route_and_the_scheduler_call_one_engine():
    """Two callers, one derivation. A scheduler that grew its own copy of the ingest would
    be a second answer to "what has this system learned"."""
    import inspect

    from app.experience import scheduler as scheduler_module

    source = inspect.getsource(scheduler_module.ExperienceScheduler.run_once)

    assert "ingest(" in source


# --------------------------------------------------- B18 req 47/48: the entity graph


def _entities(db: Session, kind: str) -> list[Entity]:
    return list(db.execute(select(Entity).where(Entity.kind == kind)).scalars().all())


def test_a_pass_builds_the_graph_that_had_never_had_a_writer(db):
    """req 47. `Entity`, `EntityEdge`, eight kinds, `create_entity`/`create_edge` and a
    REST surface have existed since M5 and nothing in `app/` ever called one: the matrix
    measured it as `üretimde boş`."""
    _event(db, at=NOW - timedelta(minutes=5), n=1)

    result = _scheduler().tick(db, now=NOW)

    assert result.entities > 0
    assert [e.name for e in _entities(db, "system")] == [SUBSYSTEM_RESEARCH]
    assert "research.complete" in [e.name for e in _entities(db, "capability")]
    assert result.edges > 0


def test_the_graph_is_built_only_from_what_the_ledger_already_names(db):
    """Nothing here parses a sentence. There is no ledger field that names a person, so
    there are no `person` nodes - and saying that is more useful than a graph of guesses.
    """
    _event(db, at=NOW - timedelta(minutes=5), n=1)

    _scheduler().tick(db, now=NOW)

    assert _entities(db, "person") == []
    assert _entities(db, "decision") == []


def test_one_entity_however_many_devices_saw_it(db):
    """req 48, and it is the identity SCHEME that satisfies it rather than anything built
    here: `Entity` is unique on (kind, name) and carries no device column, so the same
    logical subsystem observed through two different sources is ONE node. That was true
    when the table was written and untestable while nothing wrote to it."""
    for n, source_ref in enumerate(("from-desktop", "from-phone")):
        ledger_service.record(
            db,
            ledger_service.ActivityEvent(
                event_type=EVENT_TYPE_RESEARCH_COMPLETED,
                subsystem=SUBSYSTEM_RESEARCH,
                action="research.complete",
                status="completed",
                factual_summary=f"{n} numaralı araştırma tamamlandı ve raporu hazır efendim.",
                source="live",
                source_ref=source_ref,
                occurred_at=NOW - timedelta(minutes=5 + n),
            ),
        )
    db.commit()

    _scheduler().tick(db, now=NOW)

    assert len(_entities(db, "system")) == 1
    assert len(_entities(db, "capability")) == 1


def test_a_second_pass_adds_no_duplicate_nodes_or_edges(db):
    _event(db, at=NOW - timedelta(minutes=5), n=1)
    scheduler = _scheduler()
    scheduler.tick(db, now=NOW)
    before = (len(_entities(db, "system")), len(_entities(db, "capability")))

    scheduler.last_run_at = None
    scheduler.tick(db, now=NOW + timedelta(seconds=1))

    assert (len(_entities(db, "system")), len(_entities(db, "capability"))) == before
    assert len(db.execute(select(EntityEdge)).scalars().all()) == 1


def test_a_graph_that_cannot_be_written_never_stops_the_ingest(db, monkeypatch):
    from app.experience import scheduler as scheduler_module

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the graph went away")

    monkeypatch.setattr(scheduler_module, "sync_from_events", _boom)
    _event(db, at=NOW - timedelta(minutes=5), n=1)

    result = _scheduler().tick(db, now=NOW)

    assert result.episodic_created == 1, "the memory was still written"
    assert result.entities == 0
