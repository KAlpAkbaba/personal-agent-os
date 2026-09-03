"""Unit tests (no real WebSocket/DB server) for two M13 broker changes:

1. Every hello refreshes ``devices.capabilities_json``/``software_version``
   (``app.broker.service.apply_hello``, wired into ``app.broker.ws`` §handshake).
2. The sweeper delivers PENDING, never-delivered commands to devices already
   connected to this process, not only on (re)connect
   (``BrokerRuntime._deliver_pending_for_connected``).

A minimal fake WebSocket (``send_json`` records frames) exercises
``app.broker.ws.deliver_command`` and the runtime sweep loop's delivery path
directly, against an in-memory SQLite engine wired onto the runtime.
"""

import asyncio
import base64
import uuid
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.broker import service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.broker.ws import deliver_command
from app.config import Settings

BROKER_TABLES = [Device.__table__, DeviceSession.__table__, DeviceCommand.__table__,
                 EnrollmentToken.__table__, AuditEvent.__table__]


class FakeWebSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail = fail

    async def send_json(self, frame: dict[str, Any]) -> None:
        if self.fail:
            raise ConnectionError("socket closed")
        self.sent.append(frame)


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


@pytest.fixture()
def runtime() -> BrokerRuntime:
    # StaticPool + check_same_thread=False: BrokerRuntime's real code paths
    # (e.g. mark_command_delivered) run DB work via asyncio.to_thread, which
    # would otherwise hand a bare in-memory sqlite:// engine a FRESH empty
    # database on the worker thread (SingletonThreadPool default).
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in BROKER_TABLES:
        table.create(engine)
    rt = BrokerRuntime(Settings(_env_file=None))
    rt._engine = engine  # inject the in-memory engine (unit-test only)
    rt._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield rt
    engine.dispose()


def _enroll(runtime: BrokerRuntime) -> Device:
    with runtime.session() as db:
        return service.enroll_device(
            db, name="pc", platform="windows", public_key_spki_b64=_spki(),
            capabilities=["desktop.open_application"], trace_id=None,
        )


# --------------------------------------------------------------------- hello


def test_apply_hello_updates_capabilities_and_version_on_reconnect(runtime: BrokerRuntime) -> None:
    device = _enroll(runtime)
    with runtime.session() as db:
        service.apply_hello(
            db, device.id, capabilities=["browser.chrome", "browser.fetch_evidence"],
            software_version="2.0.0",
        )
        refreshed = service.get_device(db, device.id)
        assert refreshed.capabilities_json == ["browser.chrome", "browser.fetch_evidence"]
        assert refreshed.software_version == "2.0.0"


def test_apply_hello_overwrites_stale_capabilities(runtime: BrokerRuntime) -> None:
    device = _enroll(runtime)
    with runtime.session() as db:
        service.apply_hello(db, device.id, capabilities=["browser.chrome"], software_version="1")
        # A later hello without browser.chrome means the worker is gone —
        # selection must stop offering it, so the refresh must OVERWRITE.
        service.apply_hello(db, device.id, capabilities=["desktop.open_application"],
                             software_version="1")
        refreshed = service.get_device(db, device.id)
        assert "browser.chrome" not in refreshed.capabilities_json


# ------------------------------------------------------------------- sweeper


def test_sweep_delivers_pending_command_to_connected_device(runtime: BrokerRuntime) -> None:
    device = _enroll(runtime)
    ws = FakeWebSocket()
    connection = DeviceConnection(device_id=device.id, session_id=uuid.uuid4(), websocket=ws)
    runtime.connections[device.id] = connection

    # A command row created "by another process" while this device happens
    # to be connected here — never delivered by this process's own connect
    # handler, since the connection already existed.
    with runtime.session() as db:
        service.create_command(
            db, device_id=device.id, capability="desktop.open_application",
            payload={"application": "notepad"}, idempotency_key="sweep-1",
            timeout_s=60, trace_id="t",
        )

    asyncio.run(runtime._deliver_pending_for_connected())

    assert len(ws.sent) == 1
    assert ws.sent[0]["command"]["capability"] == "desktop.open_application"
    with runtime.session() as db:
        command = service.get_command(db, device.id, uuid.UUID(ws.sent[0]["command"]["command_id"]))
        assert command.status == "delivered"


def test_sweep_does_not_redeliver_already_delivered_command(runtime: BrokerRuntime) -> None:
    device = _enroll(runtime)
    ws = FakeWebSocket()
    connection = DeviceConnection(device_id=device.id, session_id=uuid.uuid4(), websocket=ws)
    runtime.connections[device.id] = connection

    with runtime.session() as db:
        command, _ = service.create_command(
            db, device_id=device.id, capability="desktop.open_application", payload={},
            idempotency_key="sweep-2", timeout_s=60, trace_id="t",
        )
        service.mark_command_delivered(db, command.id)

    asyncio.run(runtime._deliver_pending_for_connected())
    assert ws.sent == []  # already delivered; sweeper only picks up PENDING rows


def test_sweep_ignores_devices_with_no_connection(runtime: BrokerRuntime) -> None:
    device = _enroll(runtime)
    with runtime.session() as db:
        service.create_command(
            db, device_id=device.id, capability="desktop.open_application", payload={},
            idempotency_key="sweep-3", timeout_s=60, trace_id="t",
        )
    # No connection registered for the device -> nothing to iterate/deliver.
    asyncio.run(runtime._deliver_pending_for_connected())  # must not raise


def test_sweep_skips_commands_for_unrelated_connected_devices(runtime: BrokerRuntime) -> None:
    device_a = _enroll(runtime)
    with runtime.session() as db:
        device_b = service.enroll_device(
            db, name="pc-b", platform="windows", public_key_spki_b64=_spki(),
            capabilities=[], trace_id=None,
        )
    ws_a = FakeWebSocket()
    runtime.connections[device_a.id] = DeviceConnection(
        device_id=device_a.id, session_id=uuid.uuid4(), websocket=ws_a
    )
    with runtime.session() as db:
        service.create_command(
            db, device_id=device_b.id, capability="desktop.open_application", payload={},
            idempotency_key="sweep-4", timeout_s=60, trace_id="t",
        )
    asyncio.run(runtime._deliver_pending_for_connected())
    assert ws_a.sent == []  # the pending command belongs to device_b, not connected here


def test_deliver_command_marks_delivered_and_returns_true(runtime: BrokerRuntime) -> None:
    device = _enroll(runtime)
    ws = FakeWebSocket()
    connection = DeviceConnection(device_id=device.id, session_id=uuid.uuid4(), websocket=ws)
    with runtime.session() as db:
        command, _ = service.create_command(
            db, device_id=device.id, capability="desktop.open_application", payload={},
            idempotency_key="direct-1", timeout_s=60, trace_id="t",
        )
    delivered = asyncio.run(deliver_command(runtime, connection, command))
    assert delivered is True
    assert len(ws.sent) == 1


def test_deliver_command_returns_false_on_socket_failure(runtime: BrokerRuntime) -> None:
    device = _enroll(runtime)
    ws = FakeWebSocket(fail=True)
    connection = DeviceConnection(device_id=device.id, session_id=uuid.uuid4(), websocket=ws)
    with runtime.session() as db:
        command, _ = service.create_command(
            db, device_id=device.id, capability="desktop.open_application", payload={},
            idempotency_key="direct-2", timeout_s=60, trace_id="t",
        )
    delivered = asyncio.run(deliver_command(runtime, connection, command))
    assert delivered is False


def test_expire_due_commands_still_runs_alongside_sweep_delivery(runtime: BrokerRuntime) -> None:
    device = _enroll(runtime)
    with runtime.session() as db:
        service.create_command(
            db, device_id=device.id, capability="desktop.open_application", payload={},
            idempotency_key="expire-1", timeout_s=0.001, trace_id="t",
        )
    import time

    time.sleep(0.01)
    with runtime.session() as db:
        due = service.expire_due_commands(db)
    assert len(due) == 1
    assert due[0].status == "expired"
