"""M12 integration (ADR-0039): the ``voice_sideband`` device-protocol frame.

What the realtime session service pushes must be exactly what the device
protocol schema declares and what the Windows agent forwards: same type, same
event vocabulary, same field names, bounded size. These tests pin the three
against each other so none can drift silently again.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest

from app.broker import frames
from app.voice.realtime_sessions import sideband

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA = REPO_ROOT / "packages" / "schemas" / "device-protocol.schema.json"


def _schema() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def test_schema_declares_the_frame_additively() -> None:
    schema = _schema()
    refs = [entry["$ref"] for entry in schema["oneOf"]]
    assert refs[-1] == "#/$defs/voice_sideband"
    # every v1 frame is still there, untouched in order
    assert refs[:-1] == [f"#/$defs/{t}" for t in (
        "hello", "challenge", "auth", "welcome", "heartbeat", "heartbeat_ack",
        "command", "command_ack", "cancel", "error")]
    definition = schema["$defs"]["voice_sideband"]
    assert definition["properties"]["type"] == {"const": frames.VOICE_SIDEBAND_FRAME_TYPE}
    assert tuple(definition["properties"]["event"]["enum"]) == frames.VOICE_SIDEBAND_EVENTS
    assert definition["required"] == ["type", "session_id", "event", "payload"]
    assert definition["additionalProperties"] is False


@pytest.mark.parametrize("event", sideband.SIDEBAND_EVENTS)
def test_every_service_frame_validates_against_the_protocol_model(event: str) -> None:
    frame = sideband.sideband_frame(uuid.uuid4(), event, {"call_id": "c1", "n": 1})
    parsed = frames.VoiceSidebandFrame.model_validate(frame)
    assert parsed.type == "voice_sideband" and parsed.event == event
    assert set(frame) == {"type", "session_id", "event", "payload", "at"}
    assert frames.voice_sideband_frame_size(frame) < frames.MAX_VOICE_SIDEBAND_FRAME_BYTES


def test_the_frame_is_outbound_only_and_never_accepted_inbound() -> None:
    frame = sideband.sideband_frame(uuid.uuid4(), "say", {"text": "Bir dakika."})
    parsed, error = frames.parse_frame(json.dumps(frame))
    assert parsed is None
    assert error["error"]["class"] == "validation_error"
    assert "voice_sideband" in error["error"]["message"]


def test_unknown_event_and_extra_field_are_refused_by_the_model() -> None:
    with pytest.raises(ValueError):
        sideband.sideband_frame(uuid.uuid4(), "telemetry", {})
    with pytest.raises(ValueError):
        frames.VoiceSidebandFrame.model_validate({
            "type": "voice_sideband", "session_id": str(uuid.uuid4()), "event": "say",
            "payload": {}, "extra": 1})


class _Broker:
    def __init__(self) -> None:
        self.sent: list[tuple[uuid.UUID, dict]] = []

    def is_online(self, device_id: uuid.UUID) -> bool:
        return True

    async def send_frame(self, device_id: uuid.UUID, frame: dict) -> bool:
        self.sent.append((device_id, frame))
        return True


def test_broker_sideband_refuses_an_oversize_frame_so_it_stays_queued() -> None:
    broker = _Broker()
    pusher = sideband.BrokerSideband(broker)
    device = uuid.uuid4()

    async def run() -> tuple[bool, bool]:
        pusher.bind_loop(asyncio.get_running_loop())
        small = sideband.sideband_frame(uuid.uuid4(), "say", {"text": "kısa"})
        big = sideband.sideband_frame(uuid.uuid4(), "tool_completed",
                                      {"result": {"blob": "x" * (16 * 1024)}})
        ok = await asyncio.to_thread(pusher.push, device_id=device, frame=small)
        refused = await asyncio.to_thread(pusher.push, device_id=device, frame=big)
        return ok, refused

    ok, refused = asyncio.run(run())
    assert ok is True and refused is False
    assert [f["event"] for _, f in broker.sent] == ["say"]
