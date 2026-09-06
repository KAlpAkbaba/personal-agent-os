"""``state.now`` and its composer (docs/M18_ACTION_CONTRACT.md §3, §4; tests §8).

The owner's defect (2026-09-06): "Kendi sisteminde şu anda ne görüyorsun?" was answered
with counts of truth kinds. The composer speaks the current state: every fact carries
source / observed_at / age_s / confidence / stale; a stale fact is said as stale; the eye
scope has its own sentences; ``activity.explain`` delegates to the same composer, so the
answer is identical whichever tool the model picked; and it is concise.
"""

# ruff: noqa: F811 - the shared `wired` fixture is imported and then named as a parameter
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.receipt import contains_fake_completion
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import EVENT_TYPE_VOICE_STATE_ANSWERED, SUBSYSTEM_VOICE
from app.presence.engine import PresenceFusionEngine, get_engine, set_engine
from app.presence.eye import disable_eye
from app.presence.observations import Observation
from app.presence.service import reset_heartbeat
from app.state.now import (
    KEY_CORE_HEALTH,
    KEY_EYE_ENABLED,
    KEY_EYE_LAST_OBSERVATION,
    KEY_OWNER_PRESENCE,
    KEY_VOICE_SESSION,
    compose_live_state,
)
from app.voice.realtime_sessions import service
from app.voice.realtime_sessions.models import RealtimeSessionRow
from tests.unit.test_voice_realtime_sessions import _create, wired  # noqa: F401

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
FACT_KEYS = {"key", "value", "source", "observed_at", "age_s", "confidence", "stale"}


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_presence():
    previous = get_engine()
    set_engine(PresenceFusionEngine())
    reset_heartbeat()
    try:
        yield
    finally:
        set_engine(previous)
        reset_heartbeat()


def _observation(*, t: float, **overrides) -> Observation:
    defaults = dict(
        person_present=True,
        presence_confidence=0.9,
        activity_level="medium",
        posture="upright",
        awake_state="awake",
        observed_at=NOW + timedelta(seconds=t),
        source="camera",
    )
    defaults.update(overrides)
    return Observation(**defaults)


def _seen_engine() -> PresenceFusionEngine:
    engine = PresenceFusionEngine()
    engine.add_observation(_observation(t=0), now=NOW)
    engine.add_observation(_observation(t=25), now=NOW + timedelta(seconds=25))
    return engine


def _facts(result: dict) -> dict[str, dict]:
    return {f["key"]: f for f in result["facts"]}


def _uncertainties(result: dict) -> dict[str, str]:
    return {u["subject"]: u["reason"] for u in result["uncertainties"]}


def _sentences(speech: str) -> int:
    return len(re.findall(r"[.!?](?:\s|$)", speech))


def _assert_concise(speech: str) -> None:
    assert 1 <= _sentences(speech) <= 3, speech
    assert not contains_fake_completion(speech), speech
    assert "kayıt" not in speech.lower(), speech
    assert "gözlemim var" not in speech, speech  # no counts of truth kinds
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", speech), speech  # no ids


# ------------------------------------------------------------------ the composer


def test_facts_carry_source_observed_at_age_confidence_and_stale(session) -> None:
    result = compose_live_state(
        session, scope="all", session_id=None, now=NOW, presence_runtime=PresenceFusionEngine()
    )
    assert result["query_kind"] == "world_state"
    assert result["subsystem"] == "worldmodel"
    assert result["observed_at"] == "2026-09-06T12:00:00Z"
    facts = _facts(result)
    for fact in facts.values():
        assert set(fact) == FACT_KEYS, fact
    assert facts[KEY_CORE_HEALTH]["value"] == "ok"
    assert facts[KEY_CORE_HEALTH]["source"] == "health_probe"
    assert facts[KEY_CORE_HEALTH]["stale"] is False
    assert facts[KEY_EYE_ENABLED]["value"] is True
    assert facts[KEY_EYE_ENABLED]["source"] == "ledger"
    # what could not be established is an uncertainty with a reason, never a guess
    unc = _uncertainties(result)
    assert unc[KEY_VOICE_SESSION] == "no_session"
    assert unc[KEY_OWNER_PRESENCE] == "no_observations_yet"
    assert unc[KEY_EYE_LAST_OBSERVATION] == "no_camera_observations"
    assert KEY_OWNER_PRESENCE not in facts
    _assert_concise(result["speech"])
    assert result["speech"] == (
        "Cloud Core sağlıklı, göz açık. Şu an geçerli bir varlık gözlemim yok."
    )


def test_a_seen_owner_is_spoken_as_seen_with_a_fresh_fact(session) -> None:
    engine = _seen_engine()
    result = compose_live_state(session, now=NOW + timedelta(seconds=30), presence_runtime=engine)
    facts = _facts(result)
    presence = facts[KEY_OWNER_PRESENCE]
    assert presence["value"] == "present"
    assert presence["source"] == "presence_engine"
    assert presence["stale"] is False
    assert presence["age_s"] == 5.0
    assert 0.0 < presence["confidence"] <= 1.0
    last = facts[KEY_EYE_LAST_OBSERVATION]
    assert last["value"] == 5.0 and last["stale"] is False
    assert "Şu an sizi karşımda görüyorum." in result["speech"]
    _assert_concise(result["speech"])


def test_a_stale_presence_is_spoken_as_stale(session) -> None:
    engine = _seen_engine()
    result = compose_live_state(
        session, now=NOW + timedelta(seconds=25 + 400), presence_runtime=engine
    )
    facts = _facts(result)
    assert facts[KEY_OWNER_PRESENCE]["stale"] is True
    assert facts[KEY_EYE_LAST_OBSERVATION]["stale"] is True
    assert (
        "Son doğrulanmış varlık gözlemi 400 saniye önceydi; şu an kesin doğrulayamıyorum."
        in result["speech"]
    )
    assert "görüyorum" not in result["speech"]
    _assert_concise(result["speech"])


def test_health_results_from_a_caller_become_the_core_fact(session) -> None:
    degraded = {"db": {"status": "ok"}, "redis": {"status": "fail"}}
    result = compose_live_state(session, now=NOW, health=degraded)
    assert _facts(result)[KEY_CORE_HEALTH]["value"] == "degraded"
    assert result["speech"].startswith("Cloud Core kısmen sağlıklı")
    ok = compose_live_state(session, now=NOW, health=lambda: {"db": {"status": "ok"}})
    assert _facts(ok)[KEY_CORE_HEALTH]["value"] == "ok"


def test_eye_scope_sentences(session) -> None:
    # open, fresh observation
    engine = _seen_engine()
    result = compose_live_state(
        session, scope="eye", now=NOW + timedelta(seconds=30), presence_runtime=engine
    )
    assert result["query_kind"] == "eye_state"
    assert set(_facts(result)) == {KEY_EYE_ENABLED, KEY_EYE_LAST_OBSERVATION}
    assert result["speech"] == "Göz açık efendim; son gözlem 5 saniye önce."
    assert _sentences(result["speech"]) == 1

    # open, but the camera stopped delivering (past its TTL)
    stale = compose_live_state(
        session, scope="eye", now=NOW + timedelta(seconds=25 + 120), presence_runtime=engine
    )
    assert stale["speech"] == (
        "Göz açık görünüyor ama 120 saniyedir gözlem gelmiyor; kamerayı doğrulayamıyorum."
    )

    # open, never delivered
    never = compose_live_state(
        session, scope="eye", now=NOW, presence_runtime=PresenceFusionEngine()
    )
    assert never["speech"] == (
        "Göz açık görünüyor ama hiç gözlem gelmedi; kamerayı doğrulayamıyorum."
    )
    assert _uncertainties(never)[KEY_EYE_LAST_OBSERVATION] == "no_camera_observations"

    # closed
    disable_eye(session, reason="owner")
    closed = compose_live_state(session, scope="eye", now=NOW, presence_runtime=engine)
    assert closed["speech"] == "Göz kapalı efendim."
    assert _facts(closed)[KEY_EYE_ENABLED]["value"] is False
    assert KEY_EYE_LAST_OBSERVATION not in _uncertainties(closed)


def test_a_disabled_eye_is_the_reason_presence_is_unknown(session) -> None:
    engine = _seen_engine()
    set_engine(engine)
    disable_eye(session, reason="owner")  # invalidates the camera evidence
    result = compose_live_state(session, now=NOW + timedelta(seconds=30), presence_runtime=engine)
    assert _uncertainties(result)[KEY_OWNER_PRESENCE] == "eye_disabled"
    assert result["speech"] == "Cloud Core sağlıklı, göz kapalı."
    presence_scope = compose_live_state(
        session, scope="presence", now=NOW + timedelta(seconds=30), presence_runtime=engine
    )
    assert presence_scope["speech"] == "Göz kapalı; varlık gözlemi yapmıyorum efendim."


def test_single_scopes_are_one_sentence(session) -> None:
    for scope, expected in (
        ("voice", "Ses oturumunu şu an doğrulayamıyorum."),
        ("devices", "Cihazların durumunu şu an doğrulayamıyorum."),
        ("release", "Dağıtım durumunu şu an doğrulayamıyorum."),
    ):
        result = compose_live_state(session, scope=scope, now=NOW)
        assert result["speech"] == expected, scope
        assert _sentences(result["speech"]) == 1


def test_unknown_scope_is_refused(session) -> None:
    with pytest.raises(ValueError):
        compose_live_state(session, scope="everything", now=NOW)


# ------------------------------------------------------------------ the tools


def _tool(client, sid: str, call_id: str, name: str, **arguments) -> dict:
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": call_id, "name": name, "arguments": arguments},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "succeeded", body
    return body["result"]


def test_state_now_and_activity_explain_speak_the_same_live_answer(wired) -> None:
    client, _, runtime, sideband, *_ = wired
    sid = _create(client)["session_id"]

    live = _tool(client, sid, "s1", "state.now", question="Kendi sisteminde şu anda ne görüyorsun?")
    assert live["query_kind"] == "world_state"
    assert live["subsystem"] == "worldmodel"
    assert live["scope"] == "all"
    facts = _facts(live)
    assert facts[KEY_VOICE_SESSION]["value"] == "connected"
    assert facts[KEY_VOICE_SESSION]["source"] == "realtime_session"
    assert facts[KEY_EYE_ENABLED]["value"] is True
    _assert_concise(live["speech"])
    assert live["speech"] == (
        "Cloud Core sağlıklı, ses bağlı, göz açık. Şu an geçerli bir varlık gözlemim yok."
    )

    explained = _tool(
        client, sid, "s2", "activity.explain", question="Kendi sisteminde şu anda ne görüyorsun?"
    )
    assert explained["speech"] == live["speech"]
    assert explained["routed"] == "state.now"
    assert explained["query_kind"] == "world_state"
    assert explained["subsystem"] == "worldmodel"
    assert explained["intent"]["intent"] == "explain"
    assert explained["intent"]["klass"] == "query"
    assert explained["narration_session_id"] is None
    # no narration cursor was pushed for a live answer
    assert "narration_cursor" not in sideband.events()

    # session_activity correlates both on query_kind / subsystem
    with runtime.session() as db:
        activity = service.session_activity(db, db.get(RealtimeSessionRow, uuid.UUID(sid)))
        rows = ledger_service.query(
            db, subsystems=[SUBSYSTEM_VOICE], event_types=[EVENT_TYPE_VOICE_STATE_ANSWERED]
        )
    calls = {c["name"]: c for c in activity["tool_calls"]}
    for name in ("state.now", "activity.explain"):
        assert calls[name]["query_kind"] == "world_state"
        assert calls[name]["subsystem"] == "worldmodel"
        assert calls[name]["speech_head"] == live["speech"][:80]
    assert calls["activity.explain"]["routed"] == "state.now"
    # the ledger: what was answered, never the sentence
    assert len(rows) == 2
    detail = rows[0].detail_json
    assert detail["query_kind"] == "world_state" and detail["scope"] == "all"
    assert KEY_VOICE_SESSION in detail["facts"]
    assert KEY_OWNER_PRESENCE in detail["uncertainties"]
    assert detail["stale"] == []
    assert "sağlıklı" not in str(detail) and "sağlıklı" not in rows[0].factual_summary


def test_kamera_acik_mi_is_the_eye_scope_on_both_tools(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    live = _tool(client, sid, "k1", "state.now", question="Kamera açık mı?")
    assert live["query_kind"] == "eye_state" and live["scope"] == "eye"
    assert live["speech"] == (
        "Göz açık görünüyor ama hiç gözlem gelmedi; kamerayı doğrulayamıyorum."
    )
    explained = _tool(client, sid, "k2", "activity.explain", question="Göz açık mı?")
    assert explained["speech"] == live["speech"]
    assert explained["query_kind"] == "eye_state"
    with runtime.session() as db:
        disable_eye(db, reason="owner")
    closed = _tool(client, sid, "k3", "state.now", question="Kameran açık mı?")
    assert closed["speech"] == "Göz kapalı efendim."
    explicit = _tool(client, sid, "k4", "state.now", question="Durum?", scope="eye")
    assert explicit["speech"] == "Göz kapalı efendim."


def test_plain_current_state_questions_reach_the_composer(wired) -> None:
    client, *_ = wired
    sid = _create(client)["session_id"]
    for n, question in enumerate(("Ses bağlı mı?", "Cihaz çevrimiçi mi?", "Şu an ne çalışıyor?")):
        result = _tool(client, sid, f"q{n}", "activity.explain", question=question)
        assert result["routed"] == "state.now", question
        assert result["query_kind"] == "world_state"


def test_state_now_rejects_a_bad_scope(wired) -> None:
    client, *_ = wired
    sid = _create(client)["session_id"]
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": "b1", "name": "state.now", "arguments": {"question": "?", "scope": "x"}},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert r.json()["error"]["error_class"] == "validation_error"
