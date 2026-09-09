"""M12 unit tests: the realtime session service over real HTTP on SQLite.

Offline: identity, the realtime tables, ``audit_events``, ``voice_profiles``
and the narration/artifact tables share one in-memory engine, the simulator
is the selected provider, and a recording sideband stands in for the broker
WebSocket. What is asserted is the spec §4/§6/§7/§9 contract: capability
selection, a per-session credential that is never the vendor key and is
never stored, idempotent tool relay, the long-running preamble + completion
push, client events feeding the benchmark, continuity across clients, and
audit rows that carry ids/timings only.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
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
from app.voice.models import VoiceProfile
from app.voice.providers import TRANSPORT_SIMULATED
from app.voice.realtime_sessions import service
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.realtime_sessions.tools import RESEARCH_PREAMBLE_TR
from app.voice.simulator import SimulatedRealtimeProvider
from tests.identity_support import IDENTITY_TABLES, bearer

# A recognisable NON-secret sentinel (deliberately not shaped like any vendor
# key format, so the repository's secret-hygiene scan has nothing to flag).
VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"
DEVICE_ID = uuid.uuid4()

#: research.start's own tables (M18.2 follow-up to ADR-0067): the tool now creates a
#: REAL task/run through app.research.service.start_browser_research, so the fixture
#: needs the same tables test_research_routes.py's does.
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


def _enroll_online_device(broker: BrokerRuntime, *, capabilities=("browser.chrome",)) -> uuid.UUID:
    """A browser.chrome-capable device research.start's device selection can find —
    mirrors tests/unit/test_research_routes.py's own helper."""
    with broker.session() as db:
        device = broker_service.enroll_device(
            db,
            name="ev-pc",
            platform="windows",
            public_key_spki_b64=_spki(),
            capabilities=list(capabilities),
            trace_id=None,
        )
    broker.connections[device.id] = DeviceConnection(
        device_id=device.id, session_id=uuid.uuid4(), websocket=object()
    )
    return device.id


def _patched_temporal_client():
    """Stands in for app.research.service.connect_temporal's Client.connect during a
    research.start follow-up (the DB half runs for real; Temporal never does)."""
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    return patch("app.research.service.Client.connect", AsyncMock(return_value=fake_client))


DOC = """# Rapor

Birinci madde: maliyetler arttı.

İkinci madde: gecikme düştü.

Üçüncü madde: memnuniyet yükseldi.
"""


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
    # M18.2 follow-up to ADR-0067: research.start needs a real BrokerRuntime (device
    # selection) and ArtifactRuntime (task/run persistence), both bound to this test's
    # own engine — the same wiring app.main.create_app gives production, and the same
    # pattern test_research_routes.py's own fixture uses.
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

    issued = identity.service.issue_session(client_kind="desktop", label="pc", device_id=DEVICE_ID)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issued.token}"
    try:
        yield client, identity, runtime, sideband, issued, engine
    finally:
        client.close()


def _create(client, **body):
    response = client.post("/v1/voice/realtime/sessions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _audit_rows(runtime, session_id: str, action: str | None = None) -> list[AuditEvent]:
    with runtime.session() as db:
        stmt = select(AuditEvent).where(
            AuditEvent.category == service.AUDIT_CATEGORY, AuditEvent.subject_ref == session_id
        )
        if action:
            stmt = stmt.where(AuditEvent.action == action)
        return list(db.execute(stmt.order_by(AuditEvent.id)).scalars())


# ------------------------------------------------------------------ create


def test_create_selects_by_capability_and_returns_the_contract(wired) -> None:
    client, _, runtime, _, issued, _ = wired
    data = _create(client)
    assert set(data) >= {
        "session_id",
        "provider",
        "transport",
        "credential",
        "tools",
        "instructions",
        "expires_at",
        "state",
    }
    assert data["provider"] == "simulator"
    assert data["transport"] == TRANSPORT_SIMULATED
    assert data["state"] == "created"
    assert {t["name"] for t in data["tools"]} == {
        "clock.now",
        "voice.intent",
        "narration.control",
        "research.start",
        # ADR-0076: follow-ups on a FINISHED research, resolved server-side.
        "research.explain",
        "research.sources",
        "research.finding_detail",
        "plan.redirect",
        "activity.explain",
        "state.now",
        "eye.enable",
        "eye.disable",
        "release.promote",
        # M18.3 (ADR-0071): the wake alarm, the displays and the ambient policy by voice.
        "alarm.create",
        "alarm.cancel",
        "alarm.snooze",
        "alarm.stop",
        "alarm.status",
        "display.off",
        "display.wake",
        "display.status",
        "ambient.set_policy",
        "ambient.test_display",
        # ADR-0079: the ambient explanation query.
        "ambient.explain",
        # M18.4 (ADR-0081): the owner's voice over self-evolution.
        "evolution.control",
        "evolution.status",
        "release.rollback",
        # M19 (docs/M19_DIGITAL_OPERATOR_SPEC.md §3): the Digital Operator.
        "operator.app_open",
        "operator.window_control",
        "operator.type",
        "operator.shell",
        "operator.cancel",
        "operator.status",
        # M20 (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3): File & Document
        # Intelligence.
        "file.search",
        "document.read",
        "document.summarize",
        "document.answer",
        "document.compare",
        "document.inspect",
        "document.common_points",
        "document.previous",
        # M21 (docs/M21_MAIL_CALENDAR_SPEC.md §4): Mail & Calendar.
        "mail.inbox",
        "mail.search",
        "mail.read",
        "mail.thread",
        "mail.draft",
        "mail.edit_draft",
        "mail.read_draft",
        "mail.send",
        "mail.discard",
        "calendar.agenda",
        "calendar.find_slot",
        "calendar.propose",
        "calendar.read_proposal",
        "calendar.commit",
        "calendar.discard",
        # M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §5): the Artifact Factory.
        "artifact.create",
        "artifact.render",
        "artifact.validate",
        "artifact.open",
        "artifact.list",
        # M23 (docs/M23_APP_FACTORY_SPEC.md §5): the App Factory.
        "app.create",
        "app.run",
        "app.test",
        "app.stop",
        "app.status",
        "app.open",
        "app.list",
        # M24 (docs/M24_CAPABILITY_GENESIS_SPEC.md §6): Capability Genesis.
        "capability.request",
        "capability.status",
        "capability.approve",
        "capability.cancel",
        # M25 (docs/M25_CREATIVE_3D_SPEC.md §5): 3D Creation.
        "scene.create",
        "scene.add",
        "scene.transform",
        "scene.material",
        "scene.light",
        "scene.camera",
        "scene.render",
        "scene.inspect",
        # M26 (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md §5): Executive Autonomy.
        "executive.start",
        "executive.status",
        "executive.explain",
        "executive.pause",
        "executive.resume",
        "executive.retry",
        "executive.amend",
        "executive.cancel",
        # ADR-0091: Owner Location Context, Live Weather, Morning Briefing.
        "weather.current",
        "weather.last_evidence",
        "location.get_default",
        "location.set_default",
        "briefing.morning",
        "briefing.system_status",
        "briefing.overnight_work",
    }
    research = next(t for t in data["tools"] if t["name"] == "research.start")
    assert research["long_running"] is True and research["preamble"] == RESEARCH_PREAMBLE_TR
    assert "Türkçe" in data["instructions"] and "yönetici özeti" in data["instructions"]
    assert "'dur'" in data["instructions"]
    # the record is device-bound to the owner session's device
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(data["session_id"]))
        assert row.device_id == DEVICE_ID
        assert row.owner_session_id == issued.context.session_id
        assert row.client_kind == "desktop"


def test_credential_is_per_session_short_lived_and_never_the_vendor_key(wired) -> None:
    client, _, runtime, _, _, engine = wired
    a = _create(client)
    b = _create(client)
    cred = a["credential"]
    assert cred["provider"] == "simulator" and cred["secret"].startswith("sim_")
    assert cred["secret"] != b["credential"]["secret"]
    assert VENDOR_KEY not in a.__repr__() and VENDOR_KEY not in b.__repr__()
    # the settings key never reaches the provider adapter's credential either
    assert cred["secret"] != VENDOR_KEY and VENDOR_KEY not in cred["secret"]
    # not persisted anywhere: neither the session row nor any audit row
    secret = cred["secret"]
    with runtime.session() as db:
        for row in db.execute(select(RealtimeSessionRow)).scalars():
            assert secret not in repr(row.context_json)
        for ev in db.execute(select(AuditEvent)).scalars():
            assert secret not in repr(ev.metadata_json)
            assert VENDOR_KEY not in repr(ev.metadata_json)
    # state endpoint never echoes it
    state = client.get(f"/v1/voice/realtime/sessions/{a['session_id']}").json()
    assert "credential" not in state and secret not in repr(state)
    minted = _audit_rows(runtime, a["session_id"], service.ACTION_CREDENTIAL_MINTED)
    assert len(minted) == 1 and minted[0].metadata_json["session_ref"] == f"sim:{a['session_id']}"
    assert "secret" not in minted[0].metadata_json


def test_create_validates_transport_and_narration_reference(wired) -> None:
    client, *_ = wired
    assert (
        client.post("/v1/voice/realtime/sessions", json={"transport": "webrtc"}).status_code == 422
    )
    assert (
        client.post("/v1/voice/realtime/sessions", json={"transport": "carrier"}).status_code == 422
    )
    assert (
        client.post(
            "/v1/voice/realtime/sessions", json={"narration_session_id": str(uuid.uuid4())}
        ).status_code
        == 422
    )
    assert client.post("/v1/voice/realtime/sessions", json={"bogus": 1}).status_code == 422


def test_no_capable_provider_is_a_503_not_a_silent_fallback(wired) -> None:
    client, _, runtime, _, _, _ = wired
    runtime.providers.clear()
    response = client.post("/v1/voice/realtime/sessions", json={})
    assert response.status_code == 503
    assert response.json()["detail"]["error_class"] == "capability_missing"
    assert runtime.health_check()["status"] == "fail"


def test_providers_endpoint_lists_capabilities_without_secrets(wired) -> None:
    client, *_ = wired
    data = client.get("/v1/voice/realtime/providers").json()
    assert data["count"] == 1 and data["providers"][0]["speech_to_speech"] is True
    assert data["selection"]["selected"] == "simulator"
    assert VENDOR_KEY not in repr(data)


# --------------------------------------------------------------- tool calls


def test_sync_tool_call_executes_once_and_replays_idempotently(wired) -> None:
    client, _, runtime, _, _, _ = wired
    sid = _create(client)["session_id"]
    body = {"call_id": "call_1", "name": "clock.now", "arguments": {}}
    first = client.post(f"/v1/voice/realtime/sessions/{sid}/tool-calls", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "succeeded" and first.json()["replayed"] is False
    assert "now" in first.json()["result"]
    again = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls", json={**body, "name": "research.start"}
    )  # same call_id wins
    assert again.json()["replayed"] is True
    assert again.json()["result"] == first.json()["result"]
    with runtime.session() as db:
        calls = list(db.execute(select(RealtimeToolCall)).scalars())
        assert len(calls) == 1 and calls[0].status == "succeeded"
    assert len(_audit_rows(runtime, sid, service.ACTION_TOOL_CALL)) == 1
    assert len(_audit_rows(runtime, sid, service.ACTION_TOOL_CALL_REPLAYED)) == 1
    assert client.get(f"/v1/voice/realtime/sessions/{sid}").json()["state"] == "active"


def test_unknown_tool_fails_without_killing_the_session(wired) -> None:
    client, *_ = wired
    sid = _create(client)["session_id"]
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": "c", "name": "shell.exec", "arguments": {}},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"]["error_class"] == "capability_missing"
    assert client.get(f"/v1/voice/realtime/sessions/{sid}").json()["state"] == "active"


def test_long_running_tool_returns_preamble_then_completes_over_the_sideband(wired) -> None:
    client, _, runtime, sideband, _, _ = wired
    _enroll_online_device(runtime.broker)
    sid = _create(client)["session_id"]
    with _patched_temporal_client():
        response = client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={
                "call_id": "call_r1",
                "name": "research.start",
                "arguments": {"topic": "OpenAI, Anthropic, Google ve açık kaynak gelişmeleri"},
            },
        )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "running"
    assert data["preamble"].startswith("Bakıyorum.")
    plan_id = data["result"]["plan_id"]
    assert data["result"]["task_id"] == plan_id  # M18.2 follow-up to ADR-0067: real ids
    assert data["result"]["workflow_id"] == f"research-browser-{plan_id}"
    state = client.get(f"/v1/voice/realtime/sessions/{sid}").json()
    assert state["plan_id"] == plan_id and state["plan"]["status"] == "running"
    assert state["plan"]["task_id"] == plan_id

    # a mid-task redirect is an honest refusal (docs/DECISIONS.md ADR-0067 amendment):
    # the M13 workflow has no signal to change scope, so nothing is claimed and the
    # SAME plan comes back unchanged - no plan_changed push, because nothing changed.
    redirect = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={
            "call_id": "call_r2",
            "name": "plan.redirect",
            "arguments": {"instruction": "Sadece OpenAI kısmına bak"},
        },
    ).json()
    assert redirect["status"] == "succeeded"
    assert redirect["result"]["status"] == "refused"
    assert redirect["result"]["plan"]["plan_id"] == plan_id
    assert redirect["result"]["plan"]["revision"] == 1
    assert redirect["result"]["plan"]["scope"] == "genel"
    assert "değiştiremiyorum" in redirect["result"]["speech"]
    assert sideband.events() == []

    # the pipeline finishes -> tool_completed pushed; the plan is completed
    done = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls/call_r1/complete",
        json={"result": {"summary": "3 kaynak"}},
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "succeeded" and done.json()["delivered"] is True
    assert sideband.events() == ["tool_completed"]
    assert sideband.frames[0][1]["payload"]["result"] == {"summary": "3 kaynak"}
    state = client.get(f"/v1/voice/realtime/sessions/{sid}").json()
    assert state["plan"]["status"] == "completed"
    # completing twice is refused
    twice = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls/call_r1/complete", json={"result": {}}
    )
    assert twice.status_code == 422
    assert (
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls/nope/complete", json={"result": {}}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls/call_r1/complete", json={}
        ).status_code
        == 422
    )


def test_undeliverable_sideband_is_queued_and_drained_on_events(wired) -> None:
    client, _, runtime, sideband, _, _ = wired
    _enroll_online_device(runtime.broker)
    sideband.deliver = False
    sid = _create(client)["session_id"]
    with _patched_temporal_client():
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "r", "name": "research.start", "arguments": {"topic": "x"}},
        )
    client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls/r/complete",
        json={"error": {"error_class": "dependency_unavailable", "message": "boom"}},
    )
    state = client.get(f"/v1/voice/realtime/sessions/{sid}").json()
    assert state["pending_sideband_count"] == 1 and state["plan"]["status"] == "failed"
    assert len(_audit_rows(runtime, sid, service.ACTION_SIDEBAND_QUEUED)) == 1
    events = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "mic_speech_start", "t_ms": 10, "turn": 1}]},
    ).json()
    assert [f["event"] for f in events["pending_sideband"]] == ["tool_completed"]
    assert events["pending_sideband"][0]["payload"]["error"]["error_class"] == (
        "dependency_unavailable"
    )
    assert client.get(f"/v1/voice/realtime/sessions/{sid}").json()["pending_sideband_count"] == 0


def test_tool_call_payload_validation(wired) -> None:
    client, *_ = wired
    sid = _create(client)["session_id"]
    url = f"/v1/voice/realtime/sessions/{sid}/tool-calls"
    assert client.post(url, json={"call_id": "", "name": "clock.now"}).status_code == 422
    assert client.post(url, json={"call_id": "c", "name": "Bad Name"}).status_code == 422
    assert (
        client.post(
            url, json={"call_id": "c", "name": "clock.now", "arguments": {"audio_pcm": "..."}}
        ).status_code
        == 422
    )
    assert (
        client.post(
            url, json={"call_id": "c", "name": "clock.now", "arguments": {"blob": "x" * 20_000}}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/voice/realtime/sessions/{uuid.uuid4()}/tool-calls",
            json={"call_id": "c", "name": "clock.now"},
        ).status_code
        == 404
    )


# ------------------------------------------------------------------- events


def test_events_feed_the_benchmark_and_resolve_intents_server_side(wired) -> None:
    client, _, runtime, _, _, _ = wired
    sid = _create(client)["session_id"]
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={
            "events": [
                {"kind": "mic_speech_start", "t_ms": 1000, "turn": 1},
                {"kind": "uplink_first_packet", "t_ms": 1040, "turn": 1},
                {"kind": "end_of_turn", "t_ms": 2200, "turn": 1},
                {"kind": "first_audio", "t_ms": 2700, "turn": 1},
                {
                    "kind": "barge_in_start",
                    "t_ms": 3000,
                    "turn": 2,
                    "payload": {"playback_stopped_ms": 70},
                },
                {"kind": "playback_stopped", "t_ms": 3070, "turn": 2},
                {
                    "kind": "utterance",
                    "t_ms": 3100,
                    "turn": 2,
                    "text": "şey, ikinci maddeyi tekrar oku",
                },
                {"kind": "utterance", "t_ms": 3200, "turn": 2, "text": "durum raporu"},
                {"kind": "state", "t_ms": 3300, "payload": {"state": "LISTENING"}},
                {"kind": "summary", "t_ms": 3400, "text": "Sahip raporun ikinci maddesini istedi."},
                {"kind": "network_lost", "t_ms": 4000},
                {"kind": "network_restored", "t_ms": 4500},
            ]
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["accepted"] == 12
    intents = data["resolved_intents"]
    assert intents[0]["intent"] == "repeat_item" and intents[0]["target_index"] == 2
    assert intents[0]["fillers_removed"] == 1
    assert intents[1]["intent"] == "none"
    state = data["state"]
    assert state["state"] == "active" and state["barge_in_count"] == 1
    assert state["fsm_state"] == "LISTENING" and state["network"] == "restored"
    assert state["transcript_summary"] == "Sahip raporun ikinci maddesini istedi."
    assert state["last_intent"] == "none"

    # the transcript text is never audited, the resolved intent is
    for row in _audit_rows(runtime, sid, service.ACTION_INTENT_RESOLVED):
        assert "text" not in row.metadata_json and "ikinci" not in repr(row.metadata_json)
        assert row.metadata_json["intent"] in ("repeat_item", "none")

    report = client.get(f"/v1/voice/realtime/sessions/{sid}/benchmark").json()
    assert report["source"] == "client" and report["is_acceptance_evidence"] is True
    assert report["metrics"]["mic_to_uplink_ms"]["p50"] == 40
    assert report["metrics"]["eot_to_first_audio_ms"]["p50"] == 500
    assert report["metrics"]["barge_in_to_stop_ms"]["p50"] == 70
    assert report["target_check"]["barge_in_to_stop_ms"]["met"] is True
    assert report["target_check"]["tool_preamble_ms"]["met"] is None


def test_eye_phrases_are_resolved_and_audited_but_the_utterance_never_mutates(wired) -> None:
    """docs/M18_ACTION_CONTRACT.md §5.3: the tool call is the ONE mutation path. An
    utterance resolved to EYE_DISABLE is audited with its class and capability, and
    the eye is exactly as it was - no durable row, no UI-state event, no bookkeeping.
    Until 2026-09-06 this path also wrote the disable itself; in owner session
    3eb6fee7 that closed the camera 7 ms after the tool call with no receipt.
    """
    from app.ledger import service as ledger_service
    from app.ledger.vocabulary import EVENT_TYPE_EYE_DISABLED, SUBSYSTEM_PRESENCE
    from app.presence.eye import is_eye_enabled
    from app.uistate.publisher import UiStatePublisher, get_publisher, set_publisher

    client, _, runtime, _, _, _ = wired
    sid = _create(client)["session_id"]
    with runtime.session() as db:
        assert is_eye_enabled(db) is True  # default: on until told otherwise

    previous = get_publisher()
    bus = UiStatePublisher()
    set_publisher(bus)
    try:
        for turn, phrase in enumerate(("gözünü kapat", "kamerayı kapat", "beni izleme"), 1):
            response = client.post(
                f"/v1/voice/realtime/sessions/{sid}/events",
                json={
                    "events": [
                        {"kind": "utterance", "t_ms": 100 * turn, "turn": turn, "text": phrase},
                    ]
                },
            )
            assert response.status_code == 200, response.text
            resolved = response.json()["resolved_intents"][0]
            assert resolved["intent"] == "eye_disable"
            assert resolved["klass"] == "action" and resolved["capability"] == "eye.disable"
    finally:
        set_publisher(previous)

    with runtime.session() as db:
        assert is_eye_enabled(db) is True
        rows = ledger_service.query(
            db, subsystems=[SUBSYSTEM_PRESENCE], event_types=[EVENT_TYPE_EYE_DISABLED]
        )
        assert rows == []
        assert "eye_safety" not in (db.get(RealtimeSessionRow, uuid.UUID(sid)).context_json)
    assert [e for e in bus.tail() if e.subsystem == "presence"] == []

    audits = _audit_rows(runtime, sid, service.ACTION_INTENT_RESOLVED)
    assert [a.metadata_json["capability"] for a in audits] == ["eye.disable"] * 3
    assert all("eye_disable" not in a.metadata_json for a in audits)


def test_events_reject_audio_unknown_kinds_and_oversize(wired) -> None:
    client, *_ = wired
    sid = _create(client)["session_id"]
    url = f"/v1/voice/realtime/sessions/{sid}/events"
    assert client.post(url, json={"events": []}).status_code == 422
    assert client.post(url, json={"events": [{"kind": "telemetry", "t_ms": 1}]}).status_code == 422
    assert (
        client.post(
            url, json={"events": [{"kind": "audio_frame", "t_ms": 1, "payload": {"audio": "AAAA"}}]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            url, json={"events": [{"kind": "audio_frame", "t_ms": 1, "payload": {"x": "y" * 5000}}]}
        ).status_code
        == 422
    )
    negative = client.post(url, json={"events": [{"kind": "audio_frame", "t_ms": -1}]})
    assert negative.status_code == 422


# --------------------------------------------------------------- continuity


def test_attach_moves_the_leg_replays_sideband_and_closes_the_old_leg(wired) -> None:
    client, identity, runtime, sideband, issued, _ = wired
    _enroll_online_device(runtime.broker)
    sideband.deliver = False  # desktop is offline: pushes queue up
    created = _create(client)
    sid = created["session_id"]
    with _patched_temporal_client():
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "r", "name": "research.start", "arguments": {"topic": "x"}},
        )
    client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "summary", "t_ms": 1, "text": "Araştırma başladı."}]},
    )
    client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls/r/complete", json={"result": {"ok": True}}
    )

    # the phone attaches with its own owner session
    phone = identity.service.issue_session(client_kind="mobile", label="phone")
    sideband.deliver = True
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/attach",
        json={"client_kind": "mobile"},
        headers=bearer(phone.token),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["credential"]["secret"] != created["credential"]["secret"]
    assert data["state"]["client_kind"] == "mobile" and data["state"]["legs"] == 2
    assert data["state"]["plan"]["status"] == "completed"
    assert data["state"]["transcript_summary"] == "Araştırma başladı."
    assert [f["event"] for f in data["pending_sideband"]] == ["tool_completed"]
    assert data["previous_leg"]["client_kind"] == "desktop"
    assert "Önceki konuşmanın özeti" in data["instructions"]
    assert "Açık plan" in data["instructions"]
    # the previous leg was told (best effort) and the audit says so
    assert sideband.events()[-1] == "leg_closed"
    assert len(_audit_rows(runtime, sid, service.ACTION_LEG_CLOSED)) == 1
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        assert row.owner_session_id == phone.context.session_id
        assert row.device_id is None  # the phone session is not device-bound

    # the desktop's owner session no longer holds the leg
    stale = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls", json={"call_id": "z", "name": "clock.now"}
    )
    assert stale.status_code == 409
    stale_events = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "end_of_turn", "t_ms": 5}]},
    )
    assert stale_events.status_code == 409
    # ...while the phone does
    ok = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": "z", "name": "clock.now"},
        headers=bearer(phone.token),
    )
    assert ok.status_code == 200
    # re-attaching from the same leg is a no-op leg-wise (reconnect after network loss)
    again = client.post(
        f"/v1/voice/realtime/sessions/{sid}/attach", json={}, headers=bearer(phone.token)
    ).json()
    assert again["state"]["legs"] == 2 and again["previous_leg"] is None


def test_close_and_expiry_refuse_further_work(wired) -> None:
    client, _, runtime, _, _, _ = wired
    sid = _create(client)["session_id"]
    closed = client.post(
        f"/v1/voice/realtime/sessions/{sid}/close", json={"reason": "owner_hung_up"}
    ).json()
    assert closed["state"] == "closed" and closed["closed_at"]
    assert (
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "c", "name": "clock.now"},
        ).status_code
        == 410
    )
    assert client.post(f"/v1/voice/realtime/sessions/{sid}/attach", json={}).status_code == 410
    assert client.post(f"/v1/voice/realtime/sessions/{sid}/close").status_code == 200  # idempotent
    audit = _audit_rows(runtime, sid, service.ACTION_SESSION_CLOSED)
    assert len(audit) == 1 and audit[0].metadata_json["reason"] == "owner_hung_up"

    expired = _create(client, session_ttl_s=60)["session_id"]
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(expired))
        row.expires_at = service.utcnow() - service.timedelta(seconds=1)
        db.commit()
    assert (
        client.post(
            f"/v1/voice/realtime/sessions/{expired}/events",
            json={"events": [{"kind": "end_of_turn", "t_ms": 1}]},
        ).status_code
        == 410
    )
    assert client.get(f"/v1/voice/realtime/sessions/{expired}").json()["state"] == "expired"
    assert len(_audit_rows(runtime, expired, service.ACTION_SESSION_EXPIRED)) == 1


def test_a_session_that_is_over_says_so_on_the_bus(wired) -> None:
    """Owner run 2026-09-06 (session a71096ca): the last ``agent.listening`` of a
    CLOSED session stayed the bus's current event, so the Core showed "listening"
    with no live session and the eye qualification read that at startup. Close
    and expiry now publish ``agent.idle`` carrying the session id."""
    from app.uistate.publisher import UiStatePublisher, get_publisher, set_publisher

    client, _, runtime, _, _, _ = wired
    sid = _create(client)["session_id"]
    expired = _create(client, session_ttl_s=60)["session_id"]
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(expired))
        row.expires_at = service.utcnow() - service.timedelta(seconds=1)
        db.commit()

    previous = get_publisher()
    bus = UiStatePublisher()
    set_publisher(bus)
    try:
        # A listening event from the live session, then the owner hangs up.
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/events",
            json={"events": [{"kind": "mic_speech_start", "t_ms": 10, "turn": 1}]},
        )
        assert bus.current().state.value == "agent.listening"
        client.post(f"/v1/voice/realtime/sessions/{sid}/close", json={"reason": "owner_hung_up"})
        current = bus.current()
        assert current.state.value == "agent.idle"
        assert current.subsystem == "voice"
        assert current.session_id == sid
        assert current.metadata == {"session_over": True}
        # Closing again is idempotent on the bus too: no second "over".
        client.post(f"/v1/voice/realtime/sessions/{sid}/close")
        assert len([e for e in bus.tail() if e.state.value == "agent.idle"]) == 1
        # Expiry, discovered lazily on the next request, says so as well.
        client.post(
            f"/v1/voice/realtime/sessions/{expired}/events",
            json={"events": [{"kind": "end_of_turn", "t_ms": 1}]},
        )
        overs = [e for e in bus.tail() if e.state.value == "agent.idle"]
        assert [e.session_id for e in overs] == [sid, expired]
        assert overs[-1].status == "expired"
    finally:
        set_publisher(previous)


# --------------------------------------------------------------- narration


def _seed_narration(engine) -> uuid.UUID:
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        artifact = Artifact(title="Rapor", current_version=1)
        db.add(artifact)
        db.flush()
        db.add(
            ArtifactVersion(
                artifact_id=artifact.id,
                version=1,
                canonical_body=DOC,
                content_hash=hashlib.sha256(DOC.encode()).hexdigest(),
            )
        )
        narration = NarrationSession(artifact_id=artifact.id, artifact_version=1)
        db.add(narration)
        db.commit()
        return narration.id


def test_narration_control_moves_the_durable_cursor_and_pushes_it(wired) -> None:
    client, _, runtime, sideband, _, engine = wired
    narration_id = _seed_narration(engine)
    created = _create(client, narration_session_id=str(narration_id))
    assert "belge anlatımı bağlı" in created["instructions"]
    sid = created["session_id"]
    url = f"/v1/voice/realtime/sessions/{sid}/tool-calls"
    r = client.post(
        url,
        json={
            "call_id": "n1",
            "name": "narration.control",
            "arguments": {"utterance": "ikinci maddeyi tekrar oku"},
        },
    ).json()
    assert r["status"] == "succeeded", r
    assert r["result"]["intent"]["intent"] == "repeat_item"
    assert r["result"]["narration"]["action"] == "jump_item"
    assert r["result"]["narration"]["current_chunk"]["text"].startswith("İkinci madde")
    assert sideband.events()[-1] == "narration_cursor"
    cursor_push = sideband.frames[-1][1]["payload"]
    assert cursor_push["state"] == "READING" and cursor_push["cursor"]["paragraph_id"]

    r = client.post(
        url, json={"call_id": "n2", "name": "narration.control", "arguments": {"utterance": "dur"}}
    ).json()
    assert r["result"]["narration"]["action"] == "paused"
    r = client.post(
        url,
        json={
            "call_id": "n3",
            "name": "narration.control",
            "arguments": {"utterance": "şey, biraz daha yavaş"},
        },
    ).json()
    assert r["result"]["narration"]["speed"] == pytest.approx(0.75)
    r = client.post(
        url,
        json={"call_id": "n4", "name": "narration.control", "arguments": {"utterance": "özet geç"}},
    ).json()
    assert r["result"]["narration"]["presentation"] == "summary"

    # the durable narration row is what another device would read back
    with runtime.session() as db:
        row = db.get(NarrationSession, narration_id)
        assert row.state == "PAUSED" and row.speed == pytest.approx(0.75)
        assert row.semantic_cursor_json["paragraph_id"] == cursor_push["cursor"]["paragraph_id"]
    state = client.get(f"/v1/voice/realtime/sessions/{sid}").json()
    assert state["narration"]["state"] == "PAUSED" and state["presentation"] == "summary"
    assert state["narration"]["cursor"]["paragraph_id"] == cursor_push["cursor"]["paragraph_id"]


def test_narration_control_without_narration_only_resolves(wired) -> None:
    client, *_ = wired
    sid = _create(client)["session_id"]
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": "n", "name": "narration.control", "arguments": {"utterance": "devam"}},
    ).json()
    assert r["result"] == {"intent": r["result"]["intent"], "narration": None}
    assert r["result"]["intent"]["intent"] == "resume"


# ------------------------------------------------------------------- audit


def test_every_step_is_audited_with_ids_and_timings_only(wired) -> None:
    client, _, runtime, _, _, _ = wired
    sid = _create(client)["session_id"]
    client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls", json={"call_id": "c", "name": "clock.now"}
    )
    client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={
            "events": [
                {"kind": "utterance", "t_ms": 1, "text": "dur"},
                {"kind": "audio_frame", "t_ms": 2, "payload": {"bytes": 640}},
            ]
        },
    )
    client.post(f"/v1/voice/realtime/sessions/{sid}/close")
    actions = [r.action for r in _audit_rows(runtime, sid)]
    assert actions == [
        service.ACTION_SESSION_CREATED,
        service.ACTION_CREDENTIAL_MINTED,
        service.ACTION_TOOL_CALL,
        service.ACTION_INTENT_RESOLVED,
        service.ACTION_CLIENT_EVENT,
        service.ACTION_SESSION_CLOSED,
    ]
    for row in _audit_rows(runtime, sid):
        assert row.action.startswith("voice_")
        flat = repr(row.metadata_json).lower()
        for banned in ("secret", "credential", "audio_pcm", "sim_"):
            assert banned not in flat, (row.action, row.metadata_json)


def test_scrubber_drops_audio_and_credential_shaped_keys() -> None:
    scrubbed = service.scrub_metadata(
        {
            "call_id": "c",
            "duration_ms": 12,
            "audio": b"\x00",
            "secret": "x",
            "api_key": "k",
            "nested": {"token": "t", "turn": 1},
            "frames": [b"\x00", 1],
            "long": "y" * 1000,
        }
    )
    assert scrubbed == {
        "call_id": "c",
        "duration_ms": 12,
        "nested": {"turn": 1},
        "frames": [1],
        "long": "y" * 256,
    }


def test_complete_is_refused_from_a_superseded_leg_and_from_a_dead_session(wired) -> None:
    # Security review finding (M12 A+E): /complete skipped require_leg/require_live while
    # its siblings enforced them, so a desktop whose leg the phone had taken over (its
    # owner bearer still valid) could inject a tool result into the live conversation,
    # and a closed session could still be written to. Gated like /tool-calls and /events.
    client, identity, runtime, sideband, _, _ = wired
    _enroll_online_device(runtime.broker)
    sid = _create(client)["session_id"]
    with _patched_temporal_client():
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": "r", "name": "research.start", "arguments": {"topic": "x"}},
        )
    phone = identity.service.issue_session(client_kind="mobile", label="phone")
    assert (
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/attach",
            json={"client_kind": "mobile"},
            headers=bearer(phone.token),
        ).status_code
        == 200
    )

    injected = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls/r/complete",
        json={"result": {"summary": "sahte sonuç"}},
    )
    assert injected.status_code == 409, injected.text
    assert "tool_completed" not in sideband.events()
    with runtime.session() as db:
        call = service.get_tool_call(db, uuid.UUID(sid), "r")
        # still running, and the injected payload never reached the record
        assert call.status == service.TOOL_STATUS_RUNNING
        assert "summary" not in (call.result_json or {})
    # ...while the leg holder completes it normally
    ok = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls/r/complete",
        json={"result": {"summary": "3 kaynak"}},
        headers=bearer(phone.token),
    )
    assert ok.status_code == 200, ok.text
    assert sideband.events()[-1] == "tool_completed"

    dead = _create(client)["session_id"]
    with _patched_temporal_client():
        client.post(
            f"/v1/voice/realtime/sessions/{dead}/tool-calls",
            json={"call_id": "r", "name": "research.start", "arguments": {"topic": "x"}},
        )
    assert client.post(f"/v1/voice/realtime/sessions/{dead}/close").status_code == 200
    assert (
        client.post(
            f"/v1/voice/realtime/sessions/{dead}/tool-calls/r/complete", json={"result": {}}
        ).status_code
        == 410
    )


@pytest.mark.parametrize(
    "spelling", ["apiKey", "api-key", "API_KEY", "Api Key", "x-api-key", "accessToken", "audioPcm"]
)
def test_forbidden_keys_are_caught_under_any_spelling_at_the_route_and_in_the_scrubber(
    wired, spelling: str
) -> None:
    # Verification finding: both layers matched the literal substring "api_key", so the
    # camelCase and hyphenated spellings a JS client naturally uses landed verbatim in an
    # audit row. One normalized blocklist now serves the validator and the scrubber.
    assert service.is_forbidden_key(spelling)
    for benign in ("call_id", "duration_ms", "turn", "t_ms", "error_class"):
        assert not service.is_forbidden_key(benign)
    assert service.scrub_metadata({spelling: "v", "turn": 1, "nested": {spelling: "v"}}) == {
        "turn": 1,
        "nested": {},
    }
    client, *_ = wired
    sid = _create(client)["session_id"]
    assert (
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/events",
            json={"events": [{"kind": "error", "t_ms": 1, "payload": {spelling: "v"}}]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={
                "call_id": "c",
                "name": "clock.now",
                "arguments": {"nested": {spelling: "v"}},
            },
        ).status_code
        == 422
    )


def test_tool_call_relayed_under_the_vendor_spelling_runs_the_cloud_core_tool(wired) -> None:
    # A real provider only ever sees research__start (OpenAI refuses dots in function
    # names); the client relays that spelling verbatim. The registry resolves it and the
    # record keeps the canonical name, so audit/idempotency never see two names.
    client, *_ = wired
    sid = _create(client)["session_id"]
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": "v1", "name": "clock__now", "arguments": {}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded" and r.json()["name"] == "clock.now"
    replay = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": "v1", "name": "clock.now", "arguments": {}},
    ).json()
    assert replay["replayed"] is True and replay["name"] == "clock.now"


def test_create_accepts_a_supported_voice_records_it_and_applies_the_owner_profile(wired) -> None:
    # ADR-0043: "arbor" is the owner's perceptual profile (not a wire voice); a session
    # may request only a voice the provider offers; both are recorded for the benchmark.
    client, _, runtime, _, _, _ = wired
    from app.voice.realtime_sessions.persona import VOICE_STYLE_ARBOR_TR

    created = _create(client)
    assert created["voice"] is None
    assert created["voice_profile"] == "arbor"
    assert VOICE_STYLE_ARBOR_TR in created["instructions"]
    state = client.get(f"/v1/voice/realtime/sessions/{created['session_id']}").json()
    assert state["voice_profile"] == "arbor" and state["voice"] is None

    # the simulator declares no supported-voice list -> a client-chosen voice is REFUSED
    # (fail closed; security review 2026-09-02), never forwarded by omission
    unlisted = client.post("/v1/voice/realtime/sessions", json={"voice": "cedar"})
    assert unlisted.status_code == 422, unlisted.text
    assert "declares no supported voices" in unlisted.json()["detail"]["message"]

    # malformed ids never reach the provider
    bad = client.post("/v1/voice/realtime/sessions", json={"voice": "Arbor!"})
    assert bad.status_code == 422


def test_create_refuses_a_voice_the_provider_does_not_offer(wired) -> None:
    client, _, runtime, _, _, _ = wired
    from app.voice.providers_openai_realtime import OpenAIRealtimeProvider

    sim = runtime.providers[next(iter(runtime.providers))]
    sim.require_supported_voice = OpenAIRealtimeProvider("k").require_supported_voice  # type: ignore[attr-defined]
    try:
        refused = client.post("/v1/voice/realtime/sessions", json={"voice": "arbor"})
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"]["details"]["supported_voices"][0] == "alloy"
        ok = client.post("/v1/voice/realtime/sessions", json={"voice": "marin"})
        assert ok.status_code == 201, ok.text
    finally:
        del sim.require_supported_voice


def test_benchmark_carries_the_noise_counters_and_the_calibration_in_force(wired) -> None:
    # ADR-0044: the client reports cumulative mic counters and calibrations as numbers in
    # "state" events; the benchmark the owner fetches must carry them (rows 6.16-6.21).
    client, *_ = wired
    sid = _create(client)["session_id"]
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={
            "events": [
                {
                    "kind": "state",
                    "t_ms": 10,
                    "payload": {
                        "mic_calibration": 1,
                        "noise_floor_db": -58.5,
                        "env": 1,
                        "clip_risk": 0,
                    },
                },
                {
                    "kind": "state",
                    "t_ms": 500,
                    "payload": {
                        "mic_metrics": 1,
                        "false_starts": 1,
                        "false_barge_ins": 0,
                        "false_turns": 0,
                        "gate_opens": 3,
                    },
                },
                {
                    "kind": "state",
                    "t_ms": 900,
                    "payload": {
                        "mic_calibration": 1,
                        "noise_floor_db": -49.0,
                        "env": 2,
                        "clip_risk": 0,
                    },
                },
                {
                    "kind": "state",
                    "t_ms": 2000,
                    "payload": {
                        "mic_metrics": 1,
                        "false_starts": 2,
                        "false_barge_ins": 1,
                        "false_turns": 1,
                        "gate_opens": 7,
                        "session_end": 1,
                    },
                },
                {"kind": "state", "t_ms": 2001, "payload": {"state": "IDLE"}},
            ]
        },
    )
    assert r.status_code == 200, r.text
    bench = client.get(f"/v1/voice/realtime/sessions/{sid}/benchmark").json()
    noise = bench["context"]["noise"]
    assert noise["reported"] is True
    assert (
        noise["false_starts"],
        noise["false_barge_ins"],
        noise["false_turns"],
        noise["gate_opens"],
    ) == (2, 1, 1, 7)
    assert noise["calibrations"] == 2 and noise["calibration"]["noise_floor_db"] == -49.0
    assert noise["metrics"]["session_end"] == 1 and "mic_metrics" not in noise["metrics"]
    assert bench["context"]["voice_profile"] == "arbor"
    # a session without any mic report says so instead of pretending zeros are evidence
    empty = _create(client)["session_id"]
    empty_bench = client.get(f"/v1/voice/realtime/sessions/{empty}/benchmark").json()
    assert empty_bench["context"]["noise"]["reported"] is False


def test_lifecycle_benchmark_fetchable_after_disconnect_and_secret_revocation(wired) -> None:
    # The owner's qualification: create -> WebRTC events + mic metrics -> disconnect ->
    # the provider's ephemeral secret is gone -> a later benchmark fetch must SUCCEED
    # from the durable record, never from browser memory. (The real incident was a
    # mistyped id; this pins that a correct id always works after close.)
    client, _, runtime, _, _, _ = wired
    from app.voice.providers_openai_realtime import OpenAIRealtimeProvider

    sim = runtime.providers[next(iter(runtime.providers))]
    sim.require_supported_voice = OpenAIRealtimeProvider("k").require_supported_voice  # type: ignore[attr-defined]
    created = _create(client, voice="cedar")
    sid = created["session_id"]
    assert (
        client.post(
            f"/v1/voice/realtime/sessions/{sid}/events",
            json={
                "events": [
                    {"kind": "mic_speech_start", "t_ms": 100, "turn": 0},
                    {"kind": "end_of_turn", "t_ms": 900, "turn": 0},
                    {"kind": "first_audio", "t_ms": 1400, "turn": 0},
                    {"kind": "barge_in_start", "t_ms": 3000, "turn": 1},
                    {"kind": "playback_stopped", "t_ms": 3090, "turn": 1},
                    {
                        "kind": "state",
                        "t_ms": 3100,
                        "payload": {"mic_calibration": 1, "noise_floor_db": -52.0, "env": 1},
                    },
                    {
                        "kind": "state",
                        "t_ms": 5000,
                        "payload": {
                            "mic_metrics": 1,
                            "false_starts": 1,
                            "false_barge_ins": 0,
                            "false_turns": 0,
                            "gate_opens": 4,
                            "session_end": 1,
                        },
                    },
                ]
            },
        ).status_code
        == 200
    )
    # disconnect: the client closes; the provider's ephemeral credential is revoked/expired
    # on the provider side and is not part of any record here
    closed = client.post(
        f"/v1/voice/realtime/sessions/{sid}/close", json={"reason": "provider_secret_revoked"}
    ).json()
    assert closed["state"] == "closed"
    # "later": a fresh request, nothing cached
    bench = client.get(f"/v1/voice/realtime/sessions/{sid}/benchmark")
    assert bench.status_code == 200, bench.text
    doc = bench.json()
    ctx = doc["context"]
    assert ctx["session_id"] == sid and ctx["state"] == "closed"
    assert ctx["provider"] == "simulator" and ctx["voice"] == "cedar"
    assert ctx["voice_profile"] == "arbor"
    assert ctx["started_at"] and ctx["ended_at"] and ctx["benchmark_snapshot_at_close"] is True
    assert ctx["noise"]["false_starts"] == 1 and ctx["noise"]["calibrations"] == 1
    assert doc["metrics"]  # latency metrics computed from the durable client timestamps
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        snap = row.context_json["benchmark_at_close"]
        assert snap["context"]["voice"] == "cedar" and "events" not in snap
        assert "secret" not in repr(snap) and "credential" not in repr(snap).lower()
    # the row is listed, newest first, with everything the owner needs to pick it
    listing = client.get("/v1/voice/realtime/sessions?limit=5").json()["sessions"]
    assert listing[0]["session_id"] == sid and listing[0]["state"] == "closed"
    assert listing[0]["voice"] == "cedar" and listing[0]["benchmark_snapshot_at_close"] is True
    # a mistyped id is a 404, not an evidence loss
    wrong = sid[:-4] + ("0000" if not sid.endswith("0000") else "1111")
    assert client.get(f"/v1/voice/realtime/sessions/{wrong}/benchmark").status_code == 404


def test_turkish_transcript_summary_round_trips_as_utf8_bytes(wired) -> None:
    # The owner's qualification record showed "Ã"/"Å": the database held correct UTF-8
    # (proven on the host) and the JSON response carried no charset, so a PowerShell 5.1
    # client decoded Latin-1. Responses now declare charset=utf-8, and the bytes are UTF-8.
    client, *_ = wired
    sid = _create(client)["session_id"]
    text = "Şey... yani İstanbul'da ığüşöç harfleri: Işık ve Görüş"
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "summary", "t_ms": 1, "text": text}]},
    )
    assert r.status_code == 200, r.text
    state = client.get(f"/v1/voice/realtime/sessions/{sid}")
    assert state.headers["content-type"].lower().startswith("application/json; charset=utf-8")
    assert state.json()["transcript_summary"] == text
    assert text.encode("utf-8") in state.content  # the raw bytes are UTF-8, never double-encoded
    assert "Ã" not in state.content.decode("utf-8")


def test_benchmark_breakdown_aggregates_client_sub_phases_and_flags(wired) -> None:
    # ADR-0047: sub-phase numbers ride EXISTING timing kinds as payload; the report
    # decomposes the headline metrics and counts fallbacks/anomalies, numbers only.
    client, *_ = wired
    sid = _create(client)["session_id"]
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={
            "events": [
                {
                    "kind": "mic_speech_start",
                    "t_ms": 100,
                    "turn": 0,
                    "payload": {"source": 1, "gate_ms": 72, "capture_lag_ms": 21},
                },
                {
                    "kind": "uplink_first_packet",
                    "t_ms": 160,
                    "turn": 0,
                    "payload": {"basis": 1, "rtp_ms": 38, "provider_ms": 260},
                },
                {"kind": "mic_speech_start", "t_ms": 5000, "turn": 1, "payload": {"gate_ms": 90}},
                {
                    "kind": "uplink_first_packet",
                    "t_ms": 5400,
                    "turn": 1,
                    "payload": {"basis": 0, "provider_ms": 391},
                },
                {
                    "kind": "barge_in_start",
                    "t_ms": 8000,
                    "turn": 2,
                    "payload": {
                        "playback_stopped_ms": 210,
                        "detect_ms": 90,
                        "stop_command_ms": 3,
                        "gain_zero_ms": 117,
                    },
                },
                {"kind": "playback_stopped", "t_ms": 8210, "turn": 2},
                {
                    "kind": "barge_in_start",
                    "t_ms": 9000,
                    "turn": 3,
                    "payload": {"playback_stopped_ms": 0, "anomaly": 1},
                },
                {"kind": "playback_stopped", "t_ms": 9000, "turn": 3},
                {
                    "kind": "first_audio",
                    "t_ms": 12000,
                    "turn": 4,
                    "payload": {
                        "response_created_ms": 310,
                        "first_delta_ms": 290,
                        "playback_ms": 74,
                    },
                },
                {"kind": "first_audio", "t_ms": 15000, "turn": 5},  # legacy client: no breakdown
                {
                    "kind": "state",
                    "t_ms": 15001,
                    "payload": {
                        "mic_calibration": 1,
                        "measured": 1,
                        "samples": 84,
                        "noise_floor_db": -51.5,
                    },
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    ctx = client.get(f"/v1/voice/realtime/sessions/{sid}/benchmark").json()["context"]
    bd = ctx["breakdown"]
    assert bd["mic_speech_start"]["events"] == 2
    # same nearest-index percentile as realtime_bench: two samples -> p50 is the lower one
    assert bd["mic_speech_start"]["gate_ms"] == {"n": 2, "p50_ms": 72.0, "p95_ms": 90.0}
    assert bd["mic_speech_start"]["capture_lag_ms"]["n"] == 1
    assert bd["uplink_first_packet"]["basis_count"] == 1  # one RTP-measured, one fallback
    assert bd["uplink_first_packet"]["provider_ms"]["n"] == 2
    assert bd["barge_in_start"]["anomaly_count"] == 1
    assert bd["barge_in_start"]["detect_ms"] == {"n": 1, "p50_ms": 90.0, "p95_ms": 90.0}
    assert bd["first_audio"]["events"] == 2 and bd["first_audio"]["without_breakdown"] == 1
    assert bd["first_audio"]["response_created_ms"]["p50_ms"] == 310.0
    assert ctx["noise"]["calibration_measured"] is True
    # and a calibration without the measured flag is reported as NOT measured
    other = _create(client)["session_id"]
    client.post(
        f"/v1/voice/realtime/sessions/{other}/events",
        json={
            "events": [
                {
                    "kind": "state",
                    "t_ms": 1,
                    "payload": {
                        "mic_calibration": 1,
                        "noise_floor_db": -60.0,
                        "env": 0,
                        "peak_db": -100,
                    },
                }
            ]
        },
    )
    ctx2 = client.get(f"/v1/voice/realtime/sessions/{other}/benchmark").json()["context"]
    assert ctx2["noise"]["calibrations"] == 1 and ctx2["noise"]["calibration_measured"] is False


def test_listing_is_newest_first_even_within_the_same_second(wired) -> None:
    # -Latest trusts sessions[0]. The server default for created_at renders at one-second
    # resolution on SQLite, so three sessions created in a burst tied and came back
    # oldest-first (verification probe, 2026-09-03). created_at is now set explicitly
    # with microseconds and the listing has a secondary key.
    client, _, runtime, _, _, _ = wired
    ids = [_create(client)["session_id"] for _ in range(3)]
    listing = client.get("/v1/voice/realtime/sessions?limit=10").json()["sessions"]
    assert [s["session_id"] for s in listing[:3]] == list(reversed(ids))
    with runtime.session() as db:
        rows = [db.get(RealtimeSessionRow, uuid.UUID(i)) for i in ids]
        stamps = [r.created_at for r in rows]
        assert stamps == sorted(stamps) and len(set(stamps)) == 3, "created_at must be distinct"
        # the property that failed: ascending order is NOT newest-first
        ascending = [str(r.id) for r in sorted(rows, key=lambda r: r.created_at)]
        assert ascending == ids and ascending != [s["session_id"] for s in listing[:3]]
