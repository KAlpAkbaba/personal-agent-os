"""M18.3 end to end, through the REAL application object (docs/DECISIONS.md ADR-0078).

The alarm suites prove every component against injected fakes: the voice tools with a
``ToolContext.live`` the test built, the clock with three counters, the sequence with a
scripted device. What none of them proved was the WIRING - and the wiring was wrong:
``RealtimeVoiceRuntime.live_sources()`` never carried the wake sequence or the device-status
registry, so in production ``alarm.stop`` by voice changed the row while the music went on,
and ``display.off`` answered "no device runtime" every time. The recurring defect class this
repository has named twice already: a component built, tested and never wired.

This module drives the owner's evening through the surfaces production uses - the realtime
relay (``POST .../events`` for the words, ``POST .../tool-calls`` for the model's tool), the
``RoutineClock``'s own ``tick_once``, the routine engine, the ``WakeAlarmRunner``, the wake
sequence, the ledger - with exactly one fake: the device, answering from a script. Nothing
sleeps; the clock is told what time it is.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.alarms import service as alarms_service
from app.alarms.models import (
    PLAYED_KIND_YOUTUBE,
    STATE_ARMED,
    STATE_PLAYING,
    STATE_SCHEDULED,
    STATE_STOPPED,
    AmbientPolicyRow,
    WakeAlarm,
)
from app.alarms.routine_port import WakeAlarmRunner
from app.alarms.sequence import RECEIPT_BY_DEVICE_CALL, WakeSequence
from app.ambient import service as ambient_service
from app.artifacts.models import Artifact, ArtifactVersion
from app.artifacts.runtime import ArtifactRuntime
from app.broker import service as broker_service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.config import Settings
from app.devices.status import DeviceStatusRegistry
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.main import create_app
from app.narration.models import NarrationSession, PronunciationEntry
from app.routines import service as routines_service
from app.routines.clock import RoutineClock
from app.routines.dispatch import ActionDispatcher, BriefingDelivery
from app.routines.models import ROUTINE_STATUS_ARMED, Routine, RoutineFiring
from app.uistate.publisher import UiStatePublisher, get_publisher, set_publisher
from app.voice.models import VoiceProfile
from app.voice.providers import FakeTTSProvider
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.simulator import SimulatedRealtimeProvider
from tests.alarms_support import FakeDeviceAction, happy_device_results
from tests.identity_support import IDENTITY_TABLES

IST = ZoneInfo("Europe/Istanbul")
VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"
SONG_URL = "https://www.youtube.com/watch?v=abc123"

TABLES = (
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
    Device.__table__,
    DeviceSession.__table__,
    DeviceCommand.__table__,
    EnrollmentToken.__table__,
    WakeAlarm.__table__,
    AmbientPolicyRow.__table__,
    Routine.__table__,
    RoutineFiring.__table__,
)


class _NoBriefing:
    def narrate(self, *, text, routine_id, firing_id):
        return BriefingDelivery(False, "not_used")


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


@pytest.fixture(autouse=True)
def _fresh_publisher():
    set_publisher(UiStatePublisher())
    yield
    set_publisher(UiStatePublisher())


@pytest.fixture()
def wired():
    """The application, its relay and its clock, with the device as the only fake."""
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

    device = FakeDeviceAction(results=happy_device_results())
    sequence = WakeSequence(device_action=device, tts=FakeTTSProvider(synthetic_speech=False))
    statuses = DeviceStatusRegistry()
    # The one registration production makes (app.main), made the same way here.
    runtime.register_live(wake_sequence=sequence, device_statuses=statuses)

    with broker.session() as db:
        enrolled = broker_service.enroll_device(
            db,
            name="ev-pc",
            platform="windows",
            public_key_spki_b64=_spki(),
            capabilities=["desktop.alarm_arm", "desktop.display_wake", "browser.media_play"],
            trace_id=None,
        )
    broker.connections[enrolled.id] = DeviceConnection(
        device_id=enrolled.id, session_id=uuid.uuid4(), websocket=object()
    )

    dispatcher = ActionDispatcher(
        briefing=_NoBriefing(),
        device_action=device,
        wake_alarm=WakeAlarmRunner(session_factory=factory, sequence=sequence),
    )

    def build_clock() -> RoutineClock:
        """A clock exactly as app.main wires one: the three ticks, in order."""
        return RoutineClock(
            session_factory=factory,
            evaluate_due=lambda s, now: routines_service.evaluate_due(
                s, now=now, dispatcher=dispatcher
            ),
            alarm_tick=lambda s, now: alarms_service.tick(s, sequence=sequence, now=now),
            ambient_tick=lambda s, now: ambient_service.tick(s, sequence=sequence, now=now),
            interval_s=10,
            enabled=True,
        )

    issued = identity.service.issue_session(
        client_kind="desktop", label="pc", device_id=uuid.uuid4()
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issued.token}"
    try:
        yield client, factory, device, statuses, build_clock
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


class _FrozenClock:
    """The alarm service stamps ``playing_since`` / ``greeting_due_at`` with its own
    ``utcnow`` (the runner passes no ``now``), while the ticks take the moment they are
    given. In production both are wall time; here both are THIS clock, moved by hand."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def _tick(clock: RoutineClock, now: datetime, frozen: _FrozenClock | None = None) -> None:
    if frozen is not None:
        frozen.now = now
    assert asyncio.run(clock.tick_once(now=now)) is True


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _alarm(factory, alarm_id: uuid.UUID) -> WakeAlarm:
    with factory() as session:
        alarm = session.get(WakeAlarm, alarm_id)
        assert alarm is not None
        session.refresh(alarm)
        return alarm


def _rows(factory, event_type: str) -> list[ActivityEventRow]:
    with factory() as session:
        return [
            r
            for r in session.execute(select(ActivityEventRow)).scalars().all()
            if r.event_type == event_type
        ]


def _receipted(factory) -> list[str]:
    return [r.action for r in _rows(factory, "action.receipt")]


def _states() -> list[str]:
    return [e.state.value for e in get_publisher().tail(limit=200)]


# ----------------------------------------------------------------- the wiring


def test_the_app_hands_the_wake_sequence_and_the_status_registry_to_the_voice_tools() -> None:
    """The exact defect: what the route gives a tool is what the app built, not None."""
    app = create_app(Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY))
    live = app.state.voice_realtime.live_sources()
    assert live["wake_sequence"] is app.state.wake_sequence
    assert live["device_statuses"] is app.state.device_statuses
    assert live["broker_runtime"] is app.state.broker


def test_a_runtime_registers_live_sources_additively() -> None:
    runtime = RealtimeVoiceRuntime(Settings(_env_file=None), providers={})
    assert "wake_sequence" not in runtime.live_sources()
    marker = object()
    runtime.register_live(wake_sequence=marker)
    runtime.register_live(device_statuses="registry")
    live = runtime.live_sources()
    assert live["wake_sequence"] is marker
    assert live["device_statuses"] == "registry"
    assert live["voice_runtime"] is runtime


def test_no_tool_schema_names_an_argument_the_relay_refuses() -> None:
    """The second wiring defect the end-to-end run surfaced: alarm.create's argument was
    called ``when_text``, and the relay refuses any argument key containing ``text``
    (service.FORBIDDEN_KEY_PARTS - transcripts and credentials never ride a tool call). The
    handler's own tests passed it the key directly and never met the route. Every schema in
    the registry, every nesting level, against the one blocklist."""
    from app.voice.realtime_sessions.service import is_forbidden_key
    from app.voice.realtime_sessions.tools import default_registry

    def walk(schema: dict, path: str = ""):
        for name, sub in (schema.get("properties") or {}).items():
            yield path + name
            if isinstance(sub, dict):
                yield from walk(sub, path + name + ".")

    registry = default_registry()
    refused = {
        name: [
            key
            for key in walk(registry.get(name).parameters)
            if is_forbidden_key(key.split(".")[-1])
        ]
        for name in registry.names()
    }
    assert {name: keys for name, keys in refused.items() if keys} == {}


# ------------------------------------------------------ the owner's evening


def test_the_alarm_path_from_the_owners_words_to_the_receipts(wired, monkeypatch) -> None:
    """Spec §3.1-§3.5, §8, §11 as ONE run through the relay, the clock and the sequence.

    create by voice -> durable schedule in Europe/Istanbul -> the clock arms the device ->
    the clock fires at the instant, once -> display wake, the alarm profile, the media
    verified, the ramp, a receipt per physical step -> the greeting on a later tick with
    duck and restore -> "Alarmı kapat." by voice stops the medium that is playing ->
    the test alarm cleans itself up. Every assertion is on a durable row or a device call.
    """
    client, factory, device, _statuses, build_clock = wired
    clock = build_clock()
    frozen = _FrozenClock(datetime.now(UTC))
    monkeypatch.setattr(alarms_service, "utcnow", frozen)
    sid = _create(client)

    # 1. Created by voice: the canonical router classifies the words, the model calls
    #    the tool, the receipt speaks, the rows are durable and in the owner's timezone.
    said = _say(client, sid, "90 saniye sonra YouTube'dan Time ile test alarmı kur.")
    assert said["resolved_intents"][0]["intent"] == "alarm_test_create"
    before = datetime.now(UTC)
    created = _tool(
        client,
        sid,
        "c-create",
        "alarm.create",
        {
            "when_spoken": "90 saniye sonra test alarmı kur.",
            "test": True,
            "media": {"url": SONG_URL, "title": "Time"},
        },
    )
    assert created["status"] == "succeeded", created
    receipt = created["result"]
    assert receipt["terminal_status"] == "verified"
    assert receipt["speech"]
    alarm_id = uuid.UUID(receipt["alarm"]["alarm_id"])

    alarm = _alarm(factory, alarm_id)
    assert alarm.state == STATE_SCHEDULED and alarm.is_test is True
    scheduled_for = _aware(alarm.scheduled_for)
    assert before + timedelta(seconds=85) <= scheduled_for <= before + timedelta(seconds=95)
    assert alarm.timezone == "Europe/Istanbul"
    assert alarm.local_time == scheduled_for.astimezone(IST).strftime("%H:%M")
    assert alarm.resolved_media_identity == {
        "kind": PLAYED_KIND_YOUTUBE,
        "url": SONG_URL,
        "title": "Time",
    }
    with factory() as session:
        routine = session.get(Routine, alarm.routine_id)
        assert routine is not None and routine.status == ROUTINE_STATUS_ARMED
        assert routine.trigger_kind == "at"
    assert len(_rows(factory, "alarm.scheduled")) == 1

    # 2. The clock arms the device; the device acknowledges; the row says ARMED.
    _tick(clock, before, frozen)
    assert device.count("desktop.alarm_arm") == 1
    assert str(alarm_id) in json.dumps(device.payload_for("desktop.alarm_arm"))
    alarm = _alarm(factory, alarm_id)
    assert alarm.state == STATE_ARMED and alarm.armed_at is not None
    assert "alarm.armed" in _states()

    # 3. At the instant the clock fires it - through the routine engine, the runner and
    #    the sequence - and every physical step leaves a receipt.
    fire_at = scheduled_for + timedelta(seconds=5)
    _tick(clock, fire_at, frozen)
    alarm = _alarm(factory, alarm_id)
    assert alarm.state == STATE_PLAYING, alarm.state
    assert device.count("desktop.play_audio") == 0  # the greeting waits for the ramp
    assert alarm.media_kind == PLAYED_KIND_YOUTUBE
    assert alarm.last_firing_id is not None
    called = device.capabilities_called()
    for capability in (
        "desktop.alarm_disarm",
        "desktop.display_wake",
        "browser.session_open",
        "browser.media_play",
        "browser.media_volume",
    ):
        assert capability in called, called
    assert called.index("desktop.display_wake") < called.index("browser.media_play")
    opened = device.payload_for("browser.session_open")
    assert opened["profile"] == "alarm" and opened["session_kind"] == "media"
    assert device.payload_for("browser.media_play")["url"] == SONG_URL
    assert device.count("desktop.alarm_start") == 0  # music, so no tone
    # One receipt per physical step, under the receipt vocabulary ADR-0071 gives each
    # device call (RECEIPT_BY_DEVICE_CALL), so a qualification can read the whole wake
    # sequence off the ledger alone.
    receipted = _receipted(factory)
    for capability in (
        "desktop.display_wake",
        "browser.session_open",
        "browser.media_play",
        "browser.media_volume",
    ):
        assert RECEIPT_BY_DEVICE_CALL[capability] in receipted, (capability, receipted)
    with factory() as session:
        assert session.query(RoutineFiring).count() == 1
    assert "alarm.firing" in _states() and "alarm.playing" in _states()

    # 4. A second process's tick at the same instant fires nothing twice.
    _tick(build_clock(), fire_at + timedelta(seconds=1), frozen)
    with factory() as session:
        assert session.query(RoutineFiring).count() == 1
    assert device.count("browser.media_play") == 1

    # 5. The greeting on a later tick, over the music, ducked and restored.
    alarm = _alarm(factory, alarm_id)
    assert alarm.greeting_due_at is not None
    volume_calls_before = device.count("browser.media_volume")
    _tick(clock, _aware(alarm.greeting_due_at) + timedelta(seconds=1), frozen)
    assert device.count("desktop.play_audio") == 1
    greeting = device.payload_for("desktop.play_audio")
    audio_url = greeting["audio"]["url"]
    assert audio_url.startswith("/v1/alarms/audio/"), greeting
    assert greeting["audio"]["format"] == "wav" and greeting["audio"]["bytes"] > 0
    # The single-use token the payload carries is redeemable exactly once, through the
    # one deliberately open route (test_identity_enforcement names it), and it serves the
    # TTS bytes the sequence stored - the background greeting path needs no session at all.
    served = client.get(audio_url, headers={"Authorization": ""})
    assert served.status_code == 200, served.text
    assert served.content[:4] == b"RIFF"
    assert client.get(audio_url, headers={"Authorization": ""}).status_code == 404
    assert device.count("browser.media_volume") >= volume_calls_before + 2  # duck + restore
    alarm = _alarm(factory, alarm_id)
    assert alarm.greeted_at is not None and alarm.state == STATE_PLAYING
    assert "alarm.greeting" in _states()
    assert RECEIPT_BY_DEVICE_CALL["desktop.play_audio"] in _receipted(factory)

    # 6. "Alarmı kapat." by voice: the tool reaches the SAME sequence and the medium that
    #    is actually playing stops - the defect this module exists for.
    device.reset()
    frozen.now = frozen.now + timedelta(seconds=5)
    _say(client, sid, "Alarmı kapat.", turn=2, t_ms=200_000)
    stopped = _tool(client, sid, "c-stop", "alarm.stop", {})
    assert stopped["status"] == "succeeded", stopped
    assert stopped["result"]["terminal_status"] == "verified"
    assert stopped["result"]["speech"]
    assert device.count("browser.media_stop") == 1
    assert device.count("desktop.alarm_disarm") == 1
    alarm = _alarm(factory, alarm_id)
    assert alarm.state == STATE_STOPPED
    assert alarm.media_session_id is None
    with factory() as session:
        routine = session.get(Routine, alarm.routine_id)
        assert routine.status != ROUTINE_STATUS_ARMED
    assert len(_rows(factory, "alarm.cleaned_up")) == 1
    assert "alarm.stopped" in _states()

    # 7. And the record says so, from durable rows alone.
    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()
    calls = {c["call_id"]: c for c in activity["tool_calls"]}
    assert calls["c-create"]["status"] == "succeeded" and calls["c-create"]["speech_chars"] > 0
    assert calls["c-stop"]["status"] == "succeeded" and calls["c-stop"]["speech_chars"] > 0


def test_a_stop_by_voice_before_the_fix_would_have_left_the_music_playing(
    wired, monkeypatch
) -> None:
    """The negative, kept as a guard: with no wake sequence on the live sources the row
    changes and no device is told. This is what production did on contract v9."""
    client, factory, device, _statuses, build_clock = wired
    runtime = client.app.state.voice_realtime
    runtime.register_live(wake_sequence=None)
    clock = build_clock()
    frozen = _FrozenClock(datetime.now(UTC))
    monkeypatch.setattr(alarms_service, "utcnow", frozen)
    sid = _create(client)
    created = _tool(
        client,
        sid,
        "c-create",
        "alarm.create",
        {"when_spoken": "90 saniye sonra test alarmı kur.", "test": True},
    )
    alarm_id = uuid.UUID(created["result"]["alarm"]["alarm_id"])
    scheduled_for = _aware(_alarm(factory, alarm_id).scheduled_for)
    _tick(clock, datetime.now(UTC), frozen)
    _tick(clock, scheduled_for + timedelta(seconds=5), frozen)
    assert _alarm(factory, alarm_id).state == STATE_PLAYING
    device.reset()

    _tool(client, sid, "c-stop", "alarm.stop", {})
    assert _alarm(factory, alarm_id).state == STATE_STOPPED
    # ...and NOTHING reached the device: the tone would still be ringing.
    assert device.calls == []


def test_display_status_by_voice_reads_the_registry_the_app_registered(wired) -> None:
    client, _factory, _device, statuses, _build_clock = wired
    sid = _create(client)
    device_id = uuid.uuid4()
    statuses.record(
        device_id,
        {"display_state": "off", "input_idle_s": 900, "armed_alarms": 0},
        now=datetime.now(UTC),
    )
    answered = _tool(client, sid, "c-status", "display.status", {})
    assert answered["status"] == "succeeded", answered
    result = answered["result"]
    assert result["display"] == "off", result
    assert [d["device_id"] for d in result["devices"]] == [str(device_id)]
    assert result["speech"]


def test_display_off_by_voice_reaches_the_device_through_the_sequence(wired) -> None:
    client, factory, device, _statuses, _build_clock = wired
    sid = _create(client)
    _say(client, sid, "Ekranları kapat.")
    answered = _tool(client, sid, "c-off", "display.off", {})
    assert answered["status"] == "succeeded", answered
    result = answered["result"]
    assert result["error_class"] is None, result
    assert result["execution_status"] == "executed"
    assert device.count("desktop.display_off") == 1
    assert RECEIPT_BY_DEVICE_CALL["desktop.display_off"] in _receipted(factory)
