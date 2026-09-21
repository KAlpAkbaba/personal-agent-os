"""ADR-0199 stage 2: the pinch-mouse's pointer-streaming session, through the REAL
application object - ``operator.pointer_session`` (``begin``/``end``) and the pointer
WebSocket (``app.voice.realtime_sessions.pointer_ws``), the same relay/router path
``tests/unit/test_operator_tools.py`` and ``tests/unit/test_gesture_events.py`` use.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.broker.models import DeviceCommand
from app.broker.runtime import DeviceConnection
from app.ledger.models import ActivityEventRow
from app.routines.dispatch import DeviceRunResult
from app.voice.realtime_sessions import pointer_session
from app.voice.realtime_sessions.models import RealtimeSessionRow
from tests.unit.test_operator_tools import _create, _tool, _wired


class FakeDeviceSocket:
    """Stands in for the companion's own websocket on the ONE connected
    ``DeviceConnection`` (``BrokerRuntime.connections``): records every frame
    ``BrokerRuntime.send_frame`` sends, and can be told to block (a ``threading.Event``,
    not an ``asyncio.Event`` - the test drives it from the main thread, the server runs
    the ASGI app in its own background thread/loop under ``TestClient``)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.gate: threading.Event | None = None

    async def send_json(self, frame: dict) -> None:
        if self.gate is not None:
            await asyncio.to_thread(self.gate.wait)
        self.sent.append(frame)


def _media_ready(device, window_id: str = "w-9101-365601875") -> None:
    """A YouTube-titled window on the desktop, foreground, so ``WINDOW_REF_MEDIA``
    resolves to it - the same recipe ``test_gesture_events`` uses."""
    device.results["window.list"] = DeviceRunResult(
        True,
        result={
            "windows": [
                {
                    "window_id": window_id,
                    "title": "(965) NFS Most Wanted - YouTube - Google Chrome",
                    "foreground": True,
                    "image": "chrome.exe",
                }
            ]
        },
    )


def _wired_with_socket():
    client, factory, device, operator_service = _wired()
    _media_ready(device)
    broker = client.app.state.broker
    device_id = next(iter(broker.connections))
    fake_socket = FakeDeviceSocket()
    broker.connections[device_id] = DeviceConnection(
        device_id=device_id,
        session_id=uuid.uuid4(),
        websocket=fake_socket,
    )
    return client, factory, device, fake_socket


def _begin(client, sid: str) -> dict:
    call = _tool(client, sid, "operator.pointer_session", {"action": "begin"})
    assert call["status"] == "succeeded", call
    return call["result"]


def _context(factory, sid: str) -> dict:
    with factory() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        return dict(row.context_json or {})


def _last_pointer_receipt(factory) -> dict:
    with factory() as db:
        row = (
            db.execute(
                select(ActivityEventRow)
                .where(ActivityEventRow.action == "operator.pointer_session")
                .order_by(ActivityEventRow.occurred_at.desc())
            )
            .scalars()
            .first()
        )
        return dict(row.detail_json) if row is not None else {}


def _last_pointer_audit(factory, action: str) -> dict:
    from app.broker.models import AuditEvent

    with factory() as db:
        row = (
            db.execute(
                select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id.desc())
            )
            .scalars()
            .first()
        )
        return dict(row.metadata_json or {}) if row is not None else {}


def _device_command_count(factory) -> int:
    with factory() as db:
        return len(db.execute(select(DeviceCommand)).scalars().all())


def _wait_for_server_close(ws) -> None:
    """The server never answers a frame (no acks) - the only signal a test has that it
    finished processing (the flush, the receipt, the audit row) is the close it sends at
    the very end. Blocks until that arrives."""
    import contextlib

    from starlette.websockets import WebSocketDisconnect

    with contextlib.suppress(WebSocketDisconnect):
        ws.receive_text()


# ------------------------------------------------------------------------------ begin


def test_begin_activates_the_media_window_then_opens_pointer_stream_begin() -> None:
    client, factory, device, _fake_socket = _wired_with_socket()
    sid = _create(client)

    result = _begin(client, sid)

    assert result["status"] == "open"
    assert isinstance(result["stream_token"], str) and len(result["stream_token"]) >= 32
    assert result["expires_at"]
    calls = device.capabilities_called()
    assert calls.index("window.activate") < calls.index("pointer.stream_begin")
    begin_payload = device.payload_for("pointer.stream_begin")
    assert begin_payload["window_id"] == device.payload_for("window.activate")["window_id"]
    assert begin_payload["session"]

    record = _context(factory, sid)["pointer_session"]
    assert record["stream_token"] == result["stream_token"]
    assert record["session"] == begin_payload["session"]
    assert record["used"] is False


def test_begin_with_no_desktop_but_the_shell_asks_for_a_window() -> None:
    """No MEDIA window to open the stream on - "Hangi pencere?", not a receipt (the
    same clarification ``operator.key`` answers for a gesture with nothing to act on)."""
    client, factory, _device, _fake_socket = _wired()  # default fixture: window.list -> []
    sid = _create(client)

    call = _tool(client, sid, "operator.pointer_session", {"action": "begin"})

    assert call["status"] == "needs_clarification", call
    assert call["result"]["status"] == "needs_clarification"


# -------------------------------------------------------------------------- the socket


def test_socket_forwards_a_move_as_one_pointer_stream_frame_to_the_device() -> None:
    client, factory, device, fake_socket = _wired_with_socket()
    sid = _create(client)
    opened = _begin(client, sid)
    pointer_session_id = device.payload_for("pointer.stream_begin")["session"]

    # The real browser path: the bearer token as a QUERY PARAMETER, since a native
    # WebSocket constructor cannot set an Authorization header.
    token = client.headers["Authorization"].split(" ", 1)[1]
    url = f"/v1/voice/realtime/sessions/{sid}/pointer?token={token}"
    with client.websocket_connect(url) as ws:
        ws.send_json({"t": "hello", "stream_token": opened["stream_token"]})
        ws.send_json({"t": "move", "dx": 5, "dy": -3, "seq": 1})
        ws.send_json({"t": "end"})
        _wait_for_server_close(ws)

    assert fake_socket.sent == [
        {
            "kind": "pointer_stream",
            "session": pointer_session_id,
            "frames": [{"t": "move", "dx": 5, "dy": -3}],
        }
    ]
    assert _context(factory, sid).get("pointer_session") is None
    receipt = _last_pointer_receipt(factory)
    assert receipt["observed_after"]["server"] == {
        "moves": 1,
        "buttons": 0,
        "dropped": 0,
        "duration_ms": receipt["observed_after"]["server"]["duration_ms"],
    }
    assert device.payload_for("pointer.stream_end") == {"session": pointer_session_id}


def test_socket_forwards_button_events_in_order_alongside_a_move() -> None:
    client, factory, device, fake_socket = _wired_with_socket()
    sid = _create(client)
    opened = _begin(client, sid)
    pointer_session_id = device.payload_for("pointer.stream_begin")["session"]

    with client.websocket_connect(f"/v1/voice/realtime/sessions/{sid}/pointer") as ws:
        ws.send_json({"t": "hello", "stream_token": opened["stream_token"]})
        ws.send_json({"t": "button", "button": "left", "action": "down"})
        ws.send_json({"t": "button", "button": "left", "action": "up"})
        ws.send_json({"t": "end"})
        _wait_for_server_close(ws)

    # Two frames sent back to back, with nothing slowing the "device" down, may land in
    # one pointer_stream batch or two - the sender drains as fast as it can, and this
    # test's claim is ORDER, not batching (the coalescing test above owns batching,
    # under a gate that makes it deterministic). Every ``kind``/``session`` matches, and
    # flattening every batch's frames back to one sequence must read in the order sent.
    assert all(f["kind"] == "pointer_stream" for f in fake_socket.sent)
    assert all(f["session"] == pointer_session_id for f in fake_socket.sent)
    flattened = [item for f in fake_socket.sent for item in f["frames"]]
    assert flattened == [
        {"t": "button", "button": "left", "action": "down"},
        {"t": "button", "button": "left", "action": "up"},
    ]
    receipt = _last_pointer_receipt(factory)
    assert receipt["observed_after"]["server"]["buttons"] == 2
    assert receipt["observed_after"]["server"]["moves"] == 0


def test_the_socket_coalesces_moves_while_the_device_link_is_slow() -> None:
    client, factory, device, fake_socket = _wired_with_socket()
    sid = _create(client)
    opened = _begin(client, sid)

    fake_socket.gate = threading.Event()  # unset: the device "link" is slow
    with client.websocket_connect(f"/v1/voice/realtime/sessions/{sid}/pointer") as ws:
        ws.send_json({"t": "hello", "stream_token": opened["stream_token"]})
        ws.send_json({"t": "move", "dx": 5, "dy": 5, "seq": 1})
        # Give the server's sender task a moment to pick up move 1 and block inside
        # send_json on the gate before move 2/3 arrive - otherwise they might land in
        # the SAME first batch, which would also coalesce but prove nothing about a
        # slow link specifically.
        import time

        time.sleep(0.05)
        ws.send_json({"t": "move", "dx": 3, "dy": -2, "seq": 2})
        ws.send_json({"t": "move", "dx": 1, "dy": 1, "seq": 3})
        time.sleep(0.05)
        fake_socket.gate.set()  # release move 1's send; the sender then drains 2+3 as ONE
        ws.send_json({"t": "end"})
        _wait_for_server_close(ws)

    assert len(fake_socket.sent) == 2, fake_socket.sent
    assert fake_socket.sent[0]["frames"] == [{"t": "move", "dx": 5, "dy": 5}]
    assert fake_socket.sent[1]["frames"] == [{"t": "move", "dx": 4, "dy": -1}]


def test_the_socket_drops_frames_over_30_per_second_and_counts_them() -> None:
    client, factory, device, fake_socket = _wired_with_socket()
    sid = _create(client)
    opened = _begin(client, sid)

    with client.websocket_connect(f"/v1/voice/realtime/sessions/{sid}/pointer") as ws:
        ws.send_json({"t": "hello", "stream_token": opened["stream_token"]})
        for i in range(35):
            ws.send_json({"t": "move", "dx": 1, "dy": 0, "seq": i})
        ws.send_json({"t": "end"})
        _wait_for_server_close(ws)

    receipt = _last_pointer_receipt(factory)
    assert receipt["observed_after"]["server"]["dropped"] == 5
    assert receipt["observed_after"]["server"]["moves"] == 30


# --------------------------------------------------------------------- token refusals


def test_a_wrong_stream_token_is_refused_and_audited() -> None:
    import pytest
    from starlette.websockets import WebSocketDisconnect

    from app.voice.realtime_sessions.pointer_ws import CLOSE_POLICY

    client, factory, _device, _fake_socket = _wired_with_socket()
    sid = _create(client)
    _begin(client, sid)

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/v1/voice/realtime/sessions/{sid}/pointer") as ws:
            ws.send_json({"t": "hello", "stream_token": "not-the-live-token"})
            ws.receive_text()
    assert excinfo.value.code == CLOSE_POLICY
    audited = _last_pointer_audit(factory, "voice_pointer_stream_refused")
    assert audited == {"reason": "bad_stream_token"}
    # Refused, not consumed: the record the real token belongs to is untouched.
    assert _context(factory, sid)["pointer_session"]["used"] is False


def test_an_expired_stream_token_is_refused() -> None:
    import pytest
    from starlette.websockets import WebSocketDisconnect

    from app.voice.realtime_sessions.pointer_ws import CLOSE_POLICY

    client, factory, _device, _fake_socket = _wired_with_socket()
    sid = _create(client)
    opened = _begin(client, sid)

    with factory() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        ctx = dict(row.context_json or {})
        stale_expiry = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        stale = {**ctx["pointer_session"], "expires_at": stale_expiry}
        ctx["pointer_session"] = stale
        row.context_json = dict(ctx)
        db.commit()

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/v1/voice/realtime/sessions/{sid}/pointer") as ws:
            ws.send_json({"t": "hello", "stream_token": opened["stream_token"]})
            ws.receive_text()
    assert excinfo.value.code == CLOSE_POLICY


def test_a_stream_token_is_single_use() -> None:
    import pytest
    from starlette.websockets import WebSocketDisconnect

    from app.voice.realtime_sessions.pointer_ws import CLOSE_POLICY

    client, factory, device, fake_socket = _wired_with_socket()
    sid = _create(client)
    opened = _begin(client, sid)

    with client.websocket_connect(f"/v1/voice/realtime/sessions/{sid}/pointer") as ws:
        ws.send_json({"t": "hello", "stream_token": opened["stream_token"]})
        ws.send_json({"t": "end"})
        _wait_for_server_close(ws)

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/v1/voice/realtime/sessions/{sid}/pointer") as ws2:
            ws2.send_json({"t": "hello", "stream_token": opened["stream_token"]})
            ws2.receive_text()
    assert excinfo.value.code == CLOSE_POLICY


# ------------------------------------------------------------------------------- end


def test_end_action_closes_the_stream_with_a_zero_count_receipt() -> None:
    client, factory, device, _fake_socket = _wired_with_socket()
    sid = _create(client)
    _begin(client, sid)
    pointer_session_id = device.payload_for("pointer.stream_begin")["session"]

    call = _tool(client, sid, "operator.pointer_session", {"action": "end"})

    assert call["status"] == "succeeded", call
    assert call["result"]["observed_after"]["server"] == {
        "moves": 0,
        "buttons": 0,
        "dropped": 0,
        "duration_ms": call["result"]["observed_after"]["server"]["duration_ms"],
    }
    assert device.payload_for("pointer.stream_end") == {"session": pointer_session_id}
    assert _context(factory, sid).get("pointer_session") is None


def test_end_with_nothing_open_is_a_truthful_noop() -> None:
    client, _factory, device, _fake_socket = _wired_with_socket()
    sid = _create(client)

    call = _tool(client, sid, "operator.pointer_session", {"action": "end"})

    assert call["status"] == "succeeded", call
    assert call["result"]["execution_status"] == "noop"
    assert call["result"]["terminal_status"] == "already"
    assert "pointer.stream_end" not in device.capabilities_called()


def test_action_is_required_and_never_inferred() -> None:
    client, _factory, _device, _fake_socket = _wired_with_socket()
    sid = _create(client)

    call = _tool(client, sid, "operator.pointer_session", {})

    assert call["status"] == "failed", call


# --------------------------------------------------------------------------- silence


def test_the_socket_ends_after_silence(monkeypatch) -> None:
    monkeypatch.setattr(pointer_session, "SILENCE_TIMEOUT_S", 0.2)
    client, factory, device, _fake_socket = _wired_with_socket()
    sid = _create(client)
    opened = _begin(client, sid)

    import pytest
    from starlette.websockets import WebSocketDisconnect

    from app.voice.realtime_sessions.pointer_ws import CLOSE_NORMAL

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/v1/voice/realtime/sessions/{sid}/pointer") as ws:
            ws.send_json({"t": "hello", "stream_token": opened["stream_token"]})
            ws.receive_text()
    assert excinfo.value.code == CLOSE_NORMAL
    assert device.payload_for("pointer.stream_end")
    audited = _last_pointer_audit(factory, "voice_pointer_stream_closed")
    assert audited["reason"] == "silence_timeout"


# --------------------------------------------------------------- no device_commands row


def test_nothing_in_the_pointer_stream_ever_creates_a_device_commands_row() -> None:
    client, factory, device, fake_socket = _wired_with_socket()
    sid = _create(client)
    opened = _begin(client, sid)

    with client.websocket_connect(f"/v1/voice/realtime/sessions/{sid}/pointer") as ws:
        ws.send_json({"t": "hello", "stream_token": opened["stream_token"]})
        for i in range(5):
            ws.send_json({"t": "move", "dx": 1, "dy": 1, "seq": i})
        ws.send_json({"t": "button", "button": "left", "action": "click"})
        ws.send_json({"t": "end"})
        _wait_for_server_close(ws)

    # The fake device port never writes device_commands (it is not the real dispatch
    # path) - the assertion that matters is that a move/button NEVER became a call to
    # it at all: only the two capabilities begin/end use.
    assert set(device.capabilities_called()) <= {
        "window.list",
        "window.activate",
        "pointer.stream_begin",
        "pointer.stream_end",
    }
    assert _device_command_count(factory) == 0
