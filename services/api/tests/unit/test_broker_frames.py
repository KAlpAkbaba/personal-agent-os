"""Unit tests: protocol frame parsing and malformed-frame handling."""

import json
import uuid
from datetime import UTC

from app.broker import frames
from app.broker.frames import (
    AgentErrorFrame,
    AuthFrame,
    CommandAckFrame,
    HeartbeatFrame,
    HelloFrame,
    parse_frame,
)

DEVICE_ID = str(uuid.uuid4())
COMMAND_ID = str(uuid.uuid4())


def test_valid_hello_parses() -> None:
    frame, error = parse_frame(
        json.dumps(
            {
                "type": "hello",
                "protocol_version": 1,
                "device_id": DEVICE_ID,
                "software_version": "0.1.0",
                "capabilities": ["desktop.open_application"],
            }
        )
    )
    assert error is None
    assert isinstance(frame, HelloFrame)
    assert str(frame.device_id) == DEVICE_ID


def test_valid_auth_heartbeat_ack_error_parse() -> None:
    frame, _ = parse_frame(json.dumps({"type": "auth", "signature": "QUJD"}))
    assert isinstance(frame, AuthFrame)

    frame, _ = parse_frame(json.dumps({"type": "heartbeat", "seq": 0}))
    assert isinstance(frame, HeartbeatFrame)

    frame, _ = parse_frame(
        json.dumps(
            {
                "type": "command_ack",
                "command_id": COMMAND_ID,
                "status": "failed",
                "result": None,
                "error": {"class": "capability_missing", "message": "nope", "retryable": False},
            }
        )
    )
    assert isinstance(frame, CommandAckFrame)
    assert frame.error is not None and frame.error.error_class == "capability_missing"

    frame, _ = parse_frame(
        json.dumps(
            {"type": "error", "error": {"class": "validation_error", "message": "bad frame"}}
        )
    )
    assert isinstance(frame, AgentErrorFrame)


def test_not_json_yields_validation_error_frame() -> None:
    frame, error = parse_frame("this is not json{{{")
    assert frame is None
    assert error is not None
    assert error["type"] == "error"
    assert error["error"]["class"] == "validation_error"


def test_non_object_and_missing_type_rejected() -> None:
    for raw in ("[1,2,3]", '"hello"', "42", json.dumps({"no_type": True})):
        frame, error = parse_frame(raw)
        assert frame is None
        assert error is not None and error["error"]["class"] == "validation_error"


def test_unknown_frame_type_rejected() -> None:
    frame, error = parse_frame(json.dumps({"type": "warp_drive"}))
    assert frame is None
    assert error is not None and "warp_drive" in error["error"]["message"]


def test_extra_fields_rejected() -> None:
    frame, error = parse_frame(json.dumps({"type": "heartbeat", "seq": 1, "extra": "x"}))
    assert frame is None
    assert error is not None and error["error"]["class"] == "validation_error"


def test_invalid_ack_status_rejected_and_references_command_id() -> None:
    frame, error = parse_frame(
        json.dumps({"type": "command_ack", "command_id": COMMAND_ID, "status": "exploded"})
    )
    assert frame is None
    assert error is not None
    assert error["error"]["class"] == "validation_error"
    assert error["command_id"] == COMMAND_ID


def test_bad_capability_name_in_hello_rejected() -> None:
    frame, error = parse_frame(
        json.dumps(
            {
                "type": "hello",
                "protocol_version": 1,
                "device_id": DEVICE_ID,
                "software_version": "1",
                "capabilities": ["Desktop.OPEN"],
            }
        )
    )
    assert frame is None
    assert error is not None and error["error"]["class"] == "validation_error"


def test_unknown_error_class_rejected() -> None:
    frame, error = parse_frame(
        json.dumps(
            {
                "type": "command_ack",
                "command_id": COMMAND_ID,
                "status": "failed",
                "error": {"class": "made_up_error", "message": "x"},
            }
        )
    )
    assert frame is None
    assert error is not None


def test_outbound_frame_builders_match_schema_shapes() -> None:
    challenge = frames.challenge_frame("QUJDREVGRw==")
    assert challenge == {"type": "challenge", "nonce": "QUJDREVGRw=="}

    welcome = frames.welcome_frame(COMMAND_ID, 10)
    assert welcome["type"] == "welcome"
    assert welcome["heartbeat_interval_s"] == 10

    ack = frames.heartbeat_ack_frame(7)
    assert ack == {"type": "heartbeat_ack", "seq": 7}

    cancel = frames.cancel_frame(COMMAND_ID)
    assert cancel == {"type": "cancel", "command_id": COMMAND_ID}

    from datetime import datetime

    command = frames.command_frame(
        command_id=COMMAND_ID,
        idempotency_key="k" * 8,
        capability="desktop.open_application",
        payload={"application": "notepad"},
        expires_at=datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC),
        trace_id="trace-1",
    )
    assert command["type"] == "command"
    assert command["command"]["expires_at"] == "2026-08-31T12:00:00Z"
    assert command["command"]["payload"] == {"application": "notepad"}
