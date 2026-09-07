"""The corpus harness: one owner utterance through the real canonical path.

    utterance -> POST /events (the boundary right after transcription)
              -> the ONE router (resolved intent, class, reference, policy changes)
              -> the contract-expected tool through POST /tool-calls, with the arguments
                 the persona instructs the model to pass (derived from the contract here,
                 never from a second parser)
              -> the forbidden tools through the SAME relay, where the product's guard is
                 the thing under test (a research.start on a follow-up turn is refused)
              -> the fake device's calls checked against the case's side-effect policy
              -> the client's speech lifecycle events for the turn, then the session record

No test here calls a handler directly and none knows a phrase table of its own.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.receipt import contains_fake_completion
from app.alarms import service as alarms_service
from app.alarms.models import AmbientPolicyRow, WakeAlarm
from app.alarms.sequence import WakeSequence
from app.alarms.tr_time import parse_when_struct
from app.ambient import service as ambient_service
from app.ambient.holdoff import HoldoffRegistry, set_holdoffs
from app.artifacts import service as artifact_service
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
from app.devices.status import DeviceStatusRegistry
from app.documents.index import DocumentIndex
from app.documents.models import DocumentIndexRow
from app.documents.service import DocumentService
from app.evolution.models import Capability, CapabilityGap, EvolutionOpportunity, SkillVersion
from app.evolution.runtime import EvolutionRuntime
from app.evolution.supervisor import is_paused
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.main import create_app
from app.narration.models import NarrationSession, PronunciationEntry
from app.operator import focus as operator_focus
from app.operator.models import (
    FOCUS_KIND_DOCUMENT,
    FOCUS_KIND_FILE,
    FOCUS_KIND_WINDOW,
    ObjectFocusRow,
)
from app.operator.service import OperatorService, register_operator_service
from app.operator.task import STATUS_RUNNING, OperatorTask
from app.presence.engine import PresenceFusionEngine, set_engine
from app.presence.eye import disable_eye, is_eye_enabled
from app.presence.service import reset_heartbeat
from app.research import runs_service
from app.research.models import (
    STAGE_READY,
    ResearchCandidateRow,
    ResearchEvidenceRow,
    ResearchFocusRow,
    ResearchOwnerStateRow,
    ResearchReportRow,
    ResearchRunRow,
)
from app.routines.models import Routine, RoutineFiring
from app.uistate.publisher import UiStatePublisher, set_publisher
from app.voice.models import VoiceProfile
from app.voice.providers import FakeTTSProvider
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from app.voice.realtime_sessions.research_announcer import ResearchToolCallAnnouncer
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.simulator import SimulatedRealtimeProvider
from tests.alarms_support import FakeDeviceAction, happy_device_results
from tests.documents_support import document_capability_results, extract_result
from tests.identity_support import IDENTITY_TABLES
from tests.voice_corpus.corpus import (
    CTX_ALARM_RINGING,
    CTX_ALARM_SCHEDULED,
    CTX_COMMON_POINTS_FOCUSED,
    CTX_DOCUMENT_FOCUSED,
    CTX_DOCX_FOCUSED,
    CTX_EYE_DISABLED,
    CTX_FILE_FOCUSED,
    CTX_OPERATOR_RUNNING,
    CTX_PPTX_FOCUSED,
    CTX_RESEARCH_FOCUS_B,
    CTX_SECRET_FILE_FOCUSED,
    CTX_WINDOW_FOCUSED,
    CTX_XLSX_FOCUSED,
    RESPONSE_CLARIFY,
    RESPONSE_CONTROL,
    RESPONSE_NONE,
    RESPONSE_REFUSED,
    RESPONSE_RUNNING,
    UtteranceCase,
)

VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"
SHARED_TOPIC = "OpenAI son gelişmeler"

TABLES = (
    RealtimeSessionRow.__table__,
    RealtimeToolCall.__table__,
    AuditEvent.__table__,
    VoiceProfile.__table__,
    NarrationSession.__table__,
    PronunciationEntry.__table__,
    Artifact.__table__,
    ArtifactVersion.__table__,
    ArtifactRender.__table__,
    Task.__table__,
    TaskRun.__table__,
    ActivityEventRow.__table__,
    PendingBriefingRow.__table__,
    Device.__table__,
    DeviceSession.__table__,
    DeviceCommand.__table__,
    EnrollmentToken.__table__,
    ResearchSource.__table__,
    ResearchRunRow.__table__,
    ResearchCandidateRow.__table__,
    ResearchEvidenceRow.__table__,
    ResearchReportRow.__table__,
    ResearchFocusRow.__table__,
    ResearchOwnerStateRow.__table__,
    WakeAlarm.__table__,
    AmbientPolicyRow.__table__,
    Routine.__table__,
    RoutineFiring.__table__,
    ObjectFocusRow.__table__,
    DocumentIndexRow.__table__,
)

#: The tools the harness may dispatch as "forbidden" because the product refuses them at
#: the relay; every other forbidden tool is asserted at the router (never dispatched).
_REFUSAL_PROVEN_BY_DISPATCH = frozenset({"research.start"})


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


def _report(topic: str, *, marker: str) -> dict:
    return {
        "topic": topic,
        "executive_summary": f"{marker} özeti.",
        "findings": [
            {
                "id": "f1",
                "title": f"{marker} birinci bulgu",
                "summary": f"{marker} — bağlam penceresi büyüdü.",
                "why_it_matters": "Uzun belgeler tek seferde işlenebiliyor.",
                "importance": 5,
                "label": "source_fact",
                "evidence_ids": ["e1"],
            },
            {
                "id": "f2",
                "title": f"{marker} ikinci bulgu",
                "summary": f"{marker} — araç kullanımı yaygınlaştı.",
                "why_it_matters": "Ajan mimarileri sadeleşiyor.",
                "importance": 4,
                "label": "source_fact",
                "evidence_ids": ["e2"],
            },
        ],
        "sources": [
            {"id": "e1", "title": f"{marker} Yayın Bir", "publisher": f"{marker} Yayın Bir"},
            {"id": "e2", "title": f"{marker} Yayın İki", "publisher": f"{marker} Yayın İki"},
        ],
        "stats": {
            "discovered": 242 if marker == "A" else 111,
            "fetched": 41 if marker == "A" else 22,
            "rejected": 28 if marker == "A" else 9,
            "rejected_by_reason": {"interstitial": 11, "duplicate": 6},
            "synthesis_provider": "fake",
        },
    }


@dataclass
class Harness:
    client: TestClient
    runtime: RealtimeVoiceRuntime
    factory: Any
    device: FakeDeviceAction
    sequence: WakeSequence
    holdoffs: HoldoffRegistry
    operator: OperatorService
    documents: DocumentService
    ids: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------- relay

    def new_session(self) -> str:
        response = self.client.post("/v1/voice/realtime/sessions", json={})
        assert response.status_code == 201, response.text
        return response.json()["session_id"]

    def say(self, sid: str, text: str, *, turn: int = 1, t_ms: int | None = None) -> dict:
        response = self.client.post(
            f"/v1/voice/realtime/sessions/{sid}/events",
            json={
                "events": [
                    {
                        "kind": "utterance",
                        "t_ms": t_ms if t_ms is not None else 1000 * turn,
                        "turn": turn,
                        "text": text,
                    }
                ]
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    def tool(self, sid: str, call_id: str, name: str, arguments: dict) -> dict:
        response = self.client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": call_id, "name": name, "arguments": arguments},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def speak(self, sid: str, *, turn: int, chars: int) -> None:
        """The client's structural speech lifecycle for the turn (ADR-0066): what the web
        controller reports once the provider's audio started and drained."""
        base = 1000 * turn
        response = self.client.post(
            f"/v1/voice/realtime/sessions/{sid}/events",
            json={
                "events": [
                    {"kind": "end_of_turn", "t_ms": base + 100, "turn": turn},
                    {
                        "kind": "spoken",
                        "t_ms": base + 500,
                        "turn": turn,
                        "payload": {"chars": chars},
                    },
                    {"kind": "first_audio", "t_ms": base + 600, "turn": turn},
                    {"kind": "response_done", "t_ms": base + 2000, "turn": turn},
                    {"kind": "audio_done", "t_ms": base + 2600, "turn": turn},
                ]
            },
        )
        assert response.status_code == 200, response.text

    def activity(self, sid: str) -> dict:
        return self.client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()

    # ---------------------------------------------------------- contexts

    def _complete_research(self, *, topic: str, marker: str, ready_at: datetime) -> tuple[str, str]:
        sid = self.new_session()
        fake = AsyncMock()
        fake.start_workflow = AsyncMock(return_value=None)
        with patch("app.research.service.Client.connect", AsyncMock(return_value=fake)):
            started = self.tool(sid, f"start-{marker}", "research.start", {"topic": topic})
        assert started["status"] == "running", started
        task_id = uuid.UUID(started["result"]["task_id"])
        report_json = _report(topic, marker=marker)
        with self.factory() as db:
            artifact = artifact_service.get_or_create_artifact_for_task(
                db, task_id=None, title=f"Araştırma raporu — {marker}", kind="research_report"
            )
            db.commit()
            artifact_id = artifact.id
            runs_service.upsert_report(
                db, task_id, report_json=report_json, synthesis_provider="fake"
            )
            runs_service.set_report_artifact(db, task_id, artifact_id)
            task = artifact_service.get_task(db, task_id)
            task.ready_at = ready_at
            db.commit()
            runs_service.update_run(db, task_id, stage=STAGE_READY, event={"stage": STAGE_READY})
            ledger_service.record(
                db,
                ledger_service.build_research_completed_event(
                    task_id=task_id,
                    occurred_at=ready_at,
                    report_json=report_json,
                    artifact_id=artifact_id,
                ),
            )
            db.commit()
        announcer = ResearchToolCallAnnouncer(self.runtime.session, RecordingSideband(deliver=True))
        assert announcer.sweep_once() == 1
        return str(task_id), str(artifact_id)

    def seed(self, context: str) -> None:
        if context == CTX_RESEARCH_FOCUS_B:
            base = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
            a_task, a_art = self._complete_research(topic=SHARED_TOPIC, marker="A", ready_at=base)
            b_task, b_art = self._complete_research(
                topic=SHARED_TOPIC, marker="B", ready_at=base + timedelta(minutes=25)
            )
            self.ids.update(
                {
                    "research:A": a_task,
                    "research:B": b_task,
                    "artifact:A": a_art,
                    "artifact:B": b_art,
                }
            )
        elif context in (CTX_ALARM_RINGING, CTX_ALARM_SCHEDULED):
            now = datetime.now(UTC)
            with self.factory() as db:
                alarm = alarms_service.create_alarm(
                    db, when=parse_when_struct({"relative_seconds": 30}, now=now)
                )
                alarm_id = alarm.id
                if context == CTX_ALARM_RINGING:
                    decision = alarms_service.fire_alarm(
                        db,
                        alarm_id,
                        sequence=self.sequence,
                        firing_id=uuid.uuid4(),
                        now=now + timedelta(seconds=35),
                    )
                    assert decision.fired, decision
            self.ids["alarm"] = str(alarm_id)
            self.device.reset()
        elif context == CTX_EYE_DISABLED:
            with self.factory() as db:
                disable_eye(db, reason="corpus_context", action_id="corpus-eye-off")
                db.commit()
        elif context == CTX_WINDOW_FOCUSED:
            # Two DISTINCT rows: "w-0" (an older window) then "w-1" (current), so both
            # focus.current() -> "w-1" and focus.previous() -> "w-0" resolve to something
            # real ("Önceki pencereye dön." needs a genuine previous window to activate).
            with self.factory() as db:
                base = datetime.now(UTC) - timedelta(seconds=5)
                operator_focus.set_focus(
                    db,
                    FOCUS_KIND_WINDOW,
                    "w-0",
                    label="Hesap Makinesi",
                    source="test_context",
                    now=base,
                )
                operator_focus.set_focus(
                    db,
                    FOCUS_KIND_WINDOW,
                    "w-1",
                    label="Adsız - Not Defteri",
                    source="test_context",
                    now=base + timedelta(seconds=1),
                )
        elif context == CTX_DOCUMENT_FOCUSED:
            # Exactly the pair the task brief names: rapor.pdf current, sunum-q3.pptx
            # the previous (a genuine distinct earlier row, the same two-timestamp
            # discipline CTX_WINDOW_FOCUSED already uses for "önceki pencereye dön").
            with self.factory() as db:
                base = datetime.now(UTC) - timedelta(seconds=5)
                previous_row = self._index_document(db, "sunum-q3.pptx", now=base)
                self._focus_document(db, previous_row, source="test_context", now=base)
                current_row = self._index_document(db, "rapor.pdf", now=base + timedelta(seconds=1))
                self._focus_document(
                    db, current_row, source="test_context", now=base + timedelta(seconds=1)
                )
        elif context == CTX_DOCX_FOCUSED:
            with self.factory() as db:
                now = datetime.now(UTC)
                row = self._index_document(db, "sozlesmeler/2026/sozlesme.docx", now=now)
                self._focus_document(db, row, source="test_context", now=now)
        elif context == CTX_XLSX_FOCUSED:
            with self.factory() as db:
                now = datetime.now(UTC)
                row = self._index_document(db, "butce-2026.xlsx", now=now)
                self._focus_document(db, row, source="test_context", now=now)
        elif context == CTX_PPTX_FOCUSED:
            with self.factory() as db:
                now = datetime.now(UTC)
                row = self._index_document(db, "sunum-q3.pptx", now=now)
                self._focus_document(db, row, source="test_context", now=now)
        elif context == CTX_FILE_FOCUSED:
            # A FILE is focused (as if a search just found it) but never extracted: the
            # spec §3 branch "current file not yet extracted -> extract it first".
            from tests.documents_support import file_id_for, file_record

            with self.factory() as db:
                record = file_record("rapor.pdf")
                operator_focus.set_focus(
                    db,
                    FOCUS_KIND_FILE,
                    file_id_for("rapor.pdf"),
                    label=record["name"],
                    source="test_context",
                )
        elif context == CTX_COMMON_POINTS_FOCUSED:
            # truth.json's own common_points fixture: butce-2026.xlsx, kod.py, notlar.md.
            with self.factory() as db:
                base = datetime.now(UTC) - timedelta(seconds=10)
                for n, path in enumerate(("butce-2026.xlsx", "kod.py", "notlar.md")):
                    self._index_document(db, path, now=base + timedelta(seconds=n))
        elif context == CTX_SECRET_FILE_FOCUSED:
            with self.factory() as db:
                operator_focus.set_focus(
                    db,
                    FOCUS_KIND_FILE,
                    "file:secret-sentinel",
                    label=".env",
                    source="test_context",
                )
        elif context == CTX_OPERATOR_RUNNING:
            with self.factory() as db:
                operator_focus.set_focus(
                    db,
                    FOCUS_KIND_WINDOW,
                    "w-1",
                    label="Adsız - Not Defteri",
                    source="test_context",
                    now=datetime.now(UTC),
                )
            # A task genuinely mid-flight, in the exact slot start_task fills - the seam
            # OperatorService.set_current_task documents, never a bypass of the loop.
            self.operator.set_current_task(
                OperatorTask(
                    id=uuid.uuid4(),
                    goal="open notepad",
                    steps=[],
                    plan_name="open_application",
                    status=STATUS_RUNNING,
                    current_step=0,
                    last_observed={"window": {"title": "Adsız - Not Defteri"}},
                )
            )

    # ------------------------------------------------------------- M20: documents

    def _index_document(self, db, path: str, *, now: datetime) -> DocumentIndexRow:
        """Pre-index a fixture straight from the oracle (tests/documents_support.py) —
        no device call, exactly as if the owner had already read it once."""
        return DocumentIndex().upsert(
            db, device_id="device:default", extract=extract_result(path), now=now
        )

    def _focus_document(self, db, row: DocumentIndexRow, *, source: str, now: datetime) -> None:
        operator_focus.set_focus(
            db, FOCUS_KIND_DOCUMENT, row.doc_id, label=row.title or row.name, source=source, now=now
        )
        operator_focus.set_focus(
            db, FOCUS_KIND_FILE, row.file_id, label=row.name, source=source, now=now
        )

    def research_task_ids(self) -> set[str]:
        with self.factory() as db:
            return {str(t) for t in db.execute(select(ResearchRunRow.task_id)).scalars().all()}

    def alarm_state(self, alarm_id: str) -> tuple[str, int, int]:
        with self.factory() as db:
            row = db.get(WakeAlarm, uuid.UUID(alarm_id))
            db.refresh(row)
            return row.state, int(row.snooze_minutes), int(row.snooze_count)

    def alarm_rows(self) -> int:
        with self.factory() as db:
            return len(db.execute(select(WakeAlarm.id)).scalars().all())

    def eye_enabled(self) -> bool:
        with self.factory() as db:
            return is_eye_enabled(db)

    def evolution_paused(self) -> bool:
        with self.factory() as db:
            return is_paused(db)


def build_harness() -> Harness:
    """A fresh application per case: real relay, real router, real services, fake device."""
    settings = Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in IDENTITY_TABLES:
        table.create(engine)
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    app = create_app(settings)
    identity = IdentityRuntime(settings, engine=engine, root=InMemoryCredentialRoot())
    identity.service.bootstrap()
    app.state.identity = identity
    broker = BrokerRuntime(settings)
    broker._engine = engine
    broker._session_factory = factory
    app.state.broker = broker
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = factory
    app.state.artifacts = artifacts
    sim = SimulatedRealtimeProvider()
    runtime = RealtimeVoiceRuntime(
        settings,
        engine=engine,
        providers={sim.name: sim},
        sideband=RecordingSideband(deliver=True),
        broker=broker,
        artifacts=artifacts,
    )
    app.state.voice_realtime = runtime

    device = FakeDeviceAction(results={**happy_device_results(), **document_capability_results()})
    sequence = WakeSequence(device_action=device, tts=FakeTTSProvider())
    statuses = DeviceStatusRegistry()
    # The evolution engine gets its OWN in-memory database: on a StaticPool SQLite engine
    # every session shares one connection, so the evolution service's session exit would
    # roll back the relay's uncommitted tool-call row mid-call (a harness artefact; Postgres
    # gives each session its own connection). Nothing in a corpus case needs the two to
    # share rows: the pause switch is on the ledger, which the tools read through ctx.db.
    evolution_engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        ActivityEventRow.__table__,
        Capability.__table__,
        SkillVersion.__table__,
        CapabilityGap.__table__,
        EvolutionOpportunity.__table__,
    ):
        table.create(evolution_engine)
    evolution = EvolutionRuntime(settings, engine=evolution_engine)
    # M19 (docs/M19_DIGITAL_OPERATOR_SPEC.md §4): the SAME fake device port every operator
    # tool reaches through ``ctx.live["device_action"]`` (production's is the SAME object
    # the wake sequence holds; here it is the same ``device`` fake every other family
    # already uses). ``register_operator_service`` mirrors ``create_app``'s module-wide
    # registration so the router's ringing-aware Cancel/Status pair can see it too.
    operator_service = OperatorService()
    register_operator_service(operator_service)
    # M20 (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3): the SAME fake device, through
    # the SAME live-source path - one document authority, never a second one.
    document_service = DocumentService()
    runtime.register_live(
        wake_sequence=sequence,
        device_statuses=statuses,
        evolution_runtime=evolution,
        evolution_service=evolution.evolution_service,
        settings=settings,
        device_action=device,
        operator=operator_service,
        document_service=document_service,
    )
    holdoffs = HoldoffRegistry()
    set_holdoffs(holdoffs)
    set_publisher(UiStatePublisher())
    set_engine(PresenceFusionEngine())
    reset_heartbeat()
    ambient_service.cancel_display_test()
    ambient_service.reset_holdoff_restore()

    with broker.session() as db:
        enrolled = broker_service.enroll_device(
            db,
            name="ev-pc",
            platform="windows",
            public_key_spki_b64=_spki(),
            capabilities=[
                "browser.chrome",
                "desktop.alarm_arm",
                "desktop.display_wake",
                "app.launch",
            ],
            trace_id=None,
        )
    broker.connections[enrolled.id] = DeviceConnection(
        device_id=enrolled.id, session_id=uuid.uuid4(), websocket=object()
    )
    issued = identity.service.issue_session(
        client_kind="desktop", label="pc", device_id=uuid.uuid4()
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issued.token}"
    return Harness(
        client=client,
        runtime=runtime,
        factory=factory,
        device=device,
        sequence=sequence,
        holdoffs=holdoffs,
        operator=operator_service,
        documents=document_service,
    )


# ------------------------------------------------------------- the contract


def _local_eye(state: str) -> dict:
    return {
        "local": {
            "state": state,
            "running": state == "ACTIVE",
            "camera_label": "Integrated Camera" if state == "ACTIVE" else None,
            "error_class": None,
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "changed": True,
        }
    }


def contract_arguments(case: UtteranceCase, tool: str, resolved: dict) -> dict:
    """The arguments the persona tells the model to pass for ``tool`` on this utterance."""
    text = case.utterance
    args: dict[str, Any] = {}
    if tool == "activity.explain":
        args = {"question": text}
    elif tool == "state.now":
        args = {"question": text}
    elif tool == "research.explain":
        level = "technical" if resolved.get("intent") == "technical" else None
        if resolved.get("research_class") == "research_technical_explanation":
            level = "technical"
        elif resolved.get("intent") == "summarize":
            level = "executive"
        elif resolved.get("intent") == "detail":
            level = "detail"
        args = {"level": level} if level else {}
    elif tool == "research.start":
        args = {"topic": text}
    elif tool == "alarm.create":
        args = {"when_spoken": text}
        if resolved.get("intent") == "alarm_test_create":
            args["test"] = True
    elif tool in ("eye.disable", "eye.enable"):
        args = {
            "utterance": text,
            "observed_after": _local_eye("DISABLED" if tool == "eye.disable" else "ACTIVE"),
        }
    elif tool in (
        "narration.control",
        "release.promote",
        "release.rollback",
        "evolution.control",
        "voice.intent",
    ):
        args = {"utterance": text}
    elif tool == "operator.app_open":
        args = {"application": text}
    elif tool == "operator.window_control":
        action = {
            "window_close": "close",
            "window_maximize": "maximize",
            "window_minimize": "minimize",
            "window_restore": "restore",
            "window_previous": "previous",
        }.get(resolved.get("intent"))
        args = {"action": action} if action else {}
    elif tool == "operator.type":
        # No default fallback to the WHOLE utterance: a real model passes only the text
        # it actually extracted (nothing, for "Şuraya yazar mısın?"), never the sentence
        # that asked it to write something. The turn's own text_to_type is what a
        # succeeding case actually exercises; case.tool_arguments overrides when a case
        # wants to prove the model's own argument specifically.
        args = {}
    elif tool == "operator.shell":
        args = {"query": resolved.get("shell_query")} if resolved.get("shell_query") else {}
    args.update(case.tool_arguments)
    return args


@dataclass
class CaseResult:
    case_id: str
    category: str
    source: str
    utterance: str
    context: str
    expected_intent: str | None
    resolved_intent: str | None = None
    research_class: str | None = None
    research_reference: str | None = None
    expected_tool: str | None = None
    tool_status: str | None = None
    verdict: str = (
        "pending"  # correct | clarification | wrong_route | forbidden_side_effect | error
    )
    problems: list[str] = field(default_factory=list)
    target_id: str | None = None
    speech_head: str = ""
    #: The tool's full spoken response: what the TTS -> STT loopback proxy synthesises
    #: (app.voice.loopback). ``speech_head`` stays the short form for the confusion rows.
    speech: str = ""
    #: The case's response class (ok / refused / ...), so a report reader can pick the
    #: cases that carry an answer without knowing the corpus.
    expected_response: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "source": self.source,
            "utterance": self.utterance,
            "context": self.context,
            "expected_intent": self.expected_intent,
            "resolved_intent": self.resolved_intent,
            "research_class": self.research_class,
            "research_reference": self.research_reference,
            "expected_tool": self.expected_tool,
            "tool_status": self.tool_status,
            "verdict": self.verdict,
            "problems": list(self.problems),
            "target_id": self.target_id,
            "speech_head": self.speech_head,
            "speech": self.speech,
            "expected_response": self.expected_response,
        }


def run_case(case: UtteranceCase, *, harness: Harness | None = None) -> CaseResult:
    h = harness or build_harness()
    result = CaseResult(
        case_id=case.case_id,
        category=case.category,
        source=case.source,
        utterance=case.utterance,
        context=case.context,
        expected_intent=case.expected_intent,
        expected_tool=case.expected_tool,
        expected_response=case.expected_response,
    )
    try:
        h.seed(case.context)
        sid = h.new_session()
        h.device.reset()
        tasks_before = h.research_task_ids()
        alarms_before = h.alarm_rows()

        said = h.say(sid, case.utterance)
        resolved = said["resolved_intents"][0]
        result.resolved_intent = resolved.get("intent")
        result.research_class = resolved.get("research_class")
        result.research_reference = resolved.get("research_reference")

        # 1. The router.
        if case.expected_intent is not None and resolved.get("intent") != case.expected_intent:
            result.problems.append(
                f"intent {resolved.get('intent')!r} != expected {case.expected_intent!r}"
            )
        expected_class = case.expected.get("research_class")
        if expected_class and resolved.get("research_class") != expected_class:
            result.problems.append(
                f"research_class {resolved.get('research_class')!r} != {expected_class!r}"
            )
        if case.expected_tool is not None:
            mapped = resolved.get("capability")
            if (
                mapped is not None
                and mapped != case.expected_tool
                and case.expected_tool
                not in (
                    "research.explain",
                    "research.sources",
                    "research.finding_detail",
                    "research.start",
                    "state.now",
                    "activity.explain",
                )
            ):
                result.problems.append(f"router capability {mapped!r} != {case.expected_tool!r}")
        changes = case.expected.get("policy_changes_include")
        if changes:
            got = resolved.get("policy_changes") or {}
            for key, value in changes.items():
                if got.get(key) != value:
                    result.problems.append(f"policy_changes {got!r} lacks {key}={value}")

        # 2. The expected tool, through the relay.
        if (
            case.expected_response in (RESPONSE_CONTROL, RESPONSE_NONE)
            or case.expected_tool is None
        ):
            if result.problems:
                result.verdict = "wrong_route"
            else:
                result.verdict = "correct"
            return result

        call = h.tool(
            sid, "c-1", case.expected_tool, contract_arguments(case, case.expected_tool, resolved)
        )
        result.tool_status = call["status"]
        body = call.get("result") or call.get("error") or {}
        speech = str(body.get("speech") or body.get("spoken_result") or "")
        result.speech_head = speech[:80]
        result.speech = speech

        if case.expected_response == RESPONSE_RUNNING:
            if call["status"] != "running":
                result.problems.append(
                    f"expected running, got {call['status']} {body.get('error_class')}"
                )
        elif case.expected_response == RESPONSE_REFUSED:
            refused = call["status"] == "succeeded" and (
                body.get("status") == "refused"
                or body.get("execution_status") == "refused"
                or body.get("error_class") == case.expected.get("error_class")
            )
            if not refused:
                result.problems.append(
                    f"expected a refusal receipt, got {call['status']} {body.get('status')}"
                )
            if not speech:
                result.problems.append("a refusal must speak")
        elif case.expected_response == RESPONSE_CLARIFY:
            if call["status"] != "needs_clarification":
                result.problems.append(f"expected needs_clarification, got {call['status']}")
        else:
            if call["status"] == "needs_clarification":
                result.verdict = "clarification"
                result.problems.append(f"clarified instead of answering: {speech[:60]!r}")
            elif call["status"] != "succeeded":
                result.problems.append(
                    f"expected succeeded, got {call['status']} {body.get('error_class')}"
                )
            if not speech and case.expected_tool not in ("state.now",):
                result.problems.append("no speech on the result")
        if speech and contains_fake_completion(speech):
            result.problems.append("banned completion phrase in speech")

        # 3. The target and the deterministic extras.
        target_id = body.get("research_job_id")
        result.target_id = target_id
        if case.expected_target in ("current", "previous"):
            want = h.ids.get("research:B" if case.expected_target == "current" else "research:A")
            if call["status"] == "succeeded" and target_id != want:
                result.problems.append(f"target {target_id} != {case.expected_target} ({want})")
        for key, want in case.expected.items():
            if key == "level" and body.get("level") != want:
                result.problems.append(f"level {body.get('level')!r} != {want!r}")
            elif key == "local_time" and (body.get("alarm") or {}).get("local_time") != want:
                result.problems.append(
                    f"local_time {(body.get('alarm') or {}).get('local_time')!r} != {want!r}"
                )
            elif (
                key == "weekdays"
                and ((body.get("alarm") or {}).get("recurrence") or {}).get("weekdays") != want
            ):
                result.problems.append("weekdays mismatch")
            elif key == "is_test" and (body.get("alarm") or {}).get("is_test") is not want:
                result.problems.append("is_test mismatch")
            elif key == "routed" and body.get("routed") != want:
                result.problems.append(f"routed {body.get('routed')!r} != {want!r}")
            elif key == "routed_not" and body.get("routed") == want:
                result.problems.append(f"routed to {want!r}")
            elif (
                key == "query_kind"
                and (body.get("query_kind") or (body.get("intent") or {}).get("query_kind")) != want
            ):
                result.problems.append(f"query_kind != {want!r}")
            elif key == "evolution_paused_after" and h.evolution_paused() is not want:
                result.problems.append(f"evolution paused after != {want}")
            elif key == "eye_enabled_after" and h.eye_enabled() is not want:
                result.problems.append(f"eye enabled after != {want}")
            elif key == "alarm_state":
                state, minutes, count = h.alarm_state(h.ids["alarm"])
                if state != want:
                    result.problems.append(f"alarm state {state} != {want}")
                if "snooze_minutes" in case.expected and minutes != case.expected["snooze_minutes"]:
                    result.problems.append(
                        f"snooze_minutes {minutes} != {case.expected['snooze_minutes']}"
                    )
                if "snooze_count" in case.expected and count != case.expected["snooze_count"]:
                    result.problems.append(
                        f"snooze_count {count} != {case.expected['snooze_count']}"
                    )

        # 4. Forbidden tools: research.start is dispatched and MUST be refused by the relay;
        #    every other forbidden tool is a router assertion (already made above).
        for forbidden in case.forbidden_tools:
            if forbidden in _REFUSAL_PROVEN_BY_DISPATCH:
                blocked = h.tool(
                    sid, f"forbidden-{forbidden}", forbidden, {"topic": case.utterance}
                )
                blocked_body = blocked.get("result") or {}
                if not (
                    blocked["status"] == "succeeded" and blocked_body.get("status") == "refused"
                ):
                    result.problems.append(f"{forbidden} was NOT refused: {blocked['status']}")
                    result.verdict = "forbidden_side_effect"

        # 5. Side effects on the fake device, and on the tables a wrong route would touch.
        extra = [c for c in h.device.capabilities_called() if c not in case.side_effects]
        if extra:
            result.problems.append(f"device calls outside the policy: {extra}")
            result.verdict = "forbidden_side_effect"
        if case.expected_tool != "research.start" and h.research_task_ids() != tasks_before:
            result.problems.append("a research task was created")
            result.verdict = "forbidden_side_effect"
        if case.expected_tool != "alarm.create" and h.alarm_rows() != alarms_before:
            result.problems.append("an alarm row was created")
            result.verdict = "forbidden_side_effect"

        # 6. The speech lifecycle, structurally: the client reports the turn and the record
        #    shows the tool's speech and the audible turn from durable rows alone.
        if call["status"] in ("succeeded", "needs_clarification") and speech:
            h.speak(sid, turn=1, chars=len(speech))
            activity = h.activity(sid)
            recorded = next(c for c in activity["tool_calls"] if c["call_id"] == "c-1")
            if recorded["speech_chars"] <= 0:
                result.problems.append("record shows no speech")
            kinds = {e["kind"] for e in activity["client_events"]}
            if not {"first_audio", "audio_done"} <= kinds:
                result.problems.append("record shows no audible turn")

        if result.verdict == "pending":
            result.verdict = "wrong_route" if result.problems else "correct"
        return result
    except Exception as exc:  # noqa: BLE001 - a crashed case is a finding, not a crash of the suite
        result.verdict = "error"
        result.problems.append(f"{type(exc).__name__}: {exc}"[:300])
        return result
    finally:
        h.client.close()
        set_holdoffs(HoldoffRegistry())
        set_publisher(UiStatePublisher())


def build_report(results: list[CaseResult], *, corpus_version: int) -> dict[str, Any]:
    by_verdict: dict[str, int] = {}
    by_category: dict[str, dict[str, int]] = {}
    for r in results:
        by_verdict[r.verdict] = by_verdict.get(r.verdict, 0) + 1
        cat = by_category.setdefault(r.category, {})
        cat[r.verdict] = cat.get(r.verdict, 0) + 1
    confusion = [
        {
            "case_id": r.case_id,
            "utterance": r.utterance,
            "expected": r.expected_intent,
            "resolved": r.resolved_intent,
            "problems": r.problems,
        }
        for r in results
        if r.verdict != "correct"
    ]
    forbidden = by_verdict.get("forbidden_side_effect", 0)
    wrong = by_verdict.get("wrong_route", 0) + by_verdict.get("error", 0)
    summary = "HEALTHY" if forbidden == 0 and wrong == 0 else "REGRESSION_FOUND"
    return {
        "suite": "OwnerUtteranceSuite",
        "corpus_version": corpus_version,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "total_cases": len(results),
        "passed": by_verdict.get("correct", 0),
        "clarification": by_verdict.get("clarification", 0),
        "failed_routing": wrong,
        "forbidden_side_effects": forbidden,
        "by_category": by_category,
        "by_source": _count(results, "source"),
        "summary": summary,
        "confusion": confusion[:60],
        "results": [r.as_dict() for r in results],
    }


def _count(results: list[CaseResult], attr: str) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = out.setdefault(getattr(r, attr), {})
        bucket[r.verdict] = bucket.get(r.verdict, 0) + 1
    return out


__all__ = [
    "CaseResult",
    "Harness",
    "build_harness",
    "build_report",
    "contract_arguments",
    "run_case",
]
