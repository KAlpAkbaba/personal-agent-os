"""ResearchToolCallAnnouncer: completes a research.start tool call once its
research run's durable row is terminal (M18.2 DEFECT 2, ADR-0067).

Same shape as ``test_mobile_announcer.py``: the sweeper is exercised directly against
a session factory and a recording sideband, never through the app's lifespan (which
these fast unit tests never run) — the linkage a real ``research.start`` would record
(``task_id`` on the tool call's own ``result_json``) is fabricated here exactly as a
future wiring of the tool would produce it.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.broker.models import AuditEvent
from app.research.models import (
    STAGE_DISCOVERING,
    STAGE_FAILED,
    STAGE_READY,
    ResearchReportRow,
    ResearchRunRow,
)
from app.voice.realtime_sessions.models import (
    REALTIME_STATE_ACTIVE,
    REALTIME_STATE_CLOSED,
    TOOL_STATUS_FAILED,
    TOOL_STATUS_RUNNING,
    TOOL_STATUS_SUCCEEDED,
    RealtimeSessionRow,
    RealtimeToolCall,
)
from app.voice.realtime_sessions.research_announcer import (
    RESEARCH_START_TOOL,
    ResearchToolCallAnnouncer,
    pending_research_tool_call_ids,
)
from app.voice.realtime_sessions.sideband import RecordingSideband

NOW = datetime(2026, 9, 6, tzinfo=UTC)

REPORT_JSON = {
    "topic": "OpenAI, Anthropic ve Google karşılaştırması",
    "executive_summary": "240 kaynak incelendi.",
    "findings": [
        {
            "id": "f1",
            "title": "Bulgu Bir",
            "summary": "Kaynak: Yayın — Bulgu Bir (2026-09-05).",
            "why_it_matters": "Doğrudan konuyla ilgili.",
            "importance": 5,
            "label": "source_fact",
            "evidence_ids": ["e1"],
        }
    ],
    "sources": [{"id": "e1", "title": "Kaynak Bir", "url": "https://kaynak.example.com/x"}],
    "stats": {"discovered": 240, "fetched": 33, "rejected": 28, "evidence": 1},
}


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        RealtimeSessionRow.__table__,
        RealtimeToolCall.__table__,
        AuditEvent.__table__,
        ResearchRunRow.__table__,
        ResearchReportRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def scope():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return scope


def _seed_session(scope, *, state: str = REALTIME_STATE_ACTIVE) -> uuid.UUID:
    with scope() as session:
        row = RealtimeSessionRow(
            id=uuid.uuid4(),
            provider="simulator",
            transport="webrtc",
            client_kind="desktop",
            device_id=uuid.uuid4(),
            owner_session_id=uuid.uuid4(),
            language="tr-TR",
            state=state,
            context_json={},
            transcript_summary="",
            created_at=NOW,
            expires_at=NOW + timedelta(hours=1),
            updated_at=NOW,
        )
        session.add(row)
        session.commit()
        return row.id


def _seed_call(scope, session_id: uuid.UUID, *, call_id: str, task_id: str | None) -> None:
    result_json = {"status": "running", "plan_id": "p1"}
    if task_id is not None:
        result_json["task_id"] = task_id
    with scope() as session:
        session.add(
            RealtimeToolCall(
                session_id=session_id,
                call_id=call_id,
                name=RESEARCH_START_TOOL,
                arguments_json={"topic": "test"},
                status=TOOL_STATUS_RUNNING,
                long_running=True,
                result_json=result_json,
            )
        )
        session.commit()


def _seed_run(scope, task_id: uuid.UUID, *, stage: str, error: str | None = None) -> None:
    with scope() as session:
        session.add(ResearchRunRow(task_id=task_id, stage=stage, error=error))
        session.commit()


def _seed_report(scope, task_id: uuid.UUID) -> None:
    with scope() as session:
        session.add(
            ResearchReportRow(
                task_id=task_id, report_json=REPORT_JSON, synthesis_provider="deterministic"
            )
        )
        session.commit()


def _get_call(scope, call_id: str) -> RealtimeToolCall:
    with scope() as session:
        row = session.query(RealtimeToolCall).filter(RealtimeToolCall.call_id == call_id).one()
        session.expunge(row)
        return row


def test_a_ready_run_completes_the_call_with_spoken_result(session_factory) -> None:
    task_id = uuid.uuid4()
    session_id = _seed_session(session_factory)
    _seed_call(session_factory, session_id, call_id="r1", task_id=str(task_id))
    _seed_run(session_factory, task_id, stage=STAGE_READY)
    _seed_report(session_factory, task_id)
    sideband = RecordingSideband(deliver=True)
    announcer = ResearchToolCallAnnouncer(session_factory, sideband)

    assert announcer.sweep_once() == 1

    call = _get_call(session_factory, "r1")
    assert call.status == TOOL_STATUS_SUCCEEDED
    assert call.result_json["spoken_result"].startswith("Efendim, 'OpenAI")
    assert "Bulgu Bir" in call.result_json["spoken_result"]
    # never the pipeline's own diagnostics
    assert "240" not in call.result_json["spoken_result"]
    assert "elendi" not in call.result_json["spoken_result"]
    assert call.result_json["diagnostics"]["discovered_count"] == 240
    assert sideband.events() == ["tool_completed"]
    _, frame = sideband.frames[0]
    assert frame["payload"]["status"] == "succeeded"
    assert frame["payload"]["result"]["spoken_result"] == call.result_json["spoken_result"]

    # idempotent: a second sweep finds nothing left to complete
    assert announcer.sweep_once() == 0


def test_a_failed_run_completes_the_call_with_an_honest_failure(session_factory) -> None:
    task_id = uuid.uuid4()
    session_id = _seed_session(session_factory)
    _seed_call(session_factory, session_id, call_id="r2", task_id=str(task_id))
    _seed_run(session_factory, task_id, stage=STAGE_FAILED, error="insufficient_valid_findings")
    announcer = ResearchToolCallAnnouncer(session_factory, RecordingSideband(deliver=True))

    assert announcer.sweep_once() == 1

    call = _get_call(session_factory, "r2")
    assert call.status == TOOL_STATUS_FAILED
    assert call.error_class == "research_failed"


def test_a_run_still_in_progress_is_left_running(session_factory) -> None:
    task_id = uuid.uuid4()
    session_id = _seed_session(session_factory)
    _seed_call(session_factory, session_id, call_id="r3", task_id=str(task_id))
    _seed_run(session_factory, task_id, stage=STAGE_DISCOVERING)
    announcer = ResearchToolCallAnnouncer(session_factory, RecordingSideband(deliver=True))

    assert announcer.sweep_once() == 0
    call = _get_call(session_factory, "r3")
    assert call.status == TOOL_STATUS_RUNNING


def test_a_call_with_no_task_id_is_never_guessed_at(session_factory) -> None:
    """Every research.start call today, until the tool is wired to the real
    pipeline: nothing here invents a linkage it was not given."""
    session_id = _seed_session(session_factory)
    _seed_call(session_factory, session_id, call_id="r4", task_id=None)
    announcer = ResearchToolCallAnnouncer(session_factory, RecordingSideband(deliver=True))

    assert announcer.sweep_once() == 0
    call = _get_call(session_factory, "r4")
    assert call.status == TOOL_STATUS_RUNNING


def test_a_run_ready_but_missing_its_report_row_answers_honestly(session_factory) -> None:
    """A defensive path: READY with no report row (should not happen in practice) is
    answered as insufficient evidence, never as an invented result."""
    task_id = uuid.uuid4()
    session_id = _seed_session(session_factory)
    _seed_call(session_factory, session_id, call_id="r5", task_id=str(task_id))
    _seed_run(session_factory, task_id, stage=STAGE_READY)
    announcer = ResearchToolCallAnnouncer(session_factory, RecordingSideband(deliver=True))

    assert announcer.sweep_once() == 1
    call = _get_call(session_factory, "r5")
    assert call.status == TOOL_STATUS_SUCCEEDED
    assert call.result_json["findings"] == []
    assert "yeterli doğrulanmış kaynak bulamadım" in call.result_json["spoken_result"]


def test_completion_is_recorded_even_when_the_session_has_since_closed(session_factory) -> None:
    """A research run can easily outlive the realtime session that started it — the
    outcome must still be recorded durably, even though there is nowhere live to
    deliver 'tool_completed' to."""
    task_id = uuid.uuid4()
    session_id = _seed_session(session_factory, state=REALTIME_STATE_CLOSED)
    _seed_call(session_factory, session_id, call_id="r6", task_id=str(task_id))
    _seed_run(session_factory, task_id, stage=STAGE_READY)
    _seed_report(session_factory, task_id)
    sideband = RecordingSideband(deliver=True)
    announcer = ResearchToolCallAnnouncer(session_factory, sideband)

    assert announcer.sweep_once() == 1
    call = _get_call(session_factory, "r6")
    assert call.status == TOOL_STATUS_SUCCEEDED
    assert sideband.frames == []  # nothing live to push to; recorded regardless


def test_pending_ids_lists_what_the_sweep_has_not_yet_reached(session_factory) -> None:
    task_id = uuid.uuid4()
    session_id = _seed_session(session_factory)
    _seed_call(session_factory, session_id, call_id="r7", task_id=str(task_id))
    with session_factory() as session:
        assert list(pending_research_tool_call_ids(session)) == ["r7"]
    _seed_run(session_factory, task_id, stage=STAGE_READY)
    _seed_report(session_factory, task_id)
    ResearchToolCallAnnouncer(session_factory, RecordingSideband(deliver=True)).sweep_once()
    with session_factory() as session:
        assert list(pending_research_tool_call_ids(session)) == []


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(session_factory) -> None:
    announcer = ResearchToolCallAnnouncer(
        session_factory, RecordingSideband(deliver=True), interval_s=0.01
    )
    await announcer.start()
    await announcer.start()
    assert announcer.running
    await announcer.stop()
    await announcer.stop()
    assert not announcer.running


def test_a_run_that_failed_after_writing_its_report_still_reads_the_report(session_factory) -> None:
    """ADR-0178 E, reported not fixed: the failed branch never looked for a report, so a run
    that HAD found and written findings before a later step failed spoke its own stack trace
    ("ranking: 0 contract-valid item(s)") instead of what it found."""
    task_id = uuid.uuid4()
    session_id = _seed_session(session_factory)
    _seed_call(session_factory, session_id, call_id="r9", task_id=str(task_id))
    _seed_run(
        session_factory,
        task_id,
        stage=STAGE_FAILED,
        error="artifact_persist_failed: S3 PutObject timed out",
    )
    _seed_report(session_factory, task_id)
    announcer = ResearchToolCallAnnouncer(session_factory, RecordingSideband(deliver=True))

    assert announcer.sweep_once() == 1

    call = _get_call(session_factory, "r9")
    assert call.status == TOOL_STATUS_SUCCEEDED, call.result_json
    assert "Bulgu Bir" in call.result_json["spoken_result"]
    assert "S3" not in call.result_json["spoken_result"]


def test_a_failure_with_an_empty_report_row_is_still_a_failure(session_factory) -> None:
    """An empty report is not a report: the run's own owner-facing message stays."""
    task_id = uuid.uuid4()
    session_id = _seed_session(session_factory)
    _seed_call(session_factory, session_id, call_id="r10", task_id=str(task_id))
    _seed_run(session_factory, task_id, stage=STAGE_FAILED, error="12 sayfa okudum efendim.")
    with session_factory() as session:
        session.add(
            ResearchReportRow(task_id=task_id, report_json={}, synthesis_provider="deterministic")
        )
        session.commit()
    announcer = ResearchToolCallAnnouncer(session_factory, RecordingSideband(deliver=True))

    assert announcer.sweep_once() == 1

    call = _get_call(session_factory, "r10")
    assert call.status == TOOL_STATUS_FAILED
    assert call.error_class == "research_failed"
    assert "12 sayfa okudum" in (call.result_json or {}).get("message", "")
