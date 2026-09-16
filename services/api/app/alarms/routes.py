"""Wake alarm REST surface (M18.3 spec §3, §8).

- GET  /v1/alarms                     the alarms that still matter (terminal ones on request)
- POST /v1/alarms                     create one (armed by the next clock tick)
- GET  /v1/alarms/history             what actually happened, from the Activity Ledger
- GET  /v1/alarms/{alarm_id}
- POST /v1/alarms/{alarm_id}/cancel
- POST /v1/alarms/{alarm_id}/snooze
- POST /v1/alarms/{alarm_id}/stop
- GET  /v1/alarms/audio/{token}       the greeting WAV — NO owner session (see below)

Owner-gated like every other surface, with ONE deliberate exception, declared the same way
``app.broker.routes`` declares its own (``POST /enroll``): a surface with its OWN credential
rather than an open one. The greeting audio route is on a SEPARATE router with no
``require_owner_session`` dependency, because the fetcher is a Windows service holding no
owner session — the 256-bit single-use token minted by ``app.alarms.audio_store`` IS the
authority, it expires in five minutes, it is redeemed once, and its sha256 travels inside
the signed device command so the companion can verify it got the bytes that were meant.

Nothing here runs a wake sequence. Creating an alarm writes rows and arms a routine; the
CLOCK (``app.routines.clock``) does the physical work, so an HTTP request can never be the
thing that is holding a device command open.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.alarms import history as alarm_history
from app.alarms import service as alarms_service
from app.alarms import speech as alarm_speech
from app.alarms.audio_store import AudioStore, get_audio_store
from app.alarms.models import MAX_SNOOZE_MINUTES, WakeAlarm
from app.alarms.state import IllegalAlarmTransition
from app.alarms.tr_time import (
    DEFAULT_TIMEZONE,
    UnparsedWhen,
    parse_when_struct,
    parse_when_text,
)
from app.artifacts.runtime import ArtifactRuntime
from app.errors import owner_detail
from app.identity.dependencies import require_owner_session

#: Bumped whenever this surface's shape changes (mirrors ROUTINES_VERSION / LEDGER_VERSION).
ALARMS_VERSION = 1

router = APIRouter(
    prefix="/v1/alarms", tags=["alarms"], dependencies=[Depends(require_owner_session)]
)

#: The token-authenticated exception (module docstring). Its own router so the exemption is
#: a visible, single line rather than a per-route flag someone could copy by accident.
audio_router = APIRouter(prefix="/v1/alarms", tags=["alarms"])


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


def _sequence(request: Request) -> Any:
    """The process's wake sequence, when one is wired. ``None`` in a process without a
    broker (every unit test): cancel/stop still work, they simply have no device to tell."""
    return getattr(request.app.state, "wake_sequence", None)


class WhenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_seconds: int | None = Field(default=None, ge=1, le=24 * 3600)
    date: str | None = Field(default=None, max_length=32)
    time: str | None = Field(default=None, max_length=5)
    weekdays: list[int] | None = None


class MediaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(default=None, max_length=2000)
    title: str | None = Field(default=None, max_length=200)
    remembered: str | None = Field(default=None, max_length=200)


class CreateAlarmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    when: WhenIn | None = None
    when_text: str | None = Field(default=None, max_length=300)
    media: MediaIn | None = None
    test: bool = False
    label: str | None = Field(default=None, max_length=200)
    timezone: str = Field(default=DEFAULT_TIMEZONE, max_length=64)
    snooze_minutes: int = Field(default=5, ge=1, le=MAX_SNOOZE_MINUTES)
    greeting_enabled: bool = True
    greeting_text: str | None = Field(default=None, max_length=300)
    display_wake: bool = True


class SnoozeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minutes: int | None = Field(default=None, ge=1, le=MAX_SNOOZE_MINUTES)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=200)


@router.get("/policy")
async def alarms_policy() -> dict[str, Any]:
    """The vocabulary and bounds this Cloud Core understands, so a caller never guesses."""
    from app.alarms.models import ALARM_STATES, MEDIA_SOURCE_KINDS, PLAYED_KINDS

    return {
        "alarms_version": ALARMS_VERSION,
        "states": list(ALARM_STATES),
        "media_source_kinds": list(MEDIA_SOURCE_KINDS),
        "played_kinds": list(PLAYED_KINDS),
        "max_snooze_minutes": MAX_SNOOZE_MINUTES,
        "default_timezone": DEFAULT_TIMEZONE,
    }


@router.post("", status_code=201)
async def create_alarm(request: Request, body: CreateAlarmRequest) -> dict[str, Any]:
    artifacts = _artifacts(request)
    now = alarms_service.utcnow()
    try:
        if body.when_text:
            parsed = parse_when_text(body.when_text, now=now, timezone=body.timezone)
        elif body.when is not None:
            parsed = parse_when_struct(
                body.when.model_dump(exclude_none=True), now=now, timezone=body.timezone
            )
        else:
            raise UnparsedWhen("either 'when' or 'when_text' is required")
    except UnparsedWhen as exc:
        raise HTTPException(
            status_code=422,
            detail=owner_detail(
                "validation_error",
                specific=(
                    "Söylediğin zamanı kesin olarak yerleştiremedim; "
                    "saati başka türlü söyler misin?"
                ),
            ),
        ) from exc

    def write() -> WakeAlarm:
        with artifacts.session() as session:
            alarm = alarms_service.create_alarm(
                session,
                when=parsed,
                media=body.media.model_dump(exclude_none=True) if body.media else None,
                is_test=body.test,
                label=body.label,
                greeting_policy={"enabled": body.greeting_enabled, "text": body.greeting_text},
                display_wake_policy={"enabled": body.display_wake},
                snooze_minutes=body.snooze_minutes,
            )
            return alarms_service.alarm_dict(alarm)  # type: ignore[return-value]

    payload = await asyncio.to_thread(write)
    return {
        **payload,
        "speech": alarm_speech.alarm_created_speech(
            local_time=parsed.local_time,
            relative_seconds=parsed.relative_seconds,
            weekdays=parsed.weekdays,
            tomorrow=parsed.matched == "tomorrow",
            is_test=body.test,
        ),
    }


@router.get("")
async def list_alarms(
    request: Request, include_terminal: bool = False, limit: int = 100
) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as session:
            rows = alarms_service.list_alarms(
                session, include_terminal=include_terminal, limit=limit
            )
            return [alarms_service.alarm_dict(r) for r in rows]

    return {"alarms": await asyncio.to_thread(load)}


# Declared BEFORE the /{alarm_id} routes for the same reason "wake-song" is: "history" is
# not a UUID and must not be captured by them.
@router.get("/history")
async def get_alarm_history(
    request: Request,
    alarm_id: uuid.UUID | None = None,
    include_tests: bool = True,
    limit: int = alarm_history.DEFAULT_LIMIT,
) -> dict[str, Any]:
    """B13 req 285: every alarm event, newest first, read from the Activity Ledger.

    NOT from the `wake_alarms` table, and that is the point. A recurring alarm reuses its
    row: `_release` rewinds `terminal_state`/`terminal_at`/`terminal_reason` to `None` when
    it re-schedules for tomorrow, so the row can only ever describe the NEXT occurrence.
    "Did my 07:30 ring on Tuesday?" is unanswerable from it by construction. The ledger
    keeps each occurrence separately, because its idempotency key contains the occurrence.
    """
    artifacts = _artifacts(request)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as session:
            return alarm_history.alarm_history(
                session, alarm_id=alarm_id, include_tests=include_tests, limit=limit
            )

    return {"history": await asyncio.to_thread(load)}


class WakeSongRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=2000)
    title: str | None = Field(default=None, max_length=200)


# Declared BEFORE the /{alarm_id} routes: "wake-song" is not a UUID and must not be
# captured by them.
@router.get("/wake-song")
async def get_wake_song(request: Request) -> dict[str, Any]:
    """The owner's approved wake song, or ``{"wake_song": null}``."""
    artifacts = _artifacts(request)

    def load() -> dict[str, Any] | None:
        with artifacts.session() as session:
            return alarms_service.get_wake_song(session)

    return {"wake_song": await asyncio.to_thread(load)}


@router.put("/wake-song")
async def put_wake_song(request: Request, body: WakeSongRequest) -> dict[str, Any]:
    """Remember the wake song the owner named (spec §3.8: "an already approved remembered
    wake song"). Only an http(s) URL the owner gave; the system never picks one."""
    artifacts = _artifacts(request)

    def write() -> dict[str, Any]:
        with artifacts.session() as session:
            try:
                return alarms_service.set_wake_song(session, url=body.url, title=body.title)
            except alarms_service.InvalidAlarmRequest as exc:
                raise HTTPException(
                    status_code=422, detail=owner_detail("validation_error")
                ) from exc

    return {"wake_song": await asyncio.to_thread(write)}


@router.get("/{alarm_id}")
async def get_alarm(request: Request, alarm_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> dict[str, Any] | None:
        with artifacts.session() as session:
            alarm = alarms_service.get_alarm(session, alarm_id)
            return alarms_service.alarm_dict(alarm) if alarm else None

    payload = await asyncio.to_thread(load)
    if payload is None:
        raise HTTPException(status_code=404, detail="alarm not found")
    return payload


@router.post("/{alarm_id}/cancel")
async def cancel_alarm(
    request: Request, alarm_id: uuid.UUID, body: CancelRequest | None = None
) -> dict[str, Any]:
    artifacts = _artifacts(request)
    sequence = _sequence(request)
    reason = (body.reason if body else None) or "owner"

    def write() -> dict[str, Any]:
        with artifacts.session() as session:
            alarm = alarms_service.cancel_alarm(
                session, alarm_id, sequence=sequence, reason=reason
            )
            return alarms_service.alarm_dict(alarm)

    try:
        payload = await asyncio.to_thread(write)
    except alarms_service.AlarmNotFoundError as exc:
        raise HTTPException(status_code=404, detail=owner_detail("not_found")) from exc
    except IllegalAlarmTransition as exc:
        raise HTTPException(status_code=409, detail=owner_detail("lifecycle_violation")) from exc
    return {**payload, "speech": alarm_speech.ALARM_CANCELLED_TR}


@router.post("/{alarm_id}/stop")
async def stop_alarm(request: Request, alarm_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)
    sequence = _sequence(request)

    def write() -> dict[str, Any]:
        with artifacts.session() as session:
            alarm = alarms_service.stop_alarm(session, alarm_id, sequence=sequence)
            return alarms_service.alarm_dict(alarm)

    try:
        payload = await asyncio.to_thread(write)
    except alarms_service.AlarmNotFoundError as exc:
        raise HTTPException(status_code=404, detail=owner_detail("not_found")) from exc
    return {**payload, "speech": alarm_speech.ALARM_STOPPED_TR}


@router.post("/{alarm_id}/snooze")
async def snooze_alarm(
    request: Request, alarm_id: uuid.UUID, body: SnoozeRequest | None = None
) -> dict[str, Any]:
    artifacts = _artifacts(request)
    sequence = _sequence(request)
    minutes = body.minutes if body else None

    def write() -> dict[str, Any]:
        with artifacts.session() as session:
            alarm = alarms_service.snooze_alarm(
                session, alarm_id, minutes=minutes, sequence=sequence
            )
            return alarms_service.alarm_dict(alarm)

    try:
        payload = await asyncio.to_thread(write)
    except alarms_service.AlarmNotFoundError as exc:
        raise HTTPException(status_code=404, detail=owner_detail("not_found")) from exc
    except alarms_service.InvalidAlarmRequest as exc:
        raise HTTPException(status_code=409, detail=owner_detail("lifecycle_violation")) from exc
    return {
        **payload,
        "speech": alarm_speech.alarm_snoozed_speech(
            minutes=payload["snooze_minutes"], local_time=payload["local_time"]
        ),
    }


# ------------------------------------------------------------------ the audio route


def _store(request: Request) -> AudioStore:
    return getattr(request.app.state, "alarm_audio_store", None) or get_audio_store()


@audio_router.get("/audio/{token}")
async def get_greeting_audio(
    request: Request, token: Annotated[str, Field(max_length=128)]
) -> Response:
    """Redeem a one-time greeting token (module docstring).

    Unknown, expired and already-redeemed tokens are all a plain 404: the three are
    indistinguishable on purpose, so a probe learns nothing from the difference.
    """
    audio = await asyncio.to_thread(_store(request).take, token)
    if audio is None:
        raise HTTPException(status_code=404, detail="audio not available")
    payload, content_type = audio
    return Response(
        content=payload,
        media_type=content_type,
        headers={"Cache-Control": "no-store", "Content-Length": str(len(payload))},
    )


__all__ = ["ALARMS_VERSION", "audio_router", "router"]
