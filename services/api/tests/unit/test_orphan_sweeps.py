"""B06 req 9/10/11/13/206/219/220: the rows nothing ever closed.

Two orphans ran in production for days, and both were visible in the same place - the world
model's "running" count and ``/v1/state/now`` - as work in progress:

* a realtime session was only ever ended by somebody USING it (``require_live`` said so in a
  comment: "nothing sweeps in the background"), so a browser tab closed on 2026-09-09 was
  still ``active`` on the 12th, six or seven of them at once;
* a research run whose workflow went away stayed in ``discovering`` for three days.
  ``fail_unstarted_research`` covered the run that never started; nothing covered the one that
  started and was abandoned.

The voice sweep is deliberately NOT an age. ``expires_at`` has been NULL since migration 0038
because a fixed horizon written at creation killed every web session at exactly one hour,
mid-sentence (ADR-0105, owner directive "hiç kapanmasın"). It is idleness: a live conversation
stamps ``updated_at`` on every event, so it is never reached.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import TASK_STATUS_FAILED_TERMINAL, TASK_STATUS_RUNNING, Task
from app.research import runs_service
from app.research.models import (
    STAGE_DISCOVERING,
    STAGE_FAILED,
    STAGE_READY,
    ResearchRunRow,
)
from app.research.service import ABANDONED_RUN_AFTER, sweep_abandoned_runs
from app.voice.realtime_sessions.models import (
    REALTIME_STATE_ACTIVE,
    REALTIME_STATE_CLOSED,
    REALTIME_STATE_CREATED,
    REALTIME_STATE_EXPIRED,
    RealtimeSessionRow,
)
from app.voice.realtime_sessions.service import IDLE_SESSION_AFTER, sweep_idle_sessions

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def _factory(*tables):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in tables:
        table.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


# ------------------------------------------------------------------ voice sessions


@pytest.fixture()
def voice_db():
    from app.broker.models import AuditEvent

    factory = _factory(RealtimeSessionRow.__table__, AuditEvent.__table__)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _session_row(db, *, state: str, updated_at: datetime) -> RealtimeSessionRow:
    row = RealtimeSessionRow(
        id=uuid.uuid4(),
        provider="openai-realtime",
        transport="webrtc",
        client_kind="web",
        owner_session_id=uuid.uuid4(),
        state=state,
        context_json={},
        created_at=updated_at,
        updated_at=updated_at,
    )
    db.add(row)
    db.commit()
    return row


def test_a_tab_closed_days_ago_stops_being_an_active_conversation(voice_db) -> None:
    stale = _session_row(
        voice_db, state=REALTIME_STATE_ACTIVE, updated_at=NOW - timedelta(days=3)
    )

    closed = sweep_idle_sessions(voice_db, now=NOW)

    assert closed == [stale.id]
    voice_db.refresh(stale)
    assert stale.state == REALTIME_STATE_EXPIRED
    # SQLite hands timestamps back naive where PostgreSQL keeps them aware; the instant is
    # what is being asserted, not the driver's tzinfo.
    assert stale.closed_at is not None
    assert stale.closed_at.replace(tzinfo=UTC) == NOW


def test_the_audit_row_says_when_the_session_went_quiet(voice_db) -> None:
    """Not when the sweep ran — that is already on the row. The first version of this sweep
    read ``updated_at`` after stamping it, so every audit row claimed the session went idle
    at the exact moment it was swept."""
    from app.broker.models import AuditEvent

    last_seen = NOW - timedelta(days=4)
    stale = _session_row(voice_db, state=REALTIME_STATE_ACTIVE, updated_at=last_seen)

    sweep_idle_sessions(voice_db, now=NOW)

    audit = voice_db.execute(
        select(AuditEvent).where(AuditEvent.subject_ref == str(stale.id))
    ).scalar_one()
    assert audit.action == "voice_session_expired"
    assert audit.metadata_json["reason"] == "idle"
    # `_iso` renders UTC with a trailing Z, the shape every other voice audit row uses
    assert audit.metadata_json["idle_since"] == "2026-09-08T12:00:00Z"


def test_a_conversation_with_a_recent_event_is_never_touched(voice_db) -> None:
    """The whole point of ADR-0105: a session the owner is IN must not be ended under them."""
    live = _session_row(
        voice_db, state=REALTIME_STATE_ACTIVE, updated_at=NOW - timedelta(minutes=5)
    )

    assert sweep_idle_sessions(voice_db, now=NOW) == []
    voice_db.refresh(live)
    assert live.state == REALTIME_STATE_ACTIVE


def test_a_very_old_session_that_is_still_being_used_survives(voice_db) -> None:
    """Idleness, not age. A session created a week ago and used a minute ago is live - which
    is exactly the case the one-hour horizon used to kill mid-sentence."""
    old_but_busy = RealtimeSessionRow(
        id=uuid.uuid4(),
        provider="openai-realtime",
        transport="webrtc",
        client_kind="web",
        owner_session_id=uuid.uuid4(),
        state=REALTIME_STATE_ACTIVE,
        context_json={},
        created_at=NOW - timedelta(days=7),
        updated_at=NOW - timedelta(minutes=1),
    )
    voice_db.add(old_but_busy)
    voice_db.commit()

    assert sweep_idle_sessions(voice_db, now=NOW) == []


def test_a_session_that_never_reported_anything_is_swept_too(voice_db) -> None:
    """`created` and never used: the leg was never established. Still an orphan."""
    never = _session_row(
        voice_db, state=REALTIME_STATE_CREATED, updated_at=NOW - timedelta(days=2)
    )

    assert sweep_idle_sessions(voice_db, now=NOW) == [never.id]


def test_an_already_closed_session_is_left_alone(voice_db) -> None:
    closed = _session_row(
        voice_db, state=REALTIME_STATE_CLOSED, updated_at=NOW - timedelta(days=5)
    )

    assert sweep_idle_sessions(voice_db, now=NOW) == []
    voice_db.refresh(closed)
    assert closed.state == REALTIME_STATE_CLOSED


def test_the_idle_bound_is_hours_not_minutes(voice_db) -> None:
    """A guard on the bound itself: tightening it towards the old one-hour horizon would
    reintroduce the defect ADR-0105 exists for."""
    assert IDLE_SESSION_AFTER >= timedelta(hours=6)


# ---------------------------------------------------------------- research runs


@pytest.fixture()
def research_db():
    from app.artifacts.models import Artifact, TaskRun

    factory = _factory(
        Task.__table__, TaskRun.__table__, Artifact.__table__, ResearchRunRow.__table__
    )
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _run(db, *, stage: str, updated_at: datetime, task_status: str = TASK_STATUS_RUNNING) -> Task:
    task = Task(id=uuid.uuid4(), intent="araştır", status=task_status, created_at=updated_at)
    db.add(task)
    db.flush()
    db.add(
        ResearchRunRow(
            task_id=task.id,
            stage=stage,
            progress_json={},
            events_json=[],
            created_at=updated_at,
            updated_at=updated_at,
        )
    )
    db.commit()
    return task


def test_a_run_whose_workflow_went_away_is_closed_with_its_task(research_db) -> None:
    task = _run(research_db, stage=STAGE_DISCOVERING, updated_at=NOW - timedelta(days=3))

    closed = sweep_abandoned_runs(research_db, now=NOW)

    assert closed == [task.id]
    research_db.refresh(task)
    assert task.status == TASK_STATUS_FAILED_TERMINAL
    assert task.error_class == "workflow_abandoned"
    run = research_db.get(ResearchRunRow, task.id)
    assert run.stage == STAGE_FAILED


def test_a_run_that_moved_recently_is_left_to_its_workflow(research_db) -> None:
    task = _run(research_db, stage=STAGE_DISCOVERING, updated_at=NOW - timedelta(minutes=30))

    assert sweep_abandoned_runs(research_db, now=NOW) == []
    research_db.refresh(task)
    assert task.status == TASK_STATUS_RUNNING


def test_a_finished_run_is_never_reopened(research_db) -> None:
    _run(research_db, stage=STAGE_READY, updated_at=NOW - timedelta(days=30))

    assert sweep_abandoned_runs(research_db, now=NOW) == []


def test_a_task_the_workflow_already_closed_is_not_transitioned_again(research_db) -> None:
    """The run row can lag behind its task. Reconciling the run must not push a terminal task
    through a transition it has already made."""
    task = _run(
        research_db,
        stage=STAGE_DISCOVERING,
        updated_at=NOW - timedelta(days=3),
        task_status=TASK_STATUS_FAILED_TERMINAL,
    )

    closed = sweep_abandoned_runs(research_db, now=NOW)

    assert closed == [task.id]
    research_db.refresh(task)
    assert task.status == TASK_STATUS_FAILED_TERMINAL
    assert research_db.get(ResearchRunRow, task.id).stage == STAGE_FAILED


def test_the_abandoned_bound_leaves_room_for_a_person(research_db) -> None:
    """An interactive stage waits on the OWNER clearing a challenge page. The bound has to be
    long enough that "they are still on it" has genuinely stopped being true."""
    assert ABANDONED_RUN_AFTER >= timedelta(hours=12)


def test_both_sweeps_are_registered_on_the_real_application_object() -> None:
    """A sweep nothing runs is the defect this batch is about; the previous one was found the
    same way (three sweeps existed and nothing called them, 2026-09-11)."""
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None))
    names = app.state.retention_sweeper.names
    assert "idle_voice_sessions" in names
    assert "abandoned_research_runs" in names


def test_runs_service_is_what_writes_the_failure(research_db) -> None:
    """The sweep goes through the run service rather than writing the row itself, so the
    stage transition, the event log and the UI state all happen the one way they happen."""
    task = _run(research_db, stage=STAGE_DISCOVERING, updated_at=NOW - timedelta(days=2))
    sweep_abandoned_runs(research_db, now=NOW)
    run = runs_service.get_run(research_db, task.id)
    assert run is not None
    assert run.stage == STAGE_FAILED
    assert run.events_json, "the run records WHY it was closed"
