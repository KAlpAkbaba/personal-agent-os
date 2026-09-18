"""A command created while a connection is opening must reach the device ONCE.

CI, 2026-09-18 (run 35337102502): one command id produced two ``broker_command_delivered``
lines 3 ms apart - the first carrying the POST's trace_id, the second none. A connection is
registered for live dispatch before it replays the commands that were waiting for it, so a
command created inside that window is delivered by the POST *and* re-read by the replay,
which still sees it unacked. The integration test noticed only by accident, because the
extra frame arrived where it expected a heartbeat ack, and only on a slow runner.

The protocol tolerates a duplicate (the agent re-acks; that is what
``test_duplicate_ws_delivery_tolerated_via_reack`` is about). Tolerating is not intending:
the frame is a real ``desktop.open_application`` handed to the device a second time, and
"the agent will cope" is not a reason to send it.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass, field
from typing import Any

from app.broker import ws as broker_ws
from app.broker.runtime import DeviceConnection


@dataclass
class FakeSocket:
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def send_json(self, frame: dict[str, Any]) -> None:
        self.sent.append(frame)


@dataclass
class FakeCommand:
    id: uuid.UUID
    trace_id: str | None = None
    capability: str = "desktop.open_application"
    payload_json: dict[str, Any] = field(default_factory=dict)
    idempotency_key: uuid.UUID = field(default_factory=uuid.uuid4)
    expires_at: Any = field(
        default_factory=lambda: datetime.now(UTC) + timedelta(minutes=5)
    )


class FakeRuntime:
    """Only what the two functions under test touch."""

    def __init__(self, deliverable: list[FakeCommand]) -> None:
        self.deliverable = deliverable
        self.counters: Counter[str] = Counter()

    def session(self):  # pragma: no cover - never entered; the loaders are patched
        raise AssertionError("the test patches every database call")


def _connection(socket: FakeSocket) -> DeviceConnection:
    return DeviceConnection(
        device_id=uuid.uuid4(), session_id=uuid.uuid4(), websocket=socket
    )


def _patch_db(monkeypatch, runtime: FakeRuntime) -> None:
    monkeypatch.setattr(broker_ws, "_mark_delivered", lambda *_a, **_k: None)
    monkeypatch.setattr(
        broker_ws.service,
        "deliverable_commands",
        lambda _db, _device_id: list(runtime.deliverable),
    )
    # _redeliver_pending's loader opens a session purely to call the patched query.
    monkeypatch.setattr(FakeRuntime, "session", lambda self: _NullSession())


class _NullSession:
    def __enter__(self):
        return None

    def __exit__(self, *_exc):
        return False


def test_a_command_dispatched_during_the_opening_window_is_not_replayed(monkeypatch):
    socket = FakeSocket()
    connection = _connection(socket)
    command = FakeCommand(id=uuid.uuid4(), trace_id="t-1")
    runtime = FakeRuntime([command])
    _patch_db(monkeypatch, runtime)

    async def scenario() -> None:
        # The POST reaches the freshly registered connection first...
        assert await broker_ws.deliver_command(runtime, connection, command)
        # ...and the opening replay then re-reads the same still-unacked row.
        await broker_ws._redeliver_pending(runtime, connection)

    asyncio.run(scenario())

    ids = [f["command"]["command_id"] for f in socket.sent if f.get("type") == "command"]
    assert ids == [str(command.id)], f"the device was handed the same command twice: {ids}"


def test_the_replay_still_delivers_what_live_dispatch_did_not(monkeypatch):
    """The guard must not turn the replay into a no-op - that is its whole purpose."""
    socket = FakeSocket()
    connection = _connection(socket)
    waiting = FakeCommand(id=uuid.uuid4(), trace_id="t-2")
    runtime = FakeRuntime([waiting])
    _patch_db(monkeypatch, runtime)

    asyncio.run(broker_ws._redeliver_pending(runtime, connection))

    ids = [f["command"]["command_id"] for f in socket.sent if f.get("type") == "command"]
    assert ids == [str(waiting.id)]


def test_the_window_closes_so_a_long_connection_accumulates_nothing(monkeypatch):
    """After the opening replay the guard is dropped: a later duplicate is a genuine
    redelivery, and a connection that lives for days holds no growing set of ids."""
    socket = FakeSocket()
    connection = _connection(socket)
    runtime = FakeRuntime([])
    _patch_db(monkeypatch, runtime)

    asyncio.run(broker_ws._redeliver_pending(runtime, connection))
    assert connection.replay_guard is None

    later = FakeCommand(id=uuid.uuid4())
    asyncio.run(broker_ws.deliver_command(runtime, connection, later))
    assert connection.replay_guard is None, "the closed window must stay closed"
    assert connection.already_sent(later.id) is False
