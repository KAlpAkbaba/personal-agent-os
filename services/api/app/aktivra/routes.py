"""Aktivra's channel over HTTP (the contract: ``packages/protocol/AKTIVRA_EVENTS.md``).

``POST /v1/aktivra/events`` carries Aktivra's OWN bearer token (``app.aktivra.auth``) and no
owner session - so it is one of the deliberately open routes of
``tests/unit/test_identity_enforcement.py``. The checks, in order:

1. no token configured -> 404 (there is no channel);
2. missing or wrong token -> 401 and an ``aktivra.rejected`` ledger line (fingerprint only);
3. a body over 4 KB, an extra field, an attachment, a title over 120 or a summary over 280
   characters -> 422;
4. an event id already here -> 200 with the first notification id (no second notification);
5. the eleventh new event within an hour -> 429 (counted from the table, a restart keeps it);
6. otherwise -> 201 ``{"notification_id": ...}``.

``GET /v1/aktivra/status`` is the owner's: connected or not, the last event's time, the count
of the last 24 hours. Neither response ever carries the token.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.aktivra import auth, service
from app.identity.dependencies import require_owner_session

router = APIRouter(prefix="/v1/aktivra", tags=["aktivra"])

MAX_BODY_BYTES: Final[int] = 4096
MAX_EVENTS_PER_HOUR: Final[int] = 10
MAX_TITLE_CHARS: Final[int] = 120
MAX_SUMMARY_CHARS: Final[int] = 280


class AktivraEvent(BaseModel):
    """The whole body. Anything else - a customer's name, an attachment - is refused (422)."""

    model_config = ConfigDict(extra="forbid")

    event_id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")]
    title: Annotated[str, Field(min_length=1, max_length=MAX_TITLE_CHARS)]
    summary: Annotated[str | None, Field(max_length=MAX_SUMMARY_CHARS)] = None
    severity: Literal["important", "info"]
    occurred_at: datetime


def _session_factory(request: Request):
    return request.app.state.artifacts.session


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _refused(errors: list[dict[str, Any]]) -> HTTPException:
    """422 naming the fields and the rule - never echoing the input back."""
    return HTTPException(
        status_code=422,
        detail=[
            {"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg", "")} for e in errors
        ],
    )


def _accept(request: Request, event: AktivraEvent) -> tuple[int, str | None]:
    with _session_factory(request)() as db:
        existing = service.find(db, event.event_id)
        if existing is not None:
            notification_id = existing.notification_id
            return 200, str(notification_id) if notification_id else None
        now = datetime.now(UTC)
        if service.events_in_last_hour(db, now=now) >= MAX_EVENTS_PER_HOUR:
            return 429, None
        accepted = service.accept(
            db,
            event_id=event.event_id,
            title=event.title,
            summary=event.summary or "",
            severity=event.severity,
            occurred_at=event.occurred_at,
            now=now,
        )
        notification_id = str(accepted.notification_id) if accepted.notification_id else None
        return (201 if accepted.created else 200), notification_id


def _reject(request: Request, verdict: auth.Verdict) -> None:
    with _session_factory(request)() as db:
        service.reject(db, fingerprint=verdict.fingerprint, reason=verdict.reason)


@router.post("/events")
async def aktivra_event(request: Request) -> JSONResponse:
    setting = request.app.state.settings.aktivra_inbound_token
    if not auth.configured(setting):
        raise HTTPException(status_code=404, detail="Not Found")
    verdict = auth.verify(request.headers.get("authorization"), setting)
    if not verdict.ok:
        await asyncio.to_thread(_reject, request, verdict)
        raise HTTPException(
            status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"}
        )

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise _refused([{"loc": ["body"], "msg": f"gövde {MAX_BODY_BYTES} bayttan büyük"}])
    try:
        event = AktivraEvent.model_validate(json.loads(raw))
    except (ValueError, TypeError) as exc:
        errors = exc.errors() if isinstance(exc, ValidationError) else []
        raise _refused(errors or [{"loc": ["body"], "msg": "geçersiz JSON"}]) from None

    code, notification_id = await asyncio.to_thread(_accept, request, event)
    if code == 429:
        raise HTTPException(
            status_code=429, detail=f"Saatte en çok {MAX_EVENTS_PER_HOUR} olay kabul edilir."
        )
    return JSONResponse(status_code=code, content={"notification_id": notification_id})


def _status(request: Request) -> dict[str, Any]:
    configured = auth.configured(request.app.state.settings.aktivra_inbound_token)
    with _session_factory(request)() as db:
        last, count = service.status(db)
    return {"configured": configured, "last_event_at": _iso(last), "events_24h": count}


@router.get("/status", dependencies=[Depends(require_owner_session)])
async def aktivra_status(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(_status, request)


__all__ = [
    "MAX_BODY_BYTES",
    "MAX_EVENTS_PER_HOUR",
    "MAX_SUMMARY_CHARS",
    "MAX_TITLE_CHARS",
    "AktivraEvent",
    "router",
]


# aktivra-inbound-events, bound by app/registry.py (card registry-models-and-routers).
ROUTERS = [router]
