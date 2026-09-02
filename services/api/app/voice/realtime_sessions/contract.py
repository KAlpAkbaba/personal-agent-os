"""The canonical, versioned wire contract of the realtime session API.

One document, generated from the Pydantic request models that actually validate
requests, so the web client (and any other client) validates what it sends against
exactly what the server accepts. It is committed at
``packages/protocol/realtime-session-contract.json`` (a test fails when it drifts)
and served at ``GET /v1/voice/realtime/contract`` so a client can adapt to the
VERSION it is talking to instead of guessing.

Why this exists: the first real owner qualification hit ``HTTP 422`` at session
creation because the page (at HEAD) sent ``voice`` to a deployed API (one release
older) whose request model is ``extra="forbid"``. The status alone said nothing;
the body said ``extra_forbidden`` on ``body.voice``. Contract drift is now visible
before a request is sent, and a 422 is shown field by field.

Version history (bump on every change to a request model's accepted fields):
  1 - M12 tracks A+E: create/attach/tool-call/complete/events as first shipped.
  2 - ADR-0043: ``voice`` on create (a wire voice from the provider's list).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.voice.realtime_sessions.routes import (
    AttachRequest,
    CreateSessionRequest,
    EventsRequest,
    ToolCallRequest,
    ToolCompleteRequest,
)

CONTRACT_VERSION = 2
#: Fields a version-1 server (the first shipped contract) accepts on create; a
#: client that gets 404 from the contract endpoint is talking to v1.
LEGACY_V1_CREATE_FIELDS = ("client_kind", "transport", "language", "narration_session_id",
                           "session_ttl_s")

_MODELS: dict[str, type[BaseModel]] = {
    "create_session": CreateSessionRequest,
    "attach": AttachRequest,
    "tool_call": ToolCallRequest,
    "tool_complete": ToolCompleteRequest,
    "events": EventsRequest,
}


def realtime_contract() -> dict[str, Any]:
    """The contract document: JSON Schema per request body, plus the version."""
    schemas = {name: model.model_json_schema() for name, model in _MODELS.items()}
    return {
        "kind": "pagentos.realtime_session_contract",
        "contract_version": CONTRACT_VERSION,
        "requests": schemas,
        "legacy": {"1": {"create_session": list(LEGACY_V1_CREATE_FIELDS)}},
    }


def create_fields(version: int | None = None) -> tuple[str, ...]:
    """The create-session field names for a contract version (default: current)."""
    if version == 1:
        return LEGACY_V1_CREATE_FIELDS
    return tuple(CreateSessionRequest.model_json_schema()["properties"].keys())


__all__ = ["CONTRACT_VERSION", "LEGACY_V1_CREATE_FIELDS", "create_fields", "realtime_contract"]
