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
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from app.appfactory.models import AppProjectRow
from app.appfactory.service import AppFactoryService
from app.artifacts import factory as artifact_factory
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
from app.artifacts.spec import ArtifactSpec
from app.briefing.models import BriefingPreferencesRow
from app.briefing.service import BriefingService
from app.broker import service as broker_service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.calendar.models import (
    CalendarIndexRow,
    CalendarProposalRow,
)
from app.calendar.providers import FakeCalendarWriter
from app.calendar.service import CalendarService
from app.config import Settings
from app.creative3d.models import SceneRow
from app.creative3d.service import SceneService
from app.devices.status import DeviceStatusRegistry
from app.documents.index import DocumentIndex
from app.documents.models import DocumentIndexRow
from app.documents.service import DocumentService
from app.evolution.models import Capability, CapabilityGap, EvolutionOpportunity, SkillVersion
from app.evolution.runtime import EvolutionRuntime
from app.evolution.supervisor import is_paused
from app.executive.models import ExecutiveRunRow, ExecutiveStepRow
from app.genesis.catalogue import GenesisInterfaceCatalogue, set_catalogue
from app.genesis.models import GenesisRun
from app.genesis.runtime import GenesisRuntime
from app.genesis.service import register_genesis_service
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.location.models import LocationContextRow
from app.location.service import LocationService
from app.mail.models import MailDraftRow, MailIndexRow
from app.mail.providers import FakeMailSender
from app.mail.service import MailService
from app.main import create_app
from app.narration.models import NarrationSession, PronunciationEntry
from app.object_store import InMemoryObjectStore
from app.operator import focus as operator_focus
from app.operator.models import (
    FOCUS_KIND_ARTIFACT,
    FOCUS_KIND_DOCUMENT,
    FOCUS_KIND_EVENT,
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
from app.research.browser_gateway import FakeBrowserGateway
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
from app.security.models import AuthorizedAsset
from app.uistate.publisher import UiStatePublisher, set_publisher
from app.voice.models import VoiceProfile
from app.voice.providers import FakeTTSProvider
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from app.voice.realtime_sessions.research_announcer import ResearchToolCallAnnouncer
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.simulator import SimulatedRealtimeProvider
from app.weather.models import WeatherQueryEvidenceRow
from app.weather.providers import FakeWeatherProvider
from app.weather.service import WeatherService
from tests.alarms_support import FakeDeviceAction, happy_device_results
from tests.appfactory_support import appfactory_capability_results
from tests.artifacts_support import artifact_capability_results
from tests.creative3d_support import FakeCreative3DDevice
from tests.documents_support import document_capability_results, extract_result
from tests.identity_support import IDENTITY_TABLES
from tests.mail_calendar_support import build_fake_calendar_provider, build_fake_mail_provider
from tests.voice_corpus.corpus import (
    CTX_ALARM_RINGING,
    CTX_ALARM_SCHEDULED,
    CTX_ALARM_WAKE_SONG_SET,
    CTX_ALARM_WAKE_SONG_URL,
    CTX_APP_RUNNING,
    CTX_APP_SCAFFOLDED,
    CTX_ARTIFACT_FOCUSED,
    CTX_COMMON_POINTS_FOCUSED,
    CTX_COUNTERBOX_RUNNING,
    CTX_DOCUMENT_ARTIFACT_FOCUSED,
    CTX_DOCUMENT_FOCUSED,
    CTX_DOCX_FOCUSED,
    CTX_DRAFT_READ_BACK,
    CTX_EVENT_FOCUSED,
    CTX_EYE_DISABLED,
    CTX_FILE_FOCUSED,
    CTX_LAMPBOX_RUNNING,
    CTX_MESSAGE_FOCUSED,
    CTX_OPERATOR_RUNNING,
    CTX_PPTX_FOCUSED,
    CTX_PROPOSAL_READ_BACK,
    CTX_RESEARCH_FOCUS_B,
    CTX_SCENE_BLENDER,
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
    MailIndexRow.__table__,
    MailDraftRow.__table__,
    CalendarIndexRow.__table__,
    CalendarProposalRow.__table__,
    AppProjectRow.__table__,
    SceneRow.__table__,
    ExecutiveRunRow.__table__,
    ExecutiveStepRow.__table__,
    LocationContextRow.__table__,
    WeatherQueryEvidenceRow.__table__,
    BriefingPreferencesRow.__table__,
)

#: The tools the harness may dispatch as "forbidden" because the product refuses them at
#: the relay; every other forbidden tool is asserted at the router (never dispatched).
#: ``mail.send``/``calendar.commit`` (M21 security review H1, ADR-0084 addendum 2): a
#: model calling either DIRECTLY, exactly as a hostile document could steer it to, with
#: no owner MAIL_SEND/CALENDAR_COMMIT turn behind it — the confirmation gate, not the
#: router, is what refuses this one (mc.send.no_owner_turn / mc.commit.no_owner_turn).
_REFUSAL_PROVEN_BY_DISPATCH = frozenset({"research.start", "mail.send", "calendar.commit"})


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
    mail: MailService
    calendar: CalendarService
    app_factory: AppFactoryService
    browser_gateway: FakeBrowserGateway
    genesis: GenesisRuntime
    creative3d: SceneService
    location: LocationService
    weather: WeatherService
    briefing: BriefingService
    ids: dict[str, str] = field(default_factory=dict)
    #: M24 (docs/M24_CAPABILITY_GENESIS_SPEC.md §6, §7): the live fixture application
    #: (CounterBoxServer/LampBoxServer) a CTX_COUNTERBOX_RUNNING/CTX_LAMPBOX_RUNNING case
    #: started, for reading its real state after the case (the "forbidden side effect"
    #: check) — None for every other case. ``_genesis_fixture_cm`` is the context manager
    #: itself, closed by ``run_case``'s own ``finally`` block.
    genesis_fixture: Any = None
    _genesis_fixture_cm: Any = None

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
        elif context == CTX_ALARM_WAKE_SONG_SET:
            # 2026-09-08 wake-song defect fix: the owner has already approved a wake song
            # once (``PUT /v1/alarms/wake-song``) — the precondition a PLAIN alarm-create
            # phrase (no media named) needs to resolve to the song rather than the tone.
            with self.factory() as db:
                alarms_service.set_wake_song(
                    db, url=CTX_ALARM_WAKE_SONG_URL, title="Corpus Wake Song"
                )
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
        elif context == CTX_ARTIFACT_FOCUSED:
            # M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §5): a REAL artifact, made through
            # the real factory against the harness's in-memory object store — never a
            # bare focus row pointing at nothing, the same "genuine fixture, not a
            # sentinel" discipline CTX_DOCUMENT_FOCUSED already uses. An older row (a
            # one-slide presentation) then the current one (the budget spreadsheet, an
            # xlsx AND a csv render, both valid) so "önceki dosyayı aç" resolves to
            # something real too.
            artifacts_runtime = self.runtime.artifacts
            with self.factory() as db:
                older = artifact_factory.create(
                    db,
                    artifacts_runtime.store,
                    spec=ArtifactSpec.model_validate(
                        {
                            "kind": "presentation",
                            "title": "Q3 Sunum",
                            "slides": [{"title": "Giriş", "bullets": ["Genel bakış"]}],
                        }
                    ),
                )
                operator_focus.set_focus(
                    db,
                    FOCUS_KIND_ARTIFACT,
                    str(older.artifact_id),
                    label=older.title,
                    source="test_context",
                )
                current = artifact_factory.create(
                    db,
                    artifacts_runtime.store,
                    spec=ArtifactSpec.model_validate(
                        {
                            "kind": "spreadsheet",
                            "title": "Bütçe 2026",
                            "sheets": [
                                {
                                    "name": "Özet",
                                    "columns": ["Kalem", "Tutar"],
                                    "rows": [["Kira", 12000], ["Maaş", 45000]],
                                    "totals": {"Tutar": "sum"},
                                }
                            ],
                            "spoken_numbers": [12000, 45000],
                        }
                    ),
                )
                operator_focus.set_focus(
                    db,
                    FOCUS_KIND_ARTIFACT,
                    str(current.artifact_id),
                    label=current.title,
                    source="test_context",
                )
            self.ids["artifact:current"] = str(current.artifact_id)
            self.ids["artifact:previous"] = str(older.artifact_id)
        elif context == CTX_DOCUMENT_ARTIFACT_FOCUSED:
            # The same discipline with the kinds swapped: an older spreadsheet, then a
            # current DOCUMENT — the artifact "Bunu PDF yap" can honestly turn into a PDF.
            artifacts_runtime = self.runtime.artifacts
            with self.factory() as db:
                older = artifact_factory.create(
                    db,
                    artifacts_runtime.store,
                    spec=ArtifactSpec.model_validate(
                        {
                            "kind": "spreadsheet",
                            "title": "Bütçe 2026",
                            "sheets": [
                                {
                                    "name": "Özet",
                                    "columns": ["Kalem", "Tutar"],
                                    "rows": [["Kira", 12000], ["Maaş", 45000]],
                                }
                            ],
                            "spoken_numbers": [12000, 45000],
                        }
                    ),
                )
                operator_focus.set_focus(
                    db,
                    FOCUS_KIND_ARTIFACT,
                    str(older.artifact_id),
                    label=older.title,
                    source="test_context",
                )
                current = artifact_factory.create(
                    db,
                    artifacts_runtime.store,
                    spec=ArtifactSpec.model_validate(
                        {
                            "kind": "document",
                            "title": "Toplantı Notları",
                            "sections": [
                                {
                                    "heading": "Giriş",
                                    "level": 1,
                                    "paragraphs": ["Toplantının notları."],
                                }
                            ],
                            "spoken_numbers": [],
                        }
                    ),
                )
                operator_focus.set_focus(
                    db,
                    FOCUS_KIND_ARTIFACT,
                    str(current.artifact_id),
                    label=current.title,
                    source="test_context",
                )
            self.ids["artifact:current"] = str(current.artifact_id)
            self.ids["artifact:previous"] = str(older.artifact_id)
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
        elif context == CTX_MESSAGE_FOCUSED:
            # The latest message from Ali (uid 104, truth.json's own "Re: Proje planı") —
            # a real fake-provider read, exactly as if the owner had just heard it.
            with self.factory() as db:
                self.mail.read(db, target="Ali")
        elif context == CTX_DRAFT_READ_BACK:
            with self.factory() as db:
                self.mail.read(db, target="Ali")
                self.mail.draft_reply(db, body="Yarın 10'da uygunum.", target="current")
                # H1 (ADR-0084 addendum 2): PREPARE alone no longer counts as "read back"
                # - the router's own draft_pending flag (app.voice.realtime_sessions.
                # service) requires the row to actually be in DRAFT_STATE_READ_BACK, so a
                # context named "already read back" now has to genuinely perform that
                # act. A sentinel session/turn is fine here: this stamps the ROUTER-level
                # "something is pending" signal (state alone, not session-bound); a case
                # that goes on to actually CONFIRM must still read it back for REAL, in
                # ITS OWN session, through the real tool (see mc.send.confirmed's
                # ``pre_turn`` in tests/voice_corpus/corpus.py) — the confirmation gate's
                # own session/turn binding is never satisfied by this seed alone.
                self.mail.read_draft(db, session_id="seed:draft_read_back", turn=0)
        elif context == CTX_EVENT_FOCUSED:
            with self.factory() as db:
                operator_focus.set_focus(
                    db,
                    FOCUS_KIND_EVENT,
                    "ev-dis@fixture.example",
                    label="Diş hekimi",
                    source="test_context",
                )
        elif context == CTX_PROPOSAL_READ_BACK:
            with self.factory() as db:
                self.calendar.propose(
                    db,
                    summary="Kontrol",
                    start=datetime(2026, 9, 15, 11, 0, tzinfo=UTC),
                    end=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
                )
                # See CTX_DRAFT_READ_BACK's identical comment just above.
                self.calendar.read_proposal(db, session_id="seed:proposal_read_back", turn=0)
        elif context in (CTX_APP_SCAFFOLDED, CTX_APP_RUNNING):
            # M23 (docs/M23_APP_FACTORY_SPEC.md §5): a REAL app_projects row, made
            # through the real AppFactoryService.create against the fake device (the
            # same "genuine fixture, not a sentinel" discipline CTX_ARTIFACT_FOCUSED
            # already uses) — sets the current ``project`` focus as a side effect
            # (AppFactoryService.create's own focus_module.set_focus call).
            with self.factory() as db:
                created = self.app_factory.create(
                    db,
                    self.device,
                    spec={"name": "Yapılacaklar", "kind": "web_static", "template": "task-tracker"},
                    session_id="seed:app_scaffolded",
                )
                assert created["execution_status"] == "executed", created
                self.ids["app:project"] = created["project_id"]
                if context == CTX_APP_RUNNING:
                    ran = self.app_factory.run(
                        db, self.device, target="current", session_id="seed:app_running"
                    )
                    assert ran["execution_status"] == "executed", ran
        elif context == CTX_SCENE_BLENDER:
            # M25 (docs/M25_CREATIVE_3D_SPEC.md §5): a REAL scenes row, made through
            # the real SceneService.create against the fake device (the same "genuine
            # fixture, not a sentinel" discipline CTX_APP_SCAFFOLDED already uses) —
            # sets the current ``scene`` focus as a side effect
            # (SceneService._finish's own focus_module.set_focus call), so "Bir küp
            # ekle."/"Render al." resolve to something real.
            with self.factory() as db:
                created = self.creative3d.create(
                    db,
                    self.device,
                    plan={
                        "tool": "blender",
                        "project": "corpus-fixture",
                        "scene": "demo",
                        "operations": [{"op": "create_scene"}],
                    },
                    session_id="seed:scene_blender",
                )
                assert created["execution_status"] == "executed", created
                self.ids["scene:current"] = created["scene_id"]
        elif context in (CTX_COUNTERBOX_RUNNING, CTX_LAMPBOX_RUNNING):
            # M24 (docs/M24_CAPABILITY_GENESIS_SPEC.md §6, §7): the REAL fixture
            # application, started on a free port for the duration of THIS ONE case (a
            # fresh harness per case, module docstring's own "no test here calls a
            # handler directly" — the fixture is the genuine dependency
            # capability.request talks to, never a mock), registered into the SAME
            # catalogue app.voice.intents' matchers read (a fresh, empty one per
            # harness build_harness() already set).
            from app.genesis.catalogue import CatalogueEntry, OperationAlias, get_catalogue
            from tests.fixtures.genesis import counterbox_app, lampbox_app

            # Every phrase/verb below is listed in BOTH its dotted and diacritic-
            # stripped spelling — the same discipline app.voice.intents' own
            # ``_APP_RUN_VERB_FORMS`` ("çalıştır"/"calistir") already follows,
            # since the corpus's own asr_noise variants strip diacritics from the
            # utterance BEFORE it ever reaches the router/catalogue.
            if context == CTX_COUNTERBOX_RUNNING:
                cm = counterbox_app.serve()
                server = cm.__enter__()
                entry = CatalogueEntry(
                    name="counterbox",
                    url=server.spec_url,
                    target_phrases=(
                        "sayaç kutusu",
                        "sayac kutusu",
                        "sayaç kutusunu",
                        "sayac kutusunu",
                        "sayacı",
                        "sayaci",
                        "sayaç",
                        "sayac",
                    ),
                    operations=(
                        OperationAlias("read", ("kaç", "kac")),
                        OperationAlias(
                            "increment",
                            (
                                "artır",
                                "artir",
                                "arttır",
                                "arttir",
                                "artırsana",
                                "artirsana",
                                "arttırsana",
                                "arttirsana",
                                "artırır",
                                "artirir",
                                "arttırır",
                                "arttirir",
                            ),
                        ),
                        OperationAlias(
                            "reset",
                            (
                                "sıfırla",
                                "sifirla",
                                "sıfırlasana",
                                "sifirlasana",
                                "sıfırlar",
                                "sifirlar",
                            ),
                        ),
                    ),
                )
            else:
                cm = lampbox_app.serve()
                server = cm.__enter__()
                entry = CatalogueEntry(
                    name="lampbox",
                    url=server.spec_url,
                    target_phrases=(
                        "test lambası",
                        "test lambasi",
                        "test lambasını",
                        "test lambasini",
                        "lambayı",
                        "lambayi",
                        "lamba",
                    ),
                    operations=(
                        OperationAlias("state", ("durumu", "durumda")),
                        OperationAlias(
                            "toggle",
                            (
                                "aç",
                                "ac",
                                "açsana",
                                "acsana",
                                "kapat",
                                "kapatsana",
                                "değiştir",
                                "degistir",
                            ),
                        ),
                    ),
                )
            get_catalogue().register(entry)
            self._genesis_fixture_cm = cm
            self.genesis_fixture = server

    # ------------------------------------------------------------- M21: mail/calendar

    def mail_sent_count(self) -> int:
        return len(self.mail._sender.sent)  # type: ignore[attr-defined]

    def calendar_committed_count(self) -> int:
        writer = self.calendar._writer  # type: ignore[attr-defined]
        return len(writer.created) + len(writer.updated)

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
    settings = Settings(
        _env_file=None,
        voice_openai_api_key=VENDOR_KEY,
        # M21 (docs/M21_MAIL_CALENDAR_SPEC.md §3): the host flags a real deployment sets
        # out of band, on out of the autonomous system's own reach — set here so the
        # corpus proves the READ-BACK gate specifically (not merely "the flag is off"),
        # the same way a real qualification run would with the owner's account
        # configured and sending genuinely enabled.
        mail_send_enabled=True,
        calendar_write_enabled=True,
    )
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
    # M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §5): artifact.create/render/validate need a
    # real ObjectStore to write render bytes to; an in-memory one keeps this suite fully
    # offline (task brief: no network) the same way test_artifact_wiring.py's own client
    # fixture does. Every prior corpus case never touched ``.store`` at all.
    artifacts._store = InMemoryObjectStore()
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

    # M25 (docs/M25_CREATIVE_3D_SPEC.md §2-§4, ADR-0088): the SAME ``project.scaffold``/
    # ``project.run`` capability names the App Factory already uses (module docstring:
    # one desktop authority, never a second path) — this fake tells the two apart by
    # payload shape the same way a real device would (``FakeCreative3DDevice.scaffold``'s
    # own docstring), so it must be spread AFTER ``appfactory_capability_results()``
    # to be the one actually reached for both.
    creative3d_device = FakeCreative3DDevice(
        unity_available=False, render_dir=Path(tempfile.mkdtemp(prefix="creative3d-render-"))
    )
    device = FakeDeviceAction(
        results={
            **happy_device_results(),
            **document_capability_results(),
            **artifact_capability_results(),
            **appfactory_capability_results(),
            **creative3d_device.capability_results(),
        }
    )
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
        GenesisRun.__table__,
        # app.security.provider.RegistryAuthorizationProvider (the default
        # mutation-authorization source, app.evolution.runtime.EvolutionRuntime
        # .authorization) queries this table for ANY genesis run against a
        # MUTATING operation — present here even though no row is ever
        # inserted by this harness (a mutating op always parks at
        # awaiting_approval, exactly as production would with nothing
        # enrolled).
        AuthorizedAsset.__table__,
    ):
        table.create(evolution_engine)
    evolution = EvolutionRuntime(settings, engine=evolution_engine)
    # M24 (docs/M24_CAPABILITY_GENESIS_SPEC.md §5, ADR-0087): the genesis
    # runtime is a thin wrapper over the SAME EvolutionRuntime (registry,
    # gaps, authorization) — no second engine, no second registry. The
    # publish roots are overridden to a FRESH temp directory per harness
    # call: EvolutionRuntime.skills_root otherwise defaults to a real,
    # process-wide path (PAGENTOS_EVOLUTION_SKILLS_ROOT or
    # <repo>/skills/generated), and a published skill version is immutable
    # (app.genesis.service._publish) — two independent harnesses (two
    # separate in-memory databases, no shared history) publishing the SAME
    # capability_id/version to that ONE real directory would collide.
    evolution.skills_root = Path(tempfile.mkdtemp(prefix="genesis-skills-"))
    evolution.work_root = Path(tempfile.mkdtemp(prefix="genesis-work-"))
    genesis = GenesisRuntime(evolution)
    app.state.genesis = genesis
    register_genesis_service(genesis.service)
    # M24: a fresh, EMPTY catalogue per harness build — production starts empty too
    # (module docstring); the "genesis" corpus category's own seed() populates it with
    # the fixture's spoken name once its fixture app is actually listening.
    set_catalogue(GenesisInterfaceCatalogue())
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
    # M21 (docs/M21_MAIL_CALENDAR_SPEC.md §2, §4, ADR-0084): the FAKE providers loading
    # the fixture mailbox/calendar directly (never the reals ``create_app`` itself would
    # have built from empty settings) — replaces what ``create_app`` wired, the same way
    # ``document_service`` above replaces its own, so every mail/calendar tool call in a
    # corpus case reaches the SAME fake sender/writer this harness can inspect.
    mail_provider = build_fake_mail_provider()
    mail_sender = FakeMailSender()
    mail_service = MailService(mail_provider, mail_sender)
    calendar_provider = build_fake_calendar_provider()
    calendar_writer = FakeCalendarWriter()
    calendar_service = CalendarService(calendar_provider, calendar_writer)
    app.state.mail_service = mail_service
    app.state.calendar_service = calendar_service
    # M23 (docs/M23_APP_FACTORY_SPEC.md §1-§4): the App Factory's own service, reading
    # the SAME fake device port every other family holds, plus the M13 fake browser
    # gateway (task brief: "the M13 fake gateway in unit tests") for ``app.open``/
    # exercising a running web project.
    app_factory_service = AppFactoryService()
    browser_gateway = FakeBrowserGateway()
    app.state.app_factory_service = app_factory_service
    # M25 (docs/M25_CREATIVE_3D_SPEC.md §2-§4, ADR-0088): 3D Creation's own service,
    # reading the SAME fake device port every other family holds, with the SAME
    # in-memory object store artifacts already uses (task brief: no network).
    creative3d_service = SceneService(object_store=artifacts.store)
    app.state.creative3d_service = creative3d_service
    # ADR-0091 (Owner Location Context / Live Weather / Morning Briefing): the FAKE
    # weather provider (never real network in a corpus run — task brief: no network),
    # with a durable default location set so "Hava nasıl?" has a deterministic answer
    # the same way every other family's fixture gives one; explicit-place cases
    # ("İstanbul'da hava nasıl?", "Ankara'da...") exercise the resolver's tier 1
    # regardless of this default.
    location_service = LocationService()
    weather_service = WeatherService(
        location_service=location_service, provider=FakeWeatherProvider()
    )
    briefing_service = BriefingService()
    with broker.session() as _db:
        location_service.set_default(_db, city="İstanbul")
    app.state.location_service = location_service
    app.state.weather_service = weather_service
    app.state.briefing_service = briefing_service
    runtime.register_live(
        wake_sequence=sequence,
        device_statuses=statuses,
        evolution_runtime=evolution,
        evolution_service=evolution.evolution_service,
        settings=settings,
        device_action=device,
        operator=operator_service,
        document_service=document_service,
        mail_service=mail_service,
        calendar_service=calendar_service,
        app_factory_service=app_factory_service,
        browser_gateway=browser_gateway,
        genesis_service=genesis.service,
        creative3d_service=creative3d_service,
        location_service=location_service,
        weather_service=weather_service,
        briefing_service=briefing_service,
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
        mail=mail_service,
        calendar=calendar_service,
        app_factory=app_factory_service,
        browser_gateway=browser_gateway,
        genesis=genesis,
        creative3d=creative3d_service,
        location=location_service,
        weather=weather_service,
        briefing=briefing_service,
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
    elif tool == "executive.start":
        args = {"directive": text}
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
    elif tool == "mail.search":
        args = {"query": text}
    elif tool == "mail.draft":
        args = {"body": text}
    elif tool in ("calendar.agenda", "calendar.find_slot", "calendar.propose"):
        args = {"when_spoken": text}
    elif tool == "artifact.create":
        kind = resolved.get("artifact_kind") or "document"
        title = resolved.get("artifact_title") or "Adsız"
        numbers = resolved.get("spoken_numbers")
        args = {"kind": kind, "title": title, "spec": _default_artifact_spec(kind, title, numbers)}
    elif tool == "artifact.render":
        fmt = _format_from_utterance(text)
        args = {"format": fmt} if fmt else {}
    elif tool == "app.create":
        template = resolved.get("app_template") or "task-tracker"
        name = resolved.get("app_name") or "Adsız Uygulama"
        args = {"template": template, "name": name}
    # M25 (docs/M25_CREATIVE_3D_SPEC.md §5): the 3D-creation family. The router
    # resolves the tool word and (for scene.add) the primitive kind - everything
    # else here is a plausible model argument a real persona would send after
    # hearing the prior receipt's own object name (spec's "numbers spoken are
    # numbers read back" rule, mirrored for names): a fixed default identifier a
    # case overrides via ``tool_arguments`` when it needs a specific one.
    elif tool == "scene.add":
        kind = resolved.get("scene_kind") or "cube"
        args = {"kind": kind}
    elif tool == "scene.transform":
        args = {"name": "Nesne", "location": [1.0, 0.0, 0.0]}
    elif tool == "scene.material":
        args = {"name": "Nesne", "color": [1.0, 0.0, 0.0, 1.0]}
    elif tool == "scene.light":
        args = {"name": "Isik", "energy": 5.0}
    elif tool == "scene.camera":
        args = {"name": "Kamera", "look_at": "Nesne"}
    # artifact.validate / artifact.open / artifact.list / app.run / app.test / app.stop /
    # app.status / app.open / app.list / scene.create / scene.render / scene.inspect
    # need no default argument at all — every one of them resolves its target from the
    # durable focus state, never from a wire argument (the same rule
    # mail.inbox/mail.read/... already follow).
    args.update(case.tool_arguments)
    return args


#: M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §5): the format word a "Bunu PDF yap"-shaped
#: utterance names — the ONE thing the router does not extract (``ResolvedIntent`` has
#: no format field at all; the model's own tool choice/argument carries it), read here
#: exactly the way a real model reads it off the utterance.
_FORMAT_WORDS: dict[str, str] = {
    "pdf": "pdf",
    "excel": "xlsx",
    "xlsx": "xlsx",
    "word": "docx",
    "docx": "docx",
    "csv": "csv",
    "json": "json",
    "html": "html",
    "powerpoint": "pptx",
    "pptx": "pptx",
    "metin": "txt",
    "txt": "txt",
}


def _format_from_utterance(text: str) -> str | None:
    lowered = text.lower()
    for word, fmt in _FORMAT_WORDS.items():
        if word in lowered:
            return fmt
    return None


def _letter(index: int) -> str:
    """A label with no digit in it: the spec's never-invented rule counts every digit the
    owner did not say, so the persona's own labels are letters (A, B, C, ... AA)."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _default_artifact_spec(kind: str, title: str, numbers: list[float] | None) -> dict:
    """A spec the persona would plausibly send for ``kind`` — its own structural
    numbers are exactly ``numbers`` (a router-derived subset by construction), so a
    default case always satisfies the spec's own "never invented" rule; a case that
    wants to prove the REFUSAL overrides ``spec`` entirely via ``tool_arguments``."""
    values = list(numbers or [])
    if kind == "spreadsheet":
        rows = [[f"Kalem {_letter(i)}", n] for i, n in enumerate(values)] or [["Kalem", "—"]]
        return {
            "kind": kind,
            "title": title,
            "sheets": [{"name": "Özet", "columns": ["Kalem", "Tutar"], "rows": rows}],
        }
    if kind == "dataset":
        rows = [[f"Satır {_letter(i)}", n] for i, n in enumerate(values)] or [["Satır", "—"]]
        return {"kind": kind, "title": title, "columns": ["Ad", "Değer"], "rows": rows}
    if kind == "presentation":
        bullets = [str(n) for n in values] or ["İçerik"]
        return {"kind": kind, "title": title, "slides": [{"title": title, "bullets": bullets}]}
    # document | page
    paragraphs = [f"Tutar: {n}" for n in values] or ["İçerik."]
    return {
        "kind": kind,
        "title": title,
        "sections": [{"heading": title, "level": 1, "paragraphs": paragraphs}],
    }


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


def _genesis_fixture_state(server: Any) -> Any:
    """A snapshot of a live genesis fixture's real state, regardless of shape
    (CounterBoxServer.value vs. LampBoxServer.on/.brightness) — None when no
    fixture is running this case (every non-genesis case)."""
    if server is None:
        return None
    if hasattr(server, "value"):
        return server.value
    return (server.on, server.brightness)


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
        mail_sent_before = h.mail_sent_count()
        calendar_committed_before = h.calendar_committed_count()
        genesis_state_before = _genesis_fixture_state(h.genesis_fixture)

        main_turn = 1
        # ADR-0084 addendum 2 / M26 ADR-0089: every preceding turn, through the REAL
        # tool, in THIS session, at increasing turns — the case's own utterance runs
        # strictly after all of them (UtteranceCase.preceding_turns's own docstring).
        # A tool that legitimately answers with a clarification rather than success
        # (e.g. a retry_step aimed at a step that has not failed yet, in a case built to
        # exercise exactly that) is accepted too — TOOL_STATUS_NEEDS_CLARIFICATION is
        # not a harness failure, only an unexpected status name is.
        for pre_text, pre_tool in case.preceding_turns:
            main_turn += 1
            h.say(sid, pre_text, turn=main_turn - 1)
            pre_call = h.tool(sid, f"c-pre{main_turn - 1}", pre_tool, {})
            if pre_call["status"] not in ("succeeded", "needs_clarification"):
                result.problems.append(
                    f"preceding turn {pre_tool!r} did not succeed: {pre_call['status']}"
                )

        said = h.say(sid, case.utterance, turn=main_turn)
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
                    # M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §5): "Bunu PDF yap" is the
                    # SAME ARTIFACT_CREATE intent as a genuinely new artifact ("bir kind
                    # word plus the create verb" - intents.py's own docstring), because
                    # the router names the deterministic part of an utterance, never
                    # which of two tools answers it (contract §2). The MODEL, seeing a
                    # deictic format request against an artifact that already exists,
                    # picks artifact.render over artifact.create.
                    "artifact.render",
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
            elif body.get("execution_status") == "refused" or body.get("status") == "refused":
                # A refusal receipt is a succeeded CALL (the M18 contract) — an OK case
                # that quietly receives one was never proven. Measured on 2026-09-08: the
                # persona's own "Kalem 1" label tripped the never-invented rule and several
                # art.create cases passed while refused.
                why = body.get("error_class") or body.get("reason") or speech[:60]
                result.problems.append(f"expected an executed receipt, got a refusal: {why!r}")
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
            elif key == "resolved_media_url":
                # 2026-09-08 wake-song defect fix: the exact URL that will actually play —
                # never invented, never rewritten (directive item G, "assert ... the exact
                # URL").
                got_url = ((body.get("alarm") or {}).get("resolved_media_identity") or {}).get(
                    "url"
                )
                if got_url != want:
                    result.problems.append(f"resolved_media_url {got_url!r} != {want!r}")
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

        # M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §5): the "never invented" rule, checked
        # end to end — every number ``artifact.create`` actually built the spec's
        # structure from (``body["numbers"]``, ``app.artifacts.spec``'s own
        # ``_structure_numbers()``) must be a SUBSET of what the router extracted from
        # the owner's own words (``resolved["spoken_numbers"]``). Unconditional, on
        # every succeeded create — never opt-in, since inventing a number is exactly
        # the defect this whole rule exists to catch.
        if case.expected_tool == "artifact.create" and call["status"] == "succeeded":
            spoken = resolved.get("spoken_numbers")
            produced = body.get("numbers")
            if spoken and isinstance(produced, list):
                allowed = {float(n) for n in spoken}
                invented = sorted(n for n in produced if float(n) not in allowed)
                if invented:
                    result.problems.append(
                        f"artifact spec invented number(s) not spoken: {invented}"
                    )
                    result.verdict = "forbidden_side_effect"

        # 4. Forbidden tools: research.start is dispatched and MUST be refused by the relay;
        #    every other forbidden tool is a router assertion (already made above).
        for forbidden in case.forbidden_tools:
            if forbidden in _REFUSAL_PROVEN_BY_DISPATCH:
                # mail.send/calendar.commit take no arguments at all (their own schema);
                # only research.start's own forbidden-dispatch check needs a topic.
                forbidden_args = (
                    {}
                    if forbidden in ("mail.send", "calendar.commit")
                    else {"topic": case.utterance}
                )
                blocked = h.tool(sid, f"forbidden-{forbidden}", forbidden, forbidden_args)
                blocked_body = blocked.get("result") or {}
                # A research.start refusal is a SUCCEEDED call whose own result carries
                # status="refused" (ADR-0075's plain dict); mail.send/calendar.commit's
                # refusal is the ActionReceipt shape instead (execution_status="refused",
                # module M21 security review H1) — both are "the tool call succeeded and
                # its own answer says refused", never a 4xx/5xx or an unhandled crash.
                refused = blocked_body.get("status") == "refused" or (
                    blocked_body.get("execution_status") == "refused"
                )
                if not (blocked["status"] == "succeeded" and refused):
                    result.problems.append(f"{forbidden} was NOT refused: {blocked['status']}")
                    result.verdict = "forbidden_side_effect"
                elif forbidden == "mail.send" and h.mail_sent_count() > mail_sent_before:
                    result.problems.append("mail.send was refused but something was sent anyway")
                    result.verdict = "forbidden_side_effect"
                elif (
                    forbidden == "calendar.commit"
                    and h.calendar_committed_count() > calendar_committed_before
                ):
                    result.problems.append(
                        "calendar.commit was refused but something was committed anyway"
                    )
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
        # M21 (docs/M21_MAIL_CALENDAR_SPEC.md §5, ADR-0084): mail.send/calendar.commit
        # never touch the fake DEVICE at all — the check above cannot see them. A case
        # naming "mail.send"/"calendar.commit" in ``side_effects`` must reach the fake
        # sender/writer EXACTLY once; every other case must reach it exactly zero times.
        mail_sent_after = h.mail_sent_count()
        calendar_committed_after = h.calendar_committed_count()
        mail_sent_delta = mail_sent_after - mail_sent_before
        calendar_committed_delta = calendar_committed_after - calendar_committed_before
        if "mail.send" in case.side_effects:
            if mail_sent_delta != 1:
                result.problems.append(
                    f"mail.send reached the fake sender {mail_sent_delta} times, want 1"
                )
                result.verdict = "forbidden_side_effect"
        elif mail_sent_delta != 0:
            result.problems.append(
                f"mail.send reached the fake sender ({mail_sent_delta}x) unexpectedly"
            )
            result.verdict = "forbidden_side_effect"
        if "calendar.commit" in case.side_effects:
            if calendar_committed_delta != 1:
                result.problems.append(
                    f"calendar.commit reached the writer {calendar_committed_delta}x, want 1"
                )
                result.verdict = "forbidden_side_effect"
        elif calendar_committed_delta != 0:
            result.problems.append(
                f"calendar.commit reached the writer ({calendar_committed_delta}x) unexpectedly"
            )
            result.verdict = "forbidden_side_effect"

        # 5b. M24 (docs/M24_CAPABILITY_GENESIS_SPEC.md §6, §7): the fixture's REAL state,
        #     read back after the case — the same "reaches the sink exactly once, or
        #     exactly never" policy the mail/calendar checks above enforce for their own
        #     non-device sinks. A capability.request/approve build+dispatch chain also
        #     runs the release gates (evaluation, the independent reviewer's own re-run,
        #     shadow, canary) against the SAME live fixture (app.genesis.adapter's own
        #     module docstring), so a case naming SIDE_EFFECTS_CAPABILITY_MUTATE only
        #     asserts the state CHANGED, never a specific delta.
        genesis_state_after = _genesis_fixture_state(h.genesis_fixture)
        if "capability.mutate" in case.side_effects:
            if genesis_state_after == genesis_state_before:
                result.problems.append("the genesis fixture's state did not change")
                result.verdict = "forbidden_side_effect"
        elif h.genesis_fixture is not None and genesis_state_after != genesis_state_before:
            result.problems.append(
                f"the genesis fixture's state changed unexpectedly: "
                f"{genesis_state_before!r} -> {genesis_state_after!r}"
            )
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
        if h._genesis_fixture_cm is not None:  # noqa: SLF001 - this module owns the field
            h._genesis_fixture_cm.__exit__(None, None, None)


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
