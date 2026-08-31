"""Narration REST surface (M4, API_AND_PROTOCOLS §7).

Endpoints:
- POST   /v1/narration/sessions                     start a session for an artifact
- GET    /v1/narration/sessions/{id}                session + cursor
- GET    /v1/narration/sessions/{id}/cursor         semantic cursor only
- PATCH  /v1/narration/sessions/{id}/cursor         update cursor (cross-device)
- POST   /v1/narration/sessions/{id}/command        oku/dur/devam/tekrar/jump/explain
- GET    /v1/narration/pronunciation                list dictionary
- PUT    /v1/narration/pronunciation                upsert an entry
- DELETE /v1/narration/pronunciation/{id}           delete an entry
- POST   /v1/narration/preview                      normalize text (no persistence)

The narration runtime (DB session factory) is created lazily on app.state on
first use so app wiring stays a single import + one include_router line.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.artifacts import service as artifact_service
from app.logging import get_logger
from app.narration import commands, service
from app.narration.engine import Cursor, build_plan
from app.narration.models import NARRATION_STATES, NarrationSession, PronunciationEntry
from app.narration.normalizer import normalize
from app.narration.runtime import NarrationRuntime

logger = get_logger("app.narration.routes")

router = APIRouter(prefix="/v1/narration")

_STATE_KEY = "_state"  # reserved key inside semantic_cursor_json for machine state


def _runtime(request: Request) -> NarrationRuntime:
    state = request.app.state
    if not hasattr(state, "narration"):
        from app.config import get_settings

        state.narration = NarrationRuntime(get_settings())
    return state.narration


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------- state <-> json mapping


def _cursor_top_level(cursor: Cursor | None) -> dict[str, Any]:
    return cursor.as_dict() if cursor else {}


def _pack_state(st: commands.NarrationState) -> dict[str, Any]:
    """Persisted JSON: the spec cursor at top level (so any device can read it)
    plus a reserved ``_state`` blob carrying saved_cursor/anchor for
    explain-then-return continuity."""
    payload = _cursor_top_level(st.cursor)
    payload[_STATE_KEY] = {
        "state": st.state.value,
        "saved_cursor": st.saved_cursor.as_dict() if st.saved_cursor else None,
        "paragraph_anchor": st.paragraph_anchor.as_dict() if st.paragraph_anchor else None,
        "speed": st.speed,
    }
    return payload


def _unpack_state(row: NarrationSession) -> commands.NarrationState:
    data = dict(row.semantic_cursor_json or {})
    meta = data.get(_STATE_KEY) or {}
    return commands.NarrationState(
        state=commands.State(row.state),
        cursor=Cursor.from_dict(data),
        saved_cursor=Cursor.from_dict(meta.get("saved_cursor")),
        paragraph_anchor=Cursor.from_dict(meta.get("paragraph_anchor")),
        speed=row.speed,
    )


# --------------------------------------------------------------- payloads


def _session_payload(row: NarrationSession) -> dict[str, Any]:
    cursor = {k: v for k, v in (row.semantic_cursor_json or {}).items() if k != _STATE_KEY}
    return {
        "session_id": str(row.id),
        "artifact_id": str(row.artifact_id),
        "artifact_version": row.artifact_version,
        "device_id": str(row.device_id) if row.device_id else None,
        "state": row.state,
        "speed": row.speed,
        "playback_seconds": row.playback_seconds,
        "cursor": cursor,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _pron_payload(row: PronunciationEntry) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "token": row.token,
        "spoken_form": row.spoken_form,
        "context": row.context,
        "explicit": row.explicit,
        "confidence": row.confidence,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


# --------------------------------------------------------------- sessions


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: uuid.UUID
    artifact_version: int = Field(default=1, ge=1)
    device_id: uuid.UUID | None = None
    voice_profile_id: uuid.UUID | None = None
    speed: float = Field(default=1.0, ge=0.5, le=3.0)


@router.post("/sessions", status_code=201)
async def create_session(request: Request, body: CreateSessionRequest) -> dict[str, Any]:
    runtime = _runtime(request)

    def work() -> dict[str, Any] | None:
        with runtime.session() as session:
            artifact = artifact_service.get_artifact(session, body.artifact_id)
            if artifact is None:
                return None
            row = service.create_session(
                session,
                artifact_id=body.artifact_id,
                artifact_version=body.artifact_version,
                device_id=body.device_id,
                voice_profile_id=body.voice_profile_id,
                speed=body.speed,
            )
            return _session_payload(row)

    payload = await asyncio.to_thread(work)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown artifact")
    logger.info("narration_session_created", session_id=payload["session_id"])
    return payload


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: uuid.UUID) -> dict[str, Any]:
    runtime = _runtime(request)

    def work() -> dict[str, Any] | None:
        with runtime.session() as session:
            row = service.get_session(session, session_id)
            return _session_payload(row) if row else None

    payload = await asyncio.to_thread(work)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown narration session")
    return payload


@router.get("/sessions/{session_id}/cursor")
async def get_cursor(request: Request, session_id: uuid.UUID) -> dict[str, Any]:
    payload = await get_session(request, session_id)
    return {
        "session_id": payload["session_id"],
        "state": payload["state"],
        "speed": payload["speed"],
        "playback_seconds": payload["playback_seconds"],
        "cursor": payload["cursor"],
    }


class UpdateCursorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cursor: dict[str, Any] | None = None
    playback_seconds: float | None = Field(default=None, ge=0.0)
    state: str | None = None
    speed: float | None = Field(default=None, ge=0.5, le=3.0)
    device_id: uuid.UUID | None = None

    @field_validator("state")
    @classmethod
    def _valid_state(cls, value: str | None) -> str | None:
        # Reject unknown states here (422) instead of letting a bad value land
        # in the DB and later 500 the /command endpoint (M4 review #3).
        if value is not None and value not in NARRATION_STATES:
            raise ValueError(f"invalid narration state: {value!r}")
        return value


@router.patch("/sessions/{session_id}/cursor")
async def patch_cursor(
    request: Request, session_id: uuid.UUID, body: UpdateCursorRequest
) -> dict[str, Any]:
    runtime = _runtime(request)

    def work() -> dict[str, Any] | None:
        with runtime.session() as session:
            current = service.get_session(session, session_id)
            if current is None:
                return None
            # Preserve the reserved _state blob when a device patches the cursor.
            new_cursor = body.cursor
            if new_cursor is not None:
                existing_meta = (current.semantic_cursor_json or {}).get(_STATE_KEY)
                if existing_meta is not None and _STATE_KEY not in new_cursor:
                    new_cursor = {**new_cursor, _STATE_KEY: existing_meta}
            row = service.update_cursor(
                session,
                session_id,
                cursor=new_cursor,
                playback_seconds=body.playback_seconds,
                state=body.state,
                speed=body.speed,
                device_id=body.device_id,
            )
            return _session_payload(row) if row else None

    payload = await asyncio.to_thread(work)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown narration session")
    return payload


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    utterance: str | None = Field(default=None, max_length=2000)
    command: str | None = Field(default=None, max_length=64)
    target_index: int | None = None
    speed: float | None = None
    device_id: uuid.UUID | None = None
    mode: str = Field(default="narration")


@router.post("/sessions/{session_id}/command")
async def post_command(
    request: Request, session_id: uuid.UUID, body: CommandRequest
) -> dict[str, Any]:
    runtime = _runtime(request)

    # Resolve the parsed command outside the DB thread (pure).
    if body.utterance:
        parsed = commands.parse_utterance(body.utterance)
        if parsed is None:
            raise HTTPException(status_code=422, detail="unrecognized narration command")
    elif body.command:
        try:
            cmd = commands.Command(body.command)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"unknown command: {body.command!r}"
            ) from exc
        parsed = commands.ParsedCommand(cmd, speed=body.speed, target_index=body.target_index)
    else:
        raise HTTPException(status_code=422, detail="provide 'utterance' or 'command'")

    def work() -> dict[str, Any] | None:
        with runtime.session() as session:
            row = service.get_session(session, session_id)
            if row is None:
                return None
            version = artifact_service.get_version(
                session, row.artifact_id, row.artifact_version
            )
            if version is None:
                version = artifact_service.get_current_version(session, row.artifact_id)
            body_md = version.canonical_body if version else ""
            pron = service.pronunciation_map(session)
            plan = build_plan(
                body_md,
                artifact_id=str(row.artifact_id),
                version=row.artifact_version,
                mode=body.mode,
                pronunciation=pron,
            )
            state = _unpack_state(row)
            result = commands.apply(state, parsed, plan)
            new_state = result.state
            packed = _pack_state(new_state)
            service.update_cursor(
                session,
                session_id,
                cursor=packed,
                state=new_state.state.value,
                speed=new_state.speed,
                device_id=body.device_id,
            )
            chunk = plan.chunk_at(new_state.cursor) if new_state.cursor else None
            fresh = service.get_session(session, session_id)
            payload = _session_payload(fresh) if fresh else {}
            payload.update(
                {
                    "action": result.action,
                    "ok": result.ok,
                    "message": result.message,
                    "current_chunk": (
                        {"chunk_id": chunk.chunk_id, "kind": chunk.kind, "text": chunk.text}
                        if chunk and new_state.state == commands.State.READING
                        else None
                    ),
                }
            )
            return payload

    payload = await asyncio.to_thread(work)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown narration session")
    return payload


# --------------------------------------------------------- pronunciation


@router.get("/pronunciation")
async def list_pronunciation(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)

    def work() -> list[dict[str, Any]]:
        with runtime.session() as session:
            return [_pron_payload(e) for e in service.list_pronunciations(session)]

    return {"entries": await asyncio.to_thread(work)}


class UpsertPronunciationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)
    spoken_form: str = Field(min_length=1, max_length=512)
    context: str | None = Field(default=None, max_length=128)
    explicit: bool = True
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


@router.put("/pronunciation", status_code=200)
async def put_pronunciation(
    request: Request, body: UpsertPronunciationRequest
) -> dict[str, Any]:
    runtime = _runtime(request)

    def work() -> dict[str, Any]:
        with runtime.session() as session:
            row = service.upsert_pronunciation(
                session,
                token=body.token,
                spoken_form=body.spoken_form,
                context=body.context,
                explicit=body.explicit,
                confidence=body.confidence,
            )
            return _pron_payload(row)

    payload = await asyncio.to_thread(work)
    logger.info("pronunciation_upserted", token=body.token)
    return payload


@router.delete("/pronunciation/{entry_id}", status_code=200)
async def delete_pronunciation(request: Request, entry_id: uuid.UUID) -> dict[str, Any]:
    runtime = _runtime(request)

    def work() -> bool:
        with runtime.session() as session:
            return service.delete_pronunciation(session, entry_id)

    deleted = await asyncio.to_thread(work)
    if not deleted:
        raise HTTPException(status_code=404, detail="unknown pronunciation entry")
    return {"deleted": True, "id": str(entry_id)}


# --------------------------------------------------------------- preview


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20000)
    mode: str = Field(default="narration")
    use_pronunciation: bool = True


@router.post("/preview")
async def preview(request: Request, body: PreviewRequest) -> dict[str, Any]:
    runtime = _runtime(request)

    def work() -> str:
        pron: dict[str, str] = {}
        if body.use_pronunciation:
            with runtime.session() as session:
                pron = service.pronunciation_map(session)
        return normalize(body.text, mode=body.mode, pronunciation=pron)

    spoken = await asyncio.to_thread(work)
    return {"text": body.text, "mode": body.mode, "spoken": spoken}


__all__ = ["router"]
