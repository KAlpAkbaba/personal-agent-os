"""Unit tests: app.routines.dispatch (M18, ADR-0060).

Every test fakes BriefingPort/DeviceActionPort (or, for RealtimeSayBriefing/
BrokerDeviceAction themselves, fakes the sideband pusher / broker+selection/device-command
seams one level down) — nothing here opens a browser, plays audio, changes system volume or
turns a display off. ``services/browser/tests/test_test_isolation_guards.py`` is the reason
that boundary exists at all; this file does not need it because it never gets close enough
to a real device to need guarding.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.routines.dispatch as dispatch_mod
from app.devices.selection import NoCapableDeviceError
from app.narration.models import PronunciationEntry
from app.routines.actions import (
    ACTION_KIND_ALARM,
    ACTION_KIND_BROWSER_ACTION,
    ACTION_KIND_DISPLAY_ACTION,
    ACTION_KIND_MEDIA_PLAYBACK,
    ACTION_KIND_VOICE_BRIEFING,
    DEFAULT_WAKE_VOLUME_END,
    DEFAULT_WAKE_VOLUME_RAMP_SECONDS,
    DEFAULT_WAKE_VOLUME_START,
    DISPATCH_STATUS_FAILED,
    DISPATCH_STATUS_REFUSED,
    DISPATCH_STATUS_SUCCEEDED,
)
from app.routines.dispatch import (
    CAPABILITY_BROWSER_NAVIGATE,
    CAPABILITY_BROWSER_SESSION_OPEN,
    CAPABILITY_DESKTOP_ALARM_START,
    DISPLAY_ACTION_QUALIFIED,
    ActionDispatcher,
    BriefingDelivery,
    DeviceRunResult,
    RealtimeSayBriefing,
    get_routine_dispatcher,
    register_routine_dispatcher,
)
from app.voice.realtime_sessions.models import (
    REALTIME_STATE_ACTIVE,
    REALTIME_STATE_CREATED,
    RealtimeSessionRow,
)
from app.voice.realtime_sessions.sideband import SB_SAY, RecordingSideband

ROUTINE_ID = uuid.uuid4()
FIRING_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _reset_routine_dispatcher_registry():
    """The registry is a process-wide global (house style: app.devices.commands.
    register_broker_runtime) — every test that touches it resets it, so it never leaks
    into a test file that never wires it (app.routines.service's own tests, notably)."""
    register_routine_dispatcher(None)
    yield
    register_routine_dispatcher(None)


class FakeBriefing:
    def __init__(self, delivery: BriefingDelivery) -> None:
        self.delivery = delivery
        self.calls: list[dict[str, object]] = []

    def narrate(self, *, text, routine_id, firing_id):
        self.calls.append({"text": text, "routine_id": routine_id, "firing_id": firing_id})
        return self.delivery


class FakeDeviceAction:
    """Replays a queued list of ``DeviceRunResult``, one per ``.run()`` call, and records
    every call's arguments for assertions."""

    def __init__(self, results: list[DeviceRunResult]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, object]] = []

    def run(self, *, capability, payload, idempotency_key, timeout_s):
        self.calls.append(
            {
                "capability": capability,
                "payload": payload,
                "idempotency_key": idempotency_key,
                "timeout_s": timeout_s,
            }
        )
        if not self._results:
            raise AssertionError("FakeDeviceAction.run called more times than results queued")
        return self._results.pop(0)


def _dispatcher(*, briefing=None, device_action=None, routine_label=None) -> ActionDispatcher:
    return ActionDispatcher(
        briefing=briefing or FakeBriefing(BriefingDelivery(True)),
        device_action=device_action or FakeDeviceAction([DeviceRunResult(True)]),
        routine_label=routine_label,
    )


# --------------------------------------------------------------------------- voice_briefing


def test_voice_briefing_routes_to_the_briefing_port_and_succeeds_when_delivered() -> None:
    briefing = FakeBriefing(BriefingDelivery(True, "delivered"))
    outcome = _dispatcher(briefing=briefing).dispatch(
        routine_id=ROUTINE_ID,
        firing_id=FIRING_ID,
        action={"kind": ACTION_KIND_VOICE_BRIEFING, "detail": {"text": "Günaydın"}},
    )
    assert outcome.status == DISPATCH_STATUS_SUCCEEDED
    assert briefing.calls == [
        {"text": "Günaydın", "routine_id": ROUTINE_ID, "firing_id": FIRING_ID}
    ]


def test_voice_briefing_not_delivered_is_a_failed_outcome_not_a_success() -> None:
    """``delivered`` is the sideband push's own boolean and nothing else - a queued-but-
    undelivered frame must not read as success (module docstring / ADR-0060)."""
    briefing = FakeBriefing(BriefingDelivery(False, "no_live_session"))
    outcome = _dispatcher(briefing=briefing).dispatch(
        routine_id=ROUTINE_ID,
        firing_id=FIRING_ID,
        action={"kind": ACTION_KIND_VOICE_BRIEFING, "detail": {"text": "Günaydın"}},
    )
    assert outcome.status == DISPATCH_STATUS_FAILED
    assert outcome.ok is False
    assert "no_live_session" in outcome.reason


def test_voice_briefing_missing_text_is_refused_without_calling_the_port() -> None:
    briefing = FakeBriefing(BriefingDelivery(True))
    outcome = _dispatcher(briefing=briefing).dispatch(
        routine_id=ROUTINE_ID,
        firing_id=FIRING_ID,
        action={"kind": ACTION_KIND_VOICE_BRIEFING, "detail": {}},
    )
    assert outcome.status == DISPATCH_STATUS_REFUSED
    assert briefing.calls == []


# ------------------------------------------------------------------------------------ alarm


def test_alarm_routes_to_alarm_start_with_the_firing_id_and_routine_label() -> None:
    device = FakeDeviceAction(
        [DeviceRunResult(True, result={"started": True, "alarm_id": str(FIRING_ID)})]
    )
    dispatcher = _dispatcher(device_action=device, routine_label=lambda rid: "Sabah alarmı")
    outcome = dispatcher.dispatch(
        routine_id=ROUTINE_ID,
        firing_id=FIRING_ID,
        action={
            "kind": ACTION_KIND_ALARM,
            "detail": {"wake_volume": {"start": 0.1, "end": 0.6, "ramp_seconds": 120}},
        },
    )
    assert outcome.status == DISPATCH_STATUS_SUCCEEDED
    assert len(device.calls) == 1
    call = device.calls[0]
    assert call["capability"] == CAPABILITY_DESKTOP_ALARM_START
    assert call["payload"]["alarm_id"] == str(FIRING_ID)
    assert call["payload"]["wake_volume"] == {"start": 0.1, "end": 0.6, "ramp_seconds": 120}
    assert call["payload"]["label"] == "Sabah alarmı"
    assert call["payload"]["max_duration_s"] == 300


def test_alarm_uses_ramp_defaults_when_wake_volume_absent() -> None:
    device = FakeDeviceAction([DeviceRunResult(True)])
    outcome = _dispatcher(device_action=device).dispatch(
        routine_id=ROUTINE_ID, firing_id=FIRING_ID, action={"kind": ACTION_KIND_ALARM, "detail": {}}
    )
    assert outcome.status == DISPATCH_STATUS_SUCCEEDED
    assert device.calls[0]["payload"]["wake_volume"] == {
        "start": DEFAULT_WAKE_VOLUME_START,
        "end": DEFAULT_WAKE_VOLUME_END,
        "ramp_seconds": DEFAULT_WAKE_VOLUME_RAMP_SECONDS,
    }


@pytest.mark.parametrize(
    "wake_volume",
    [
        {"start": 0.9, "end": 1.0, "ramp_seconds": 1},  # a jolt, not a ramp
        {"start": 0.3, "end": 0.1, "ramp_seconds": 10},  # start above end
        {"start": 0.1, "end": 0.5, "ramp_seconds": 0},  # non-positive ramp
    ],
)
def test_alarm_ramp_is_re_asserted_at_dispatch_and_refused_if_violated(wake_volume) -> None:
    """Defence in depth (ADR-0060): validate_alarm already refuses this at routine
    CREATION time; dispatch must refuse it too for a row written before that validator
    existed, and — either way — never invent or clamp a volume, and never reach a device."""
    device = FakeDeviceAction([])
    outcome = _dispatcher(device_action=device).dispatch(
        routine_id=ROUTINE_ID,
        firing_id=FIRING_ID,
        action={"kind": ACTION_KIND_ALARM, "detail": {"wake_volume": wake_volume}},
    )
    assert outcome.status == DISPATCH_STATUS_REFUSED
    assert device.calls == []


def test_alarm_device_failure_is_a_failed_outcome() -> None:
    device = FakeDeviceAction([DeviceRunResult(False, "no_capable_device", "çevrimiçi cihaz yok")])
    outcome = _dispatcher(device_action=device).dispatch(
        routine_id=ROUTINE_ID, firing_id=FIRING_ID, action={"kind": ACTION_KIND_ALARM, "detail": {}}
    )
    assert outcome.status == DISPATCH_STATUS_FAILED
    assert "no_capable_device" in outcome.reason


# ---------------------------------------------------------------------------- media_playback


def test_media_playback_opens_a_session_then_navigates_with_the_owners_exact_url() -> None:
    owner_url = "https://example.com/owner-chosen-episode?ep=42&Ref=Keep-Me"
    device = FakeDeviceAction(
        [
            DeviceRunResult(True, result={"created": True}),
            DeviceRunResult(True, result={"url": owner_url, "page_kind": "ok"}),
        ]
    )
    outcome = _dispatcher(device_action=device).dispatch(
        routine_id=ROUTINE_ID,
        firing_id=FIRING_ID,
        action={
            "kind": ACTION_KIND_MEDIA_PLAYBACK,
            "detail": {"url": owner_url, "title": "Bölüm 42"},
        },
    )
    assert outcome.status == DISPATCH_STATUS_SUCCEEDED
    assert len(device.calls) == 2
    open_call, navigate_call = device.calls
    assert open_call["capability"] == CAPABILITY_BROWSER_SESSION_OPEN
    assert navigate_call["capability"] == CAPABILITY_BROWSER_NAVIGATE
    # byte for byte, never rewritten, never substituted (task brief's own acceptance case).
    assert navigate_call["payload"]["url"] == owner_url


def test_media_playback_never_navigates_if_session_open_fails() -> None:
    device = FakeDeviceAction([DeviceRunResult(False, "dependency_unavailable", "worker offline")])
    outcome = _dispatcher(device_action=device).dispatch(
        routine_id=ROUTINE_ID,
        firing_id=FIRING_ID,
        action={"kind": ACTION_KIND_MEDIA_PLAYBACK, "detail": {"url": "https://example.com/x"}},
    )
    assert outcome.status == DISPATCH_STATUS_FAILED
    assert len(device.calls) == 1  # navigate was never attempted


def test_media_playback_missing_url_is_refused_without_touching_a_device() -> None:
    device = FakeDeviceAction([])
    outcome = _dispatcher(device_action=device).dispatch(
        routine_id=ROUTINE_ID,
        firing_id=FIRING_ID,
        action={"kind": ACTION_KIND_MEDIA_PLAYBACK, "detail": {}},
    )
    assert outcome.status == DISPATCH_STATUS_REFUSED
    assert device.calls == []


# ----------------------------------------------------------------------------- browser_action


def test_browser_action_routes_to_browser_dot_action_name_and_strips_the_action_key() -> None:
    device = FakeDeviceAction([DeviceRunResult(True, result={"ok": True})])
    outcome = _dispatcher(device_action=device).dispatch(
        routine_id=ROUTINE_ID,
        firing_id=FIRING_ID,
        action={
            "kind": ACTION_KIND_BROWSER_ACTION,
            "detail": {"action": "navigate", "url": "https://example.com", "session_id": "s1"},
        },
    )
    assert outcome.status == DISPATCH_STATUS_SUCCEEDED
    call = device.calls[0]
    assert call["capability"] == "browser.navigate"
    assert call["payload"] == {"url": "https://example.com", "session_id": "s1"}


def test_browser_action_outside_the_allowlist_is_refused_and_names_it() -> None:
    device = FakeDeviceAction([])
    outcome = _dispatcher(device_action=device).dispatch(
        routine_id=ROUTINE_ID,
        firing_id=FIRING_ID,
        action={"kind": ACTION_KIND_BROWSER_ACTION, "detail": {"action": "eval_javascript"}},
    )
    assert outcome.status == DISPATCH_STATUS_REFUSED
    assert "eval_javascript" in outcome.reason
    assert device.calls == []


# ----------------------------------------------------------------------------- display_action


def test_display_action_is_always_refused_and_never_reaches_a_device() -> None:
    assert DISPLAY_ACTION_QUALIFIED is False  # the product gate this test pins
    device = FakeDeviceAction([])
    outcome = _dispatcher(device_action=device).dispatch(
        routine_id=ROUTINE_ID,
        firing_id=FIRING_ID,
        action={"kind": ACTION_KIND_DISPLAY_ACTION, "detail": {"action": "off"}},
    )
    assert outcome.status == DISPATCH_STATUS_REFUSED
    assert device.calls == []
    assert outcome.detail.get("code") == "display_action_not_qualified"


# -------------------------------------------------------------------------- unknown action kind


def test_unknown_action_kind_is_refused() -> None:
    outcome = _dispatcher().dispatch(
        routine_id=ROUTINE_ID, firing_id=FIRING_ID, action={"kind": "make_coffee", "detail": {}}
    )
    assert outcome.status == DISPATCH_STATUS_REFUSED


# ------------------------------------------------------------------------------------ registry


def test_registry_round_trips_and_defaults_to_none() -> None:
    assert get_routine_dispatcher() is None
    dispatcher = _dispatcher()
    register_routine_dispatcher(dispatcher)
    assert get_routine_dispatcher() is dispatcher
    register_routine_dispatcher(None)
    assert get_routine_dispatcher() is None


# ------------------------------------------------------------------- BrokerDeviceAction seam


class _ClosableSession:
    def close(self) -> None:  # pragma: no cover - trivial
        pass


def test_broker_device_action_reports_dependency_unavailable_with_no_broker(monkeypatch) -> None:
    monkeypatch.setattr(dispatch_mod, "get_broker_runtime", lambda: None)
    action = dispatch_mod.BrokerDeviceAction(session_factory=lambda: _ClosableSession())
    result = action.run(capability="browser.chrome", payload={}, idempotency_key="k", timeout_s=1.0)
    assert result.ok is False
    assert result.error_class == "dependency_unavailable"


def test_broker_device_action_never_lets_no_capable_device_escape(monkeypatch) -> None:
    """Task brief: NoCapableDeviceError never escapes this class - it becomes a failed
    DeviceRunResult with error_class="no_capable_device"."""
    monkeypatch.setattr(dispatch_mod, "get_broker_runtime", lambda: object())
    monkeypatch.setattr(dispatch_mod, "list_device_views", lambda session, runtime: [])

    def _raise(*args, **kwargs):
        raise NoCapableDeviceError("çevrimiçi cihaz yok", capability="browser.chrome")

    monkeypatch.setattr(dispatch_mod, "select_device", _raise)

    action = dispatch_mod.BrokerDeviceAction(session_factory=lambda: _ClosableSession())
    result = action.run(capability="browser.chrome", payload={}, idempotency_key="k", timeout_s=1.0)
    assert result.ok is False
    assert result.error_class == "no_capable_device"
    assert "çevrimiçi cihaz yok" in result.message


# --------------------------------------------------------------------- RealtimeSayBriefing seam


@pytest.fixture()
def realtime_session_factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    RealtimeSessionRow.__table__.create(engine)
    PronunciationEntry.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _make_realtime_session(factory, *, state, device_id, expires_at) -> uuid.UUID:
    with factory() as session:
        row = RealtimeSessionRow(
            id=uuid.uuid4(),
            provider="sim",
            transport="simulated",
            client_kind="web",
            device_id=device_id,
            owner_session_id=uuid.uuid4(),
            state=state,
            expires_at=expires_at,
        )
        session.add(row)
        session.commit()
        return row.id


def test_realtime_say_briefing_delivers_when_a_live_session_is_bound_to_a_device(
    realtime_session_factory,
) -> None:
    device_id = uuid.uuid4()
    session_id = _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=device_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    sideband = RecordingSideband(deliver=True)
    briefing = RealtimeSayBriefing(session_factory=realtime_session_factory, sideband=sideband)

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.delivered is True
    assert sideband.events() == [SB_SAY]
    pushed_device_id, frame = sideband.frames[0]
    assert pushed_device_id == device_id
    assert frame["session_id"] == str(session_id)
    assert frame["payload"]["text"]


def test_realtime_say_briefing_not_delivered_with_no_live_session(realtime_session_factory) -> None:
    sideband = RecordingSideband(deliver=True)
    briefing = RealtimeSayBriefing(session_factory=realtime_session_factory, sideband=sideband)

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.delivered is False
    assert delivery.reason == "no_live_session"
    assert sideband.frames == []


def test_realtime_say_briefing_not_delivered_when_session_has_no_bound_device(
    realtime_session_factory,
) -> None:
    _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_CREATED,
        device_id=None,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    sideband = RecordingSideband(deliver=True)
    briefing = RealtimeSayBriefing(session_factory=realtime_session_factory, sideband=sideband)

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.delivered is False
    assert delivery.reason == "no_bound_device"
    assert sideband.frames == []


def test_realtime_say_briefing_not_delivered_when_the_push_itself_fails(
    realtime_session_factory,
) -> None:
    device_id = uuid.uuid4()
    _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=device_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    sideband = RecordingSideband(deliver=False)  # push() itself reports failure
    briefing = RealtimeSayBriefing(session_factory=realtime_session_factory, sideband=sideband)

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.delivered is False
    assert delivery.reason == "push_failed"


def test_realtime_say_briefing_ignores_an_expired_session(realtime_session_factory) -> None:
    device_id = uuid.uuid4()
    _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=device_id,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),  # already expired
    )
    sideband = RecordingSideband(deliver=True)
    briefing = RealtimeSayBriefing(session_factory=realtime_session_factory, sideband=sideband)

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.delivered is False
    assert delivery.reason == "no_live_session"
