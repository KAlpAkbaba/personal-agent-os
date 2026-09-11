"""Rows a stopped process left mid-flight are closed, and nothing else is (Phase 8).

A synchronous tool call and a native build each run inside one request. A process that
stops mid-request - a crash, or a colour drained during a release - leaves the row
`running` / `building` for ever. These sweeps close them; what is legitimately still going
(a long-running tool call waiting on its task, a build that is minutes old, a planned build
waiting for the owner's "build it") is left exactly as it was.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.nativefactory.interrupted import SPEECH_BUILD_INTERRUPTED, fail_interrupted_builds
from app.nativefactory.models import (
    STATE_BUILDING,
    STATE_FAILED,
    STATE_PLANNED,
    STATE_TESTING,
    NativeBuildRow,
)
from app.voice.realtime_sessions.models import (
    TOOL_STATUS_FAILED,
    TOOL_STATUS_RUNNING,
    RealtimeSessionRow,
    RealtimeToolCall,
)
from app.voice.realtime_sessions.service import (
    SPEECH_TOOL_CALL_INTERRUPTED,
    fail_interrupted_tool_calls,
)

NOW = datetime(2026, 9, 11, 20, 0, tzinfo=UTC)


def _session(*tables):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in tables:
        table.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _call(db, *, name: str, long_running: bool, age: timedelta) -> RealtimeToolCall:
    call = RealtimeToolCall(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        call_id=f"c-{uuid.uuid4().hex[:6]}",
        name=name,
        arguments_json={},
        status=TOOL_STATUS_RUNNING,
        long_running=long_running,
        created_at=NOW - age,
    )
    db.add(call)
    db.commit()
    return call


def test_a_synchronous_call_a_stopped_process_left_running_is_closed() -> None:
    db = _session(RealtimeSessionRow.__table__, RealtimeToolCall.__table__)
    stuck = _call(db, name="native.build", long_running=False, age=timedelta(hours=3))
    recent = _call(db, name="operator.type", long_running=False, age=timedelta(minutes=5))
    waiting = _call(db, name="research.start", long_running=True, age=timedelta(days=2))

    assert fail_interrupted_tool_calls(db, now=NOW) == 1

    db.refresh(stuck), db.refresh(recent), db.refresh(waiting)
    assert stuck.status == TOOL_STATUS_FAILED
    assert stuck.error_class == "interrupted"
    assert stuck.result_json["speech"] == SPEECH_TOOL_CALL_INTERRUPTED
    assert stuck.completed_at is not None
    # Still possibly working, and waiting on its task: both untouched.
    assert recent.status == TOOL_STATUS_RUNNING
    assert waiting.status == TOOL_STATUS_RUNNING
    # A second pass finds nothing more to close.
    assert fail_interrupted_tool_calls(db, now=NOW) == 0


def _build(db, *, state: str, age: timedelta) -> NativeBuildRow:
    row = NativeBuildRow(
        id=uuid.uuid4(),
        slug="notlarim",
        display_name="Notlarım",
        stack="dotnet_wpf",
        template="notes-desktop",
        target="windows_exe",
        version="0.1.0",
        state=state,
        spec_json={},
        created_at=NOW - age,
        updated_at=NOW - age,
    )
    db.add(row)
    db.commit()
    return row


def test_a_build_a_stopped_process_left_in_flight_is_closed_and_a_planned_one_is_not() -> None:
    db = _session(NativeBuildRow.__table__)
    stuck = _build(db, state=STATE_BUILDING, age=timedelta(hours=5))
    recent = _build(db, state=STATE_TESTING, age=timedelta(minutes=10))
    planned = _build(db, state=STATE_PLANNED, age=timedelta(days=3))

    assert fail_interrupted_builds(db, now=NOW) == 1

    db.refresh(stuck), db.refresh(recent), db.refresh(planned)
    assert stuck.state == STATE_FAILED
    assert stuck.error_class == "interrupted"
    assert SPEECH_BUILD_INTERRUPTED in stuck.error_message
    assert "building" in stuck.error_message  # the step it stopped in, for native.check
    assert recent.state == STATE_TESTING
    # Planned is waiting for the owner's "build it", which may come days later.
    assert planned.state == STATE_PLANNED
