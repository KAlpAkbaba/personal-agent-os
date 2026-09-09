"""The Digital Operator's voice tools, through the REAL application object
(docs/M19_DIGITAL_OPERATOR_SPEC.md §3, §4) — the same relay/router/tool path the corpus
uses (``tests/voice_corpus``), narrowed here to the specific contracts that category
covers in aggregate: an utterance -> the tool -> the plan -> the fake device port -> the
receipt and its speech; the secret refusal; no device -> ``capability_missing``.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Artifact, ArtifactVersion
from app.artifacts.runtime import ArtifactRuntime
from app.broker import service as broker_service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.config import Settings
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.main import create_app
from app.narration.models import NarrationSession, PronunciationEntry
from app.operator import focus as operator_focus
from app.operator.models import FOCUS_KIND_WINDOW, ObjectFocusRow
from app.operator.service import OperatorService
from app.operator.task import STATUS_RUNNING, OperatorTask
from app.routines.dispatch import DeviceRunResult
from app.voice.models import VoiceProfile
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.simulator import SimulatedRealtimeProvider
from tests.alarms_support import (
    FakeDeviceAction,
    happy_operator_device_results,
)
from tests.alarms_support import window_id as window_id_for
from tests.identity_support import IDENTITY_TABLES

VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"

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
    ObjectFocusRow.__table__,
)


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


def _wired(*, with_device_action: bool = True):
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

    device = FakeDeviceAction(results=happy_operator_device_results())
    operator_service = OperatorService()
    live_kwargs = {"operator": operator_service}
    if with_device_action:
        live_kwargs["device_action"] = device
    runtime.register_live(**live_kwargs)

    with broker.session() as db:
        enrolled = broker_service.enroll_device(
            db,
            name="ev-pc",
            platform="windows",
            public_key_spki_b64=_spki(),
            capabilities=["app.launch", "window.current", "window.close", "window.list"],
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
    return client, factory, device, operator_service


def _create(client) -> str:
    response = client.post("/v1/voice/realtime/sessions", json={})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _say(client, sid: str, text: str) -> dict:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "utterance", "t_ms": 1000, "turn": 1, "text": text}]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _tool(client, sid: str, name: str, arguments: dict) -> dict:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": f"c-{uuid.uuid4()}", "name": name, "arguments": arguments},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _focus_window(factory, *, window_id: str = window_id_for(1), device=None) -> None:
    """Remember a window AND, when a device is given, put it on that device's desktop.

    A remembered window that is not open is a contradiction the fixture should not be able
    to state silently: the resolver holds "current" against the real window list before
    anything acts on it, because the owner closes windows (ADR-0101).
    """
    with factory() as db:
        operator_focus.set_focus(
            db, FOCUS_KIND_WINDOW, window_id, label="Adsız - Not Defteri", source="test"
        )
    if device is not None:
        # The same window.list answers two different questions at two different moments:
        # "which windows exist?" before a plan acts, and "is it gone?" after a close. A
        # single canned answer cannot be right for both, so this one reads the device's own
        # call log -- the closest a fake gets to a desktop that changes.
        row = {"window_id": window_id, "title": "Adsız - Not Defteri", "foreground": True}

        def _listing(_payload, _device=device, _id=window_id, _row=row):
            closed = any(
                call["capability"] == "window.close" and call["payload"].get("window_id") == _id
                for call in _device.calls
            )
            return DeviceRunResult(True, result={"windows": [] if closed else [_row]})

        device.results["window.list"] = _listing


# --------------------------------------------------------------------------- app_open


def test_app_open_launches_notepad_and_focuses_its_window() -> None:
    client, factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Not Defteri'ni aç.")
    call = _tool(client, sid, "operator.app_open", {"application": "Not Defteri"})

    assert call["status"] == "succeeded", call
    body = call["result"]
    assert "Not Defteri" in body["speech"]
    assert body["execution_status"] == "executed"
    assert body["terminal_status"] == "verified"
    assert device.capabilities_called() == ["app.launch", "window.current"]

    with factory() as db:
        current = operator_focus.current(db, FOCUS_KIND_WINDOW)
    assert current is not None and current.object_id == window_id_for(1)


def test_app_open_never_reaches_the_browser_worker() -> None:
    client, factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Chrome'u aç.")
    call = _tool(client, sid, "operator.app_open", {"application": "Chrome"})
    assert call["status"] == "succeeded", call
    assert "browser.session_open" not in device.capabilities_called()
    assert "browser.navigate" not in device.capabilities_called()
    assert device.capabilities_called() == ["app.launch", "window.current"]


def test_an_app_outside_the_allowlist_is_refused_naming_it() -> None:
    client, factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Winamp'ı aç.")
    call = _tool(client, sid, "operator.app_open", {"application": "Winamp"})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "unknown_application"
    assert device.calls == []
    assert "Not Defteri" in body["speech"] or "not defteri" in body["speech"].lower()


def test_no_device_action_is_a_capability_missing_receipt() -> None:
    client, factory, device, _operator = _wired(with_device_action=False)
    sid = _create(client)
    _say(client, sid, "Not Defteri'ni aç.")
    call = _tool(client, sid, "operator.app_open", {"application": "Not Defteri"})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "capability_missing"
    assert "operatör yetkisi yok" in body["speech"]
    assert device.calls == []


# ---------------------------------------------------------------------- window_control


def test_window_close_resolves_the_current_window_through_focus() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Bunu kapat.")
    call = _tool(client, sid, "operator.window_control", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert device.capabilities_called() == ["window.list", "window.close", "window.list"]
    assert "kapat" in body["speech"].lower()


def test_window_close_with_no_focus_asks_which_window_and_touches_nothing() -> None:
    client, factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Bunu kapat.")
    call = _tool(client, sid, "operator.window_control", {})
    assert call["status"] == "needs_clarification", call
    assert call["result"]["speech"] == "Hangi pencere?"
    assert [c for c in device.capabilities_called() if c != "window.list"] == []


def test_window_maximize_and_minimize_verify_the_observed_state() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Pencereyi büyüt.")
    call = _tool(client, sid, "operator.window_control", {})
    assert call["status"] == "succeeded", call
    assert device.capabilities_called() == ["window.list", "window.maximize"]

    sid2 = _create(client)
    device.reset()
    _say(client, sid2, "Bu pencereyi küçült.")
    call2 = _tool(client, sid2, "operator.window_control", {})
    assert call2["status"] == "succeeded", call2
    assert device.capabilities_called() == ["window.list", "window.minimize"]


def test_window_previous_activates_the_older_window() -> None:
    client, factory, device, _operator = _wired()
    # Explicit, staggered timestamps: two set_focus calls back to back can land on the
    # SAME datetime.now(UTC) reading (Windows clock resolution), and the tie-break
    # (row id) is a random UUID, not chronological.
    base = datetime.now(UTC)
    with factory() as db:
        operator_focus.set_focus(
            db, FOCUS_KIND_WINDOW, window_id_for(0), label="Hesap Makinesi", source="t", now=base
        )
        operator_focus.set_focus(
            db,
            FOCUS_KIND_WINDOW,
            window_id_for(1),
            label="Not Defteri",
            source="t",
            now=base + timedelta(seconds=1),
        )
    sid = _create(client)
    _say(client, sid, "Önceki pencereye dön.")
    call = _tool(client, sid, "operator.window_control", {})
    assert call["status"] == "succeeded", call
    assert device.capabilities_called() == ["window.list", "window.activate"]
    assert device.payload_for("window.activate") == {"window_id": window_id_for(0)}


# --------------------------------------------------------------------------------- type


def test_type_refuses_a_secret_looking_request_and_never_touches_the_keyboard() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Buraya şifremi yaz.")
    call = _tool(client, sid, "operator.type", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "secret_refused"
    assert body["speech"] == "Şifreleri ben yazmam efendim."
    assert device.calls == []


def test_type_writes_the_owners_words_and_verifies_the_value() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Buraya merhaba yaz.")
    call = _tool(client, sid, "operator.type", {})
    assert call["status"] == "succeeded", call
    # window.list first: "current" is held against the real desktop before anything acts on
    # it. Then the plan itself, unchanged.
    assert device.capabilities_called() == [
        "window.list",
        "window.activate",
        "keyboard.type",
        "ui.inspect",
    ]
    assert device.payload_for("keyboard.type")["text"] == "merhaba"


def test_type_with_no_text_at_all_asks_rather_than_guesses() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Şuraya yazar mısın?")
    call = _tool(client, sid, "operator.type", {})
    assert call["status"] == "needs_clarification", call
    assert call["result"]["speech"] == "Ne yazmamı istersiniz?"
    assert device.calls == []


# -------------------------------------------------------------------------------- shell


def test_shell_ip_reads_the_devices_ipv4() -> None:
    client, factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "IP adresimi göster.")
    call = _tool(client, sid, "operator.shell", {})
    assert call["status"] == "succeeded", call
    assert "192.168.1.50" in call["result"]["speech"]
    assert device.capabilities_called() == ["terminal.execute"]
    assert device.payload_for("terminal.execute") == {"command": "ipconfig"}


def test_shell_hostname_reads_the_devices_name() -> None:
    client, factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Bilgisayarın adı ne?")
    call = _tool(client, sid, "operator.shell", {})
    assert call["status"] == "succeeded", call
    assert "MAIL" in call["result"]["speech"]
    assert device.payload_for("terminal.execute") == {"command": "hostname"}


# ------------------------------------------------------------------------------ cancel


def test_cancel_with_nothing_running_says_so_and_touches_nothing() -> None:
    client, factory, device, operator = _wired()
    sid = _create(client)
    call = _tool(client, sid, "operator.cancel", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "noop"
    assert body["speech"] == "Şu anda çalışan bir işlem yok efendim."
    assert device.calls == []


def test_cancel_stops_the_running_task() -> None:
    client, factory, device, operator = _wired()
    task = OperatorTask(
        id=uuid.uuid4(), goal="open notepad", steps=[], plan_name="open_application",
        status=STATUS_RUNNING, current_step=0,
    )
    operator.set_current_task(task)
    sid = _create(client)
    call = _tool(client, sid, "operator.cancel", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert body["speech"] == "İptal ettim efendim."
    assert task.cancel_requested is True


# ------------------------------------------------------------------------------ status


def test_status_with_nothing_running() -> None:
    client, factory, device, _operator = _wired()
    sid = _create(client)
    call = _tool(client, sid, "operator.status", {})
    assert call["status"] == "succeeded", call
    assert call["result"]["speech"] == "Şu anda bir şey yapmıyorum efendim."


def test_status_while_a_task_runs_names_the_step_and_the_window() -> None:
    client, factory, device, operator = _wired()
    task = OperatorTask(
        id=uuid.uuid4(),
        goal="open notepad",
        steps=[],
        plan_name="open_application",
        status=STATUS_RUNNING,
        current_step=1,
        last_observed={"window": {"title": "Adsız - Not Defteri"}},
    )
    operator.set_current_task(task)
    sid = _create(client)
    call = _tool(client, sid, "operator.status", {})
    assert call["status"] == "succeeded", call
    speech = call["result"]["speech"]
    assert "open_application" in speech
    assert "Adsız - Not Defteri" in speech
