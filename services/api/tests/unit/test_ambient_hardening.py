"""Ambient hardening (ADR-0079): quiet hours, keep-on, the camera grace, holdoffs restored
after a restart, the explicit-wake and alarm-wake holdoffs wired, the owner's own words
setting the policy, and the explanation surface. Unit level; the scenario matrix is
``test_ambient_scenarios.py``."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.receipt import contains_fake_completion
from app.alarms import service as alarms_service
from app.alarms import speech as alarm_speech
from app.alarms.audio_store import AudioStore
from app.alarms.models import AmbientPolicyRow, WakeAlarm
from app.alarms.sequence import RECEIPT_BY_DEVICE_CALL, WakeSequence
from app.alarms.tr_time import parse_when_struct
from app.ambient import service as ambient_service
from app.ambient.holdoff import (
    SOURCE_ALARM_WAKE,
    SOURCE_INPUT,
    SOURCE_OWNER_COMMAND,
    SOURCE_OWNER_RETURN,
    HoldoffRegistry,
    set_holdoffs,
)
from app.ambient.policy import (
    ACTION_DISPLAY_OFF,
    ACTION_NONE,
    QUIET_HOURS_INSIDE,
    QUIET_HOURS_OUTSIDE,
    QUIET_HOURS_UNSET,
    REASON_OWNER_KEEP_ON,
    REASON_OWNER_LIKELY_ASLEEP,
    REASON_PERCEPTION_STALE,
    AmbientInputs,
    AmbientPolicy,
    asleep_needed_s,
    decide,
    quiet_hours_state,
    validate_quiet_hours,
)
from app.artifacts.runtime import ArtifactRuntime
from app.broker.models import Device
from app.config import Settings
from app.devices.status import DeviceStatusRegistry
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.presence.states import PresenceState
from app.routines.models import Routine, RoutineFiring
from app.uistate.publisher import UiStatePublisher, set_publisher
from app.voice.intents import Intent, ambient_policy_changes, resolve_intent
from app.voice.realtime_sessions import tools_ambient
from app.voice.realtime_sessions.tools import ToolContext, default_registry
from tests.alarms_support import FakeDeviceAction, build_session_factory, happy_device_results
from tests.identity_support import authenticate, install_identity

#: 03:00 Istanbul on a Wednesday - inside a 23:30-07:30 quiet window.
NIGHT = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
#: 15:00 Istanbul - outside it.
AFTERNOON = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
WINDOW = {"start": "23:30", "end": "07:30"}


@pytest.fixture()
def session():
    with build_session_factory()() as s:
        yield s


@pytest.fixture()
def holdoffs() -> HoldoffRegistry:
    registry = HoldoffRegistry()
    set_holdoffs(registry)
    return registry


@pytest.fixture()
def device():
    return FakeDeviceAction(results=happy_device_results())


@pytest.fixture()
def sequence(device):
    return WakeSequence(device_action=device, tts=None)


@pytest.fixture(autouse=True)
def _fresh_process():
    set_publisher(UiStatePublisher())
    ambient_service.cancel_display_test()
    ambient_service.reset_presence_watermark()
    ambient_service.reset_holdoff_restore()
    yield
    set_publisher(UiStatePublisher())
    ambient_service.cancel_display_test()
    ambient_service.reset_holdoff_restore()
    set_holdoffs(HoldoffRegistry())


def _asleep_inputs(*, held_s: float, confidence: float = 0.9) -> AmbientInputs:
    return AmbientInputs(
        display_on=True,
        presence_state=PresenceState.LIKELY_ASLEEP,
        presence_confidence=confidence,
        presence_held_s=held_s,
        presence_stale=False,
        eye_enabled=True,
        perception_age_s=3.0,
    )


# ------------------------------------------------------------------ quiet hours


def test_quiet_hours_are_read_in_the_owners_timezone_and_cross_midnight() -> None:
    policy = AmbientPolicy(quiet_hours=WINDOW)
    assert quiet_hours_state(policy, NIGHT) == QUIET_HOURS_INSIDE
    assert quiet_hours_state(policy, AFTERNOON) == QUIET_HOURS_OUTSIDE
    # 07:29 Istanbul is inside; 07:30 is out.
    assert quiet_hours_state(policy, datetime(2026, 9, 9, 4, 29, tzinfo=UTC)) == QUIET_HOURS_INSIDE
    assert quiet_hours_state(policy, datetime(2026, 9, 9, 4, 30, tzinfo=UTC)) == QUIET_HOURS_OUTSIDE
    assert quiet_hours_state(AmbientPolicy(), NIGHT) == QUIET_HOURS_UNSET
    # A window that cannot be read never lowers a threshold.
    broken = AmbientPolicy(quiet_hours={"start": "soon", "end": "later"})
    assert quiet_hours_state(broken, NIGHT) == QUIET_HOURS_UNSET


def test_asleep_needs_longer_evidence_outside_the_quiet_hours() -> None:
    policy = AmbientPolicy(
        auto_off_enabled=True,
        asleep_after_s=600,
        asleep_after_outside_quiet_s=1800,
        quiet_hours=WINDOW,
    )
    assert asleep_needed_s(policy, NIGHT) == 600
    assert asleep_needed_s(policy, AFTERNOON) == 1800
    assert asleep_needed_s(AmbientPolicy(asleep_after_s=600), AFTERNOON) == 600

    # 15 minutes of LIKELY_ASLEEP: enough at night, not in the afternoon.
    fifteen = _asleep_inputs(held_s=900.0)
    assert decide(fifteen, policy, NIGHT).action == ACTION_DISPLAY_OFF
    afternoon = decide(fifteen, policy, AFTERNOON)
    assert afternoon.action == ACTION_NONE
    assert afternoon.evidence["needed_s"] == 1800
    assert afternoon.evidence["quiet_hours"] == QUIET_HOURS_OUTSIDE
    assert decide(_asleep_inputs(held_s=1900.0), policy, AFTERNOON).reason == (
        REASON_OWNER_LIKELY_ASLEEP
    )


def test_validate_quiet_hours_accepts_one_shape_and_clears_with_an_empty_object() -> None:
    assert validate_quiet_hours(None) is None
    assert validate_quiet_hours({}) is None
    assert validate_quiet_hours({"start": "23:30", "end": "7:30"}) == {
        "start": "23:30",
        "end": "07:30",
        "timezone": "Europe/Istanbul",
    }
    for bad in (
        {"start": "23:30"},
        {"start": "25:00", "end": "07:30"},
        "23:30-07:30",
        5,
        {"start": "07:30", "end": "07:30"},
        {"start": "23:30", "end": "07:30", "timezone": "Mars/Base"},
    ):
        with pytest.raises(ValueError):
            validate_quiet_hours(bad)


def test_the_policy_row_persists_every_new_setting(session) -> None:
    policy, applied = ambient_service.set_policy(
        session,
        {
            "keep_on": True,
            "asleep_after_outside_quiet_s": 2400,
            "camera_unknown_grace_s": 90,
            "quiet_hours": {"start": "23:00", "end": "06:45"},
        },
        now=NIGHT,
    )
    assert set(applied) == {
        "keep_on",
        "asleep_after_outside_quiet_s",
        "camera_unknown_grace_s",
        "quiet_hours",
    }
    assert policy.keep_on is True
    assert policy.quiet_hours == {"start": "23:00", "end": "06:45", "timezone": "Europe/Istanbul"}
    # A second read, from the row alone (what a restarted process would see).
    row = session.get(AmbientPolicyRow, "owner")
    session.expire_all()
    again = AmbientPolicy.from_row(row)
    assert again == policy
    assert AmbientPolicy(**again.as_dict()) == again
    # ...and an empty object clears the window; a malformed one is refused before it lands.
    cleared, applied = ambient_service.set_policy(session, {"quiet_hours": {}}, now=NIGHT)
    assert cleared.quiet_hours is None and "quiet_hours" in applied
    with pytest.raises(ValueError):
        ambient_service.set_policy(session, {"quiet_hours": {"start": "x", "end": "y"}}, now=NIGHT)


# --------------------------------------------------------------- keep-on + grace


def test_keep_on_outranks_holdoffs_alarm_context_and_the_policy_switch() -> None:
    inputs = AmbientInputs(
        display_on=True,
        presence_state=PresenceState.AWAY,
        presence_confidence=1.0,
        presence_held_s=99999.0,
        presence_stale=False,
        eye_enabled=True,
        perception_age_s=1.0,
    )
    decision = decide(inputs, AmbientPolicy(auto_off_enabled=True, keep_on=True), NIGHT)
    assert decision.reason == REASON_OWNER_KEEP_ON
    # Only when the display is known ON: an unknown display is still the first reason.
    dark = replace(inputs, display_on=None)
    assert decide(dark, AmbientPolicy(keep_on=True), NIGHT).reason == "display_not_on"


def test_the_camera_grace_is_owner_configurable() -> None:
    inputs = _asleep_inputs(held_s=5000.0)
    tight = AmbientPolicy(auto_off_enabled=True, asleep_after_s=60, camera_unknown_grace_s=2)
    assert decide(inputs, tight, NIGHT).reason == REASON_PERCEPTION_STALE  # 3 s old > 2 s
    loose = AmbientPolicy(auto_off_enabled=True, asleep_after_s=60, camera_unknown_grace_s=10)
    assert decide(inputs, loose, NIGHT).action == ACTION_DISPLAY_OFF
    never = replace(inputs, perception_age_s=None)
    assert decide(never, loose, NIGHT).reason == REASON_PERCEPTION_STALE


# -------------------------------------------------------- holdoffs after a restart


def _row(
    session, *, event_type: str, action: str, at: datetime, detail: dict | None = None
) -> None:
    ledger_service.record(
        session,
        ledger_service.ActivityEvent(
            event_type=event_type,
            subsystem="ambient",
            action=action,
            severity="info",
            factual_summary=f"{event_type} at {at.isoformat()}",
            source="live",
            source_ref=f"test:{event_type}:{at.timestamp()}",
            occurred_at=at,
            detail_json=detail or {},
        ),
    )


def test_holdoffs_are_rebuilt_from_the_ledger_for_the_time_they_have_left(
    session, holdoffs
) -> None:
    now = NIGHT
    _row(
        session,
        event_type="owner.input_active",
        action="owner_input_active",
        at=now - timedelta(seconds=100),
    )
    _row(
        session,
        event_type="ambient.policy_changed",
        action="ambient_policy_changed",
        at=now - timedelta(seconds=400),
    )
    _row(
        session, event_type="alarm.firing", action="alarm_firing", at=now - timedelta(seconds=1000)
    )
    _row(
        session,
        event_type="presence.state_changed",
        action="state_changed",
        at=now - timedelta(seconds=30),
        detail={"to_state": "returned"},
    )
    restored = ambient_service.restore_holdoffs(session, holdoffs=holdoffs, now=now)
    assert set(restored) == {
        SOURCE_INPUT,
        SOURCE_OWNER_COMMAND,
        SOURCE_ALARM_WAKE,
        SOURCE_OWNER_RETURN,
    }
    by_source = {h.source: h for h in holdoffs.active(now=now)}
    policy = AmbientPolicy()
    assert by_source[SOURCE_INPUT].until == now + timedelta(seconds=policy.input_holdoff_s - 100)
    assert by_source[SOURCE_OWNER_COMMAND].until == now + timedelta(
        seconds=policy.command_holdoff_s - 400
    )
    assert by_source[SOURCE_ALARM_WAKE].until == now + timedelta(
        seconds=policy.alarm_holdoff_s - 1000
    )
    assert by_source[SOURCE_OWNER_RETURN].until == now + timedelta(
        seconds=policy.return_holdoff_s - 30
    )
    assert all(h.reason.startswith("restored:") for h in by_source.values())


def test_an_expired_witness_restores_nothing_and_a_returned_state_only_when_it_is_the_newest(
    session, holdoffs
) -> None:
    now = NIGHT
    _row(
        session,
        event_type="owner.input_active",
        action="owner_input_active",
        at=now - timedelta(seconds=700),
    )
    _row(
        session,
        event_type="presence.state_changed",
        action="state_changed",
        at=now - timedelta(seconds=10),
        detail={"to_state": "away"},
    )
    assert ambient_service.restore_holdoffs(session, holdoffs=holdoffs, now=now) == {}
    assert holdoffs.active(now=now) == ()


def test_the_first_tick_of_a_process_restores_once(session, holdoffs, sequence) -> None:
    now = NIGHT
    _row(
        session,
        event_type="owner.input_active",
        action="owner_input_active",
        at=now - timedelta(seconds=60),
    )
    runtimes = ambient_service.AmbientRuntimes(statuses=DeviceStatusRegistry(), holdoffs=holdoffs)
    ambient_service.tick(session, sequence=sequence, runtimes=runtimes, now=now)
    assert holdoffs.is_active(SOURCE_INPUT, now=now)
    holdoffs.clear()
    ambient_service.tick(
        session, sequence=sequence, runtimes=runtimes, now=now + timedelta(seconds=10)
    )
    assert not holdoffs.is_active(
        SOURCE_INPUT, now=now + timedelta(seconds=10)
    )  # once, not every tick
    ambient_service.reset_holdoff_restore()  # "a fresh process"
    ambient_service.tick(
        session, sequence=sequence, runtimes=runtimes, now=now + timedelta(seconds=20)
    )
    assert holdoffs.is_active(SOURCE_INPUT, now=now + timedelta(seconds=20))


# ------------------------------------------------------ the holdoffs that were unwired


def _ctx(
    session, *, sequence=None, now: datetime = NIGHT, context: dict | None = None
) -> ToolContext:
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="web",
        context=context or {},
        db=session,
        now=now,
        call_id="call-1",
        live={"wake_sequence": sequence} if sequence is not None else {},
    )


def test_an_explicit_wake_by_voice_starts_the_command_holdoff(session, sequence, holdoffs) -> None:
    result = tools_ambient.display_wake(_ctx(session, sequence=sequence), {})
    assert result["terminal_status"] == "verified"
    assert holdoffs.is_active(SOURCE_OWNER_COMMAND, now=NIGHT)


def test_fire_alarm_starts_the_alarm_holdoff_and_so_does_a_local_ring(
    session, sequence, holdoffs
) -> None:
    alarm = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 5}, now=NIGHT)
    )
    alarms_service.fire_alarm(
        session,
        alarm.id,
        sequence=sequence,
        firing_id=uuid.uuid4(),
        now=NIGHT + timedelta(seconds=10),
    )
    assert holdoffs.is_active(SOURCE_ALARM_WAKE, now=NIGHT + timedelta(seconds=10))
    holdoffs.clear()
    other = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 60}, now=NIGHT)
    )
    alarms_service.reconcile_local_fired(
        session, [str(other.id)], now=NIGHT + timedelta(seconds=70)
    )
    assert holdoffs.is_active(SOURCE_ALARM_WAKE, now=NIGHT + timedelta(seconds=70))


# ------------------------------------------------------------ the owner's words


@pytest.mark.parametrize(
    ("utterance", "intent", "changes"),
    [
        (
            "Ben yokken ekranları kapat.",
            Intent.AMBIENT_POLICY_SET,
            {"off_when_away": True, "auto_off_enabled": True},
        ),
        ("Ben yokken ekranları kapatma.", Intent.AMBIENT_POLICY_SET, {"off_when_away": False}),
        (
            "Uyuduğumda ekranları kapat.",
            Intent.AMBIENT_POLICY_SET,
            {"off_when_asleep": True, "auto_off_enabled": True},
        ),
        ("Uyuduğumda ekranları kapatma.", Intent.AMBIENT_POLICY_SET, {"off_when_asleep": False}),
        (
            "Uyurken ekranları kapat.",
            Intent.AMBIENT_POLICY_SET,
            {"off_when_asleep": True, "auto_off_enabled": True},
        ),
        ("Otomatik ekran yönetimini aç.", Intent.AMBIENT_POLICY_SET, {"auto_off_enabled": True}),
        (
            "Otomatik ekran yönetimini kapat.",
            Intent.AMBIENT_POLICY_SET,
            {"auto_off_enabled": False},
        ),
        ("Otomatik ekran kapatmayı kapat.", Intent.AMBIENT_POLICY_SET, {"auto_off_enabled": False}),
        ("Ekranı açık tut.", Intent.AMBIENT_POLICY_SET, {"keep_on": True}),
        ("Ekranı açık tutma.", Intent.AMBIENT_POLICY_SET, {"keep_on": False}),
        ("Ben geri geldiğimde ekranı aç.", Intent.AMBIENT_POLICY_SET, {"wake_on_return": True}),
        ("Ben geri geldiğimde ekranı açma.", Intent.AMBIENT_POLICY_SET, {"wake_on_return": False}),
        ("Ekranları neden kapattın?", Intent.AMBIENT_EXPLAIN, None),
        ("Neden açık bıraktın?", Intent.AMBIENT_EXPLAIN, None),
        ("Şu an ekran politikası ne?", Intent.AMBIENT_EXPLAIN, None),
    ],
)
def test_every_owner_phrase_sets_what_it_says(utterance: str, intent: Intent, changes) -> None:
    resolved = resolve_intent(utterance)
    assert resolved.intent is intent, utterance
    assert resolved.policy_changes == changes, utterance
    assert resolved.to_dict()["policy_changes"] == changes


def test_the_explain_and_policy_phrases_do_not_shadow_their_neighbours() -> None:
    assert resolve_intent("Ekranları kapat.").intent is Intent.DISPLAY_OFF
    assert resolve_intent("Ekranları aç.").intent is Intent.DISPLAY_WAKE
    assert resolve_intent("Ekranlar açık mı?").intent is Intent.DISPLAY_QUERY
    assert resolve_intent("Neden önemli?").intent is not Intent.AMBIENT_EXPLAIN
    assert resolve_intent("Gözünü kapat.").intent is Intent.EYE_DISABLE
    assert ambient_policy_changes(("ekranları", "kapat")) is None


def test_the_tool_applies_the_owners_words_over_the_models_booleans(session, holdoffs) -> None:
    """ "Uyuduğumda ekranları kapatma." with a model that passed off_when_asleep=True."""
    context = {
        "last_utterance": {
            "at": NIGHT.isoformat().replace("+00:00", "Z"),
            "intent": "ambient_policy_set",
            "policy_changes": {"off_when_asleep": False},
        }
    }
    result = tools_ambient.ambient_set_policy(
        _ctx(session, context=context), {"off_when_asleep": True, "auto_off": True}
    )
    assert result["observed_after"]["server"]["derived_from_turn"] is True
    assert result["observed_after"]["server"]["changed"] == {"off_when_asleep": False}
    assert result["speech"] == alarm_speech.AMBIENT_OFF_WHEN_ASLEEP_OFF_TR
    assert ambient_service.get_policy(session).off_when_asleep is False
    assert ambient_service.get_policy(session).auto_off_enabled is False  # nothing else moved

    # A stale record (older than the turn window) is not the turn: the arguments apply.
    stale = {
        "last_utterance": {
            **context["last_utterance"],
            "at": (NIGHT - timedelta(hours=1)).isoformat(),
        }
    }
    result = tools_ambient.ambient_set_policy(_ctx(session, context=stale), {"keep_on": True})
    assert result["observed_after"]["server"]["derived_from_turn"] is False
    assert result["speech"] == alarm_speech.AMBIENT_KEEP_ON_TR
    assert ambient_service.get_policy(session).keep_on is True

    # Saying it again changes nothing, and still confirms the preference that stands.
    again = tools_ambient.ambient_set_policy(_ctx(session, context=stale), {"keep_on": True})
    assert again["execution_status"] == "noop"
    assert again["speech"] == alarm_speech.AMBIENT_KEEP_ON_TR


# ------------------------------------------------------------- the explanation


def test_explain_answers_from_the_facts_and_says_when_there_is_none(
    session, holdoffs, sequence, device
) -> None:
    facts = ambient_service.explain(
        session,
        runtimes=ambient_service.AmbientRuntimes(
            statuses=DeviceStatusRegistry(), holdoffs=holdoffs
        ),
        now=NIGHT,
    )
    speech = facts["speech"]
    assert facts["decision"]["action"] == ACTION_NONE
    assert facts["last_display_action"] is None and facts["last_input_at"] is None
    assert alarm_speech.AMBIENT_EXPLAIN_NO_OFF_TR in speech
    assert "Kayıtlı bir klavye ya da fare kullanımı yok." in speech
    assert "Otomatik ekran kapatma kapalı." in speech
    assert not contains_fake_completion(speech)

    # A real display.off receipt, an input row and a holdoff: all three are named.
    statuses = DeviceStatusRegistry()
    statuses.record(
        uuid.uuid4(), {"display_state": "on", "input_idle_s": 1000, "armed_alarms": 0}, now=NIGHT
    )
    sequence.display_off(session, reason="owner_command", action_id="a-1", now=NIGHT)
    _row(
        session,
        event_type="owner.input_active",
        action="owner_input_active",
        at=NIGHT - timedelta(seconds=30),
    )
    holdoffs.start(SOURCE_INPUT, seconds=600, now=NIGHT - timedelta(seconds=30))
    ambient_service.set_policy(
        session, {"auto_off_enabled": True, "quiet_hours": WINDOW}, holdoffs=holdoffs, now=NIGHT
    )
    facts = ambient_service.explain(
        session,
        runtimes=ambient_service.AmbientRuntimes(statuses=statuses, holdoffs=holdoffs),
        now=NIGHT,
    )
    speech = facts["speech"]
    assert facts["last_display_action"]["capability"] == "display.off"
    assert "kapattım" in speech and "siz istediniz" in speech
    assert "Son klavye ya da fare kullanımı" in speech
    assert "Otomatik ekran kapatma açık" in speech
    assert "Sessiz saatler 23:30-07:30; şu an içindeyiz." in speech
    assert facts["decision"]["reason"].startswith("holdoff:")
    assert "otomatik kapatma beklemede" in speech
    assert not contains_fake_completion(speech)


def test_the_explain_tool_is_a_query_that_speaks_the_facts(session, holdoffs) -> None:
    registry = default_registry()
    spec = registry.get("ambient.explain")
    assert spec is not None and "speech" in spec.description
    result = tools_ambient.ambient_explain(_ctx(session), {})
    assert result["routed"] == "ambient_policy"
    assert result["speech"] and "decision" in result and "policy" in result


# --------------------------------------------------------------------- the route

ROUTE_TABLES = [
    WakeAlarm.__table__,
    AmbientPolicyRow.__table__,
    Routine.__table__,
    RoutineFiring.__table__,
    ActivityEventRow.__table__,
    Device.__table__,
]


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ROUTE_TABLES:
        table.create(engine)
    settings = Settings(_env_file=None)
    runtime = ArtifactRuntime(settings)
    runtime._engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.artifacts = runtime
    app.state.wake_sequence = None
    app.state.alarm_audio_store = AudioStore()
    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    try:
        yield test_client
    finally:
        test_client.close()
        engine.dispose()


def test_the_policy_route_takes_the_new_settings_and_the_explain_route_answers(client) -> None:
    put = client.put(
        "/v1/ambient/policy",
        json={
            "keep_on": True,
            "quiet_hours": {"start": "23:30", "end": "07:30"},
            "camera_unknown_grace_s": 60,
        },
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["policy"]["keep_on"] is True
    assert body["policy"]["quiet_hours"]["start"] == "23:30"
    assert body["policy"]["camera_unknown_grace_s"] == 60
    assert body["decision"]["reason"] in ("display_not_on", "owner_keep_on")

    bad = client.put("/v1/ambient/policy", json={"quiet_hours": {"start": "late", "end": "07:30"}})
    assert bad.status_code == 422

    cleared = client.put("/v1/ambient/policy", json={"clear_quiet_hours": True})
    assert cleared.status_code == 200 and cleared.json()["policy"]["quiet_hours"] is None

    explained = client.get("/v1/ambient/explain")
    assert explained.status_code == 200, explained.text
    assert explained.json()["speech"]
    assert "'Ekranı açık tut' tercihi geçerli" in explained.json()["speech"]


# ------------------------------------------------------------- no host sleep


def test_no_device_call_in_the_display_path_can_sleep_lock_or_shut_down_the_host() -> None:
    """ADR-0079 §9 (and 14.13): DISPLAY OFF != SYSTEM SLEEP, on the cloud side too."""
    forbidden = ("sleep", "hibernate", "shutdown", "lock", "logoff", "reboot", "suspend")
    for capability in RECEIPT_BY_DEVICE_CALL:
        assert not any(word in capability.lower() for word in forbidden), capability


def test_a_spoken_wait_reaches_the_policy_and_is_said_back(session, holdoffs) -> None:
    """The owner's own example, end to end: the screens go dark after fifteen minutes away
    and they want five. Every piece of this existed except the two lines that carried the
    number -- the router returned booleans, and the tool's reader dropped anything that was
    not one (ADR-0108)."""
    assert ambient_service.get_policy(session).away_after_s == 900

    context = {
        "last_utterance": {
            "at": NIGHT.isoformat().replace("+00:00", "Z"),
            "intent": "ambient_policy_set",
            "policy_changes": resolve_intent("Ekran kapanma süresini 5 dakika yap.").policy_changes,
        }
    }
    result = tools_ambient.ambient_set_policy(_ctx(session, context=context), {})

    assert ambient_service.get_policy(session).away_after_s == 300
    assert result["observed_after"]["server"]["changed"] == {"away_after_s": 300}
    # It says the number back: "tamam" would leave the owner not knowing which one landed.
    assert "5 dakika" in result["speech"]


def test_a_wait_the_reader_used_to_drop_is_no_longer_dropped(session, holdoffs) -> None:
    """The narrow guard on the exact line that lost it: a bool-only filter over the
    recorded changes. With ints dropped, the tool would have raised "needs at least one
    of" on a sentence the router had understood perfectly."""
    context = {
        "last_utterance": {
            "at": NIGHT.isoformat().replace("+00:00", "Z"),
            "intent": "ambient_policy_set",
            "policy_changes": {"asleep_after_s": 1200},
        }
    }
    result = tools_ambient.ambient_set_policy(_ctx(session, context=context), {})

    assert result["observed_after"]["server"]["derived_from_turn"] is True
    assert ambient_service.get_policy(session).asleep_after_s == 1200
    assert "20 dakika" in result["speech"]
