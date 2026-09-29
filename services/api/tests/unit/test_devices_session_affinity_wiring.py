"""ADR-0208 through the REAL application object: which machine acts when the owner names none.

``test_devices_session_affinity.py`` pins the rule. This file pins the wiring the way
``test_operator_wiring.py`` does for the operator: a session created over HTTP on the real
``create_app`` object, a tool called over HTTP, and the device command that comes out of the
real ``BrokerDeviceAction`` -> ``select_device`` -> ``DeviceCommandClient`` path (only the
last hop, the wire to the machine, is a recording fake).

The scenario is 2026-09-29: two enrolled machines, the owner at the OFFICE one, the home one
off (or, on a better day, on and seen more recently). The probe tool is ``operator.screenshot``
- one real ``device_action.run`` with no device named - because the office machine advertises
no operator family (ADR-0203) and the app-launch path that broke that day is another
engineer's work (routing single launches through ``desktop.open_application``). What this file
proves is the part that is this change's: WHICH device a session's device command names.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
from app.broker.models import (
    AuditEvent,
    Device,
    DeviceCommand,
    DeviceSession,
    EnrollmentToken,
)
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.config import Settings
from app.devices import selection
from app.devices.commands import CommandSucceeded, register_broker_runtime
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.main import create_app
from app.narration.models import NarrationSession, PronunciationEntry
from app.research.models import (
    ResearchCandidateRow,
    ResearchEvidenceRow,
    ResearchReportRow,
    ResearchRunRow,
)
from app.routines.dispatch import BrokerDeviceAction
from app.voice.models import SpeakerVerdictRow, VoiceProfile
from app.voice.realtime_sessions import service
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.simulator import SimulatedRealtimeProvider
from tests.identity_support import IDENTITY_TABLES

VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"
OFFICE_IP = "100.80.20.54"  # GMKADIRAKBABA's tailnet address (ADR-0203)
HOME_IP = "100.92.148.30"  # MAIL's
EDGE_IP = "172.18.0.4"  # the nginx edge as its container sees it
PNG_1PX = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360f8cfc00000030101006e9f5c7d"
        "0000000049454e44ae426082"
    )
).decode("ascii")

SCREEN = "screen.capture"
HOME_CAPS = ["desktop.open_application", "app.launch", "window.current", SCREEN]
OFFICE_CAPS = ["desktop.open_application", "desktop.open_artifact", SCREEN]

TABLES = (
    *IDENTITY_TABLES,
    RealtimeSessionRow.__table__,
    RealtimeToolCall.__table__,
    AuditEvent.__table__,
    VoiceProfile.__table__,
    SpeakerVerdictRow.__table__,
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
    Task.__table__,
    TaskRun.__table__,
    ArtifactRender.__table__,
    ResearchSource.__table__,
    ResearchRunRow.__table__,
    ResearchCandidateRow.__table__,
    ResearchEvidenceRow.__table__,
    ResearchReportRow.__table__,
)


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


class RecordingCommandClient:
    """The last hop only: what the broker would have put on the wire, and to whom."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> CommandSucceeded:
        self.calls.append(kwargs)
        return CommandSucceeded(
            result={"png_base64": PNG_1PX, "width": 1, "height": 1}, command_id=uuid.uuid4()
        )

    @property
    def targets(self) -> list[uuid.UUID]:
        return [c["device_id"] for c in self.calls]


class Stack:
    def __init__(self, settings: Settings, database: Path) -> None:
        self.settings = settings
        # A FILE, not the usual shared in-memory connection: the device port opens its own
        # session (as it does in production) while the tool call's transaction is open, and
        # on one shared connection its close() would roll that transaction back.
        engine = create_engine(
            f"sqlite:///{database}", connect_args={"check_same_thread": False}
        )
        for table in TABLES:
            table.create(engine)
        self.engine = engine
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.app = create_app(settings)
        # The app's OWN broker (the object `register_broker_runtime` and the ws handler use),
        # bound to this test's database.
        self.broker: BrokerRuntime = self.app.state.broker
        self.broker._engine = engine
        self.broker._session_factory = self.factory
        identity = IdentityRuntime(settings, engine=engine, root=InMemoryCredentialRoot())
        identity.service.bootstrap()
        self.app.state.identity = identity
        self.identity = identity
        # The app's OWN device port, pointed at this database and a recording last hop.
        self.command_client = RecordingCommandClient()
        self.device_action: BrokerDeviceAction = self.app.state.device_action
        self.device_action._session_factory = self.factory
        self.device_action._command_client = self.command_client  # type: ignore[assignment]
        sim = SimulatedRealtimeProvider()
        # research.start (a voice tool that selects a device itself) needs the artifact runtime.
        artifacts = ArtifactRuntime(settings)
        artifacts._engine = engine
        artifacts._session_factory = self.factory
        self.voice = RealtimeVoiceRuntime(
            settings,
            engine=engine,
            providers={sim.name: sim},
            sideband=RecordingSideband(deliver=True),
            broker=self.broker,
            artifacts=artifacts,
        )
        self.voice.register_live(device_action=self.device_action)
        self.app.state.voice_realtime = self.voice
        register_broker_runtime(self.broker)

    def enroll(
        self,
        name: str,
        capabilities: list[str],
        *,
        online: bool,
        ip: str | None = None,
        seen_s_ago: float | None = None,
    ) -> uuid.UUID:
        """``seen_s_ago`` decides who the OLD rule prefers (the most recently seen wins the
        healthiest tie-break). Unless a test says otherwise the machine named "MAIL" was
        seen a second ago and every other one a minute ago, so the old rule is the home PC:
        a test that expects the OFFICE device is therefore not passing by luck."""
        if seen_s_ago is None:
            seen_s_ago = 1.0 if name == "MAIL" else 60.0
        with self.broker.session() as db:
            device = broker_service.enroll_device(
                db,
                name=name,
                platform="windows",
                public_key_spki_b64=_spki(),
                capabilities=capabilities,
                trace_id=None,
            )
            device.last_seen_at = datetime.now(UTC) - timedelta(seconds=seen_s_ago)
            db.commit()
            device_id = device.id
        if online:
            self.broker.connections[device_id] = DeviceConnection(
                device_id=device_id,
                session_id=uuid.uuid4(),
                websocket=object(),
                peer_ip=ip,
            )
        return device_id

    def revoke(self, device_id: uuid.UUID) -> None:
        with self.broker.session() as db:
            broker_service.revoke_device(db, device_id, trace_id=None)

    def client(
        self,
        *,
        bound_device: uuid.UUID | None = None,
        peer: tuple[str, int] = ("testclient", 50000),
        headers: dict[str, str] | None = None,
    ) -> TestClient:
        issued = self.identity.service.issue_session(
            client_kind="web", label="web shell", device_id=bound_device
        )
        client = TestClient(self.app, client=peer)
        client.headers["Authorization"] = f"Bearer {issued.token}"
        for name, value in (headers or {}).items():
            client.headers[name] = value
        return client


@pytest.fixture()
def make_stack(tmp_path: Path):
    stacks: list[Stack] = []

    def build(**settings: Any) -> Stack:
        stack = Stack(
            Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY, **settings),
            tmp_path / f"affinity-{len(stacks)}.db",
        )
        stacks.append(stack)
        return stack

    try:
        yield build
    finally:
        register_broker_runtime(None)
        for stack in stacks:
            stack.engine.dispose()


def _open_session(client: TestClient, **body: Any) -> str:
    response = client.post("/v1/voice/realtime/sessions", json=body)
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _shoot(client: TestClient, session_id: str) -> dict[str, Any]:
    """One `operator.screenshot`: a single `device_action.run(screen.capture)` naming no device."""
    response = client.post(
        f"/v1/voice/realtime/sessions/{session_id}/tool-calls",
        json={"call_id": uuid.uuid4().hex, "name": "operator.screenshot", "arguments": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded", body
    return body


# --------------------------------------------------------------- source (a): declared


def test_a_session_that_declares_the_office_device_acts_on_it_while_the_home_pc_is_on(
    make_stack,
) -> None:
    stack = make_stack()
    home = stack.enroll("MAIL", HOME_CAPS, online=True)
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True)

    # No declaration: the old rule - the most recently seen machine, the home PC.
    plain = stack.client()
    _shoot(plain, _open_session(plain))
    assert stack.command_client.targets[-1] == home

    # The office declares itself and is served by itself, though the old rule would not.
    declared = stack.client()
    _shoot(declared, _open_session(declared, device_id=str(office)))
    assert stack.command_client.targets[-1] == office
    # ... and from the home machine's session the home machine, symmetrically.
    home_client = stack.client()
    _shoot(home_client, _open_session(home_client, device_id=str(home)))
    assert stack.command_client.targets[-1] == home


def test_todays_event_the_office_session_with_the_home_pc_off(make_stack) -> None:
    stack = make_stack()
    stack.enroll("MAIL", HOME_CAPS, online=False)
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True)

    client = stack.client()
    _shoot(client, _open_session(client, device_id=str(office)))

    assert stack.command_client.targets == [office]


def test_a_declared_device_that_is_offline_falls_back_to_the_old_rule(make_stack) -> None:
    stack = make_stack()
    home = stack.enroll("MAIL", HOME_CAPS, online=True)
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=False)

    client = stack.client()
    _shoot(client, _open_session(client, device_id=str(office)))

    assert stack.command_client.targets == [home]


def test_a_declared_device_without_the_capability_falls_back_to_the_old_rule(make_stack) -> None:
    stack = make_stack()
    home = stack.enroll("MAIL", HOME_CAPS, online=True)
    office = stack.enroll("GMKADIRAKBABA", ["desktop.open_application"], online=True)  # no screen

    client = stack.client()
    _shoot(client, _open_session(client, device_id=str(office)))

    assert stack.command_client.targets == [home]


@pytest.mark.parametrize("kind", ["unknown", "revoked"])
def test_an_unknown_or_revoked_declared_id_is_ignored_and_never_an_error(make_stack, kind) -> None:
    stack = make_stack()
    home = stack.enroll("MAIL", HOME_CAPS, online=True)
    if kind == "unknown":
        claimed = uuid.uuid4()
    else:
        claimed = stack.enroll("ESKI", HOME_CAPS, online=True)
        stack.revoke(claimed)
        stack.broker.connections.pop(claimed, None)

    client = stack.client()
    # Creating the session with the claim succeeds exactly as without it: no error that would
    # tell a caller which ids exist.
    session_id = _open_session(client, device_id=str(claimed))
    _shoot(client, session_id)

    assert stack.command_client.targets == [home]


def test_a_claimed_device_id_grants_no_device_trust(make_stack, monkeypatch) -> None:
    """M19b: "a freshly enrolled device with tailnet reachability but no authorisation gains
    nothing". The claim moves WHICH device acts; it must not become the owner-session device
    binding the step-up policy reads as trust."""
    stack = make_stack()
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True)
    seen: list[bool] = []
    real = service.step_up_policy.evaluate

    def spy(db, **kwargs):
        seen.append(kwargs["device_trusted"])
        return real(db, **kwargs)

    monkeypatch.setattr(service.step_up_policy, "evaluate", spy)

    client = stack.client()  # an identity session bound to NO device
    _shoot(client, _open_session(client, device_id=str(office)))
    assert seen == [False]

    bound = stack.client(bound_device=office)  # the native-client case: bound by identity
    _shoot(bound, _open_session(bound))
    assert seen == [False, True]


def test_the_declaration_is_not_the_sideband_target(make_stack) -> None:
    """`row.device_id` is where sideband frames go and where trust is read; a client's claim
    is kept apart from it."""
    stack = make_stack()
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True)
    client = stack.client()
    session_id = _open_session(client, device_id=str(office))

    with stack.voice.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(session_id))
        assert row is not None
        assert row.device_id is None
        assert row.context_json["declared_device_id"] == str(office)


def test_attach_replaces_the_declaration_with_the_new_legs_own(make_stack) -> None:
    stack = make_stack()
    home = stack.enroll("MAIL", HOME_CAPS, online=True)
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True)
    client = stack.client()
    session_id = _open_session(client, device_id=str(office))

    # A leg that says nothing about where it runs does not inherit the previous one's machine.
    attach_url = f"/v1/voice/realtime/sessions/{session_id}/attach"
    assert client.post(attach_url, json={}).status_code == 200
    with stack.voice.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(session_id))
        assert row is not None and row.context_json["declared_device_id"] is None

    assert client.post(attach_url, json={"device_id": str(home)}).status_code == 200
    _shoot(client, session_id)
    assert stack.command_client.targets[-1] == home


# ---------------------------------------------------- source (a'): identity-bound device


def test_an_identity_session_bound_to_a_device_is_that_devices_session(make_stack) -> None:
    stack = make_stack()
    stack.enroll("MAIL", HOME_CAPS, online=True)
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True)

    client = stack.client(bound_device=office)
    _shoot(client, _open_session(client))

    assert stack.command_client.targets == [office]


def test_a_declaration_outranks_the_identity_binding(make_stack) -> None:
    stack = make_stack()
    home = stack.enroll("MAIL", HOME_CAPS, online=True)
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True)

    client = stack.client(bound_device=office)
    _shoot(client, _open_session(client, device_id=str(home)))

    assert stack.command_client.targets == [home]


# --------------------------------------------------------------- source (b): address


def _edge_settings(**over: Any) -> dict[str, Any]:
    return {
        "device_affinity_by_source_ip": True,
        "trusted_proxy_cidrs": ("172.18.0.0/16",),
        **over,
    }


def test_the_source_address_is_off_by_default_even_from_a_matching_address(make_stack) -> None:
    stack = make_stack(trusted_proxy_cidrs=("172.18.0.0/16",))  # the switch stays off
    home = stack.enroll("MAIL", HOME_CAPS, online=True, ip=HOME_IP)
    stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True, ip=OFFICE_IP)
    assert stack.settings.device_affinity_by_source_ip is False

    client = stack.client(peer=(EDGE_IP, 40000), headers={"X-Real-IP": OFFICE_IP})
    _shoot(client, _open_session(client))

    # Nothing changed: the old rule, the home PC.
    assert stack.command_client.targets == [home]


def test_the_office_browser_reaching_cloud_core_through_the_edge_is_the_office_device(
    make_stack,
) -> None:
    stack = make_stack(**_edge_settings())
    stack.enroll("MAIL", HOME_CAPS, online=True, ip=HOME_IP)
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True, ip=OFFICE_IP)

    # The edge is the transport peer; the caller is in X-Real-IP, set by the edge.
    client = stack.client(peer=(EDGE_IP, 40000), headers={"X-Real-IP": OFFICE_IP})
    _shoot(client, _open_session(client))

    assert stack.command_client.targets == [office]


def test_a_forwarded_header_from_a_peer_that_is_not_the_edge_is_the_callers_own_claim(
    make_stack,
) -> None:
    stack = make_stack(**_edge_settings())
    home = stack.enroll("MAIL", HOME_CAPS, online=True, ip=HOME_IP)
    stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True, ip=OFFICE_IP)

    # A stranger on the tailnet writes the office address into the header. He is not the edge,
    # and his own address (100.99.99.99) matches no device: the old rule runs, the home PC.
    client = stack.client(peer=("100.99.99.99", 40000), headers={"X-Real-IP": OFFICE_IP})
    _shoot(client, _open_session(client))

    assert stack.command_client.targets == [home]


def test_the_peers_own_address_is_used_when_no_proxy_stands_in_front(make_stack) -> None:
    """Direct on the tailnet (no edge in the path, e.g. dev): the peer address IS the caller."""
    stack = make_stack(device_affinity_by_source_ip=True)  # no trusted proxies at all
    stack.enroll("MAIL", HOME_CAPS, online=True, ip=HOME_IP)
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True, ip=OFFICE_IP)

    client = stack.client(peer=(OFFICE_IP, 40000))
    _shoot(client, _open_session(client))

    assert stack.command_client.targets == [office]


def test_x_forwarded_for_is_never_read(make_stack) -> None:
    stack = make_stack(**_edge_settings())
    stack.enroll("MAIL", HOME_CAPS, online=True, ip=HOME_IP)
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True, ip=OFFICE_IP)
    # The edge saw the OFFICE caller and set X-Real-IP; the client had scribbled the HOME
    # address into the left end of X-Forwarded-For (which the edge only appends to). Reading
    # XFF would send this to the home PC - which the old rule does too, so the assertion is
    # on the office: only X-Real-IP, from the edge, gets it there.
    client = stack.client(
        peer=(EDGE_IP, 40000),
        headers={"X-Real-IP": OFFICE_IP, "X-Forwarded-For": f"{HOME_IP}, {OFFICE_IP}"},
    )
    _shoot(client, _open_session(client))
    assert stack.command_client.targets == [office]


def test_the_edge_without_the_header_names_nobody(make_stack) -> None:
    stack = make_stack(**_edge_settings())
    home = stack.enroll("MAIL", HOME_CAPS, online=True, ip=HOME_IP)
    # A device whose recorded address IS the edge's (a device that dialled in unproxied
    # from the same network - the situation before trusted_proxy_cidrs is right).
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True, ip=EDGE_IP)
    client = stack.client(peer=(EDGE_IP, 40000))  # from the edge, but no X-Real-IP
    _shoot(client, _open_session(client))
    # The peer IS the proxy and the proxy is not the caller: no address, so no device - the
    # old rule (the home PC), not the machine that happens to share the edge's address.
    assert stack.command_client.targets == [home]
    assert stack.broker.device_id_for_peer_ip(EDGE_IP) == office  # the map itself is not the guard


def test_two_devices_behind_one_address_are_no_answer_and_the_old_rule_runs(make_stack) -> None:
    stack = make_stack(**_edge_settings())
    stack.enroll("A", HOME_CAPS, online=True, ip=OFFICE_IP)
    stack.enroll("B", OFFICE_CAPS, online=True, ip=OFFICE_IP)
    assert stack.broker.device_id_for_peer_ip(OFFICE_IP) is None

    client = stack.client(peer=(EDGE_IP, 40000), headers={"X-Real-IP": OFFICE_IP})
    _shoot(client, _open_session(client))  # no error, no guess: the ordinary rule served it
    assert len(stack.command_client.targets) == 1


def test_an_address_that_matches_no_device_is_the_old_rule(make_stack) -> None:
    stack = make_stack(**_edge_settings())
    stack.enroll("MAIL", HOME_CAPS, online=True, ip=HOME_IP)
    client = stack.client(peer=(EDGE_IP, 40000), headers={"X-Real-IP": "100.1.2.3"})
    _shoot(client, _open_session(client))
    assert len(stack.command_client.targets) == 1


# ------------------------------------------------- the real handshake records the address


def test_the_real_handshake_records_where_the_device_dialled_from(make_stack) -> None:
    from cryptography.hazmat.primitives import hashes

    stack = make_stack(**_edge_settings())
    key = ec.generate_private_key(ec.SECP256R1())
    spki = base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")
    with stack.broker.session() as db:
        device = broker_service.enroll_device(
            db,
            name="GMKADIRAKBABA",
            platform="windows",
            public_key_spki_b64=spki,
            capabilities=OFFICE_CAPS,
            trace_id=None,
        )
        device_id = str(device.id)

    def sign(nonce_b64: str) -> str:
        message = base64.b64decode(nonce_b64) + device_id.encode("utf-8")
        return base64.b64encode(key.sign(message, ec.ECDSA(hashes.SHA256()))).decode("ascii")

    with TestClient(stack.app, client=(EDGE_IP, 40000)) as ws_client:
        with ws_client.websocket_connect(
            "/v1/devices/connect", headers={"X-Real-IP": OFFICE_IP}
        ) as ws:
            ws.send_json(
                {
                    "type": "hello",
                    "protocol_version": 1,
                    "device_id": device_id,
                    "software_version": "0.6.0",
                    "capabilities": OFFICE_CAPS,
                }
            )
            challenge = ws.receive_json()
            ws.send_json({"type": "auth", "signature": sign(challenge["nonce"])})
            assert ws.receive_json()["type"] == "welcome"
            # Live: the address maps to the device. Gone with the connection.
            assert stack.broker.device_id_for_peer_ip(OFFICE_IP) == uuid.UUID(device_id)
        assert stack.broker.device_id_for_peer_ip(OFFICE_IP) is None


# ------------------------------------------------- the port: desktop.open_application


def test_the_port_bound_to_the_office_names_the_office_for_desktop_open_application(
    make_stack,
) -> None:
    """The capability today's command needs, at the selection boundary the rest of this
    file also exercises: the office advertises it, the home PC is off."""
    stack = make_stack()
    stack.enroll("MAIL", HOME_CAPS, online=False)
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True)

    port = stack.device_action.bound_to([office])
    result = port.run(
        capability="desktop.open_application",
        payload={"application": "calculator"},
        idempotency_key="k-1",
        timeout_s=5,
    )

    assert stack.command_client.targets == [office]
    assert result.device_id == office
    assert result.selection_reason == selection.REASON_SESSION_AFFINITY


def test_the_unbound_port_is_the_old_rule_and_reports_as_before(make_stack) -> None:
    stack = make_stack()
    stack.enroll("MAIL", HOME_CAPS, online=False)
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True)

    result = stack.device_action.run(
        capability="desktop.open_application",
        payload={},
        idempotency_key="k-2",
        timeout_s=5,
    )

    assert stack.command_client.targets == [office]
    assert result.selection_reason == ""  # unchanged from before ADR-0208


def test_binding_to_nothing_returns_the_same_port(make_stack) -> None:
    stack = make_stack()
    assert stack.device_action.bound_to([]) is stack.device_action


def test_no_capable_device_is_still_reported_and_names_nothing_new(make_stack) -> None:
    stack = make_stack()
    stack.enroll("MAIL", HOME_CAPS, online=False)
    office = stack.enroll("GMKADIRAKBABA", OFFICE_CAPS, online=True)

    result = stack.device_action.bound_to([office]).run(
        capability="app.launch", payload={}, idempotency_key="k-3", timeout_s=5
    )

    assert (result.ok, result.error_class) == (False, "no_capable_device")
    assert result.message == "'app.launch' yeteneğine sahip çevrimiçi bir cihaz bulunamadı."
    assert stack.command_client.calls == []


# ------------------------------------------------------------------ research.start


def test_research_started_from_the_office_session_runs_on_the_office_and_says_why(
    make_stack,
) -> None:
    from app.research import service as research_service
    from app.research.runs_service import get_run

    stack = make_stack()
    caps = ["browser.chrome"]
    stack.enroll("MAIL", caps, online=True)
    office = stack.enroll("GMKADIRAKBABA", caps, online=True)

    with stack.factory() as db:
        started = research_service.start_browser_research(
            db, stack.broker, input="kuantum bilgisayarlar", session_device_ids=[office]
        )
        assert started.error is None
        assert started.device == {
            "device_id": str(office),
            "name": "GMKADIRAKBABA",
            "reason": selection.REASON_SESSION_AFFINITY,
            "reason_tr": "Komutu verdiğiniz cihaz seçildi.",
        }
        run = get_run(db, started.task_id)
        assert run is not None and run.device_id == office
        planned = [e for e in run.events_json if e.get("stage") == "planned"]
        assert planned[-1]["selection_reason"] == "session_affinity"


def test_the_research_voice_tool_starts_on_the_session_device(make_stack) -> None:
    """`research.start` selects its device itself (not through the device port): the same
    session hint has to reach it, through the real relay."""
    from unittest.mock import AsyncMock, patch

    stack = make_stack()
    caps = ["browser.chrome"]
    stack.enroll("MAIL", caps, online=True)
    office = stack.enroll("GMKADIRAKBABA", caps, online=True)
    client = stack.client()
    session_id = _open_session(client, device_id=str(office))

    fake_temporal = AsyncMock()
    fake_temporal.start_workflow = AsyncMock(return_value=None)
    with patch("app.research.service.Client.connect", AsyncMock(return_value=fake_temporal)):
        response = client.post(
            f"/v1/voice/realtime/sessions/{session_id}/tool-calls",
            json={
                "call_id": "r-1",
                "name": "research.start",
                "arguments": {"topic": "kuantum bilgisayarlar"},
            },
        )
    assert response.status_code == 200, response.text
    device = response.json()["result"]["device"]
    assert device["device_id"] == str(office)
    assert device["reason"] == selection.REASON_SESSION_AFFINITY


def test_research_with_no_session_device_is_the_old_rule_and_reports_as_before(make_stack) -> None:
    from app.research import service as research_service

    stack = make_stack()
    caps = ["browser.chrome"]
    home = stack.enroll("MAIL", caps, online=True)
    stack.enroll("GMKADIRAKBABA", caps, online=True)

    with stack.factory() as db:
        started = research_service.start_browser_research(db, stack.broker, input="konu")
        # Byte for byte what a research run reported before ADR-0208: no `reason` key.
        assert started.device == {"device_id": str(home), "name": "MAIL"}
