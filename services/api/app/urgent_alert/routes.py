"""The Kokpit's alarm page over HTTP: is it connected, and 'önemli deneme bildirimi gönder'.

Owner-gated like every settings surface; the owner's session is enough (no step-up: the test
rings his own phone and nothing else). No response carries a key - ``status`` says
"connected" and nothing about with what.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Final

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from app.identity.dependencies import require_owner_session
from app.notifications import service as notifications
from app.notifications.models import PRIORITY_URGENT, NotificationRow
from app.telephony import policy
from app.urgent_alert.loop import UrgentAlertLoop

router = APIRouter(
    prefix="/v1/urgent-alert",
    tags=["urgent-alert"],
    dependencies=[Depends(require_owner_session)],
)

#: Tests an hour, counted from the notifications table so a restart does not reset it.
MAX_TESTS_PER_HOUR: Final[int] = 3
TEST_TITLE: Final[str] = "Önemli deneme bildirimi"
TEST_BODY: Final[str] = (
    "Bu bir deneme. Telefonun sessizde bile çaldıysa önemli bildirimler sana ulaşır; "
    "'Gördüm'e basınca Kokpit 'görüldü' yazar."
)


def _loop(request: Request) -> UrgentAlertLoop:
    return request.app.state.urgent_alert_loop


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def status_of(loop: UrgentAlertLoop) -> dict[str, Any]:
    last = loop.store.last_closed()
    outcome, seen_at = last if last is not None else (None, None)
    return {
        "configured": loop.configured,
        "open_receipts": loop.store.count_open(),
        "last_seen_at": _iso(seen_at),
        "last_outcome": outcome,
    }


def _send_test(loop: UrgentAlertLoop) -> dict[str, Any] | None:
    now = loop.now()
    with loop.session_factory() as db:
        recent = db.execute(
            select(func.count())
            .select_from(NotificationRow)
            .where(
                NotificationRow.kind == policy.KIND_URGENT_ALERT_TEST,
                NotificationRow.created_at > now - timedelta(hours=1),
            )
        ).scalar_one()
        if recent >= MAX_TESTS_PER_HOUR:
            return None
        row = notifications.record(
            db,
            kind=policy.KIND_URGENT_ALERT_TEST,
            title=TEST_TITLE,
            body=TEST_BODY,
            priority=PRIORITY_URGENT,
            now=now,
        )
        return {"id": str(row.id), "kind": row.kind}


@router.get("/status")
async def urgent_alert_status(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(status_of, _loop(request))


@router.post("/test")
async def urgent_alert_test(request: Request) -> dict[str, Any]:
    """Records one important test notification; the ladder rings the phone on its next pass."""
    loop = _loop(request)
    if not loop.configured:
        raise HTTPException(
            status_code=409,
            detail="Önemli bildirim telefonu bağlı değil (Pushover anahtarları eksik).",
        )
    sent = await asyncio.to_thread(_send_test, loop)
    if sent is None:
        raise HTTPException(
            status_code=429, detail=f"Saatte en çok {MAX_TESTS_PER_HOUR} deneme gönderilebilir."
        )
    return sent


__all__ = ["MAX_TESTS_PER_HOUR", "router", "status_of"]
