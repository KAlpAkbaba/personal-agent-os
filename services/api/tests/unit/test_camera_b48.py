"""B48: the device camera's Cloud Core half (rows 300, 307, 308, 326, 327, 331, 333, 671).

The device (``devices/windows-agent/.../Camera``) analyses frames in memory and sends only the
seven structured observation fields plus its camera's state on the heartbeat
(DEVICE_PROTOCOL.md §6o). This suite drives that wire shape through the REAL objects:
``DeviceStatusRegistry`` -> ``ingest_status`` -> the presence boundary -> the fusion engine ->
``ambient.service.tick`` -> ``WakeSequence.display_off``. The only fake is the device port.

The end-to-end tests are the automated proof of row 333: a night of device heartbeats makes
"Uyurken ekranı kapat" actually fire - and the same heartbeats in the afternoon do not,
because the owner's quiet hours set the threshold.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.alarms.audio_store import AudioStore
from app.alarms.models import AmbientPolicyRow, WakeAlarm
from app.alarms.sequence import WakeSequence
from app.ambient import camera as ambient_camera
from app.ambient import ingest as ambient_ingest
from app.ambient import service as ambient_service
from app.ambient.holdoff import HoldoffRegistry
from app.ambient.policy import (
    ACTION_DISPLAY_OFF,
    REASON_NOT_HELD_LONG_ENOUGH,
    REASON_OWNER_LIKELY_ASLEEP,
)
from app.artifacts.runtime import ArtifactRuntime
from app.broker.models import AuditEvent, Device, DeviceCommand
from app.config import Settings
from app.devices.status import (
    DeviceStatusRegistry,
    get_status_registry,
    parse_status,
    set_status_registry,
)
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.presence import engine as presence_engine
from app.presence.engine import PresenceFusionEngine
from app.presence.eye import disable_eye
from app.presence.states import PresenceState
from app.routines.models import Routine, RoutineFiring
from app.uistate.publisher import UiStatePublisher, set_publisher
from tests.alarms_support import ALL_TABLES, FakeDeviceAction, happy_device_results
from tests.identity_support import authenticate, install_identity

DEVICE = uuid.UUID("5d9d7a52-0000-4000-8000-00000000b048")
#: 00:00 in Istanbul - inside a 22:00-07:00 quiet window.
NIGHT = datetime(2026, 9, 16, 21, 0, tzinfo=UTC)
#: 14:00 in Istanbul - outside it.
AFTERNOON = datetime(2026, 9, 16, 11, 0, tzinfo=UTC)
QUIET = {"start": "22:00", "end": "07:00", "timezone": "Europe/Istanbul"}


@pytest.fixture()
def db() -> Iterator[Any]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in [*ALL_TABLES, Device.__table__, DeviceCommand.__table__, AuditEvent.__table__]:
        table.create(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_process() -> Iterator[None]:
    set_publisher(UiStatePublisher())
    ambient_camera.reset()
    ambient_service.cancel_display_test()
    ambient_service.reset_presence_watermark()
    ambient_service.reset_holdoff_restore()
    previous = presence_engine.get_engine()
    presence_engine.set_engine(PresenceFusionEngine())
    # push_now walks the process-wide registry; a fresh one keeps other suites' devices out.
    previous_registry = get_status_registry()
    set_status_registry(DeviceStatusRegistry())
    yield
    set_status_registry(previous_registry)
    presence_engine.set_engine(previous)
    ambient_camera.reset()
    ambient_service.reset_holdoff_restore()
    set_publisher(UiStatePublisher())


class Sent:
    """A camera-mode sender that records instead of creating a command row."""

    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, str]] = []

    def __call__(self, session: Any, device_id: uuid.UUID, mode: str, now: datetime) -> None:
        self.calls.append((device_id, mode))


def _observation(
    at: datetime, *, posture: str = "resting", present: bool = True, **extra: Any
) -> dict:
    body = {
        "person_present": present,
        "presence_confidence": 0.95,
        "activity_level": "none",
        "posture": posture,
        "awake_state": "resting" if posture == "resting" else "uncertain",
        "observed_at": at.isoformat().replace("+00:00", "Z"),
        "source": "camera",
    }
    body.update(extra)
    return body


def _heartbeat(
    at: datetime,
    *,
    presence: dict | None = None,
    mode: str = "periodic",
    state: str = "idle",
    idle_s: float = 7200.0,
) -> dict:
    return {
        "input_idle_s": idle_s,
        "display_state": "on",
        "display_observed_at": at.isoformat(),
        "alarm_ringing": False,
        "ringing_alarm_id": None,
        "armed_alarms": 0,
        "next_alarm_at": None,
        "local_alarm_fired": [],
        "camera": {
            "mode": mode,
            "state": state,
            "interval_s": 60,
            "indicator": "armed",
            "last_check_at": at.isoformat(),
            "error": None,
        },
        "presence": presence,
    }


def _policy(db, *, mode: str = "periodic", at: datetime) -> None:
    ambient_service.set_policy(
        db,
        {
            "auto_off_enabled": True,
            "off_when_asleep": True,
            "quiet_hours": QUIET,
            "camera_mode": mode,
        },
        holdoffs=HoldoffRegistry(),
        now=at - timedelta(hours=3),
    )


# ------------------------------------------------------------------ the wire shape


def test_the_camera_block_is_normalised_and_an_unknown_value_is_nobody_knows() -> None:
    status = parse_status(DEVICE, _heartbeat(NIGHT, presence=_observation(NIGHT)))
    assert status is not None
    assert status.camera == {
        "mode": "periodic",
        "state": "idle",
        "interval_s": 60,
        "indicator": "armed",
        "last_check_at": "2026-09-16T21:00:00Z",
        "error": None,
    }
    assert status.as_dict()["camera"]["mode"] == "periodic"
    # The derived observation is readable on the device row, under its own name.
    assert status.as_dict()["camera_observation"]["posture"] == "resting"
    assert "presence" not in status.as_dict()
    smuggled = parse_status(DEVICE, {"presence": {**_observation(NIGHT), "note": "x" * 500}})
    assert smuggled is not None and smuggled.as_dict()["camera_observation"] is None

    odd = parse_status(
        DEVICE, {"camera": {"mode": "record", "state": "filming", "interval_s": True}}
    )
    assert odd is not None and odd.camera is not None
    assert odd.camera["mode"] is None and odd.camera["state"] is None
    assert odd.camera["interval_s"] is None

    no_camera = parse_status(DEVICE, {"input_idle_s": 3.0})
    assert no_camera is not None and no_camera.camera is None and no_camera.presence is None


def test_a_nested_presence_value_is_not_even_kept() -> None:
    status = parse_status(
        DEVICE, {"presence": {**_observation(NIGHT), "posture": {"frame": "iVBOR"}}}
    )
    assert status is not None and status.presence is None


def test_the_registry_hands_out_each_camera_reading_once_and_ignores_a_future_stamp() -> None:
    registry = DeviceStatusRegistry()
    first = registry.record(DEVICE, _heartbeat(NIGHT, presence=_observation(NIGHT)), now=NIGHT)
    repeat = registry.record(
        DEVICE, _heartbeat(NIGHT, presence=_observation(NIGHT)), now=NIGHT + timedelta(seconds=10)
    )
    newer_at = NIGHT + timedelta(seconds=60)
    newer = registry.record(
        DEVICE, _heartbeat(newer_at, presence=_observation(newer_at)), now=newer_at
    )
    assert first is not None and first.new_camera_observation is not None
    assert repeat is not None and repeat.new_camera_observation is None
    assert newer is not None and newer.new_camera_observation is not None

    far = newer_at + timedelta(days=1)
    future = registry.record(
        DEVICE,
        _heartbeat(newer_at, presence=_observation(far)),
        now=newer_at + timedelta(seconds=10),
    )
    assert future is not None and future.new_camera_observation is None
    # ...and it did not become the "newest" that silences the honest reading after it.
    honest_at = newer_at + timedelta(seconds=60)
    honest = registry.record(
        DEVICE, _heartbeat(honest_at, presence=_observation(honest_at)), now=honest_at
    )
    assert honest is not None and honest.new_camera_observation is not None


# ------------------------------------------------------------------ the intake


def test_a_camera_reading_enters_the_presence_model(db) -> None:
    _policy(db, at=NIGHT)
    registry = DeviceStatusRegistry()
    sent = Sent()
    result = ambient_ingest.ingest_status(
        db,
        DEVICE,
        _heartbeat(NIGHT, presence=_observation(NIGHT)),
        statuses=registry,
        holdoffs=HoldoffRegistry(),
        now=NIGHT,
    )
    assert result.camera_observed is True
    assert result.camera_refused is None
    assert presence_engine.get_engine().last_observation_at(source="camera") == NIGHT
    del sent


def test_the_owners_off_outranks_a_reading_already_on_its_way(db) -> None:
    _policy(db, mode="off", at=NIGHT)
    result = ambient_ingest.ingest_status(
        db,
        DEVICE,
        _heartbeat(NIGHT, presence=_observation(NIGHT)),
        statuses=DeviceStatusRegistry(),
        holdoffs=HoldoffRegistry(),
        now=NIGHT,
    )
    assert result.camera_observed is False
    assert result.camera_refused == ambient_ingest.CAMERA_REFUSED_MODE_OFF
    assert presence_engine.get_engine().last_observation_at(source="camera") is None


def test_a_disabled_eye_refuses_the_device_camera_too(db) -> None:
    _policy(db, at=NIGHT)
    disable_eye(db, reason="test")
    result = ambient_ingest.ingest_status(
        db,
        DEVICE,
        _heartbeat(NIGHT, presence=_observation(NIGHT)),
        statuses=DeviceStatusRegistry(),
        holdoffs=HoldoffRegistry(),
        now=NIGHT,
    )
    assert result.camera_observed is False
    assert result.camera_refused == ambient_ingest.CAMERA_REFUSED_MODE_OFF
    assert presence_engine.get_engine().last_observation_at(source="camera") is None


@pytest.mark.parametrize(
    ("tamper", "reason"),
    [
        ({"source": "input"}, "not_a_camera_observation"),
        ({"thumbnail": "x"}, "invalid_observation"),
        ({"presence_confidence": 7}, "invalid_observation"),
    ],
)
def test_the_camera_path_speaks_for_the_camera_only_and_through_the_boundary(
    db, tamper: dict, reason: str
) -> None:
    _policy(db, at=NIGHT)
    result = ambient_ingest.ingest_status(
        db,
        DEVICE,
        _heartbeat(NIGHT, presence=_observation(NIGHT, **tamper)),
        statuses=DeviceStatusRegistry(),
        holdoffs=HoldoffRegistry(),
        now=NIGHT,
    )
    assert result.camera_observed is False
    assert result.camera_refused == reason
    assert presence_engine.get_engine().last_observation_at() is None


# ------------------------------------------------------------------ the relay


def test_a_device_in_the_wrong_mode_is_told_once_a_minute(db) -> None:
    _policy(db, mode="continuous", at=NIGHT)
    status = parse_status(DEVICE, _heartbeat(NIGHT, mode="off", state="off"))
    assert status is not None
    sent = Sent()

    assert ambient_camera.reconcile(db, DEVICE, status, now=NIGHT, send=sent) == "continuous"
    assert (
        ambient_camera.reconcile(db, DEVICE, status, now=NIGHT + timedelta(seconds=30), send=sent)
        is None
    )
    assert (
        ambient_camera.reconcile(db, DEVICE, status, now=NIGHT + timedelta(seconds=61), send=sent)
        == "continuous"
    )
    assert sent.calls == [(DEVICE, "continuous"), (DEVICE, "continuous")]

    matching = parse_status(DEVICE, _heartbeat(NIGHT, mode="continuous", state="capturing"))
    assert matching is not None
    assert (
        ambient_camera.reconcile(db, DEVICE, matching, now=NIGHT + timedelta(hours=1), send=sent)
        is None
    )


def test_the_owners_veto_on_the_device_is_never_argued_with(db) -> None:
    _policy(db, mode="periodic", at=NIGHT)
    vetoed = parse_status(DEVICE, _heartbeat(NIGHT, mode="off", state="vetoed"))
    assert vetoed is not None
    sent = Sent()
    assert ambient_camera.reconcile(db, DEVICE, vetoed, now=NIGHT, send=sent, force=True) is None
    assert sent.calls == []

    # ...but an owner who then turns the mode off everywhere is still obeyed.
    vetoed_on = parse_status(DEVICE, _heartbeat(NIGHT, mode="periodic", state="vetoed"))
    assert vetoed_on is not None
    ambient_service.set_policy(db, {"camera_mode": "off"}, holdoffs=HoldoffRegistry(), now=NIGHT)
    assert ambient_camera.reconcile(db, DEVICE, vetoed_on, now=NIGHT, send=sent) == "off"


def test_a_restarted_device_still_vetoed_is_never_sent_a_non_off_mode(db, monkeypatch) -> None:
    """B48 security review (HIGH): the owner vetoed on the device, the companion restarted
    and now reports mode off with the remembered veto. Every relay path stays silent."""
    _policy(db, mode="continuous", at=NIGHT)
    registry = DeviceStatusRegistry()
    sent = Sent()
    monkeypatch.setattr(ambient_camera, "_send_command", sent)
    restarted = _heartbeat(NIGHT, mode="off", state="vetoed")
    restarted["camera"]["error"] = "owner_closed_on_device"

    for minute in range(5):
        at = NIGHT + timedelta(minutes=minute)
        result = ambient_ingest.ingest_status(
            db, DEVICE, restarted, statuses=registry, holdoffs=HoldoffRegistry(), now=at
        )
        assert result.camera_mode_sent is None

    assert ambient_camera.push_now(db, statuses=registry, now=NIGHT) == {}
    # The owner changes the policy again: still nothing but "off" may go to a vetoed camera.
    ambient_service.set_policy(db, {"camera_mode": "periodic"}, holdoffs=HoldoffRegistry())
    assert ambient_camera.push_now(db, statuses=registry, now=NIGHT) == {}
    assert sent.calls == []


def test_a_device_without_a_camera_path_is_never_asked(db) -> None:
    _policy(db, mode="continuous", at=NIGHT)
    status = parse_status(DEVICE, {"input_idle_s": 3.0})
    assert status is not None
    sent = Sent()
    assert ambient_camera.reconcile(db, DEVICE, status, now=NIGHT, send=sent) is None
    assert sent.calls == []


def test_closing_the_eye_closes_the_device_camera(db) -> None:
    _policy(db, mode="continuous", at=NIGHT)
    registry = DeviceStatusRegistry()
    registry.record(DEVICE, _heartbeat(NIGHT, mode="continuous", state="capturing"), now=NIGHT)
    assert ambient_camera.desired_mode(db) == "continuous"

    disable_eye(db, reason="Kamerayı kapat")
    sent = Sent()
    assert ambient_camera.desired_mode(db) == "off"
    assert ambient_camera.push_now(db, statuses=registry, send=sent, now=NIGHT) == {
        str(DEVICE): "off"
    }


def test_the_relay_creates_a_real_command_row_for_an_advertising_device(db) -> None:
    db.add(
        Device(
            id=DEVICE,
            name="ev-pc",
            platform="windows",
            public_key_spki_b64="x" * 40,
            capabilities_json=["desktop.activity_status", "desktop.camera_mode"],
        )
    )
    db.commit()
    _policy(db, mode="periodic", at=NIGHT)
    status = parse_status(DEVICE, _heartbeat(NIGHT, mode="off", state="off"))
    assert status is not None

    assert ambient_camera.reconcile(db, DEVICE, status, now=NIGHT) == "periodic"

    rows = db.execute(select(DeviceCommand)).scalars().all()
    assert len(rows) == 1
    assert rows[0].capability == "desktop.camera_mode"
    assert rows[0].payload_json == {"mode": "periodic", "reason": "owner_policy"}
    assert rows[0].status == "pending"


def test_an_unknown_camera_mode_is_refused_on_the_way_in(db) -> None:
    with pytest.raises(ValueError):
        ambient_service.set_policy(db, {"camera_mode": "record"}, holdoffs=HoldoffRegistry())
    assert ambient_service.get_policy(db).camera_mode == "off"


# ------------------------------------------------------------------ 307 / 308 / 333


def _night_of_heartbeats(
    db, start: datetime, minutes: int, registry: DeviceStatusRegistry
) -> list[PresenceState]:
    """What the device sends when the owner falls asleep in front of it: upright and still
    for the device's own five minutes, then resting (DEVICE_PROTOCOL.md §6o), one periodic
    check a minute. Returns the engine's state after every heartbeat."""
    states: list[PresenceState] = []
    for minute in range(minutes + 1):
        at = start + timedelta(minutes=minute)
        posture = "upright" if minute < 5 else "resting"
        ambient_ingest.ingest_status(
            db,
            DEVICE,
            _heartbeat(at, presence=_observation(at, posture=posture)),
            statuses=registry,
            holdoffs=HoldoffRegistry(),
            now=at,
        )
        current = presence_engine.get_engine().current()
        states.append(current.state if current else PresenceState.UNKNOWN)
    return states


def test_uyurken_ekrani_kapat_fires_from_device_heartbeats_at_night(db) -> None:
    _policy(db, at=NIGHT)
    registry = DeviceStatusRegistry()
    states = _night_of_heartbeats(db, NIGHT, 34, registry)

    # 307 and 308 are reachable from real device signals, in order.
    assert PresenceState.RESTING in states
    assert PresenceState.LIKELY_ASLEEP in states
    assert states.index(PresenceState.RESTING) < states.index(PresenceState.LIKELY_ASLEEP)
    assert states[-1] is PresenceState.LIKELY_ASLEEP

    device = FakeDeviceAction(results=happy_device_results())
    end = NIGHT + timedelta(minutes=34)
    result = ambient_service.tick(
        db,
        sequence=WakeSequence(device_action=device, tts=None),
        runtimes=ambient_service.AmbientRuntimes(statuses=registry, holdoffs=HoldoffRegistry()),
        now=end,
    )

    assert result.decision.action == ACTION_DISPLAY_OFF, result.decision.as_dict()
    assert result.decision.reason == REASON_OWNER_LIKELY_ASLEEP
    assert result.acted is True
    assert device.capabilities_called() == ["desktop.display_off"]
    assert device.payload_for("desktop.display_off")["reason"] == REASON_OWNER_LIKELY_ASLEEP
    assert result.decision.evidence["quiet_hours"] == "inside"


def test_the_same_heartbeats_in_the_afternoon_do_not_darken_anything(db) -> None:
    _policy(db, at=AFTERNOON)
    registry = DeviceStatusRegistry()
    states = _night_of_heartbeats(db, AFTERNOON, 40, registry)
    # Outside the quiet hours a rest must hold 30 minutes before it is sleep...
    assert states[30] is PresenceState.RESTING
    assert states[-1] is PresenceState.LIKELY_ASLEEP

    device = FakeDeviceAction(results=happy_device_results())
    result = ambient_service.tick(
        db,
        sequence=WakeSequence(device_action=device, tts=None),
        runtimes=ambient_service.AmbientRuntimes(statuses=registry, holdoffs=HoldoffRegistry()),
        now=AFTERNOON + timedelta(minutes=40),
    )
    # ...and the sleep must then hold the longer outside-quiet threshold too.
    assert result.decision.reason == REASON_NOT_HELD_LONG_ENOUGH
    assert result.decision.evidence["quiet_hours"] == "outside"
    assert device.calls == []


def test_a_camera_that_stops_delivering_darkens_nothing(db) -> None:
    _policy(db, at=NIGHT)
    registry = DeviceStatusRegistry()
    _night_of_heartbeats(db, NIGHT, 34, registry)
    device = FakeDeviceAction(results=happy_device_results())
    # Three minutes of silence from the camera (closed, blocked, unplugged): uncertain means ON.
    result = ambient_service.tick(
        db,
        sequence=WakeSequence(device_action=device, tts=None),
        runtimes=ambient_service.AmbientRuntimes(statuses=registry, holdoffs=HoldoffRegistry()),
        now=NIGHT + timedelta(minutes=37),
    )
    assert result.decision.action != ACTION_DISPLAY_OFF
    assert device.calls == []


# ------------------------------------------------------------------ 331: the panel's write


@pytest.fixture()
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        WakeAlarm.__table__,
        AmbientPolicyRow.__table__,
        Routine.__table__,
        RoutineFiring.__table__,
        ActivityEventRow.__table__,
        Device.__table__,
    ):
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
    yield test_client
    engine.dispose()


def test_the_panel_can_choose_a_camera_mode_and_nothing_else(client: TestClient) -> None:
    response = client.put("/v1/ambient/policy", json={"camera_mode": "periodic"})
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] == {"camera_mode": "periodic"}
    assert body["policy"]["camera_mode"] == "periodic"
    assert client.get("/v1/ambient/policy").json()["policy"]["camera_mode"] == "periodic"

    assert client.put("/v1/ambient/policy", json={"camera_mode": "record"}).status_code == 422
    assert client.put("/v1/ambient/policy", json={"camera_mode": "off"}).json()["changed"] == {
        "camera_mode": "off"
    }
