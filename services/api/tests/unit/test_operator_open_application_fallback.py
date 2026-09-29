"""ADR-0209: a single-step application launch on a device without the Operator.

On 2026-09-29 the owner said "ofis bilgisayarımda hesap makinesini aç". The office PC
(GMKADIRAKBABA) is deliberately enrolled WITHOUT the Digital Operator (ADR-0203), so it
advertises ``desktop.open_application`` and no ``app.launch``. The sentence reached
``operator.app_open``, whose plan's first step is ``app.launch``; selection found no online
device advertising it and the owner heard "Hesap Makinesi açamadım efendim". Before the
Operator existed this exact sentence was what ``desktop.open_application`` was for.

Everything here goes through the REAL application object: the ONE router
(``POST .../events``) decides the tool, the tool runs, and the port under it is the real
``BrokerDeviceAction`` selecting among REAL enrolled device rows - only the last hop, the
command client that would talk to a socket, is a recording fake. A fake DEVICE PORT would
prove nothing here: the defect lives in what selection does with the advertised lists.
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.routines.dispatch as dispatch_mod
from app.artifacts.models import Artifact, ArtifactVersion
from app.artifacts.runtime import ArtifactRuntime
from app.broker import service as broker_service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.config import Settings
from app.devices.commands import CommandFailed, CommandSucceeded
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.macros.models import VoiceMacroRow
from app.main import create_app
from app.narration.models import NarrationSession, PronunciationEntry
from app.operator import focus as operator_focus  # noqa: F401 - the focus table's module
from app.operator import plans as operator_plans
from app.operator.models import ObjectFocusRow
from app.operator.service import OperatorService
from app.routines.dispatch import BrokerDeviceAction
from app.voice.models import VoiceProfile
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.simulator import SimulatedRealtimeProvider
from tests.alarms_support import happy_operator_device_results
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
    VoiceMacroRow.__table__,
)

#: What each enrolled machine advertises in production (ADR-0205 inventory): MAIL is the
#: home PC installed with -Operator; GMKADIRAKBABA is the employer's machine, installed
#: without it. Only the names that matter to a launch are kept.
HOME_CAPABILITIES = [
    "app.launch",
    "window.current",
    "window.list",
    "window.activate",
    "keyboard.type",
    "desktop.open_application",
    "desktop.open_artifact",
]
OFFICE_CAPABILITIES = [
    "desktop.open_application",
    "desktop.open_artifact",
    "desktop.display_wake",
    "desktop.notify",
    "browser.chrome",
]

CALC_PATH = r"C:\Windows\System32\calc.exe"


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


class _RecordingCommands:
    """The last hop only: what the broker would put on a socket, recorded and answered.

    ``desktop.open_application`` answers the way the companion's ``AppLauncher`` does - it
    enforces ITS OWN allowlist and says ``capability_missing`` for a name outside it (so a
    test can prove the cloud does not depend on, or replace, that enforcement) - and every
    Operator capability is answered from the same happy scripts the rest of the suite uses.
    """

    def __init__(self, device_allowlist: tuple[str, ...] = ("notepad", "calc", "mspaint")) -> None:
        self.calls: list[dict[str, Any]] = []
        self.device_allowlist = device_allowlist
        #: What ``desktop.open_application`` answers when it accepts the name.
        self.launch_answer: dict[str, Any] = {"pid": 5150, "executable": CALC_PATH}
        self._scripted = happy_operator_device_results()

    def run(
        self,
        *,
        device_id: uuid.UUID,
        capability: str,
        payload: dict[str, Any],
        idempotency_key: str,
        timeout_s: float,
        trace_id: str,
        heartbeat: Any = None,
    ) -> CommandSucceeded | CommandFailed:
        self.calls.append(
            {
                "device_id": device_id,
                "capability": capability,
                "payload": dict(payload),
                "idempotency_key": idempotency_key,
            }
        )
        if capability == "desktop.open_application":
            application = payload.get("application")
            if application not in self.device_allowlist:
                return CommandFailed(
                    "capability_missing",
                    f"application '{application}' is not in the local allowlist",
                    False,
                )
            return CommandSucceeded(dict(self.launch_answer))
        scripted = self._scripted.get(capability)
        if scripted is None:
            return CommandSucceeded({})
        result = scripted(payload) if callable(scripted) else scripted
        return CommandSucceeded(dict(result.result))

    def capabilities(self) -> list[str]:
        return [c["capability"] for c in self.calls]


class _World:
    def __init__(self, client, factory, commands, ids, action) -> None:
        self.client = client
        self.factory = factory
        self.commands: _RecordingCommands = commands
        self.ids: dict[str, uuid.UUID] = ids
        self.action: BrokerDeviceAction = action


def _wired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    devices: dict[str, dict[str, Any]],
    *,
    device_allowlist: tuple[str, ...] = ("notepad", "calc", "mspaint"),
) -> _World:
    """``devices``: name -> {"capabilities": [...], "aliases": [...], "online": bool}.

    A FILE database with a real pool, not the shared in-memory connection the fake-port
    suites use: the real ``BrokerDeviceAction`` opens and closes its own sessions per
    command, and on one shared connection that close rolls back the relay's uncommitted
    tool-call row. Production gives every session its own connection, and so does this.
    """
    settings = Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'launch-fallback.db'}", connect_args={"check_same_thread": False}
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

    # The REAL port over the REAL selection; only the command client is a recorder.
    commands = _RecordingCommands(device_allowlist)
    monkeypatch.setattr(dispatch_mod, "get_broker_runtime", lambda: broker)
    action = BrokerDeviceAction(session_factory=factory, command_client=commands)
    runtime.register_live(operator=OperatorService(), device_action=action)

    ids: dict[str, uuid.UUID] = {}
    for name, spec in devices.items():
        with broker.session() as db:
            enrolled = broker_service.enroll_device(
                db,
                name=name,
                platform="windows",
                public_key_spki_b64=_spki(),
                capabilities=list(spec["capabilities"]),
                trace_id=None,
            )
            if spec.get("aliases"):
                broker_service.update_device_metadata(
                    db, enrolled.id, aliases=list(spec["aliases"]), trace_id=None
                )
        ids[name] = enrolled.id
        if spec.get("online", True):
            broker.connections[enrolled.id] = DeviceConnection(
                device_id=enrolled.id, session_id=uuid.uuid4(), websocket=object()
            )
    issued = identity.service.issue_session(
        client_kind="desktop", label="pc", device_id=uuid.uuid4()
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issued.token}"
    return _World(client, factory, commands, ids, action)


def _office_only(monkeypatch, tmp_path, **kwargs) -> _World:
    return _wired(
        monkeypatch,
        tmp_path,
        {"GMKADIRAKBABA": {"capabilities": OFFICE_CAPABILITIES, "aliases": ["ofis", "iş"]}},
        **kwargs,
    )


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


def _tool_with_id(client, sid: str, call_id: str, name: str, arguments: dict) -> dict:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": call_id, "name": name, "arguments": arguments},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _owner_says_open(world: _World, sentence: str, application: str) -> dict:
    """The ONE router hears the sentence, then the model calls the tool it routes to."""
    sid = _create(world.client)
    _say(world.client, sid, sentence)
    call = _tool(world.client, sid, "operator.app_open", {"application": application})
    assert call["status"] == "succeeded", call
    return call["result"]


def _receipt_rows(world: _World) -> list[ActivityEventRow]:
    with world.factory() as db:
        return list(
            db.execute(
                select(ActivityEventRow).where(ActivityEventRow.action == "operator.app_open")
            )
            .scalars()
            .all()
        )


# ---------------------------------------------------------------- the owner's sentence


def test_the_office_pc_without_the_operator_opens_the_calculator_directly(
    monkeypatch, tmp_path
) -> None:
    """2026-09-29, verbatim. Red before ADR-0209: ``no_capable_device`` on ``app.launch``."""
    world = _office_only(monkeypatch, tmp_path)
    body = _owner_says_open(world, "Ofis bilgisayarımda hesap makinesini aç.", "Hesap Makinesi")

    assert world.commands.capabilities() == ["desktop.open_application"]
    call = world.commands.calls[0]
    assert call["payload"] == {"application": "calc"}
    assert call["device_id"] == world.ids["GMKADIRAKBABA"]
    assert body["execution_status"] == "executed", body
    assert body["terminal_status"] == "verified"
    assert "açamadım" not in body["speech"]
    assert "Hesap Makinesi" in body["speech"]


def test_the_receipt_and_the_ledger_say_which_path_ran_and_on_which_device(
    monkeypatch, tmp_path
) -> None:
    world = _office_only(monkeypatch, tmp_path)
    body = _owner_says_open(world, "Ofis bilgisayarımda hesap makinesini aç.", "Hesap Makinesi")

    # The capability stays the registered tool's name (ADR-0114/0116: a receipt records what
    # the owner COMMANDED, and only registered names resolve in the self-model); the path
    # that actually ran is what the receipt's server observation says.
    assert body["capability"] == "operator.app_open"
    server = body["observed_after"]["server"]
    assert server["path"] == "desktop.open_application"
    assert server["application"] == "calc"
    assert server["device_id"] == str(world.ids["GMKADIRAKBABA"])
    assert server["device"] == "GMKADIRAKBABA"
    assert server.get("plan") is None, "a direct launch is not an operator plan"
    assert body["observed_after"]["local"]["pid"] == 5150

    rows = _receipt_rows(world)
    assert len(rows) == 1
    assert "desktop.open_application" in rows[0].factual_summary
    assert rows[0].detail_json["observed_after"]["server"]["path"] == "desktop.open_application"
    assert rows[0].detail_json["observed_after"]["server"]["device_id"] == str(
        world.ids["GMKADIRAKBABA"]
    )
    with world.factory() as db:
        operator_events = [
            event
            for event in db.execute(select(ActivityEventRow)).scalars().all()
            if str(event.event_type).startswith("operator.task")
        ]
    assert operator_events == [], "no operator mission ran, so none may be recorded"


def test_the_spoken_confirmation_names_the_device_it_ran_on(monkeypatch, tmp_path) -> None:
    world = _office_only(monkeypatch, tmp_path)
    body = _owner_says_open(world, "Ofis bilgisayarımda hesap makinesini aç.", "Hesap Makinesi")
    assert "ofis" in body["speech"].lower()


def test_a_launch_the_device_acknowledged_without_a_process_is_unverified(
    monkeypatch, tmp_path
) -> None:
    """What the direct route can verify is the process the device says it started. An
    acknowledgement with none is not a success the owner may be told about."""
    world = _office_only(monkeypatch, tmp_path)
    world.commands.launch_answer = {}
    body = _owner_says_open(world, "Ofis bilgisayarımda hesap makinesini aç.", "Hesap Makinesi")

    assert body["execution_status"] == "executed"
    assert body["terminal_status"] == "unverified"
    assert "doğrulayamadım" in body["speech"]
    assert " açtım efendim" not in body["speech"]


def test_a_repeated_tool_call_is_the_same_command_to_the_device(monkeypatch, tmp_path) -> None:
    """A launch is not idempotent on the device, so the command carries a key derived from
    the tool call: the same call sent twice is one command to the broker, not two windows."""
    world = _office_only(monkeypatch, tmp_path)
    sid = _create(world.client)
    _say(world.client, sid, "Ofis bilgisayarımda hesap makinesini aç.")
    _tool_with_id(world.client, sid, "c-fixed", "operator.app_open", {"application": "Hesap"})
    assert world.commands.calls[0]["idempotency_key"] == "open-application-c-fixed"


@pytest.mark.parametrize(
    ("sentence", "application", "expected"),
    [
        ("Not Defteri'ni aç.", "Not Defteri", "notepad"),
        ("Paint'i aç.", "Paint", "mspaint"),
        ("Hesap makinesi aç.", "Hesap Makinesi", "calc"),
    ],
)
def test_every_name_the_contract_accepts_is_launched_with_its_own_name(
    monkeypatch, tmp_path, sentence: str, application: str, expected: str
) -> None:
    world = _office_only(monkeypatch, tmp_path)
    body = _owner_says_open(world, sentence, application)
    assert world.commands.calls[0]["payload"] == {"application": expected}
    assert body["terminal_status"] == "verified"


# --------------------------------------------------------- the device that has the Operator


def test_the_home_pc_with_the_operator_keeps_the_operator_path_unchanged(
    monkeypatch, tmp_path
) -> None:
    world = _wired(
        monkeypatch, tmp_path, {"MAIL": {"capabilities": HOME_CAPABILITIES, "aliases": ["ev"]}}
    )
    body = _owner_says_open(world, "Ev bilgisayarımda hesap makinesini aç.", "Hesap Makinesi")

    expected_plan = operator_plans.open_application("calc")
    assert [c["capability"] for c in world.commands.calls] == [s.capability for s in expected_plan]
    assert [c["payload"] for c in world.commands.calls] == [s.payload for s in expected_plan]
    assert "desktop.open_application" not in world.commands.capabilities()
    assert body["capability"] == "operator.app_open"
    server = body["observed_after"]["server"]
    assert server["plan"] == "open_application"
    assert "path" not in server
    assert body["terminal_status"] == "verified"


def test_when_both_machines_are_online_the_operator_machine_keeps_the_launch(
    monkeypatch, tmp_path
) -> None:
    """Selection is not reordered: the device that advertises ``app.launch`` is the one the
    plan has always gone to, and nothing about an office PC being online changes that."""
    world = _wired(
        monkeypatch,
        tmp_path,
        {
            "MAIL": {"capabilities": HOME_CAPABILITIES, "aliases": ["ev"]},
            "GMKADIRAKBABA": {"capabilities": OFFICE_CAPABILITIES, "aliases": ["ofis"]},
        },
    )
    _owner_says_open(world, "Hesap makinesini aç.", "Hesap Makinesi")
    assert "desktop.open_application" not in world.commands.capabilities()
    assert {c["device_id"] for c in world.commands.calls} == {world.ids["MAIL"]}


# ---------------------------------------------------------------------------- negatives


def test_an_application_outside_the_contract_is_refused_and_nothing_is_dispatched(
    monkeypatch, tmp_path
) -> None:
    world = _office_only(monkeypatch, tmp_path)
    body = _owner_says_open(world, "Ofis bilgisayarımda Chrome'u aç.", "Chrome")

    assert world.commands.calls == []
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "unknown_application"
    assert body["speech"].startswith("Bunu açamam efendim; açabildiklerim:")
    # Only what the fallback can really open is named - not the eight the Operator knows.
    assert "Hesap Makinesi" in body["speech"] and "Chrome" not in body["speech"]
    assert body["observed_after"]["server"]["allowlist"] == ["notepad", "calc", "mspaint"]


def test_powershell_and_explorer_are_not_smuggled_through_the_direct_route(
    monkeypatch, tmp_path
) -> None:
    world = _office_only(monkeypatch, tmp_path)
    for sentence, application in (
        ("PowerShell'i aç.", "PowerShell"),
        ("Dosya gezginini aç.", "Dosya Gezgini"),
        ("Microsoft Edge'i aç.", "Microsoft Edge"),
    ):
        body = _owner_says_open(world, sentence, application)
        assert body["execution_status"] == "refused", sentence
    assert world.commands.calls == []


def test_a_refused_application_is_not_sent_to_another_device(monkeypatch, tmp_path) -> None:
    """The refusal is about the application, not the machine: no other device is tried."""
    world = _wired(
        monkeypatch,
        tmp_path,
        {
            "GMKADIRAKBABA": {"capabilities": OFFICE_CAPABILITIES, "aliases": ["ofis"]},
            "LAPTOP": {"capabilities": OFFICE_CAPABILITIES, "aliases": ["laptop"]},
        },
    )
    body = _owner_says_open(world, "Ofis bilgisayarımda Chrome'u aç.", "Chrome")
    assert body["execution_status"] == "refused"
    assert world.commands.calls == []


def test_the_device_keeps_enforcing_its_own_allowlist(monkeypatch, tmp_path) -> None:
    """The cloud names the contract's applications; the device decides. A device whose
    allowlist was narrowed says ``capability_missing`` and the owner hears it failed -
    nothing is retried another way and no other device is asked."""
    world = _office_only(monkeypatch, tmp_path, device_allowlist=("notepad",))
    body = _owner_says_open(world, "Ofis bilgisayarımda hesap makinesini aç.", "Hesap Makinesi")

    assert world.commands.capabilities() == ["desktop.open_application"]
    assert body["execution_status"] == "failed"
    assert body["terminal_status"] == "failed"
    assert body["error_class"] == "capability_missing"
    assert "açamadım" in body["speech"]


def test_a_two_step_request_on_the_office_pc_is_still_no_capable_device(
    monkeypatch, tmp_path
) -> None:
    """A launch that is only the first half of a mission is not a launch: it stays a mission,
    and the mission needs the Operator the office PC does not have."""
    from app.operator.mission import MISSION_PAUSED, MissionPorts, plan_mission, run_mission
    from app.voice.intents import Intent, resolve_intent

    sentence = "Hesap makinesini aç ve 5 yaz."
    # The ONE router does not send it to the single-launch tool at all ...
    assert resolve_intent(sentence).intent == Intent.MISSION_START
    # ... and the mission it plans, run over the same real port, still stops on the missing
    # Operator with the mission's own wording. No launch of any kind is dispatched.
    world = _office_only(monkeypatch, tmp_path)
    mission = plan_mission(sentence)
    assert len(mission.steps) == 2
    run_mission(mission, MissionPorts(device=world.action))

    assert mission.status == MISSION_PAUSED
    assert mission.escalation is not None
    assert mission.escalation["error_class"] == "no_capable_device"
    assert "bunu yapabilecek bir cihaz çevrimiçi değil" in mission.escalation["speech"]
    assert world.commands.calls == []


def test_a_device_advertising_neither_launch_is_no_capable_device(monkeypatch, tmp_path) -> None:
    world = _wired(
        monkeypatch,
        tmp_path,
        {"KIOSK": {"capabilities": ["desktop.notify", "desktop.display_wake"], "aliases": []}},
    )
    body = _owner_says_open(world, "Hesap makinesini aç.", "Hesap Makinesi")

    assert world.commands.calls == []
    assert body["execution_status"] == "failed"
    assert body["error_class"] == "capability_missing"
    assert "açamadım" in body["speech"]
    assert body["observed_after"]["server"]["plan"] == "open_application"


def test_nothing_online_is_no_capable_device_and_nothing_is_dispatched(
    monkeypatch, tmp_path
) -> None:
    world = _wired(
        monkeypatch,
        tmp_path,
        {"GMKADIRAKBABA": {"capabilities": OFFICE_CAPABILITIES, "online": False}},
    )
    body = _owner_says_open(world, "Hesap makinesini aç.", "Hesap Makinesi")
    assert world.commands.calls == []
    assert body["execution_status"] == "failed"


# --------------------------------------------------------------------- the seams


def test_the_gate_the_operator_launch_carries_still_covers_the_direct_launch() -> None:
    """The step-up tier is decided by the TOOL's name before any handler runs, so the direct
    route - which lives inside ``operator.app_open`` - cannot be reached with a lower gate."""
    from app.security.step_up import TIER_SENSITIVE, tier_of

    assert tier_of("operator.app_open") == TIER_SENSITIVE


def test_the_launch_capability_the_route_probes_is_the_one_the_plan_demands() -> None:
    from app.operator import launch_fallback

    assert (
        operator_plans.open_application("calc")[0].capability
        == launch_fallback.CAPABILITY_APP_LAUNCH
    )


def test_the_port_probe_and_the_port_run_select_the_same_device(monkeypatch, tmp_path) -> None:
    """``can_run`` must answer exactly what ``run`` would: a probe that said yes while the
    run said no_capable_device (or the reverse) would route a launch to a path that cannot
    happen. Checked over a matrix of capabilities on a two-device fleet."""
    world = _wired(
        monkeypatch,
        tmp_path,
        {
            "MAIL": {"capabilities": HOME_CAPABILITIES, "online": False},
            "GMKADIRAKBABA": {"capabilities": OFFICE_CAPABILITIES},
        },
    )
    for capability in (
        "app.launch",
        "desktop.open_application",
        "browser.chrome",
        "window.list",
        "no.such.capability",
    ):
        ran = world.action.run(
            capability=capability, payload={}, idempotency_key="k", timeout_s=1.0
        )
        assert world.action.can_run(capability) is (ran.error_class != "no_capable_device"), (
            capability
        )


def test_the_port_probe_is_false_without_a_broker(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dispatch_mod, "get_broker_runtime", lambda: None)
    action = BrokerDeviceAction(session_factory=lambda: None)
    assert action.can_run("desktop.open_application") is False


# ------------------------------------------------------- the contract mirror (drift guard)


def test_the_names_the_route_accepts_are_the_names_the_contract_and_the_device_hold() -> None:
    """The cloud must not invent an allowlist: the three names are read back from the
    device's own ``AppLauncher.DefaultAllowlist`` and from DEVICE_PROTOCOL.md section 6, and
    every one is also an id the operator contract carries (so the payload name IS the id)."""
    import re
    from pathlib import Path

    from app.operator import allowlists, launch_fallback

    root = Path(__file__).resolve().parents[4]
    launcher = (
        root / "devices" / "windows-agent" / "src" / "PagentOS.SessionCompanion" / "AppLauncher.cs"
    ).read_text(encoding="utf-8")
    default_block = launcher.split("DefaultAllowlist()", 1)[1].split("};", 1)[0]
    device_names = tuple(re.findall(r'\["(\w+)"\]\s*=', default_block))
    assert device_names == launch_fallback.DESKTOP_OPEN_APPLICATION_NAMES

    protocol = (root / "packages" / "protocol" / "DEVICE_PROTOCOL.md").read_text(encoding="utf-8")
    section = protocol.split("## 6. Capability: `desktop.open_application`", 1)[1].split(
        "## 6a", 1
    )[0]
    for name in launch_fallback.DESKTOP_OPEN_APPLICATION_NAMES:
        assert f"`{name}`" in section, name
    for name in launch_fallback.DESKTOP_OPEN_APPLICATION_NAMES:
        assert name in allowlists.APP_IDS, name
