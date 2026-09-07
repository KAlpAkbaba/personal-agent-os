"""Unit tests: the device handoff between colours (M18.4 gap 1, ADR-0081 addendum 3).

A draining colour closes every device session with 1012 (the agent reconnects within its
first backoff step, through the edge, to the colour that now holds device authority), drops
their presence at once, refuses new device connections, and says so on health. The routes
are loopback-only and idempotent; undrain is the exact reverse (rollback / reconcile).
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from app.broker import service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.routes import peer_is_loopback
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.config import Settings
from app.main import create_app

BROKER_TABLES = [
    Device.__table__,
    DeviceSession.__table__,
    DeviceCommand.__table__,
    EnrollmentToken.__table__,
    AuditEvent.__table__,
]


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed_with: int | None = None

    async def send_json(self, frame: dict[str, Any]) -> None:
        self.sent.append(frame)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


def _bind_sqlite(rt: BrokerRuntime) -> BrokerRuntime:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in BROKER_TABLES:
        table.create(engine)
    rt._engine = engine
    rt._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return rt


@pytest.fixture()
def runtime() -> BrokerRuntime:
    return _bind_sqlite(BrokerRuntime(Settings(_env_file=None)))


def _app_and_runtime() -> tuple[Any, BrokerRuntime]:
    """The real application object; its own broker (the one health and the ws handler
    use) bound to an in-memory database."""
    app = create_app(Settings(_env_file=None))
    return app, _bind_sqlite(app.state.broker)


def _connect(runtime: BrokerRuntime) -> tuple[uuid.UUID, FakeWebSocket]:
    with runtime.session() as db:
        device = service.enroll_device(
            db,
            name="ev-pc",
            platform="windows",
            public_key_spki_b64=_spki(),
            capabilities=["desktop.alarm_arm"],
            trace_id=None,
        )
        device_id = device.id
    socket = FakeWebSocket()
    runtime.register_connection(
        DeviceConnection(device_id=device_id, session_id=uuid.uuid4(), websocket=socket)
    )
    return device_id, socket


def test_drain_closes_every_device_session_with_1012_and_flags_the_colour(runtime) -> None:
    device_a, socket_a = _connect(runtime)
    device_b, socket_b = _connect(runtime)
    assert runtime.is_online(device_a) and runtime.is_online(device_b)
    assert runtime.health_check()["draining"] is False

    closed = asyncio.run(runtime.drain())

    assert closed == 2
    assert socket_a.closed_with == 1012 and socket_b.closed_with == 1012
    # Presence is gone at once - this colour must not report the devices online while the
    # other colour is the authority.
    assert not runtime.is_online(device_a) and not runtime.is_online(device_b)
    assert runtime.draining is True and runtime.drained_at is not None
    assert runtime.health_check()["draining"] is True
    assert runtime.health_check()["active_sessions"] == 0
    assert runtime.stats()["draining"] is True
    # A second drain closes nothing more and stays draining (idempotent).
    assert asyncio.run(runtime.drain()) == 0
    assert runtime.draining is True
    # The reverse.
    assert runtime.undrain() is True
    assert runtime.draining is False and runtime.drained_at is None
    assert runtime.undrain() is False


def test_the_loopback_guard_fails_closed() -> None:
    assert peer_is_loopback("127.0.0.1") and peer_is_loopback("::1")
    assert peer_is_loopback("testclient")
    assert not peer_is_loopback("100.90.158.26")
    assert not peer_is_loopback(None)


def test_the_drain_route_reports_the_handoff_and_health_shows_it() -> None:
    app, runtime = _app_and_runtime()
    device_id, socket = _connect(runtime)
    with TestClient(app) as client:
        # TestClient's peer host is "testclient" - loopback by the guard's own list.
        first = client.post("/v1/devices/drain")
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["draining"] is True and body["closed"] == 1
        assert body["active_sessions_before"] == 1 and body["active_sessions"] == 0
        assert socket.closed_with == 1012
        assert not runtime.is_online(device_id)
        again = client.post("/v1/devices/drain").json()
        assert again["draining"] is True and again["closed"] == 0
        broker_health = client.get("/v1/system/health").json()["checks"]["broker"]
        assert broker_health["draining"] is True and broker_health["active_sessions"] == 0
        # ...and the reverse: the colour takes device connections again.
        back = client.post("/v1/devices/undrain").json()
        assert back == {"draining": False, "was_draining": True, "active_sessions": 0}
        assert client.get("/v1/system/health").json()["checks"]["broker"]["draining"] is False
        assert client.post("/v1/devices/undrain").json()["was_draining"] is False


def test_the_drain_routes_refuse_a_non_loopback_peer() -> None:
    """The edge (and anything on the tailnet) is not loopback: 403, nothing drained."""
    app, runtime = _app_and_runtime()
    _connect(runtime)
    with TestClient(app, client=("100.90.158.26", 40000)) as remote:
        assert remote.post("/v1/devices/drain").status_code == 403
        assert remote.post("/v1/devices/undrain").status_code == 403
    assert runtime.draining is False and len(runtime.connections) == 1


def test_a_draining_colour_refuses_new_device_connections() -> None:
    """The handler closes a fresh connection with 1012 before any handshake work, and
    takes connections again after undrain."""
    app, runtime = _app_and_runtime()
    asyncio.run(runtime.drain())
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect("/v1/devices/connect") as ws:
                ws.receive_text()
        assert excinfo.value.code == 1012
        runtime.undrain()
        with client.websocket_connect("/v1/devices/connect") as ws:
            # Not draining: the handler runs the handshake - a non-hello first frame is
            # answered with auth_error and a close that is not the drain's 1012.
            ws.send_json({"type": "not-a-hello"})
            assert ws.receive_json()["type"] == "error"
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
            assert closed.value.code != 1012
