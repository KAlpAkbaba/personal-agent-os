"""A research is a thing the owner points at (docs/DECISIONS.md ADR-0076).

The owner's real record, 2026-09-06 evening. Two completed runs with the SAME title
("Son üç gündeki OpenAI ile ilgili gelişmeler", 19:53Z and 20:18Z), then a third on a
near-identical topic at 21:08Z. On contract v6 a fresh session's "Teknik anlat." started
a second crawl and explained THAT run. On v7 the same phrase produced the clarification
"Efendim, iki tamamlanmış araştırmam var: «…» ve «…». Hangisini anlatayım?" six times in
a row, 21:05:00–21:07:35, because nothing in the server could accept the owner's spoken
answer — and a page reload started a new WebRTC session with no focus at all.

These tests are the harness for the architectural fix, and the fixtures are deliberately
the hard case: A and B share the IDENTICAL topic "OpenAI son gelişmeler", with different
job ids and different artifacts, B completed after A. A title cannot tell them apart. The
whole point is that nothing here ever tries to.

Everything runs under the CANONICAL router and the real tool relay: utterances go through
``POST /events`` (Cloud Core resolves; the client only transcribes) and tool calls through
``POST /tool-calls`` (the guard runs before any handler). Offline, SQLite, no browser, no
Temporal.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.receipt import contains_bookkeeping, contains_fake_completion
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
from app.research import focus as research_focus
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
from app.research.reference import MISSING_QUESTION_TR
from app.voice.models import VoiceProfile
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from app.voice.realtime_sessions.research_announcer import ResearchToolCallAnnouncer
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.realtime_sessions.tools import (
    RESEARCH_EMPTY_ANSWER_TR,
    RESEARCH_FOLLOWUP_REFUSED_TR,
    RESEARCH_NO_REPORT_TR,
)
from app.voice.simulator import SimulatedRealtimeProvider
from tests.identity_support import IDENTITY_TABLES

VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"

#: The owner's own trap: two runs, one title. Identity has to come from somewhere else.
SHARED_TOPIC = "OpenAI son gelişmeler"


def _report(topic: str, *, marker: str) -> dict:
    """A report body that is IDENTIFIABLE without being distinguishable by title.

    ``marker`` rides inside the findings so a test can prove WHICH report was read even
    though both reports answer to the same name.
    """
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
    ResearchFocusRow.__table__,
    ResearchOwnerStateRow.__table__,
)


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


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

    issued = identity.service.issue_session(
        client_kind="desktop", label="pc", device_id=uuid.uuid4()
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issued.token}"
    try:
        yield client, runtime, sideband, artifacts
    finally:
        client.close()


# --------------------------------------------------------------------- helpers


def _create(client) -> str:
    response = client.post("/v1/voice/realtime/sessions", json={})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _say(client, sid: str, text: str, *, turn: int = 1, t_ms: int = 1000) -> dict:
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


def _run_ids(runtime) -> list[str]:
    with runtime.session() as db:
        return sorted(str(t) for t in db.execute(select(ResearchRunRow.task_id)).scalars().all())


def _complete(client, runtime, *, topic: str, marker: str, ready_at: datetime) -> tuple[str, str]:
    """One completed research, through the REAL start path, made terminal by hand.

    ``ready_at`` is written onto the task row, because that is what the owner hears back
    as "bugün 20:19'daki" and what the resolver compares a spoken time against.
    """
    sid = _create(client)
    with _patched_temporal_client():
        started = _tool(client, sid, f"start-{marker}", "research.start", {"topic": topic})
    assert started["status"] == "running", started
    task_id = uuid.UUID(started["result"]["task_id"])
    report_json = _report(topic, marker=marker)

    with runtime.session() as db:
        artifact = artifact_service.get_or_create_artifact_for_task(
            db, task_id=None, title=f"Araştırma raporu — {marker}", kind="research_report"
        )
        db.commit()
        artifact_id = artifact.id
        runs_service.upsert_report(db, task_id, report_json=report_json, synthesis_provider="fake")
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

    announcer = ResearchToolCallAnnouncer(runtime.session, RecordingSideband(deliver=True))
    assert announcer.sweep_once() == 1
    return str(task_id), str(artifact_id)


@pytest.fixture()
def two_same_title(wired):
    """A and B: one title, two jobs, two artifacts, B finished after A."""
    client, runtime, _sideband, _artifacts = wired
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    a = _complete(client, runtime, topic=SHARED_TOPIC, marker="A", ready_at=base)
    b = _complete(
        client, runtime, topic=SHARED_TOPIC, marker="B", ready_at=base + timedelta(minutes=25)
    )
    return a, b


def _report_snapshot(runtime, task_id: str) -> tuple:
    with runtime.session() as db:
        row = db.get(ResearchReportRow, uuid.UUID(task_id))
        assert row is not None
        return dict(row.report_json), str(row.artifact_id), row.synthesis_provider


def _clear_focus(runtime) -> None:
    """Remove every focus row: the pre-ADR-0076 world, and the only way to make two
    same-title runs GENUINELY ambiguous now that a completion focuses one."""
    with runtime.session() as db:
        db.execute(delete(ResearchFocusRow))
        db.commit()


# ------------------------------------------------------- the completion focuses


def test_a_completed_research_becomes_the_focus(wired, two_same_title) -> None:
    client, runtime, _sideband, _artifacts = wired
    (a_task, _a_art), (b_task, b_art) = two_same_title

    focus = client.get("/v1/research/focus").json()
    assert focus["current"]["research_job_id"] == b_task
    assert focus["current"]["artifact_id"] == b_art
    assert focus["current"]["source_of_focus"] == "result_just_spoken"
    assert focus["previous"]["research_job_id"] == a_task
    assert [e["research_job_id"] for e in focus["stack"]] == [b_task, a_task]
    assert focus["pending_clarification"] is None
    # Same title on both: the list is ordered by WHEN, and identified by id.
    assert {e["topic"] for e in focus["stack"]} == {SHARED_TOPIC}


def test_the_research_list_says_which_row_is_the_focus(wired, two_same_title) -> None:
    client, _runtime, _sideband, _artifacts = wired
    (a_task, _a_art), (b_task, _b_art) = two_same_title

    rows = {r["task_id"]: r for r in client.get("/v1/research").json()["tasks"]}
    assert rows[b_task]["is_focus"] is True
    assert rows[a_task]["is_focus"] is False
    for row in (rows[a_task], rows[b_task]):
        assert row["completed_at"] == row["ready_at"]
        assert row["source_count"] == 2
        assert "mode" in row


# ------------------------------------------------------- deictic and anaphoric


@pytest.mark.parametrize(
    "phrase",
    [
        "Bunu anlat.",
        "Teknik anlat.",
        "Bunun kaynaklarını söyle.",
        "Bu araştırmayı anlat.",
        "Az önceki araştırmayı anlat.",
        "Onu anlat.",
        "Son araştırmayı anlat.",
    ],
)
def test_a_deictic_follow_up_resolves_to_the_focused_run(wired, two_same_title, phrase) -> None:
    """Every way the owner points at "this one" reaches B - the run last announced -
    even though A carries the identical title."""
    client, runtime, _sideband, _artifacts = wired
    (a_task, _a_art), (b_task, b_art) = two_same_title
    before = _report_snapshot(runtime, b_task)
    runs_before = _run_ids(runtime)

    sid = _create(client)  # a NEW session: no plan, no briefing, nothing attached
    _say(client, sid, phrase)
    result = _tool(client, sid, "c-1", "research.explain", {"level": "executive"})["result"]

    assert result["status"] == "ok", result
    assert result["research_job_id"] == b_task
    assert result["research_artifact_id"] == b_art
    assert result["research_job_id"] != a_task
    assert "B" in result["speech"] and "A birinci bulgu" not in result["speech"]
    # A follow-up READS. Nothing was crawled, and the report row is byte-identical.
    assert _run_ids(runtime) == runs_before
    assert _report_snapshot(runtime, b_task) == before


def test_the_previous_research_is_reachable_and_then_becomes_current(
    wired, two_same_title
) -> None:
    """"Bir önceki araştırmayı anlat." -> A. And then "bunu" means A, because the
    conversation moved: a resolved reference IS a focus change (followup_reference)."""
    client, runtime, _sideband, _artifacts = wired
    (a_task, a_art), (b_task, _b_art) = two_same_title
    runs_before = _run_ids(runtime)

    sid = _create(client)
    _say(client, sid, "Bir önceki araştırmayı anlat.")
    result = _tool(client, sid, "c-1", "research.explain", {"level": "executive"})["result"]
    assert result["research_job_id"] == a_task
    assert result["research_artifact_id"] == a_art
    assert result["resolution_reason"] == "previous_focus"
    assert result["research_reference"] == "previous"
    assert "A birinci bulgu" in result["speech"]

    focus = client.get("/v1/research/focus").json()
    assert focus["current"]["research_job_id"] == a_task
    assert focus["current"]["source_of_focus"] == "followup_reference"
    assert focus["previous"]["research_job_id"] == b_task

    _say(client, sid, "Bunun kaynaklarını söyle.", turn=2, t_ms=5000)
    sources = _tool(client, sid, "c-2", "research.sources", {})["result"]
    assert sources["research_job_id"] == a_task
    assert "A Yayın Bir" in sources["speech"]
    assert _run_ids(runtime) == runs_before


def test_an_ordinal_counts_distinct_researches_not_rows(wired, two_same_title) -> None:
    client, _runtime, _sideband, _artifacts = wired
    (a_task, _a_art), (b_task, _b_art) = two_same_title

    sid = _create(client)
    _say(client, sid, "İkinci araştırmayı anlat.")
    result = _tool(client, sid, "c-1", "research.explain", {})["result"]
    assert result["research_job_id"] == a_task
    assert result["resolution_reason"] == "ordinal_focus"
    assert result["research_reference"] == "ordinal"

    # 1 is the current focus - which the line above has just moved to A.
    _say(client, sid, "Birinci araştırmayı anlat.", turn=2, t_ms=5000)
    first = _tool(client, sid, "c-2", "research.explain", {})["result"]
    assert first["research_job_id"] == a_task
    assert b_task != a_task


def test_a_new_session_with_no_context_still_resolves_the_durable_focus(
    wired, two_same_title
) -> None:
    """The page-reload case. A fresh WebRTC session knows nothing; the focus does.

    This is the half ADR-0075 could not have: its binding lived in the voice session's
    own ``context_json`` and died with it.
    """
    client, runtime, _sideband, _artifacts = wired
    (_a_task, _a_art), (b_task, b_art) = two_same_title

    first = _create(client)
    _say(client, first, "Teknik anlat.")
    reloaded = _create(client)  # the reload: a different session id, no shared state
    assert reloaded != first
    _say(client, reloaded, "Teknik anlat.")
    result = _tool(client, reloaded, "c-1", "research.explain", {"level": "technical"})["result"]

    assert result["research_job_id"] == b_task
    assert result["research_artifact_id"] == b_art
    assert result["resolution_reason"] == "current_focus"
    # B's OWN numbers, read from B's report row - never recomputed, never A's.
    assert "yüz on bir aday keşfedildi" in result["speech"], result["speech"]
    with runtime.session() as db:
        assert db.get(RealtimeSessionRow, uuid.UUID(reloaded)).context_json.get("plan") is None


# ------------------------------------------------------------------ UI selects


def test_the_ui_selection_sets_the_focus_and_a_deictic_follows_it(
    wired, two_same_title
) -> None:
    client, _runtime, _sideband, _artifacts = wired
    (a_task, a_art), (b_task, _b_art) = two_same_title

    response = client.post(f"/v1/research/{a_task}/focus")
    assert response.status_code == 200, response.text
    entry = response.json()["focus"]
    assert entry["research_job_id"] == a_task
    assert entry["source_of_focus"] == "owner_selected_in_ui"

    sid = _create(client)
    _say(client, sid, "Bunu anlat.")
    result = _tool(client, sid, "c-1", "research.explain", {})["result"]
    assert result["research_job_id"] == a_task
    assert result["research_artifact_id"] == a_art
    assert result["focus_source"] == "owner_selected_in_ui"
    assert result["research_job_id"] != b_task


def test_focus_on_an_unfinished_or_unknown_research_is_refused(wired) -> None:
    client, runtime, _sideband, _artifacts = wired
    assert client.post(f"/v1/research/{uuid.uuid4()}/focus").status_code == 404

    sid = _create(client)
    with _patched_temporal_client():
        started = _tool(client, sid, "s-1", "research.start", {"topic": "Henüz bitmedi"})
    running_task = started["result"]["task_id"]
    response = client.post(f"/v1/research/{running_task}/focus")
    assert response.status_code == 409
    assert response.json() == {"error": "not_completed"}
    with runtime.session() as db:
        assert db.execute(select(ResearchFocusRow)).scalars().all() == []


# ------------------------------------------------------------------- ambiguity


def test_true_ambiguity_asks_once_and_the_spoken_answer_lands(wired, two_same_title) -> None:
    """No focus, no selection, two same-title reports: ONE question, then an ANSWER.

    The owner's 21:05–21:07 record is this test's negative: the clarification was asked
    six times because nothing could hear "20:19'daki". Here it is asked once, remembered
    durably, and answered by voice.
    """
    client, runtime, _sideband, _artifacts = wired
    (a_task, _a_art), (b_task, _b_art) = two_same_title
    _clear_focus(runtime)
    runs_before = _run_ids(runtime)

    sid = _create(client)
    _say(client, sid, "OpenAI araştırmasını anlat.")
    asked_call = _tool(client, sid, "c-1", "research.explain", {})
    # ADR-0077: a question is its own terminal status - never a succeeded call.
    assert asked_call["status"] == "needs_clarification", asked_call
    asked = asked_call["result"]

    assert asked["status"] == "needs_clarification"
    assert asked["ambiguous"] is True
    assert asked["research_job_id"] is None
    assert len(asked["candidates"]) == 2
    assert asked["speech"].startswith("Aynı konuda iki araştırmanız var:")
    assert "bugün" in asked["speech"] or "dün" in asked["speech"]
    # Not a title anywhere in the question: both candidates answer to the same one.
    assert SHARED_TOPIC not in asked["speech"]
    # No report was chosen, and nothing was crawled.
    assert _run_ids(runtime) == runs_before

    pending = client.get("/v1/research/focus").json()["pending_clarification"]
    assert pending["question"] == asked["speech"]
    assert {c["research_job_id"] for c in pending["candidates"]} == {a_task, b_task}

    # The owner answers by voice, with the time the question offered.
    newer_hhmm = max(
        (c for c in pending["candidates"]),
        key=lambda c: c["completed_at"],
    )
    with runtime.session() as db:
        entry = research_focus.describe(db, newer_hhmm["research_job_id"])
    from app.research.reference import local_hhmm

    spoken_time = local_hhmm(entry.completed_at)
    _say(client, sid, f"{spoken_time}'daki.", turn=2, t_ms=6000)
    answered = _tool(client, sid, "c-2", "research.explain", {})["result"]

    assert answered["status"] == "ok", answered
    assert answered["research_job_id"] == newer_hhmm["research_job_id"]
    assert answered["resolution_reason"] == "owner_selected_by_voice"
    assert answered["research_reference"] == "selection"
    assert _run_ids(runtime) == runs_before
    # The question is spent: it is not asked a second time.
    assert client.get("/v1/research/focus").json()["pending_clarification"] is None


def test_the_ambiguity_question_names_local_times_and_says_yesterday(wired) -> None:
    """The wording itself, against the owner's own pair of times."""
    from app.research.focus import FocusEntry
    from app.research.reference import OWNER_TZ, ambiguity_question

    now = datetime(2026, 9, 6, 21, 5, tzinfo=OWNER_TZ)
    newer = FocusEntry(
        research_job_id=str(uuid.uuid4()),
        topic=SHARED_TOPIC,
        completed_at=datetime(2026, 9, 6, 20, 19, tzinfo=OWNER_TZ),
    )
    older = FocusEntry(
        research_job_id=str(uuid.uuid4()),
        topic=SHARED_TOPIC,
        completed_at=datetime(2026, 9, 6, 19, 53, tzinfo=OWNER_TZ),
    )
    assert ambiguity_question((newer, older), now=now) == (
        "Aynı konuda iki araştırmanız var: bugün 20:19'daki mı, yoksa 19:53'teki mi?"
    )

    yesterday = FocusEntry(
        research_job_id=str(uuid.uuid4()),
        topic=SHARED_TOPIC,
        completed_at=datetime(2026, 9, 5, 19, 53, tzinfo=OWNER_TZ),
    )
    question = ambiguity_question((newer, yesterday), now=now)
    assert "bugün 20:19'daki" in question and "dün 19:53'teki" in question


def test_a_shared_title_is_not_ambiguous_once_something_points_at_a_run(
    wired, two_same_title
) -> None:
    """The rule the owner's directive states outright: identity is contextual before it
    is textual. Two runs share a title; one of them is in focus; there is no question."""
    client, _runtime, _sideband, _artifacts = wired
    (_a_task, _a_art), (b_task, _b_art) = two_same_title

    sid = _create(client)
    _say(client, sid, "OpenAI araştırmasını anlat.")
    result = _tool(client, sid, "c-1", "research.explain", {})["result"]
    assert result["status"] == "ok"
    assert result["research_job_id"] == b_task
    assert result["resolution_reason"] == "current_focus"


# ------------------------------------------------------------------- the guard


def test_a_deictic_turn_with_no_research_at_all_asks_rather_than_crawls(wired) -> None:
    """The v6 defect, with the history emptied: "Teknik anlat." on a system that has
    never researched anything must NOT become a crawl.

    ADR-0075's guard stood aside here (nothing to bind), and that is exactly how a
    question became a research. Proven structurally: zero run rows before, zero after,
    and no task created after the utterance.
    """
    client, runtime, _sideband, _artifacts = wired
    with runtime.session() as db:
        assert db.execute(select(ResearchRunRow)).scalars().all() == []

    sid = _create(client)
    said_at = datetime.now(UTC)
    _say(client, sid, "Teknik anlat.")
    refused = _tool(client, sid, "c-start", "research.start", {"topic": "Teknik anlat."})

    assert refused["status"] == "succeeded", refused  # the CALL succeeded; the crawl did not
    assert refused["result"]["status"] == "refused"
    assert refused["result"]["speech"] == MISSING_QUESTION_TR
    assert refused["result"]["research_job_id"] is None
    with runtime.session() as db:
        assert db.execute(select(ResearchRunRow)).scalars().all() == []
        later = [
            t
            for t in db.execute(select(Task)).scalars().all()
            if (t.created_at or said_at).replace(tzinfo=UTC) >= said_at.replace(microsecond=0)
        ]
        assert later == [], later

    # The same is true of the follow-up tools: a question, never an invention.
    answer = _tool(client, sid, "c-explain", "research.explain", {})["result"]
    assert answer["status"] == "needs_clarification"
    assert answer["speech"] == MISSING_QUESTION_TR
    with runtime.session() as db:
        assert db.execute(select(ResearchRunRow)).scalars().all() == []


@pytest.mark.parametrize(
    "phrase",
    ["Bunu anlat.", "Bir önceki araştırmayı anlat.", "İkinci araştırmayı anlat."],
)
def test_a_reference_turn_never_starts_a_crawl(wired, two_same_title, phrase) -> None:
    client, runtime, _sideband, _artifacts = wired
    runs_before = _run_ids(runtime)

    sid = _create(client)
    _say(client, sid, phrase)
    refused = _tool(client, sid, "c-start", "research.start", {"topic": phrase})
    assert refused["result"]["status"] == "refused"
    assert refused["result"]["speech"] == RESEARCH_FOLLOWUP_REFUSED_TR
    assert _run_ids(runtime) == runs_before


def test_the_turn_after_a_clarification_is_not_a_crawl_either(wired, two_same_title) -> None:
    """An open question is not an invitation to research something."""
    client, runtime, _sideband, _artifacts = wired
    _clear_focus(runtime)
    runs_before = _run_ids(runtime)

    sid = _create(client)
    _say(client, sid, "OpenAI araştırmasını anlat.")
    assert _tool(client, sid, "c-1", "research.explain", {})["result"]["ambiguous"] is True

    _say(client, sid, "İkincisi.", turn=2, t_ms=6000)
    refused = _tool(client, sid, "c-2", "research.start", {"topic": "OpenAI"})
    assert refused["result"]["status"] == "refused"
    assert _run_ids(runtime) == runs_before
    # And the answer still lands afterwards: the guard did not spend it.
    answered = _tool(client, sid, "c-3", "research.explain", {})["result"]
    assert answered["status"] == "ok"
    assert answered["resolution_reason"] == "owner_selected_by_voice"


def test_an_explicit_rerun_still_creates_exactly_one_new_research(wired, two_same_title) -> None:
    """The guard refuses references, not research. "Yeniden yap" is not a reference."""
    client, runtime, _sideband, _artifacts = wired
    runs_before = _run_ids(runtime)

    sid = _create(client)
    events = _say(client, sid, "Araştırmayı yeniden yap.")
    assert events["resolved_intents"][0]["research_class"] == "research_retry"

    with _patched_temporal_client():
        started = _tool(client, sid, "c-retry", "research.start", {"topic": SHARED_TOPIC})
    assert started["status"] == "running", started
    after = _run_ids(runtime)
    assert len(after) == len(runs_before) + 1
    assert set(after) - set(runs_before) == {started["result"]["task_id"]}


# ----------------------------------------------------------------- the answers


def test_the_finding_detail_tool_reads_one_finding_of_the_focused_run(
    wired, two_same_title
) -> None:
    client, runtime, _sideband, _artifacts = wired
    (_a_task, _a_art), (b_task, b_art) = two_same_title
    before = _report_snapshot(runtime, b_task)

    sid = _create(client)
    _say(client, sid, "Birinci bulguyu detaylandır.")
    result = _tool(client, sid, "c-1", "research.finding_detail", {"index": 1})["result"]
    assert result["research_job_id"] == b_task
    assert result["research_artifact_id"] == b_art
    assert result["finding_index"] == 1
    assert "B birinci bulgu" in result["speech"]
    assert "B Yayın Bir" in result["speech"]
    assert _report_snapshot(runtime, b_task) == before

    out_of_range = _tool(client, sid, "c-2", "research.finding_detail", {"index": 9})["result"]
    assert out_of_range["research_job_id"] == b_task
    assert "iki bulgu var" in out_of_range["speech"]


def test_no_answer_narrates_its_own_bookkeeping(wired, two_same_title) -> None:
    """"kayıtlarımı kontrol edeceğim" was what the owner heard instead of an answer."""
    client, _runtime, _sideband, _artifacts = wired
    sid = _create(client)
    spoken: list[str] = []
    for n, (name, args) in enumerate(
        (
            ("research.explain", {"level": "executive"}),
            ("research.explain", {"level": "detail"}),
            ("research.explain", {"level": "technical"}),
            ("research.explain", {"level": "full"}),
            ("research.sources", {}),
            ("research.finding_detail", {"index": 1}),
        )
    ):
        _say(client, sid, "Bunu anlat.", turn=n + 1, t_ms=1000 * (n + 1))
        spoken.append(_tool(client, sid, f"c-{n}", name, args)["result"]["speech"])
    for speech in spoken:
        assert speech.startswith("Bu araştırmada"), speech
        assert not contains_bookkeeping(speech), speech
        assert not contains_fake_completion(speech), speech


# ---------------------------------------------------------- the session record


def test_the_session_record_names_the_job_the_reference_and_the_focus_source(
    wired, two_same_title
) -> None:
    """What the owner harness reads off GET .../activity, from durable rows alone."""
    client, _runtime, _sideband, _artifacts = wired
    (_a_task, _a_art), (b_task, b_art) = two_same_title

    sid = _create(client)
    _say(client, sid, "Bunu teknik anlat.")
    _tool(client, sid, "c-1", "research.explain", {"level": "technical"})

    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()
    call = next(c for c in activity["tool_calls"] if c["call_id"] == "c-1")
    assert call["research_job_id"] == b_task
    assert call["research_artifact_id"] == b_art
    assert call["resolution_reason"] == "current_focus"
    assert call["focus_source"] == "result_just_spoken"
    assert call["speech_chars"] > 0

    intent = activity["intents"][0]
    assert intent["research_class"] == "research_technical_explanation"
    assert intent["research_reference"] == "current"


# ------------------------------------------------ the result contract (ADR-0077)
#
# The owner's fourth record (2026-09-06, sessions c3d88970 and 96f06af4): research.explain
# recorded a clarification as a SUCCEEDED call with no target; "Bunu teknik anlat." went to
# activity.explain, which resolved the model's paraphrase instead of the turn (previous_focus
# for "bunu"), narrated the LEDGER's telemetry instead of the report and attached a narration
# session; the next turn then became a cursor move inside that narration and never reached the
# previous research. Every test below runs the exact contract under the canonical router: the
# owner's words go through /events (the ONE router), the model's tool call follows.


def _row_status(runtime, sid: str, call_id: str) -> str:
    with runtime.session() as db:
        row = db.execute(
            select(RealtimeToolCall).where(
                RealtimeToolCall.session_id == uuid.UUID(sid), RealtimeToolCall.call_id == call_id
            )
        ).scalar_one()
        return row.status


def _recorded(client, sid: str, call_id: str) -> dict:
    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()
    return next(c for c in activity["tool_calls"] if c["call_id"] == call_id)


def test_a_clarification_is_its_own_terminal_status_never_a_success(
    wired, two_same_title
) -> None:
    """call_UdBzeEH85slFqbNQ: 'succeeded', no job, a question for a result. Never again."""
    client, runtime, _sideband, _artifacts = wired
    _clear_focus(runtime)

    sid = _create(client)
    _say(client, sid, "OpenAI araştırmasını anlat.")
    call = _tool(client, sid, "c-1", "research.explain", {"level": "technical"})

    assert call["status"] == "needs_clarification", call
    assert "error" not in call
    assert call["result"]["status"] == "needs_clarification"
    assert call["result"]["research_job_id"] is None
    assert call["result"]["speech"].startswith("Aynı konuda iki araştırmanız var:")
    assert _row_status(runtime, sid, "c-1") == "needs_clarification"

    recorded = _recorded(client, sid, "c-1")
    assert recorded["status"] == "needs_clarification"
    assert recorded["research_job_id"] is None
    assert recorded["speech_chars"] > 0


def test_a_succeeded_research_answer_names_its_target_and_speaks(wired, two_same_title) -> None:
    client, runtime, _sideband, _artifacts = wired
    (_a_task, _a_art), (b_task, b_art) = two_same_title

    sid = _create(client)
    _say(client, sid, "Bunu teknik anlat.")
    call = _tool(client, sid, "c-1", "research.explain", {"level": "technical"})

    assert call["status"] == "succeeded", call
    result = call["result"]
    assert result["status"] == "ok"
    assert result["research_job_id"] == b_task
    assert result["research_artifact_id"] == b_art
    assert result["speech"].startswith("Bu araştırmada")
    assert result["provenance"]["research_job_id"] == b_task
    assert result["provenance"]["research_artifact_id"] == b_art
    # B's own numbers (the fixture gives A and B different stats on purpose)
    assert result["provenance"]["facts"]["discovered"] == 111
    assert _row_status(runtime, sid, "c-1") == "succeeded"


def test_an_ok_without_words_is_recorded_as_failed_never_as_succeeded(
    wired, two_same_title, monkeypatch
) -> None:
    """The contract's own assertion, in the relay: no target or no words -> failed."""
    import app.research.answers as answers

    client, runtime, _sideband, _artifacts = wired
    (_a_task, _a_art), (b_task, _b_art) = two_same_title
    monkeypatch.setattr(answers, "speech_for_level", lambda *_a, **_k: "")

    sid = _create(client)
    _say(client, sid, "Bunu anlat.")
    call = _tool(client, sid, "c-1", "research.explain", {})

    assert call["status"] == "failed", call
    assert call["error"]["error_class"] == "internal_bug"
    assert call["error"]["speech"] == RESEARCH_EMPTY_ANSWER_TR
    # the identity it DID resolve stays on the row: the failure is about this job
    assert call["error"]["research_job_id"] == b_task
    assert _row_status(runtime, sid, "c-1") == "failed"
    recorded = _recorded(client, sid, "c-1")
    assert recorded["status"] == "failed" and recorded["error_class"] == "internal_bug"


def test_a_research_whose_report_cannot_be_read_fails_honestly(
    wired, two_same_title, monkeypatch
) -> None:
    from app.voice.realtime_sessions import tools as tools_module

    client, runtime, _sideband, _artifacts = wired
    (_a_task, _a_art), (b_task, _b_art) = two_same_title
    monkeypatch.setattr(tools_module, "_report_for", lambda *_a, **_k: None)

    sid = _create(client)
    _say(client, sid, "Bunu anlat.")
    call = _tool(client, sid, "c-1", "research.explain", {})

    assert call["status"] == "failed", call
    assert call["error"]["error_class"] == "empty_result"
    assert call["error"]["speech"] == RESEARCH_NO_REPORT_TR
    assert call["error"]["research_job_id"] == b_task
    assert _row_status(runtime, sid, "c-1") == "failed"


def test_activity_explain_on_a_research_turn_answers_from_the_report_bound_by_the_turn(
    wired, two_same_title
) -> None:
    """The production failure, step 1 (call_JqB2Z11ZHF0oRnIb).

    The owner said "Bunu teknik anlat."; the model called activity.explain with a
    paraphrase that says "bir önceki". The TURN binds: the current focus, the report's own
    diagnostics, no ledger narration, no narration session left behind.
    """
    client, runtime, _sideband, _artifacts = wired
    (_a_task, _a_art), (b_task, b_art) = two_same_title

    sid = _create(client)
    _say(client, sid, "Bunu teknik anlat.")
    call = _tool(
        client,
        sid,
        "c-1",
        "activity.explain",
        {"question": "Bir önceki araştırmayı teknik anlat", "level": "technical"},
    )

    assert call["status"] == "succeeded", call
    answer = call["result"]
    assert answer["research_job_id"] == b_task
    assert answer["research_artifact_id"] == b_art
    assert answer["resolution_reason"] == "current_focus"
    assert answer["routed"] == "research_report"
    assert answer["answered_by"] == "research.explain"
    assert answer["level"] == "technical"
    assert answer["speech"].startswith("Bu araştırmada"), answer["speech"]
    assert "aday keşfedildi" in answer["speech"]
    assert "Research policy" not in answer["speech"]
    assert answer["narration_session_id"] is None
    assert "cognition" not in answer and answer.get("subsystem") != "ledger"
    with runtime.session() as db:
        assert db.get(RealtimeSessionRow, uuid.UUID(sid)).narration_session_id is None
    # "bunu" is the current one: the focus did not move
    assert client.get("/v1/research/focus").json()["current"]["research_job_id"] == b_task

    recorded = _recorded(client, sid, "c-1")
    assert recorded["status"] == "succeeded"
    assert recorded["research_job_id"] == b_task
    assert recorded["answered_by"] == "research.explain"
    assert recorded["routed"] == "research_report"


def test_one_turn_one_answer_whichever_tool_the_model_chose(wired, two_same_title) -> None:
    client, _runtime, _sideband, _artifacts = wired
    (_a_task, _a_art), (b_task, _b_art) = two_same_title

    sid = _create(client)
    _say(client, sid, "Bunu teknik anlat.")
    direct = _tool(client, sid, "c-1", "research.explain", {"level": "technical"})["result"]
    via_activity = _tool(
        client, sid, "c-2", "activity.explain", {"question": "Teknik anlat."}
    )["result"]

    assert direct["research_job_id"] == b_task
    assert via_activity["research_job_id"] == b_task
    assert direct["speech"] == via_activity["speech"]
    assert direct["level"] == via_activity["level"] == "technical"


def test_the_previous_research_after_a_technical_answer_is_an_answer_not_a_cursor_move(
    wired, two_same_title
) -> None:
    """The production failure, step 2 (call_1U5XeuPor2pqnVXI: routed=narration, jump_level,
    the SAME job again). "Bir önceki araştırmayı anlat." is resolved afresh."""
    client, _runtime, _sideband, _artifacts = wired
    (a_task, a_art), (b_task, _b_art) = two_same_title

    sid = _create(client)
    _say(client, sid, "Bunu teknik anlat.")
    first = _tool(
        client,
        sid,
        "c-1",
        "activity.explain",
        {"question": "Bunu teknik anlat.", "level": "technical"},
    )["result"]
    assert first["research_job_id"] == b_task

    _say(client, sid, "Bir önceki araştırmayı anlat.", turn=2, t_ms=30000)
    call = _tool(
        client, sid, "c-2", "activity.explain", {"question": "Bir önceki araştırmayı anlat."}
    )

    assert call["status"] == "succeeded", call
    second = call["result"]
    assert second["research_job_id"] == a_task
    assert second["research_artifact_id"] == a_art
    assert second["resolution_reason"] == "previous_focus"
    assert second["routed"] == "research_report"
    assert second.get("action") is None
    assert second["level"] == "executive"
    assert second["speech"].startswith("Bu araştırmada"), second["speech"]
    assert client.get("/v1/research/focus").json()["current"]["research_job_id"] == a_task


def test_a_question_about_the_system_itself_still_goes_to_the_ledger(
    wired, two_same_title
) -> None:
    """"Son yaptıklarını anlat." carries a 'son' reference and content words; neither makes
    it a research turn. The ledger answers, exactly as before."""
    client, _runtime, _sideband, _artifacts = wired

    sid = _create(client)
    _say(client, sid, "Son yaptıklarını anlat.")
    call = _tool(client, sid, "c-1", "activity.explain", {"question": "Son yaptıklarını anlat."})

    assert call["status"] == "succeeded", call
    answer = call["result"]
    assert answer.get("routed") != "research_report"
    assert "answered_by" not in answer
    assert answer["speech"]
    assert answer["narration_session_id"] is not None
