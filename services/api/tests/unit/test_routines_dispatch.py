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
from app.broker.models import AuditEvent
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
    # The queue path audits exactly like the push path does. Leaving this table out
    # would have let the queueing tests pass against a half-built world.
    AuditEvent.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _make_realtime_session(
    factory, *, state, device_id, expires_at, client_kind="web", created_at=None, updated_at=None
) -> uuid.UUID:
    with factory() as session:
        row = RealtimeSessionRow(
            id=uuid.uuid4(),
            provider="sim",
            transport="simulated",
            client_kind=client_kind,
            device_id=device_id,
            owner_session_id=uuid.uuid4(),
            state=state,
            expires_at=expires_at,
        )
        if created_at is not None:
            row.created_at = created_at
        if updated_at is not None:
            row.updated_at = updated_at
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


def test_realtime_say_briefing_finds_a_session_that_never_expires(
    realtime_session_factory,
) -> None:
    """ADR-0105 made ``expires_at`` NULLABLE -- NULL means "this ends when the owner
    ends it", which is what the owner asked for ("ses oturumu hiç kapanmasın").

    SQL comparison with NULL is NULL, never true, so ``expires_at > now`` excluded
    exactly those sessions: the ones that outlive everything. Production held an ACTIVE
    one the moment the two changes met, and the alarm's spoken briefing would have
    answered ``no_live_session`` -- truthfully, and uselessly, to an owner sitting in
    front of an open session. ADR-0105 changed the column and never went looking for
    its readers; this is that reader.
    """
    device_id = uuid.uuid4()
    session_id = _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=device_id,
        expires_at=None,
    )
    sideband = RecordingSideband(deliver=True)
    briefing = RealtimeSayBriefing(session_factory=realtime_session_factory, sideband=sideband)

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.delivered is True
    pushed_device_id, frame = sideband.frames[0]
    assert pushed_device_id == device_id
    assert frame["session_id"] == str(session_id)


def test_realtime_say_briefing_not_delivered_with_no_live_session(realtime_session_factory) -> None:
    sideband = RecordingSideband(deliver=True)
    briefing = RealtimeSayBriefing(session_factory=realtime_session_factory, sideband=sideband)

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.delivered is False
    assert delivery.reason == "no_live_session"
    assert sideband.frames == []


def test_a_session_with_no_bound_device_is_queued_not_refused(
    realtime_session_factory,
) -> None:
    """The bug this file used to pin shut.

    Every realtime session this owner has ever opened is ``web`` or ``cli`` with
    ``device_id IS NULL`` -- 94 of them in production on 2026-09-11, not one bound to a
    device. ``SB_SAY`` targets a device, so the old ``no_bound_device`` refusal was
    correct about the device and wrong about the owner: the morning briefing was never
    once spoken to the client they actually use. The browser shell drains
    ``pending_sideband`` on its next poll, so for that client the buffer IS the delivery.
    """
    session_id = _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_CREATED,
        device_id=None,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    sideband = RecordingSideband(deliver=True)
    briefing = RealtimeSayBriefing(session_factory=realtime_session_factory, sideband=sideband)

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.delivered is False, "a queue is not a delivery; the drain is"
    assert delivery.reason == "queued_to_session"
    assert sideband.frames == [], "there is no device; nothing may be pushed to one"

    # The frame is really in the session's buffer, normalized, and reachable by the drain
    # the browser shell calls -- not merely reported as queued.
    with realtime_session_factory() as db:
        row = db.get(RealtimeSessionRow, session_id)
        (frame,) = row.context_json["pending_sideband"]
    assert frame["event"] == SB_SAY
    assert frame["session_id"] == str(session_id)
    assert frame["payload"]["text"]
    assert frame["payload"]["routine_id"] == str(ROUTINE_ID)
    assert frame["payload"]["firing_id"] == str(FIRING_ID)


def test_a_device_push_that_fails_falls_back_to_the_session_buffer(
    realtime_session_factory,
) -> None:
    """A bound device that did not take the frame is not a reason for silence: the
    session is still live and its buffer still reaches the owner. ``reason`` says which
    of the two happened, because the owner is entitled to know."""
    session_id = _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    sideband = RecordingSideband(deliver=False)  # push() itself reports failure
    briefing = RealtimeSayBriefing(session_factory=realtime_session_factory, sideband=sideband)

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.delivered is False
    assert delivery.reason == "queued_to_session"
    assert sideband.events() == [SB_SAY], "it tried the device first"
    with realtime_session_factory() as db:
        row = db.get(RealtimeSessionRow, session_id)
        assert len(row.context_json["pending_sideband"]) == 1


def test_a_device_that_takes_the_frame_is_reported_as_delivered_and_queues_nothing(
    realtime_session_factory,
) -> None:
    """The other side of the fallback: a successful push must NOT also queue, or the
    owner hears the same sentence twice -- once now and once on the next drain."""
    session_id = _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    briefing = RealtimeSayBriefing(
        session_factory=realtime_session_factory, sideband=RecordingSideband(deliver=True)
    )

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.reason == "delivered"
    with realtime_session_factory() as db:
        row = db.get(RealtimeSessionRow, session_id)
        assert not (row.context_json or {}).get("pending_sideband")


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


# ------------------------------------------- which live session, when there are several


def test_the_newest_session_loses_to_one_that_can_actually_be_heard(
    realtime_session_factory,
) -> None:
    """Production, 2026-09-11: two live sessions, a ``web`` one and a ``cli`` one,
    neither device-bound. ``created_at DESC`` alone picks whichever was opened last.

    Nothing in this repository drains ``pending_sideband`` from a CLI -- those rows come
    from qualification harnesses -- so queueing to the CLI session would stamp the
    briefing delivered and put the sentence somewhere no one reads. Silent loss is the
    failure the fallback exists to end, so it must not be reintroduced by the lookup.
    """
    now = datetime.now(UTC)
    web_id = _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=None,
        expires_at=None,
        client_kind="web",
        created_at=now - timedelta(hours=10),  # older
    )
    _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=None,
        expires_at=None,
        client_kind="cli",
        created_at=now,  # newer, and unreachable
    )
    briefing = RealtimeSayBriefing(
        session_factory=realtime_session_factory, sideband=RecordingSideband(deliver=True)
    )

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.reason == "queued_to_session"
    assert delivery.detail["session_id"] == str(web_id), "the CLI buffer has no reader"
    with realtime_session_factory() as db:
        assert len(db.get(RealtimeSessionRow, web_id).context_json["pending_sideband"]) == 1


def test_a_bound_device_wins_over_a_newer_browser_session(realtime_session_factory) -> None:
    """A device speaks the sentence now; a buffer speaks it on the next poll. When both
    exist the owner should hear it now."""
    now = datetime.now(UTC)
    device_id = uuid.uuid4()
    _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=device_id,
        expires_at=None,
        client_kind="desktop",
        created_at=now - timedelta(days=2),
    )
    _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=None,
        expires_at=None,
        client_kind="web",
        created_at=now,
    )
    sideband = RecordingSideband(deliver=True)
    briefing = RealtimeSayBriefing(session_factory=realtime_session_factory, sideband=sideband)

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.reason == "delivered"
    assert sideband.frames[0][0] == device_id


def test_a_cli_session_alone_is_still_queued_rather_than_dropped(
    realtime_session_factory,
) -> None:
    """Last resort, not a refusal: a buffer that may be read later still beats certain
    silence. Only the *preference* changes when something better exists."""
    session_id = _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=None,
        expires_at=None,
        client_kind="cli",
    )
    briefing = RealtimeSayBriefing(
        session_factory=realtime_session_factory, sideband=RecordingSideband(deliver=True)
    )

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.reason == "queued_to_session"
    assert delivery.detail["session_id"] == str(session_id)


def test_among_equals_the_one_that_talked_to_us_most_recently_wins(
    realtime_session_factory,
) -> None:
    """The tie-break is recency, but recency means ``updated_at`` -- the field ``_touch``
    sets in the SAME call that drains the buffer. A tab opened this morning and used a
    minute ago is a better listener than one opened an hour ago and silent since; ordering
    by ``created_at`` gets that exactly backwards."""
    now = datetime.now(UTC)
    talkative = _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=None,
        expires_at=None,
        client_kind="web",
        created_at=now - timedelta(hours=3),
        updated_at=now,
    )
    _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=None,
        expires_at=None,
        client_kind="web",
        created_at=now,
        updated_at=now - timedelta(hours=2),
    )
    briefing = RealtimeSayBriefing(
        session_factory=realtime_session_factory, sideband=RecordingSideband(deliver=True)
    )

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.detail["session_id"] == str(talkative)


def test_a_write_that_fails_is_a_failed_delivery_not_an_escaping_exception(
    realtime_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ActionDispatcher._voice_briefing`` does not catch anything from ``narrate``, so
    an exception escaping here abandons an alarm firing mid-dispatch instead of
    reporting it. A database that would not take the frame is a failed delivery like any
    other: the briefing row stays unstamped and is spoken on the next pass."""
    import app.voice.realtime_sessions.service as realtime_service

    _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=None,
        expires_at=None,
        client_kind="web",
    )

    def explode(db, row, frame):
        raise RuntimeError("the database said no")

    monkeypatch.setattr(realtime_service, "queue_sideband_frame", explode)
    briefing = RealtimeSayBriefing(
        session_factory=realtime_session_factory, sideband=RecordingSideband(deliver=True)
    )

    delivery = briefing.narrate(text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID)

    assert delivery.delivered is False
    assert delivery.reason == "queue_failed"


def test_the_same_briefing_is_never_queued_twice_while_it_waits(
    realtime_session_factory,
) -> None:
    """The announcer sweeps every twenty seconds and a web session may not be drained for
    an hour. Without this, the owner would hear one sentence a hundred and eighty times
    the moment they next spoke -- and the buffer caps at fifty, so everything else queued
    behind it would be silently evicted."""
    one = uuid.uuid4()
    session_id = _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=None,
        expires_at=None,
        client_kind="web",
    )
    briefing = RealtimeSayBriefing(
        session_factory=realtime_session_factory, sideband=RecordingSideband(deliver=True)
    )

    first = briefing.narrate(
        text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID, briefing_ids=[one]
    )
    second = briefing.narrate(
        text="Günaydın", routine_id=ROUTINE_ID, firing_id=uuid.uuid4(), briefing_ids=[one]
    )

    assert first.reason == "queued_to_session"
    assert second.reason == "already_queued"
    with realtime_session_factory() as db:
        row = db.get(RealtimeSessionRow, session_id)
        assert len(row.context_json["pending_sideband"]) == 1


def test_a_frame_evicted_before_it_was_ever_heard_is_queued_again(
    realtime_session_factory,
) -> None:
    """The buffer keeps the last fifty frames. A briefing pushed out by a burst of tool
    progress was never heard, so "already queued" must read the buffer rather than
    remember that it once wrote there -- otherwise the eviction is a permanent silence."""
    one = uuid.uuid4()
    session_id = _make_realtime_session(
        realtime_session_factory,
        state=REALTIME_STATE_ACTIVE,
        device_id=None,
        expires_at=None,
        client_kind="web",
    )
    briefing = RealtimeSayBriefing(
        session_factory=realtime_session_factory, sideband=RecordingSideband(deliver=True)
    )
    briefing.narrate(
        text="Günaydın", routine_id=ROUTINE_ID, firing_id=FIRING_ID, briefing_ids=[one]
    )

    with realtime_session_factory() as db:  # the eviction, as the cap would do it
        row = db.get(RealtimeSessionRow, session_id)
        row.context_json = dict(row.context_json or {}) | {"pending_sideband": []}
        db.commit()

    again = briefing.narrate(
        text="Günaydın", routine_id=ROUTINE_ID, firing_id=uuid.uuid4(), briefing_ids=[one]
    )

    assert again.reason == "queued_to_session"
    with realtime_session_factory() as db:
        row = db.get(RealtimeSessionRow, session_id)
        assert len(row.context_json["pending_sideband"]) == 1
