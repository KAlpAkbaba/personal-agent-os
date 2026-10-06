"""The settings page's telephony routes, and the one-time audio url Twilio fetches.

``/v1/telephony/status`` and ``/test-call`` are owner-gated. Neither ever returns a credential:
the page learns "bağlı / bağlı değil", the owner's number masked and the Twilio number - the
Account SID and the auth token stay in the Cloud Core's env file.

``/v1/telephony/audio/{token}`` is NOT owner-gated: Twilio fetches it, and Twilio holds no
session. The token is the authority (256 bits, one fetch, ten minutes - the alarm greeting's
``AudioStore``); unknown, expired and spent tokens are the same 404.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field

from app.alarms.audio_store import AudioStore
from app.identity.dependencies import require_owner_session
from app.logging import get_logger
from app.notifications.service import QUIET_FROM, QUIET_UNTIL
from app.telephony import policy
from app.telephony.provider import TelephonyError
from app.telephony.service import (
    TEST_MESSAGE,
    OwnerCaller,
    TelephonyNotConfigured,
    mask_number,
)

logger = get_logger(__name__)

router = APIRouter(
    prefix="/v1/telephony", tags=["telephony"], dependencies=[Depends(require_owner_session)]
)
audio_router = APIRouter(prefix="/v1/telephony", tags=["telephony"])

TRIAL_NOTE: Final[str] = (
    "Twilio deneme (trial) hesabında her arama önce İngilizce bir uyarıyla başlar "
    "('You have a trial account...') ve bir tuşa basmanı ister; JARVIS ondan sonra konuşur. "
    "Hesap yükseltilince bu uyarı kalkar."
)


def _caller(request: Request) -> OwnerCaller:
    return request.app.state.telephony


def _store(request: Request) -> AudioStore:
    return request.app.state.telephony_audio_store


@router.get("/status")
async def telephony_status(request: Request) -> dict[str, Any]:
    caller = _caller(request)
    return {
        "connected": caller.configured,
        "owner_number": mask_number(caller.owner_number) if caller.owner_number else None,
        "from_number": caller.from_number or None,
        "speaks_with": caller.speaks_with,
        "max_calls_per_hour": caller.max_per_hour,
        "quiet_hours": f"{QUIET_FROM:%H:%M}-{QUIET_UNTIL:%H:%M}",
        "trial_note": TRIAL_NOTE,
    }


@router.post("/test-call")
async def test_call(request: Request) -> dict[str, Any]:
    """The owner's 'test araması yap': one call to his own number, quiet hours or not."""
    caller = _caller(request)
    try:
        outcome = await asyncio.to_thread(caller.call, policy.KIND_TEST_CALL, TEST_MESSAGE)
    except TelephonyNotConfigured:
        raise HTTPException(
            status_code=409, detail="Telefon araması bağlı değil (Twilio bilgileri eksik)."
        ) from None
    except TelephonyError:
        # The provider's own words stay in the log; the owner gets a sentence.
        logger.warning("telephony_test_call_refused", exc_info=True)
        raise HTTPException(status_code=502, detail="Twilio aramayı kabul etmedi.") from None
    return outcome.as_dict()


@audio_router.get("/audio/{token}")
async def call_audio(request: Request, token: Annotated[str, Field(max_length=128)]) -> Response:
    audio = await asyncio.to_thread(_store(request).take, token)
    if audio is None:
        raise HTTPException(status_code=404, detail="audio not available")
    payload, content_type = audio
    return Response(content=payload, media_type=content_type, headers={"Cache-Control": "no-store"})


__all__ = ["TRIAL_NOTE", "audio_router", "router"]
