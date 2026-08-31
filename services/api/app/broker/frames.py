"""Protocol v1 frame models and parsing.

Mirrors packages/schemas/device-protocol.schema.json (the authoritative
contract) as pydantic models with `extra="forbid"`, so the broker validates
every inbound frame to the same constraints without a runtime dependency on
the schema file's location.

`parse_frame` never raises: a malformed frame yields a protocol `error` frame
payload with class `validation_error` (referencing `command_id` when it is
parseable), and the caller keeps the connection open per DEVICE_PROTOCOL.md §5.
"""

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

PROTOCOL_VERSION = 1

ERROR_CLASSES = (
    "validation_error",
    "auth_error",
    "device_offline",
    "capability_missing",
    "dependency_unavailable",
    "provider_rate_limited",
    "provider_error",
    "ui_target_not_found",
    "ui_state_changed",
    "timeout",
    "command_expired",
    "cancelled",
    "retry_exhausted",
    "artifact_render_error",
    "voice_provider_error",
    "security_scope_error",
    "internal_bug",
)

CAPABILITY_PATTERN = r"^[a-z][a-z0-9_.]{1,63}$"


class _Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorObject(_Frame):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    error_class: str = Field(alias="class")
    message: str = Field(max_length=2000)
    retryable: bool | None = None

    @field_validator("error_class")
    @classmethod
    def _known_class(cls, value: str) -> str:
        if value not in ERROR_CLASSES:
            raise ValueError(f"unknown error class: {value!r}")
        return value


class HelloFrame(_Frame):
    type: Literal["hello"]
    protocol_version: int = Field(ge=1)
    device_id: uuid.UUID
    software_version: str = Field(max_length=64)
    capabilities: list[str] = Field(max_length=128)

    @field_validator("capabilities")
    @classmethod
    def _valid_capability_names(cls, value: list[str]) -> list[str]:
        for cap in value:
            if not re.match(CAPABILITY_PATTERN, cap):
                raise ValueError(f"invalid capability name: {cap!r}")
        return value


class AuthFrame(_Frame):
    type: Literal["auth"]
    signature: str = Field(max_length=512)


class HeartbeatFrame(_Frame):
    type: Literal["heartbeat"]
    seq: int = Field(ge=0)


class CommandAckFrame(_Frame):
    type: Literal["command_ack"]
    command_id: uuid.UUID
    status: Literal["accepted", "running", "succeeded", "failed"]
    result: dict[str, Any] | None = None
    error: ErrorObject | None = None


class AgentErrorFrame(_Frame):
    """`error` frame sent by the agent (e.g. answering a malformed broker frame)."""

    type: Literal["error"]
    command_id: uuid.UUID | None = None
    error: ErrorObject


INBOUND_FRAME_TYPES: dict[str, type[_Frame]] = {
    "hello": HelloFrame,
    "auth": AuthFrame,
    "heartbeat": HeartbeatFrame,
    "command_ack": CommandAckFrame,
    "error": AgentErrorFrame,
}

InboundFrame = HelloFrame | AuthFrame | HeartbeatFrame | CommandAckFrame | AgentErrorFrame


def _extract_command_id(raw: Any) -> str | None:
    if isinstance(raw, dict):
        cid = raw.get("command_id")
        if isinstance(cid, str):
            try:
                return str(uuid.UUID(cid))
            except ValueError:
                return None
    return None


def parse_frame(text: str) -> tuple[InboundFrame | None, dict[str, Any] | None]:
    """Parse one inbound text frame.

    Returns (frame, None) on success or (None, error_frame) for malformed input.
    """
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return None, error_frame("validation_error", "frame is not valid JSON")
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        return None, error_frame("validation_error", "frame must be an object with a 'type'")
    frame_type = raw["type"]
    model = INBOUND_FRAME_TYPES.get(frame_type)
    if model is None:
        return None, error_frame(
            "validation_error",
            f"unsupported frame type {frame_type!r}",
            command_id=_extract_command_id(raw),
        )
    try:
        return model.model_validate(raw), None
    except (ValidationError, ValueError) as exc:
        return None, error_frame(
            "validation_error",
            f"invalid {frame_type} frame: {exc}"[:2000],
            command_id=_extract_command_id(raw),
        )


# ---------------------------------------------------------------- outbound


def error_frame(
    error_class: str,
    message: str,
    *,
    command_id: str | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"class": error_class, "message": message[:2000]}
    if retryable is not None:
        error["retryable"] = retryable
    frame: dict[str, Any] = {"type": "error", "error": error}
    if command_id is not None:
        frame["command_id"] = command_id
    return frame


def challenge_frame(nonce_b64: str) -> dict[str, Any]:
    return {"type": "challenge", "nonce": nonce_b64}


def welcome_frame(session_id: str, heartbeat_interval_s: float) -> dict[str, Any]:
    return {
        "type": "welcome",
        "session_id": session_id,
        "heartbeat_interval_s": heartbeat_interval_s,
    }


def heartbeat_ack_frame(seq: int) -> dict[str, Any]:
    return {"type": "heartbeat_ack", "seq": seq}


def cancel_frame(command_id: str) -> dict[str, Any]:
    return {"type": "cancel", "command_id": command_id}


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def command_frame(
    *,
    command_id: str,
    idempotency_key: str,
    capability: str,
    payload: dict[str, Any],
    expires_at: datetime,
    trace_id: str,
) -> dict[str, Any]:
    return {
        "type": "command",
        "command": {
            "command_id": command_id,
            "idempotency_key": idempotency_key,
            "capability": capability,
            "payload": payload,
            "expires_at": _iso_utc(expires_at),
            "trace_id": trace_id,
        },
    }
