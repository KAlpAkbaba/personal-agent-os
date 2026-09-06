"""A research explanation never becomes a second crawl (docs/DECISIONS.md ADR-0075).

The owner's real run, 2026-09-06/07: a research had completed ("Son üç gündeki OpenAI
ile ilgili gelişmeler"), the owner opened a NEW /core voice session and said only
"Teknik anlat." The router resolved the technical intent, the model called
activity.explain at the technical level — correctly — AND ALSO called research.start,
which began a second crawl nobody asked for.

These tests are the harness for the fix, against ONE completed research fixture:

* the five follow-up phrases start no crawl, even when the model tries: the server's
  own tool relay refuses research.start on that turn;
* the explanation binds to the FIXTURE's research job and artifact, reuses its report
  row untouched, and speaks that report's diagnostics;
* "Araştırmayı yeniden yap." — the one phrase that asks for a re-run — still creates
  exactly one new research, through the real start path.

Fixture shape mirrors ``test_voice_research_start.py``'s ``wired``: offline, SQLite, a
recording sideband standing in for the device WebSocket, and no browser anywhere.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.main import create_app
from app.narration.models import NarrationSession, PronunciationEntry
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
from app.voice.intents import (
    RESEARCH_CLASS_FOLLOWUP,
    RESEARCH_CLASS_RETRY,
    RESEARCH_CLASS_TECHNICAL_EXPLANATION,
)
from app.voice.models import VoiceProfile
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from app.voice.realtime_sessions.research_announcer import ResearchToolCallAnnouncer
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.service import ACTION_RESEARCH_START_REFUSED
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.realtime_sessions.tools import (
    REASON_RESEARCH_FOLLOWUP_TURN,
    RESEARCH_FOLLOWUP_REFUSED_TR,
)
from app.voice.simulator import SimulatedRealtimeProvider
from tests.identity_support import IDENTITY_TABLES

VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"

FIXTURE_TOPIC = "Son üç gündeki OpenAI ile ilgili gelişmeler"

#: The owner's real report shape: three findings, three sources, and the pipeline's own
#: stats. Nothing here is recomputed by an explanation - a follow-up READS this.
REPORT_JSON = {
    "topic": FIXTURE_TOPIC,
    "findings": [
        {
            "id": "f1",
            "title": "Birinci Bulgu",
            "summary": "Kaynak: Yayın Bir — modelin bağlam penceresi büyüdü.",
            "why_it_matters": "Uzun belgeler tek seferde işlenebiliyor.",
            "importance": 5,
            "label": "source_fact",
            "evidence_ids": ["e1"],
        },
        {
            "id": "f2",
            "title": "İkinci Bulgu",
            "summary": "Kaynak: Yayın İki — araç kullanımı yaygınlaştı.",
            "why_it_matters": "Ajan mimarileri sadeleşiyor.",
            "importance": 4,
            "label": "source_fact",
            "evidence_ids": ["e2"],
        },
        {
            "id": "f3",
            "title": "Üçüncü Bulgu",
            "summary": "Kaynak: Yayın Üç — fiyatlandırma değişti.",
            "why_it_matters": "Maliyet planı gözden geçirilmeli.",
            "importance": 3,
            "label": "source_fact",
            "evidence_ids": ["e3"],
        },
    ],
    "sources": [
        {"id": "e1", "title": "Yayın Bir", "url": "https://bir.example.com/a"},
        {"id": "e2", "title": "Yayın İki", "url": "https://iki.example.com/b"},
        {"id": "e3", "title": "Yayın Üç", "url": "https://uc.example.com/c"},
    ],
    "stats": {
        "discovered": 242,
        "fetched": 41,
        "rejected": 28,
        "evidence": 11,
        "rejected_by_reason": {"interstitial": 11, "duplicate": 6},
        "synthesis_provider": "fake",
    },
}

#: The five phrases the owner may say after a research completes. Not one of them is a
#: request for a new crawl, and the class the ONE router gives each is pinned here too.
FOLLOWUP_PHRASES: tuple[tuple[str, str], ...] = (
    ("Teknik anlat.", RESEARCH_CLASS_TECHNICAL_EXPLANATION),
    ("Hangi sayfalar elendi?", RESEARCH_CLASS_TECHNICAL_EXPLANATION),
    ("Kaynakları söyle.", RESEARCH_CLASS_FOLLOWUP),
    ("Birinci bulguyu detaylandır.", RESEARCH_CLASS_FOLLOWUP),
    ("Neden önemli?", RESEARCH_CLASS_FOLLOWUP),
)

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
    # ADR-0076: the durable focus and the one open clarification.
    ResearchFocusRow.__table__,
    ResearchOwnerStateRow.__table__,
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


# --------------------------------------------------------------------- helpers


def _create(client) -> str:
    response = client.post("/v1/voice/realtime/sessions", json={})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _say(client, sid: str, text: str, *, turn: int = 1, t_ms: int = 1000) -> dict:
    """One owner utterance, resolved SERVER-side (the client only transcribes)."""
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "utterance", "t_ms": t_ms, "turn": turn, "text": text}]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _tool(client, sid: str, call_id: str, name: str, arguments: dict) -> dict:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": call_id, "name": name, "arguments": arguments},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _research_task_ids(runtime) -> list[str]:
    with runtime.session() as db:
        return [str(t) for t in db.execute(select(ResearchRunRow.task_id)).scalars().all()]


def _complete_a_research(client, runtime, artifacts, *, topic: str = FIXTURE_TOPIC) -> tuple:
    """One completed research, through the REAL start path, made terminal by hand.

    Returns ``(task_id, artifact_id)``. The pipeline itself never runs here (no browser,
    no Temporal): what it would have LEFT BEHIND is written - a ready run, a report row
    with an artifact, and the ``research.completed`` ledger event
    ``app.ledger.service.build_research_completed_event`` writes for it.
    """
    sid = _create(client)
    with _patched_temporal_client():
        started = _tool(client, sid, "start-1", "research.start", {"topic": topic})
    assert started["status"] == "running", started
    task_id = uuid.UUID(started["result"]["task_id"])

    with runtime.session() as db:
        report_artifact = artifact_service.get_or_create_artifact_for_task(
            db, task_id=None, title=f"Araştırma raporu — {topic}", kind="research_report"
        )
        db.commit()
        artifact_id = report_artifact.id
        runs_service.upsert_report(
            db, task_id, report_json=REPORT_JSON, synthesis_provider="fake"
        )
        runs_service.set_report_artifact(db, task_id, artifact_id)
        runs_service.update_run(db, task_id, stage=STAGE_READY, event={"stage": STAGE_READY})
        ledger_service.record(
            db,
            ledger_service.build_research_completed_event(
                task_id=task_id,
                occurred_at=datetime.now(UTC),
                report_json=REPORT_JSON,
                artifact_id=artifact_id,
            ),
        )
        db.commit()

    announcer = ResearchToolCallAnnouncer(runtime.session, RecordingSideband(deliver=True))
    assert announcer.sweep_once() == 1
    return str(task_id), str(artifact_id)


def _report_snapshot(runtime, task_id: str) -> tuple:
    with runtime.session() as db:
        row = db.get(ResearchReportRow, uuid.UUID(task_id))
        assert row is not None
        return dict(row.report_json), str(row.artifact_id), row.synthesis_provider


# ------------------------------------------------- the five follow-up phrases


@pytest.mark.parametrize(("phrase", "research_class"), FOLLOWUP_PHRASES)
def test_a_followup_phrase_never_starts_a_crawl_and_reuses_the_completed_report(
    wired, phrase: str, research_class: str
) -> None:
    """The owner's defect, phrase by phrase.

    A NEW voice session (no plan, nothing attached — exactly the owner's situation), the
    phrase, and then a model that tries BOTH tools. research.start is refused by the
    server; activity.explain answers from the completed run, by identity.
    """
    client, runtime, sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    task_id, artifact_id = _complete_a_research(client, runtime, artifacts)
    report_before = _report_snapshot(runtime, task_id)
    tasks_before = _research_task_ids(runtime)

    sid = _create(client)  # a NEW session: no plan, no briefing, no narration
    events = _say(client, sid, phrase)
    assert events["resolved_intents"][0]["research_class"] == research_class

    # The model tries to start a research anyway. The SERVER refuses, on this turn.
    refused = _tool(client, sid, "c-start", "research.start", {"topic": phrase})
    assert refused["status"] == "succeeded", refused  # the CALL succeeded; the crawl did not
    result = refused["result"]
    assert result["status"] == "refused"
    assert result["reason"] == REASON_RESEARCH_FOLLOWUP_TURN
    assert result["research_class"] == research_class
    assert result["research_job_id"] == task_id
    assert result["research_artifact_id"] == artifact_id
    assert result["speech"] == RESEARCH_FOLLOWUP_REFUSED_TR

    # research.start calls that reached the handler: none. New research tasks: none.
    assert _research_task_ids(runtime) == tasks_before
    with runtime.session() as db:
        assert db.get(ResearchRunRow, uuid.UUID(task_id)).stage == STAGE_READY

    # The explanation is about the FIXTURE's run, by id - never a re-run, never a guess.
    explained = _tool(client, sid, "c-explain", "activity.explain", {"question": phrase})
    assert explained["status"] == "succeeded", explained
    answer = explained["result"]
    assert answer["research_job_id"] == task_id
    assert answer["research_artifact_id"] == artifact_id
    assert answer["provenance"]["research_job_id"] == task_id
    assert answer["provenance"]["research_artifact_id"] == artifact_id
    assert answer["cognition"]["research_job_id"] == task_id
    assert answer["research_binding"]["research_job_id"] == task_id
    assert answer["speech"]

    # The report row is untouched: same ids, same body, same provider. Findings were
    # READ, never recomputed - and no second crawl produced a second report.
    assert _report_snapshot(runtime, task_id) == report_before
    assert report_before[0] == REPORT_JSON
    with runtime.session() as db:
        assert db.execute(select(ResearchReportRow)).scalars().all().__len__() == 1


def test_the_technical_answer_speaks_the_completed_runs_own_diagnostics(wired) -> None:
    """"Teknik anlat." / "Hangi sayfalar elendi?" answer with THAT run's numbers.

    Not recomputed, not from a fresh crawl: 242 discovered / 41 fetched / 28 rejected are
    the fixture report's own stats, and the rejection reasons are its own too.
    """
    client, runtime, sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    task_id, _artifact_id = _complete_a_research(client, runtime, artifacts)

    sid = _create(client)
    _say(client, sid, "Hangi sayfalar elendi?")
    answer = _tool(
        client, sid, "c-1", "activity.explain", {"question": "Hangi sayfalar elendi?"}
    )["result"]
    assert answer["level"] == "technical"
    assert answer["research_job_id"] == task_id
    # Spoken, so the narration normaliser has already turned the digits into Turkish
    # words - these ARE 242 / 41 / 28, said the way the owner hears them.
    speech = answer["speech"]
    assert "iki yüz kırk iki aday keşfedildi" in speech, speech
    assert "kırk bir sayfa getirildi" in speech, speech
    assert "yirmi sekiz sayfa elendi" in speech, speech
    facts = answer["provenance"]["facts"]
    assert facts["discovered"] == 242 and facts["rejected"] == 28
    assert facts["rejected_by_reason"] == {"interstitial": 11, "duplicate": 6}


def test_the_refusal_is_audited_and_readable_from_the_session_record(wired) -> None:
    client, runtime, sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    task_id, artifact_id = _complete_a_research(client, runtime, artifacts)

    sid = _create(client)
    _say(client, sid, "Teknik anlat.")
    _tool(client, sid, "c-start", "research.start", {"topic": "Teknik anlat."})

    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()
    refused_call = next(c for c in activity["tool_calls"] if c["call_id"] == "c-start")
    assert refused_call["status"] == "succeeded"
    assert refused_call["speech_head"] == RESEARCH_FOLLOWUP_REFUSED_TR[:80]
    assert activity["intents"][0]["research_class"] == RESEARCH_CLASS_TECHNICAL_EXPLANATION

    with runtime.session() as db:
        audits = (
            db.execute(
                select(AuditEvent).where(AuditEvent.action == ACTION_RESEARCH_START_REFUSED)
            )
            .scalars()
            .all()
        )
    assert len(audits) == 1
    meta = audits[0].metadata_json
    assert meta["reason"] == REASON_RESEARCH_FOLLOWUP_TURN
    assert meta["research_job_id"] == task_id
    assert meta["research_artifact_id"] == artifact_id
    assert meta["research_class"] == RESEARCH_CLASS_TECHNICAL_EXPLANATION


# ------------------------------------------------------------- the one re-run


def test_an_explicit_rerun_creates_exactly_one_new_research(wired) -> None:
    """"Araştırmayı yeniden yap." is the ONE phrase here that may crawl, and it goes
    through the real start path - a task, a run row, a workflow request."""
    client, runtime, sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    task_id, _artifact_id = _complete_a_research(client, runtime, artifacts)
    tasks_before = _research_task_ids(runtime)

    sid = _create(client)
    events = _say(client, sid, "Araştırmayı yeniden yap.")
    assert events["resolved_intents"][0]["research_class"] == RESEARCH_CLASS_RETRY

    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    with patch("app.research.service.Client.connect", AsyncMock(return_value=fake_client)):
        started = _tool(client, sid, "c-retry", "research.start", {"topic": FIXTURE_TOPIC})
    assert started["status"] == "running", started
    new_task_id = started["result"]["task_id"]
    assert new_task_id != task_id

    after = _research_task_ids(runtime)
    assert len(after) == len(tasks_before) + 1
    assert set(after) - set(tasks_before) == {new_task_id}
    assert fake_client.start_workflow.await_count == 1


def test_a_new_topic_after_a_completed_research_still_crawls(wired) -> None:
    """The guard refuses FOLLOW-UPS, not research. A new topic is a new research."""
    client, runtime, sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    _task_id, _artifact_id = _complete_a_research(client, runtime, artifacts)
    tasks_before = _research_task_ids(runtime)

    sid = _create(client)
    events = _say(client, sid, "Son üç gündeki AI agent gelişmelerini araştır.")
    assert events["resolved_intents"][0]["research_class"] == "new_research"
    with _patched_temporal_client():
        started = _tool(
            client, sid, "c-new", "research.start", {"topic": "AI agent gelişmeleri"}
        )
    assert started["status"] == "running", started
    assert len(_research_task_ids(runtime)) == len(tasks_before) + 1


# ------------------------------------------------------------------ ambiguity


def test_two_completed_researches_bind_to_the_one_the_owner_last_heard(wired) -> None:
    """ADR-0075 asked a question here; ADR-0076 answers it, and that is the point.

    Two completed runs and a FRESH session used to be ambiguous: nothing in the server
    could say which one the owner meant, so it asked - and in the owner's real run it
    asked six times, because nothing could hear the answer either. The durable focus is
    the missing half: the research whose result was last announced is the one "Teknik
    anlat." is about, in a session that has never heard of it. Still no crawl, and still
    no guess: the focus is a durable row that says why it is the focus.
    """
    client, runtime, sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    _complete_a_research(client, runtime, artifacts, topic=FIXTURE_TOPIC)
    second_task_id, second_artifact_id = _complete_a_research(
        client, runtime, artifacts, topic="Yerel modellerin son durumu"
    )
    tasks_before = _research_task_ids(runtime)

    sid = _create(client)  # a fresh session: neither run was started here
    _say(client, sid, "Teknik anlat.")

    refused = _tool(client, sid, "c-start", "research.start", {"topic": "Teknik anlat."})
    assert refused["result"]["status"] == "refused"
    assert refused["result"]["ambiguous"] is False
    assert refused["result"]["research_job_id"] == second_task_id
    assert refused["result"]["speech"] == RESEARCH_FOLLOWUP_REFUSED_TR
    assert _research_task_ids(runtime) == tasks_before

    answer = _tool(client, sid, "c-explain", "activity.explain", {"question": "Teknik anlat."})[
        "result"
    ]
    assert answer["research_job_id"] == second_task_id
    assert answer["research_artifact_id"] == second_artifact_id
    assert answer["resolution_reason"] == "current_focus"
    assert answer["speech"]
    assert _research_task_ids(runtime) == tasks_before


def test_the_session_that_started_the_research_binds_to_its_own_run(wired) -> None:
    """The session that started a research and saw it finish is bound to THAT run.

    Under ADR-0075 the binding basis was literally "session" - a linkage that died with
    the session. Under ADR-0076 the same run wins for a better reason: hearing its result
    announced put it in focus, durably, and the focus outlives the session that made it.
    """
    client, runtime, sideband, broker, artifacts = wired
    _enroll_online_device(broker)
    _complete_a_research(client, runtime, artifacts, topic="Yerel modellerin son durumu")

    # This session starts, and sees complete, its own research.
    sid = _create(client)
    with _patched_temporal_client():
        started = _tool(client, sid, "own-1", "research.start", {"topic": FIXTURE_TOPIC})
    own_task_id = uuid.UUID(started["result"]["task_id"])
    with runtime.session() as db:
        report_artifact = artifact_service.get_or_create_artifact_for_task(
            db, task_id=None, title="Araştırma raporu — kendi", kind="research_report"
        )
        db.commit()
        runs_service.upsert_report(
            db, own_task_id, report_json=REPORT_JSON, synthesis_provider="fake"
        )
        runs_service.set_report_artifact(db, own_task_id, report_artifact.id)
        runs_service.update_run(
            db, own_task_id, stage=STAGE_READY, event={"stage": STAGE_READY}
        )
        ledger_service.record(
            db,
            ledger_service.build_research_completed_event(
                task_id=own_task_id,
                occurred_at=datetime.now(UTC),
                report_json=REPORT_JSON,
                artifact_id=report_artifact.id,
            ),
        )
        db.commit()
    assert ResearchToolCallAnnouncer(runtime.session, sideband).sweep_once() == 1

    state = client.get(f"/v1/voice/realtime/sessions/{sid}").json()
    assert state["last_research"]["research_job_id"] == str(own_task_id)

    _say(client, sid, "Teknik anlat.", turn=2, t_ms=9000)
    refused = _tool(client, sid, "c-start", "research.start", {"topic": "Teknik anlat."})
    assert refused["result"]["research_job_id"] == str(own_task_id)
    assert refused["result"]["binding_basis"] == "current_focus"
    assert refused["result"]["focus_source"] == "result_just_spoken"
