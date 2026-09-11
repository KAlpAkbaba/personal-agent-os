"""Unit tests: a newer agent's hello still reaches an older Cloud Core (ADR-0118 addendum).

On 2026-09-11 the first agent to carry ADR-0118's ``build_id`` met the Cloud Core still in
production, whose hello forbade fields it did not know. The handshake was refused, the
device fell offline, and the staged installer - correctly - rolled the new agent back. The
defect was in the rule, not in either build: an ADDITIVE announcement from a newer agent
could take a device off the network, and a Cloud Core rollback to last-known-good would
have stranded every agent newer than it.

The rule that replaced it, pinned here: hello ignores what it does not know, validates what
it does know exactly as strictly as before, and says so in the log only once the peer has
authenticated. Every other inbound frame stays closed.
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import structlog
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.broker import service
from app.broker.frames import (
    MAX_IGNORED_HELLO_FIELD_CHARS,
    MAX_IGNORED_HELLO_FIELDS,
    HelloFrame,
    parse_frame,
)
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime
from app.config import Settings
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA = REPO_ROOT / "packages" / "schemas" / "device-protocol.schema.json"

#: What the agent really announced on 2026-09-11 (from the production log).
REAL_BUILD_ID = "ac0ad30ca7a0c4a1"

BROKER_TABLES = [
    Device.__table__,
    DeviceSession.__table__,
    DeviceCommand.__table__,
    EnrollmentToken.__table__,
    AuditEvent.__table__,
]


def _hello(**overrides: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": "hello",
        "protocol_version": 1,
        "device_id": str(uuid.uuid4()),
        "software_version": "0.6.0",
        "capabilities": ["desktop.open_application"],
    }
    frame.update(overrides)
    return frame


# ------------------------------------------------------------------ the frame


def test_a_hello_from_a_newer_build_is_accepted_and_what_it_adds_is_dropped() -> None:
    frame, error = parse_frame(
        json.dumps(
            _hello(build_id=REAL_BUILD_ID, locale="tr-TR", from_a_later_build={"any": 1})
        )
    )

    assert error is None, error
    assert isinstance(frame, HelloFrame)
    # What this Core knows arrives intact...
    assert frame.build_id == REAL_BUILD_ID
    assert frame.capabilities == ["desktop.open_application"]
    # ...and what it does not is named, never stored.
    assert frame.ignored_fields == ("from_a_later_build", "locale")
    assert "locale" not in frame.model_dump()
    assert "from_a_later_build" not in frame.model_dump()


def test_a_hello_with_nothing_unknown_has_nothing_to_report() -> None:
    frame, error = parse_frame(json.dumps(_hello(build_id=REAL_BUILD_ID)))

    assert error is None
    assert isinstance(frame, HelloFrame)
    assert frame.ignored_fields == ()


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("protocol_version", 0),
        ("device_id", "not-a-uuid"),
        ("software_version", "v" * 65),
        ("build_id", "b" * 65),
        ("source_revision", "s" * 65),
        ("capabilities", ["Desktop.OPEN"]),
        ("capabilities", [f"cap.n{i}" for i in range(129)]),
        ("type", "hullo"),
    ],
)
def test_every_field_hello_knows_is_still_validated_strictly(field: str, bad_value: Any) -> None:
    # Tolerance for what a Core does not know must never become tolerance for what it does:
    # an unknown field rides along with every bad value here, and the frame still fails.
    raw = _hello(**{field: bad_value, "from_a_later_build": True})

    frame, error = parse_frame(json.dumps(raw))

    assert frame is None
    assert error is not None and error["error"]["class"] == "validation_error"


def test_a_hello_missing_a_required_field_is_still_refused() -> None:
    raw = _hello(from_a_later_build=True)
    del raw["capabilities"]

    frame, error = parse_frame(json.dumps(raw))

    assert frame is None
    assert error is not None and error["error"]["class"] == "validation_error"


def test_the_dropped_names_are_bounded_because_hello_arrives_before_authentication() -> None:
    raw = _hello(**{f"k{i:03d}" + "x" * 500: i for i in range(100)})

    frame, error = parse_frame(json.dumps(raw))

    assert error is None
    assert isinstance(frame, HelloFrame)
    assert len(frame.ignored_fields) == MAX_IGNORED_HELLO_FIELDS
    assert all(len(name) <= MAX_IGNORED_HELLO_FIELD_CHARS for name in frame.ignored_fields)


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "auth", "signature": "QUJD"},
        {"type": "heartbeat", "seq": 1},
        {"type": "command_ack", "command_id": str(uuid.uuid4()), "status": "running"},
        {"type": "error", "error": {"class": "validation_error", "message": "x"}},
    ],
    ids=["auth", "heartbeat", "command_ack", "error"],
)
def test_every_other_inbound_frame_still_refuses_what_it_does_not_know(
    raw: dict[str, Any],
) -> None:
    # Only hello opened. The others grow through their designated extension points
    # (heartbeat.status, command_ack.result), which is why they can stay closed.
    frame, error = parse_frame(json.dumps({**raw, "from_a_later_build": True}))

    assert frame is None
    assert error is not None and error["error"]["class"] == "validation_error"


def test_hello_knows_exactly_the_fields_the_schema_declares() -> None:
    # The drift this addendum exists for had a second half: ADR-0118 added build_id to
    # HelloFrame and to the agent and never to the schema that calls itself authoritative.
    # So this reads the schema rather than restating it.
    hello = json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]["hello"]

    assert set(HelloFrame.model_fields) == set(hello["properties"])
    required = {name for name, spec in HelloFrame.model_fields.items() if spec.is_required()}
    assert required == set(hello["required"])
    # The schema carries both halves of the rule: emitters stay closed, receivers ignore.
    assert hello["additionalProperties"] is False
    assert "ignore" in hello["$comment"]


# ------------------------------------------------------------ the real handshake


def _app_and_runtime() -> tuple[Any, BrokerRuntime]:
    """The real application object, its own broker bound to an in-memory database."""
    app = create_app(Settings(_env_file=None))
    runtime: BrokerRuntime = app.state.broker
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in BROKER_TABLES:
        table.create(engine)
    runtime._engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return app, runtime


def _enroll(runtime: BrokerRuntime) -> tuple[str, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")
    with runtime.session() as db:
        device = service.enroll_device(
            db,
            name="ev-pc",
            platform="windows",
            public_key_spki_b64=spki,
            capabilities=["desktop.open_application"],
            trace_id=None,
        )
        return str(device.id), key


def _sign(key: ec.EllipticCurvePrivateKey, nonce_b64: str, device_id: str) -> str:
    message = base64.b64decode(nonce_b64) + device_id.encode("utf-8")
    return base64.b64encode(key.sign(message, ec.ECDSA(hashes.SHA256()))).decode("ascii")


def _ignored_events(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry.get("event") == "broker_hello_fields_ignored"]


def test_a_newer_agent_completes_the_real_handshake_and_the_skew_is_logged() -> None:
    app, runtime = _app_and_runtime()
    device_id, key = _enroll(runtime)

    with structlog.testing.capture_logs() as logs, TestClient(app) as client:
        with client.websocket_connect("/v1/devices/connect") as ws:
            ws.send_json(
                _hello(
                    device_id=device_id,
                    build_id=REAL_BUILD_ID,
                    source_revision="ef5cd12",
                    announced_by_a_later_build=True,
                )
            )
            challenge = ws.receive_json()
            assert challenge["type"] == "challenge", challenge
            ws.send_json(
                {"type": "auth", "signature": _sign(key, challenge["nonce"], device_id)}
            )
            welcome = ws.receive_json()
            assert welcome["type"] == "welcome", welcome

    events = _ignored_events(logs)
    assert len(events) == 1, logs
    assert events[0]["fields"] == ["announced_by_a_later_build"]
    assert events[0]["device_id"] == device_id
    # The fields this Core does know were applied as ever: the row carries the identity.
    with runtime.session() as db:
        row = db.get(Device, uuid.UUID(device_id))
        assert row is not None
        assert (row.build_id, row.source_revision) == (REAL_BUILD_ID, "ef5cd12")


def test_an_unauthenticated_peer_never_gets_its_field_names_into_the_log() -> None:
    app, runtime = _app_and_runtime()
    device_id, _enrolled_key = _enroll(runtime)
    stranger = ec.generate_private_key(ec.SECP256R1())

    with structlog.testing.capture_logs() as logs, TestClient(app) as client:
        with client.websocket_connect("/v1/devices/connect") as ws:
            ws.send_json(_hello(device_id=device_id, injected_name_here=True))
            challenge = ws.receive_json()
            assert challenge["type"] == "challenge", challenge
            ws.send_json(
                {"type": "auth", "signature": _sign(stranger, challenge["nonce"], device_id)}
            )
            refused = ws.receive_json()
            assert refused["type"] == "error"
            assert refused["error"]["class"] == "auth_error"

    assert _ignored_events(logs) == []
    assert not any("injected_name_here" in json.dumps(entry, default=str) for entry in logs)
