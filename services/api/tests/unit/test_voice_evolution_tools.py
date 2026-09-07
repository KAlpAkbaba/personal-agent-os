"""Unit tests: the owner's voice over self-evolution (M18.4 spec §4) - the router, the
question table, and the three tools against a real EvolutionService on SQLite."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.evolution import supervisor as evolution_supervisor
from app.evolution.models import Capability, CapabilityGap, EvolutionOpportunity, SkillVersion
from app.evolution.runtime import EvolutionRuntime
from app.explain.classify import (
    QUERY_EVOLUTION_NOW,
    QUERY_LAST_FIX,
    QUERY_PENDING_CANDIDATES,
    QUERY_RUNNING_VERSION,
    classify,
)
from app.ledger.models import ActivityEventRow
from app.voice.intents import Intent, resolve_intent
from app.voice.realtime_sessions import tools_evolution
from app.voice.realtime_sessions.tools import ToolContext, default_registry

NOW = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)

TABLES = [
    ActivityEventRow.__table__,
    Capability.__table__,
    SkillVersion.__table__,
    CapabilityGap.__table__,
    EvolutionOpportunity.__table__,
]


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("PAGENTOS_EVOLUTION_SKILLS_ROOT", str(tmp_path / "skills"))
    monkeypatch.setenv("PAGENTOS_EVOLUTION_WORK_ROOT", str(tmp_path / "work"))
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(_env_file=None)
    runtime = EvolutionRuntime(settings, engine=engine)
    return factory, runtime, settings


def _ctx(session, runtime, settings, *, utterance: str | None, action: str | None = None):
    record = None
    if utterance is not None:
        resolved = resolve_intent(utterance)
        record = {
            "at": NOW.isoformat().replace("+00:00", "Z"),
            "intent": resolved.intent.value,
            "query_kind": resolved.query_kind,
            "evolution_action": resolved.evolution_action if action is None else action,
        }
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="web",
        context={"last_utterance": record} if record else {},
        db=session,
        now=NOW + timedelta(seconds=2),
        call_id="call-1",
        live={
            "evolution_runtime": runtime,
            "evolution_service": runtime.evolution_service,
            "settings": settings,
        },
    )


def _opportunity(runtime, *, title: str, status: str) -> dict:
    with runtime.session() as session:
        row = ActivityEventRow(
            occurred_at=NOW,
            event_type="research.failed",
            subsystem="research",
            action="research_failed",
            status="failed",
            factual_summary="x",
            source="test",
            source_ref=f"ev:{uuid.uuid4().hex}",
        )
        session.add(row)
        session.commit()
        ref = str(row.event_id)
    service = runtime.evolution_service
    created = service.create_from_evidence(
        title=title,
        statement="a candidate",
        evidence_refs=[{"kind": "ledger_event", "ref": ref}],
        scores={
            "owner_relevance": 0.5,
            "expected_utility": 0.5,
            "recurrence": 0.5,
            "confidence": 0.5,
            "engineering_cost": 0.5,
            "operational_risk": 0.2,
        },
        source="ledger_event",
        source_ref=f"test:{uuid.uuid4().hex}",
    )
    current = created
    path = {
        "researching": ["researching"],
        "building": ["researching", "design_ready", "building"],
        "shadow_ready": [
            "researching",
            "design_ready",
            "building",
            "testing",
            "evaluating",
            "shadow_ready",
        ],
    }[status]
    for step in path:
        current = service.advance(current["opportunity_id"], target=step, actor="lab")
    return current


# ------------------------------------------------------------------ the router


@pytest.mark.parametrize(
    ("text", "intent", "action"),
    [
        ("Kendi kendini geliştirmeyi duraklat.", Intent.EVOLUTION_PAUSE, "pause"),
        ("Kendini geliştirmeyi durdur.", Intent.EVOLUTION_PAUSE, "pause"),
        ("Kendi kendini geliştirmeyi kapat.", Intent.EVOLUTION_PAUSE, "pause"),
        ("Kendi kendini geliştirmeyi aç.", Intent.EVOLUTION_RESUME, "resume"),
        ("Kendini geliştirmeye devam et.", Intent.EVOLUTION_RESUME, "resume"),
        ("Bu geliştirmeyi iptal et.", Intent.EVOLUTION_CANCEL, "cancel"),
        ("Bunu canlıya alma.", Intent.EVOLUTION_HOLD, "hold"),
        ("Bunu yayına alma.", Intent.EVOLUTION_HOLD, "hold"),
        ("Önceki sürüme dön.", Intent.RELEASE_ROLLBACK, None),
        ("Eski sürüme geri al.", Intent.RELEASE_ROLLBACK, None),
    ],
)
def test_the_owner_phrases_resolve_with_their_action(text, intent, action) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is intent, text
    assert resolved.evolution_action == action
    assert resolved.klass == "action"
    assert resolved.to_dict()["evolution_action"] == action


def test_neighbours_keep_their_meaning() -> None:
    assert resolve_intent("Bunu canlıya al.").intent is Intent.DEPLOY
    assert resolve_intent("Bir önceki araştırmaya dön.").intent is not Intent.RELEASE_ROLLBACK
    assert resolve_intent("Alarmı kapat.").intent is Intent.ALARM_STOP
    # "kapat" while an alarm rings is the alarm - unless the sentence is about evolution.
    assert (
        resolve_intent("Kendi kendini geliştirmeyi kapat.", alarm_ringing=True).intent
        is Intent.EVOLUTION_PAUSE
    )
    assert resolve_intent("Kapat.", alarm_ringing=True).intent is Intent.ALARM_STOP


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Şu an ne geliştiriyorsun?", QUERY_EVOLUTION_NOW),
        ("Son hangi hatayı düzelttin?", QUERY_LAST_FIX),
        ("Hangi sürüm çalışıyor?", QUERY_RUNNING_VERSION),
        ("hangi surum calisiyor", QUERY_RUNNING_VERSION),
        ("Bekleyen aday sürüm var mı?", QUERY_PENDING_CANDIDATES),
    ],
)
def test_the_four_questions_reach_their_kinds(text, kind) -> None:
    query = classify(text)
    assert query.matched and query.kind == kind
    assert query.subsystem == "evolution"
    resolved = resolve_intent(text)
    assert resolved.intent is Intent.EXPLAIN and resolved.query_kind == kind


def test_the_tools_are_on_the_manifest() -> None:
    names = set(default_registry().names())
    assert set(tools_evolution.EVOLUTION_TOOL_NAMES) <= names


# ------------------------------------------------------------------ the tools


def test_pause_and_resume_are_ledger_rows_read_back(wired) -> None:
    factory, runtime, settings = wired
    with factory() as session:
        receipt = tools_evolution.evolution_control(
            _ctx(session, runtime, settings, utterance="Kendi kendini geliştirmeyi duraklat."),
            {"utterance": "Kendi kendini geliştirmeyi duraklat."},
        )
        assert receipt["execution_status"] == "executed"
        assert receipt["terminal_status"] == "verified"
        assert receipt["observed_after"]["server"] == {"action": "pause", "paused": True}
        assert receipt["speech"] == tools_evolution.PAUSED_TR
        assert evolution_supervisor.is_paused(session) is True
        # The owner's words outrank the model's argument.
        receipt = tools_evolution.evolution_control(
            _ctx(session, runtime, settings, utterance="Kendi kendini geliştirmeyi aç."),
            {"utterance": "x", "action": "pause"},
        )
        assert receipt["observed_after"]["server"]["paused"] is False
        assert evolution_supervisor.is_paused(session) is False


def test_no_action_in_the_words_is_a_refusal_not_a_guess(wired) -> None:
    factory, runtime, settings = wired
    with factory() as session:
        receipt = tools_evolution.evolution_control(
            _ctx(session, runtime, settings, utterance=None), {"utterance": "hmm"}
        )
    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == tools_evolution.ERROR_NO_ACTION


def test_cancel_with_nothing_in_the_lab_is_a_truthful_refusal(wired) -> None:
    factory, runtime, settings = wired
    with factory() as session:
        receipt = tools_evolution.evolution_control(
            _ctx(session, runtime, settings, utterance="Bu geliştirmeyi iptal et."),
            {"utterance": "Bu geliştirmeyi iptal et."},
        )
    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == tools_evolution.ERROR_NO_CANDIDATE
    assert receipt["speech"] == tools_evolution.NO_CANDIDATE_CANCEL_TR


def test_cancel_rejects_the_one_candidate_being_built(wired) -> None:
    factory, runtime, settings = wired
    built = _opportunity(runtime, title="Kabul ifadesi koruması", status="building")
    with factory() as session:
        receipt = tools_evolution.evolution_control(
            _ctx(session, runtime, settings, utterance="Bu geliştirmeyi iptal et."),
            {"utterance": "Bu geliştirmeyi iptal et."},
        )
    assert receipt["execution_status"] == "executed"
    assert receipt["observed_after"]["server"]["status"] == "rejected"
    assert receipt["observed_after"]["server"]["reason"] == "owner_cancelled"
    assert "Kabul ifadesi koruması" in receipt["speech"]
    after = runtime.evolution_service.backlog.get(built["opportunity_id"])
    assert after["status"] == "rejected"


def test_cancel_with_two_candidates_asks_which_one(wired) -> None:
    factory, runtime, settings = wired
    _opportunity(runtime, title="Birinci", status="building")
    _opportunity(runtime, title="İkinci", status="researching")
    with factory() as session:
        receipt = tools_evolution.evolution_control(
            _ctx(session, runtime, settings, utterance="Bu geliştirmeyi iptal et."),
            {"utterance": "Bu geliştirmeyi iptal et."},
        )
    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == tools_evolution.ERROR_AMBIGUOUS_CANDIDATE
    assert "Birinci" in receipt["speech"] and "İkinci" in receipt["speech"]
    statuses = {o["status"] for o in runtime.evolution_service.list_opportunities()}
    assert statuses == {"building", "researching"}


def test_hold_rejects_the_candidate_awaiting_approval(wired) -> None:
    factory, runtime, settings = wired
    ready = _opportunity(runtime, title="Gölge aday", status="shadow_ready")
    with factory() as session:
        receipt = tools_evolution.evolution_control(
            _ctx(session, runtime, settings, utterance="Bunu canlıya alma."),
            {"utterance": "Bunu canlıya alma."},
        )
    assert receipt["execution_status"] == "executed"
    assert receipt["observed_after"]["server"]["reason"] == "owner_held"
    assert runtime.evolution_service.backlog.get(ready["opportunity_id"])["status"] == "rejected"


def test_rollback_is_always_refused_and_names_the_last_known_good(wired, monkeypatch) -> None:
    factory, runtime, _ = wired
    monkeypatch.setenv("PAGENTOS_LAST_KNOWN_GOOD", "8f3e005")
    settings = Settings(_env_file=None)
    with factory() as session:
        receipt = tools_evolution.release_rollback(
            _ctx(session, runtime, settings, utterance="Önceki sürüme dön."),
            {"utterance": "Önceki sürüme dön."},
        )
    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == "owner_authorization_required"
    assert receipt["observed_after"]["server"]["last_known_good"] == "8f3e005"
    assert "8f3e005" in receipt["speech"]
    assert receipt["capability"] == "release.rollback"


def test_the_four_answers_come_from_the_rows(wired) -> None:
    factory, runtime, settings = wired
    with factory() as session:
        now = tools_evolution.evolution_status(
            _ctx(session, runtime, settings, utterance="Şu an ne geliştiriyorsun?"), {}
        )
        assert now["kind"] == QUERY_EVOLUTION_NOW and now["routed"] == "evolution.status"
        assert "üzerinde çalıştığım bir şey yok" in now["speech"]
        _opportunity(runtime, title="Sürüm modeli", status="building")
        now = tools_evolution.evolution_status(
            _ctx(session, runtime, settings, utterance="Şu an ne geliştiriyorsun?"), {}
        )
        assert "1 aday üzerinde çalışıyorum" in now["speech"] and "Sürüm modeli" in now["speech"]
        _opportunity(runtime, title="Gölge aday", status="shadow_ready")
        pending = tools_evolution.evolution_status(
            _ctx(session, runtime, settings, utterance="Bekleyen aday sürüm var mı?"), {}
        )
        assert "Bekleyen 1 aday var" in pending["speech"] and "Gölge aday" in pending["speech"]
        version = tools_evolution.evolution_status(
            _ctx(session, runtime, settings, utterance="Hangi sürüm çalışıyor?"), {}
        )
        assert "dışa aktarılmamış" in version["speech"]
        assert "eylem sözleşmesi 12" in version["speech"]
        fix = tools_evolution.evolution_status(
            _ctx(session, runtime, settings, utterance="Son hangi hatayı düzelttin?"), {}
        )
        assert fix["speech"] == "Kayıtlarda düzeltilmiş bir olay yok efendim."
        evolution_supervisor.set_paused(session, paused=True, actor="test", now=NOW)
        paused = tools_evolution.evolution_status(
            _ctx(session, runtime, settings, utterance="Şu an ne geliştiriyorsun?"), {}
        )
        assert "duraklatılmış" in paused["speech"]


def test_status_without_the_runtime_is_an_honest_sentence(wired) -> None:
    factory, runtime, settings = wired
    with factory() as session:
        ctx = _ctx(session, runtime, settings, utterance="Hangi sürüm çalışıyor?")
        ctx.live.pop("evolution_runtime")
        answer = tools_evolution.evolution_status(ctx, {})
    assert answer["status"] is None
    assert "bağlı değil" in answer["speech"]
