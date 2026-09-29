"""One connection is handed one command ONCE, whichever paths reach for it at the same time.

The gate, 2026-09-29 (home PC): ``test_duplicate_ws_delivery_tolerated_via_reack`` read a
``command`` frame where it expected the heartbeat ack - the failure of 2026-09-18 again, a
week after its fix. That fix (``replay_guard``) wrote down "sent" AFTER the send, and the
send is an ``await``: while one path is inside it, another sees the command as not yet sent
and sends it too. ``test_broker_replay_race`` runs its two deliveries one after the other,
so it never saw the overlap.

Four paths deliver: the POST that created the command, the opening replay, the sweep for
commands another process created, and a worker thread's immediate delivery. All four go
through ``deliver_command``; the claim is taken there, before the send, with nothing awaited
between the look and the mark.

The socket here is slow on purpose: a send does not finish until the test lets it.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.broker import runtime as broker_runtime
from app.broker import ws as broker_ws
from app.broker.runtime import DeviceConnection


class SlowSocket:
    """A socket whose sends wait at a gate, so that two deliveries can overlap."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.entered = 0
        self.gate: asyncio.Event | None = None
        self.fail_next = False

    async def send_json(self, frame: dict[str, Any]) -> None:
        self.entered += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_next:
            self.fail_next = False
            raise ConnectionError("the socket died mid-send")
        self.sent.append(frame)

    def commands(self) -> list[str]:
        return [f["command"]["command_id"] for f in self.sent if f.get("type") == "command"]


@dataclass
class FakeCommand:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    trace_id: str | None = None
    capability: str = "desktop.open_application"
    payload_json: dict[str, Any] = field(default_factory=dict)
    idempotency_key: uuid.UUID = field(default_factory=uuid.uuid4)
    expires_at: Any = field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=5))


class FakeRuntime:
    def __init__(self, deliverable: list[FakeCommand] | None = None) -> None:
        self.deliverable = deliverable or []
        self.counters: Counter[str] = Counter()

    def session(self) -> Any:
        return _NullSession()


class _NullSession:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: object) -> bool:
        return False


@pytest.fixture()
def patched(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    marked: list[uuid.UUID] = []
    monkeypatch.setattr(broker_ws, "_mark_delivered", lambda _runtime, cid: marked.append(cid))
    return marked


def connection_on(socket: SlowSocket) -> DeviceConnection:
    return DeviceConnection(device_id=uuid.uuid4(), session_id=uuid.uuid4(), websocket=socket)


def test_the_replay_does_not_send_what_live_dispatch_is_in_the_middle_of_sending(
    monkeypatch: pytest.MonkeyPatch, patched: list[uuid.UUID]
) -> None:
    """The overlap the gate hit: the POST is inside its send when the replay looks."""
    socket = SlowSocket()
    connection = connection_on(socket)
    command = FakeCommand(trace_id="t-post")
    runtime = FakeRuntime([command])
    monkeypatch.setattr(
        broker_ws.service, "deliverable_commands", lambda _db, _id: list(runtime.deliverable)
    )

    async def scenario() -> None:
        socket.gate = asyncio.Event()
        live = asyncio.create_task(broker_ws.deliver_command(runtime, connection, command))
        while socket.entered == 0:
            await asyncio.sleep(0)
        replay = asyncio.create_task(broker_ws._redeliver_pending(runtime, connection))
        # The replay has loaded the row and looked; give it every chance to queue a send.
        for _ in range(50):
            await asyncio.sleep(0.002)
        socket.gate.set()
        assert await live is True
        await replay

    asyncio.run(scenario())
    assert socket.commands() == [str(command.id)], socket.commands()
    assert patched == [command.id]
    assert runtime.counters["commands_delivered"] == 1


def test_two_live_dispatches_of_one_command_send_it_once(patched: list[uuid.UUID]) -> None:
    """The POST and the sweep: the row is 'undelivered' until the first send is recorded."""
    socket = SlowSocket()
    connection = connection_on(socket)
    command = FakeCommand()
    runtime = FakeRuntime()

    async def scenario() -> list[bool]:
        socket.gate = asyncio.Event()
        first = asyncio.create_task(broker_ws.deliver_command(runtime, connection, command))
        second = asyncio.create_task(broker_ws.deliver_command(runtime, connection, command))
        for _ in range(20):
            await asyncio.sleep(0.002)
        socket.gate.set()
        return [await first, await second]

    results = asyncio.run(scenario())
    assert socket.commands() == [str(command.id)]
    assert sorted(results) == [False, True], "one call sent it; the other says it did not"
    assert patched == [command.id]


def test_a_command_the_replay_delivered_is_not_sent_again_after_the_window_closed(
    monkeypatch: pytest.MonkeyPatch, patched: list[uuid.UUID]
) -> None:
    """The other order: the replay wins, closes its window, and the POST arrives after."""
    socket = SlowSocket()
    connection = connection_on(socket)
    command = FakeCommand()
    runtime = FakeRuntime([command])
    monkeypatch.setattr(
        broker_ws.service, "deliverable_commands", lambda _db, _id: list(runtime.deliverable)
    )

    async def scenario() -> bool:
        await broker_ws._redeliver_pending(runtime, connection)
        assert connection.replay_guard is None
        return await broker_ws.deliver_command(runtime, connection, command)

    assert asyncio.run(scenario()) is False
    assert socket.commands() == [str(command.id)]


def test_a_worker_threads_delivery_and_the_loops_send_it_once(patched: list[uuid.UUID]) -> None:
    """``app.devices.commands`` delivers from a worker thread with a loop of its own; the
    claim cannot lean on one event loop's turn-taking."""
    socket = SlowSocket()
    connection = connection_on(socket)
    command = FakeCommand()
    runtime = FakeRuntime()
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def from_a_thread() -> None:
        barrier.wait(timeout=10)
        results.append(asyncio.run(broker_ws.deliver_command(runtime, connection, command)))

    threads = [threading.Thread(target=from_a_thread) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert socket.commands() == [str(command.id)]
    assert results.count(True) == 1 and results.count(False) == 7


def test_a_send_that_failed_gives_the_command_back(patched: list[uuid.UUID]) -> None:
    """A claim is for a send that HAPPENED. A socket that died mid-send delivered nothing,
    and the command must still be deliverable on this connection."""
    socket = SlowSocket()
    connection = connection_on(socket)
    command = FakeCommand()
    runtime = FakeRuntime()

    async def scenario() -> tuple[bool, bool]:
        socket.fail_next = True
        first = await broker_ws.deliver_command(runtime, connection, command)
        second = await broker_ws.deliver_command(runtime, connection, command)
        return first, second

    assert asyncio.run(scenario()) == (False, True)
    assert socket.commands() == [str(command.id)]
    assert patched == [command.id]


def test_another_command_is_not_held_back_by_the_first(patched: list[uuid.UUID]) -> None:
    socket = SlowSocket()
    connection = connection_on(socket)
    runtime = FakeRuntime()
    one, two = FakeCommand(), FakeCommand()

    async def scenario() -> None:
        assert await broker_ws.deliver_command(runtime, connection, one)
        assert await broker_ws.deliver_command(runtime, connection, two)

    asyncio.run(scenario())
    assert socket.commands() == [str(one.id), str(two.id)]


def test_a_new_connection_is_handed_the_command_again(patched: list[uuid.UUID]) -> None:
    """A redelivery across a reconnect is the protocol's own: the device may never have
    seen the first frame. The memory is the CONNECTION's, not the broker's."""
    runtime = FakeRuntime()
    command = FakeCommand()
    first, second = SlowSocket(), SlowSocket()

    async def scenario() -> None:
        assert await broker_ws.deliver_command(runtime, connection_on(first), command)
        assert await broker_ws.deliver_command(runtime, connection_on(second), command)

    asyncio.run(scenario())
    assert first.commands() == second.commands() == [str(command.id)]


def test_a_connection_that_lives_for_days_remembers_a_bounded_number(
    patched: list[uuid.UUID],
) -> None:
    socket = SlowSocket()
    connection = connection_on(socket)
    runtime = FakeRuntime()
    limit = broker_runtime.SENT_MEMORY
    commands = [FakeCommand() for _ in range(limit + 50)]

    async def scenario() -> bool:
        for command in commands:
            assert await broker_ws.deliver_command(runtime, connection, command)
        # The newest is remembered; the oldest has been let go.
        return await broker_ws.deliver_command(runtime, connection, commands[-1])

    assert asyncio.run(scenario()) is False
    assert connection.remembered() == limit
    assert len(socket.commands()) == limit + 50
