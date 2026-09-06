"""research.start starts the REAL M13 pipeline (M18.2 follow-up to ADR-0067):
task + device selection synchronously, inside the tool call's own transaction, with
the Temporal workflow started as a ``ToolContext`` follow-up the realtime-session
route awaits after commit. No capable device is an immediate, truthful failure;
a follow-up that cannot start the workflow completes the call as failed with a
truthful Turkish ``speech`` instead of leaving it running forever; and the
research-completion announcer can finish a call research.start itself started,
end to end, with no fabricated linkage.

Fixture shape mirrors test_voice_realtime_sessions.py's own ``wired`` and
test_research_routes.py's device-enrollment helper.
"""

from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts import service as artifact_service
from app.artifacts.models import (
    TASK_STATUS_FAILED_TERMINAL,
    Artifact,
    ArtifactRender,
    ArtifactVersion,
    ResearchSource,
    Task,
    TaskRun,
)
from app.artifacts.runtime import ArtifactRuntime
from app.broker import service as broker_service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.config import Settings
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.main import create_app
from app.narration.models import NarrationSession, PronunciationEntry
from app.research import runs_service
from app.research.models import (
    STAGE_FAILED,
    STAGE_PLANNED,
    STAGE_READY,
    ResearchCandidateRow,
    ResearchEvidenceRow,
    ResearchReportRow,
    ResearchRunRow,
)
from app.voice.models import VoiceProfile
from app.voice.realtime_sessions import service
from app.voice.realtime_sessions.models import (
    TOOL_STATUS_FAILED,
    TOOL_STATUS_SUCCEEDED,
    RealtimeSessionRow,
    RealtimeToolCall,
)
from app.voice.realtime_sessions.research_announcer import ResearchToolCallAnnouncer
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.realtime_sessions.tools import (
    ERROR_RESEARCH_WORKFLOW_START_FAILED,
    RESEARCH_START_NO_DEVICE_TR,
    RESEARCH_START_WORKFLOW_FAILED_TR,
)
from app.voice.simulator import SimulatedRealtimeProvider
from tests.identity_support import IDENTITY_TABLES

VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"

RESEARCH_TABLES = (
    Device.__table__,
    DeviceSession.__table__,
    DeviceCommand.__table__,
    EnrollmentToken.__table__,
    Task.__table__,
    TaskRun.__table__,
    ArtifactRender.__table__,
    ResearchSource.__table__,
    ResearchRunRow.__table__,
    ResearchCandidateRow.__table__,
    ResearchEvidenceRow.__table__,
    ResearchReportRow.__table__,
)

REPORT_JSON = {
    "topic": "agent gelişmeleri",
    "findings": [
        {
            "id": "f1",
            "title": "Bulgu Bir",
            "summary": "Kaynak: Yayın — Bulgu Bir.",
            "why_it_matters": "Doğrudan konuyla ilgili.",
            "importance": 5,
            "label": "source_fact",
            "evidence_ids": ["e1"],
        }
    ],
    "sources": [{"id": "e1", "title": "Kaynak Bir", "url": "https://kaynak.example.com/x"}],
    "stats": {"discovered": 100, "fetched": 20, "rejected": 10},
}


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


def _enroll_online_device(broker: BrokerRuntime) -> uuid.UUID:
    with broker.session() as db:
        device = broker_service.enroll_device(
            db,
            name="ev-pc",
            platform="windows",
            public_key_spki_b64=_spki(),
            capabilities=["browser.chrome"],
            trace_id=None,
        )
    broker.connections[device.id] = DeviceConnection(
        device_id=device.id, session_id=uuid.uuid4(), websocket=object()
    )
    return device.id


def _patched_temporal_client(fake_client=None):
    fake_client = fake_client or AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    return patch("app.research.service.Client.connect", AsyncMock(return_value=fake_client))


@pytest.fixture()
def wired():
    settings = Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in IDENTITY_TABLES:
        table.create(engine)
    for table in (
        RealtimeSessionRow.__table__,
        RealtimeToolCall.__table__,
        AuditEvent.__table__,
        VoiceProfile.__table__,
        NarrationSession.__table__,
        PronunciationEntry.__table__,
        Artifact.__table__,
        ArtifactVersion.__table__,
        ActivityEventRow.__table__,
        PendingBriefingRow.__table__,
        *RESEARCH_TABLES,
    ):
        table.create(engine)

    app = create_app(settings)
    identity = IdentityRuntime(settings, engine=engine, root=InMemoryCredentialRoot())
    identity.service.bootstrap()
    app.state.identity = identity
    sideband = RecordingSideband(deliver=True)
    sim = SimulatedRealtimeProvider()
    broker = BrokerRuntime(settings)
    broker._engine = engine
    broker._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.broker = broker
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    runtime = RealtimeVoiceRuntime(
        settings,
        engine=engine,
        providers={sim.name: sim},
        sideband=sideband,
        broker=broker,
        artifacts=artifacts,
    )
    app.state.voice_realtime = runtime

    issued = identity.service.issue_session(
        client_kind="desktop", label="pc", device_id=uuid.uuid4()
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issued.token}"
    try:
        yield client, runtime, sideband, broker, artifacts
    finally:
        client.close()


def _create(client) -> str:
    response = client.post("/v1/voice/realtime/sessions", json={})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


# ------------------------------------------------------------- the real start


def test_research_start_creates_a_real_task_and_run_with_recency_and_linkage(wired) -> None:
    client, runtime, sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    sid = _create(client)
    topic = "Son üç gündeki agent gelişmelerini araştır"
    with _patched_temporal_client():
        response = client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "r1", "name": "research.start", "arguments": {"topic": topic}},
        )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "running"
    task_id = data["result"]["task_id"]
    assert data["result"]["plan_id"] == task_id
    assert data["result"]["workflow_id"] == f"research-browser-{task_id}"
    assert data["result"]["device"]["device_id"]

    with artifacts.session() as db:
        task = artifact_service.get_task(db, uuid.UUID(task_id))
        assert task is not None and task.intent == topic

    with runtime.session() as db:
        run = runs_service.get_run(db, uuid.UUID(task_id))
        assert run is not None
        assert run.stage == STAGE_PLANNED
        assert run.device_id is not None
        planned_events = [e for e in run.events_json if e.get("stage") == STAGE_PLANNED]
        assert planned_events, run.events_json
        assert planned_events[-1]["source"] == "voice"
        assert planned_events[-1]["session_id"] == sid
        assert planned_events[-1]["tool_call_id"] == "r1"

    state = client.get(f"/v1/voice/realtime/sessions/{sid}").json()
    assert state["plan"]["task_id"] == task_id
    assert state["plan"]["workflow_id"] == f"research-browser-{task_id}"
    # "son üç gündeki ..." -> app.research.dates parses 3 (docs/DECISIONS.md ADR-0067
    # amendment): the SAME parser the REST plan stage uses, not a guess.
    assert state["plan"]["recency_days"] == 3


def test_research_start_follow_up_starts_the_workflow_and_persists_the_workflow_id(
    wired,
) -> None:
    client, runtime, sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    sid = _create(client)
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    with _patched_temporal_client(fake_client):
        response = client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={
                "call_id": "r1",
                "name": "research.start",
                "arguments": {"topic": "son üç gündeki agent gelişmeleri"},
            },
        )
    task_id = response.json()["result"]["task_id"]
    assert fake_client.start_workflow.await_count == 1
    request_arg = fake_client.start_workflow.call_args.args[1]
    assert request_arg.task_id == task_id
    assert request_arg.recency_days == 3

    with artifacts.session() as db:
        task = artifact_service.get_task(db, uuid.UUID(task_id))
        assert task.workflow_id == f"research-browser-{task_id}"


def test_research_start_without_capable_device_is_an_immediate_truthful_failure(wired) -> None:
    client, runtime, sideband, broker, artifacts = wired
    sid = _create(client)
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": "r1", "name": "research.start", "arguments": {"topic": "konu"}},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "failed"
    assert data["error"]["error_class"] == "capability_missing"
    assert data["error"]["speech"] == RESEARCH_START_NO_DEVICE_TR
    assert data["error"]["message"] == RESEARCH_START_NO_DEVICE_TR

    task_id = data["error"]["details"]["task_id"]
    with artifacts.session() as db:
        task = artifact_service.get_task(db, uuid.UUID(task_id))
        assert task.status == TASK_STATUS_FAILED_TERMINAL
        assert task.error_class == "no_capable_device"
    with runtime.session() as db:
        run = runs_service.get_run(db, uuid.UUID(task_id))
        assert run.stage == STAGE_FAILED
        failed_events = [e for e in run.events_json if e.get("stage") == STAGE_FAILED]
        assert failed_events[-1]["source"] == "voice"
        assert failed_events[-1]["session_id"] == sid

    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()
    research_call = next(c for c in activity["tool_calls"] if c["call_id"] == "r1")
    assert research_call["speech_head"] == RESEARCH_START_NO_DEVICE_TR[:80]


def test_research_start_workflow_start_failure_completes_the_call_as_failed_with_speech(
    wired,
) -> None:
    client, runtime, sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    sid = _create(client)
    with patch(
        "app.research.service.Client.connect", AsyncMock(side_effect=RuntimeError("no temporal"))
    ):
        response = client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "r1", "name": "research.start", "arguments": {"topic": "konu"}},
        )
    # The tool-call's OWN response is still "running": the failure is discovered only
    # once the follow-up (awaited before this response is returned, but after the
    # RUNNING payload was already built) tries to connect.
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "running"

    with runtime.session() as db:
        call = service.get_tool_call(db, uuid.UUID(sid), "r1")
        assert call.status == TOOL_STATUS_FAILED
        assert call.error_class == ERROR_RESEARCH_WORKFLOW_START_FAILED
        assert call.result_json["speech"] == RESEARCH_START_WORKFLOW_FAILED_TR

    assert sideband.events()[-1] == "tool_completed"
    _, frame = sideband.frames[-1]
    assert frame["payload"]["error"]["speech"] == RESEARCH_START_WORKFLOW_FAILED_TR
    assert frame["payload"]["error"]["error_class"] == ERROR_RESEARCH_WORKFLOW_START_FAILED

    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()
    research_call = next(c for c in activity["tool_calls"] if c["call_id"] == "r1")
    assert research_call["speech_head"] == RESEARCH_START_WORKFLOW_FAILED_TR[:80]


# --------------------------------------------------------- plan.redirect honesty


def test_plan_redirect_on_a_running_research_plan_refuses_honestly(wired) -> None:
    client, runtime, sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    sid = _create(client)
    with _patched_temporal_client():
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "r1", "name": "research.start", "arguments": {"topic": "konu"}},
        )
    redirect = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={
            "call_id": "r2",
            "name": "plan.redirect",
            "arguments": {"instruction": "Sadece OpenAI kısmına bak"},
        },
    ).json()
    assert redirect["status"] == "succeeded"  # the TOOL CALL succeeded; the redirect is refused
    assert redirect["result"]["status"] == "refused"
    assert "değiştiremiyorum" in redirect["result"]["speech"]
    assert redirect["result"]["plan"]["scope"] == "genel"  # unchanged
    assert sideband.events() == []  # nothing changed, nothing pushed


# --------------------------------------------------------------- the announcer


def test_announcer_completes_a_real_research_start_call_with_spoken_result_and_speech(
    wired,
) -> None:
    client, runtime, sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    sid = _create(client)
    with _patched_temporal_client():
        started = client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "r1", "name": "research.start", "arguments": {"topic": "konu"}},
        )
    task_id = uuid.UUID(started.json()["result"]["task_id"])

    # The pipeline (never run for real here) reaches a terminal stage with a report.
    with runtime.session() as db:
        runs_service.upsert_report(db, task_id, report_json=REPORT_JSON, synthesis_provider="fake")
        runs_service.update_run(db, task_id, stage=STAGE_READY, event={"stage": STAGE_READY})

    announcer = ResearchToolCallAnnouncer(runtime.session, sideband)
    completed = announcer.sweep_once()
    assert completed == 1

    with runtime.session() as db:
        call = service.get_tool_call(db, uuid.UUID(sid), "r1")
        assert call.status == TOOL_STATUS_SUCCEEDED
        assert call.result_json["speech"] == call.result_json["spoken_result"]
        assert "elendi" not in call.result_json["spoken_result"]

    assert sideband.events()[-1] == "tool_completed"
    _, frame = sideband.frames[-1]
    assert frame["payload"]["result"]["spoken_result"] == call.result_json["spoken_result"]
    assert frame["payload"]["result"]["speech"] == call.result_json["spoken_result"]

    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()
    research_call = next(c for c in activity["tool_calls"] if c["call_id"] == "r1")
    assert research_call["speech_head"] == call.result_json["spoken_result"][:80]
