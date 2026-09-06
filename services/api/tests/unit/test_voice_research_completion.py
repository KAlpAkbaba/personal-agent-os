"""complete_tool_call_system / find_running_tool_call_by_task_id (M18.2 DEFECT 2,
ADR-0067): the non-owner completion path a durable backend job uses to finish a
long-running tool call, and the durable evidence (the tool call row,
``session_activity``'s ``speech_head``) that a research result actually reached it.

Fixture shape mirrors ``test_voice_realtime_sessions.py``'s ``wired``: offline,
SQLite, a recording sideband standing in for the device WebSocket.
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

from app.artifacts.models import (
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
from app.research.models import (
    ResearchCandidateRow,
    ResearchEvidenceRow,
    ResearchReportRow,
    ResearchRunRow,
)
from app.research.result import build_tool_terminal_payload
from app.voice.models import VoiceProfile
from app.voice.realtime_sessions import service
from app.voice.realtime_sessions.models import (
    TOOL_STATUS_FAILED,
    TOOL_STATUS_SUCCEEDED,
    RealtimeSessionRow,
    RealtimeToolCall,
)
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.simulator import SimulatedRealtimeProvider
from tests.identity_support import IDENTITY_TABLES

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


def _patched_temporal_client():
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    return patch("app.research.service.Client.connect", AsyncMock(return_value=fake_client))


VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"

REPORT_JSON = {
    "topic": "OpenAI, Anthropic ve Google karşılaştırması",
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
    # M18.2 follow-up to ADR-0067: research.start now creates a real task/run, so this
    # fixture needs a BrokerRuntime + ArtifactRuntime bound to the same engine, exactly
    # like test_voice_realtime_sessions.py's own "wired" fixture.
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
    _enroll_online_device(broker)

    issued = identity.service.issue_session(
        client_kind="desktop", label="pc", device_id=uuid.uuid4()
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issued.token}"
    try:
        yield client, runtime, sideband
    finally:
        client.close()


def _create(client) -> str:
    response = client.post("/v1/voice/realtime/sessions", json={})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def test_complete_tool_call_system_delivers_spoken_result_verbatim(wired) -> None:
    client, runtime, sideband = wired
    sid = _create(client)
    with _patched_temporal_client():
        started = client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "r1", "name": "research.start", "arguments": {"topic": "test"}},
        )
    assert started.status_code == 200 and started.json()["status"] == "running"

    payload = build_tool_terminal_payload(REPORT_JSON)
    with runtime.session() as db:
        row = service.get_session(db, uuid.UUID(sid))
        outcome = service.complete_tool_call_system(
            db, row, call_id="r1", result=payload, error=None, sideband=sideband, trace_id=None
        )

    assert outcome is not None
    assert outcome["delivered"] is True
    assert outcome["result"]["spoken_result"] == payload["spoken_result"]
    assert sideband.events()[-1] == "tool_completed"
    _, frame = sideband.frames[-1]
    assert frame["payload"]["result"]["spoken_result"] == payload["spoken_result"]

    # Durable: the row itself now carries the schema, not just the sideband push.
    with runtime.session() as db:
        call = service.get_tool_call(db, uuid.UUID(sid), "r1")
        assert call.status == TOOL_STATUS_SUCCEEDED
        assert set(call.result_json) == {
            "spoken_result",
            "speech",
            "executive_summary",
            "findings",
            "source_summary",
            "diagnostics",
        }
        assert call.result_json["speech"] == call.result_json["spoken_result"]
        assert "elendi" not in call.result_json["spoken_result"]
        assert "100" not in call.result_json["spoken_result"]

    # session_activity's speech_head is where a harness (and this session's own
    # evidence trail) sees what was actually said, from durable rows alone.
    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()
    research_call = next(c for c in activity["tool_calls"] if c["call_id"] == "r1")
    assert research_call["speech_head"] == payload["spoken_result"][:80]


def test_complete_tool_call_system_is_idempotent_and_never_raises_on_a_replay(wired) -> None:
    client, runtime, sideband = wired
    sid = _create(client)
    with _patched_temporal_client():
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "r2", "name": "research.start", "arguments": {"topic": "test"}},
        )
    payload = build_tool_terminal_payload(REPORT_JSON)
    with runtime.session() as db:
        row = service.get_session(db, uuid.UUID(sid))
        first = service.complete_tool_call_system(
            db, row, call_id="r2", result=payload, error=None, sideband=sideband, trace_id=None
        )
        second = service.complete_tool_call_system(
            db, row, call_id="r2", result=payload, error=None, sideband=sideband, trace_id=None
        )
    assert first is not None
    assert second is None  # already resolved: a no-op, never an error


def test_complete_tool_call_system_records_an_error_honestly(wired) -> None:
    client, runtime, sideband = wired
    sid = _create(client)
    with _patched_temporal_client():
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "r3", "name": "research.start", "arguments": {"topic": "test"}},
        )
    with runtime.session() as db:
        row = service.get_session(db, uuid.UUID(sid))
        outcome = service.complete_tool_call_system(
            db,
            row,
            call_id="r3",
            result=None,
            error={"error_class": "research_failed", "message": "insufficient_valid_findings"},
            sideband=sideband,
            trace_id=None,
        )
    assert outcome is not None
    assert outcome["status"] == TOOL_STATUS_FAILED
    with runtime.session() as db:
        call = service.get_tool_call(db, uuid.UUID(sid), "r3")
        assert call.error_class == "research_failed"


def test_complete_tool_call_system_still_records_the_outcome_when_the_session_is_closed(
    wired,
) -> None:
    client, runtime, sideband = wired
    sid = _create(client)
    with _patched_temporal_client():
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "r4", "name": "research.start", "arguments": {"topic": "test"}},
        )
    assert client.post(f"/v1/voice/realtime/sessions/{sid}/close").status_code == 200
    payload = build_tool_terminal_payload(REPORT_JSON)
    before = len(sideband.frames)
    with runtime.session() as db:
        row = service.get_session(db, uuid.UUID(sid))
        outcome = service.complete_tool_call_system(
            db, row, call_id="r4", result=payload, error=None, sideband=sideband, trace_id=None
        )
    assert outcome is not None
    assert outcome["delivered"] is False
    assert len(sideband.frames) == before  # nothing live to push to
    with runtime.session() as db:
        call = service.get_tool_call(db, uuid.UUID(sid), "r4")
        assert call.status == TOOL_STATUS_SUCCEEDED  # recorded regardless


def test_find_running_tool_call_by_task_id_locates_the_right_session_and_call(wired) -> None:
    client, runtime, sideband = wired
    sid = _create(client)
    task_id = str(uuid.uuid4())
    with _patched_temporal_client():
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "r5", "name": "research.start", "arguments": {"topic": "test"}},
        )
    # A future research.start (once wired to the real pipeline) would record this
    # itself, on its own 'running' result; fabricated here for the lookup contract.
    with runtime.session() as db:
        call = service.get_tool_call(db, uuid.UUID(sid), "r5")
        call.result_json = {**(call.result_json or {}), "task_id": task_id}
        db.commit()

    with runtime.session() as db:
        found = service.find_running_tool_call_by_task_id(
            db, name="research.start", task_id=task_id
        )
        assert found is not None
        found_row, found_call = found
        assert str(found_row.id) == sid
        assert found_call.call_id == "r5"

        missing = service.find_running_tool_call_by_task_id(
            db, name="research.start", task_id=str(uuid.uuid4())
        )
        assert missing is None
