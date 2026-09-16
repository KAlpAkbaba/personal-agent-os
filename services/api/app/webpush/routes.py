"""B11 req 372: owner-session-gated REST surface for Web Push.

- GET    /v1/webpush/public-key           the VAPID public key, and an honest
                                           ``supported`` flag when no key is configured
                                           yet — never a 404/500 for "not set up".
- POST   /v1/webpush/subscriptions        register (or refresh) this browser
- GET    /v1/webpush/subscriptions        list this owner's subscriptions
- DELETE /v1/webpush/subscriptions/{id}   remove one (disable push for that browser)

Owner-gated like every other surface (``require_owner_session`` at the router level,
the same discipline ``app.notifications.routes`` documents for itself).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.errors.owner import log_and_detail
from app.identity.dependencies import require_owner_session
from app.webpush import service as webpush
from app.webpush.models import PushSubscriptionRow
from app.webpush.provider import PushError
from app.webpush.vapid import VapidKeyError, load_private_key, public_key_b64url

router = APIRouter(
    prefix="/v1/webpush", tags=["webpush"], dependencies=[Depends(require_owner_session)]
)


def _settings(request: Request) -> Any:
    return request.app.state.settings


def _session_factory(request: Request):
    return request.app.state.artifacts.session


class SubscriptionKeysIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(min_length=1, max_length=128)
    auth: str = Field(min_length=1, max_length=48)


class SubscribeRequest(BaseModel):
    """The shape ``PushSubscription.toJSON()`` produces in every browser that
    implements the Push API — passed through from ``apps/web``'s settings UI as-is."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=1, max_length=2000)
    keys: SubscriptionKeysIn
    user_agent: str = Field(default="", max_length=256)


def _subscription_dict(row: PushSubscriptionRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "endpoint_host": webpush.endpoint_host(row.endpoint),  # never the full endpoint
        "user_agent": row.user_agent,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "last_error_reason": row.last_error_reason,
        "failure_count": row.failure_count,
    }


@router.get("/public-key")
async def get_public_key(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    if not settings.webpush_vapid_private_key:
        # Honest, not an error: the web settings UI's "no server key" state reads this.
        return {"supported": False, "public_key": None, "reason": "no_vapid_key"}
    try:
        private_key = load_private_key(settings.webpush_vapid_private_key)
    except VapidKeyError:
        return {"supported": False, "public_key": None, "reason": "invalid_vapid_key"}
    return {"supported": True, "public_key": public_key_b64url(private_key)}


@router.post("/subscriptions")
async def create_subscription(request: Request, payload: SubscribeRequest) -> dict[str, Any]:
    with _session_factory(request)() as db:
        try:
            row = webpush.subscribe(
                db,
                endpoint=payload.endpoint,
                p256dh=payload.keys.p256dh,
                auth=payload.keys.auth,
                user_agent=payload.user_agent,
            )
        except PushError as exc:
            # B22 req 705: the owner never meets a Python exception. `exc.reason` (a
            # reason CLASS like "invalid_endpoint") goes to the LOG under `where`, never
            # into the response body - the SSRF allowlist's refusal is a security rule,
            # not a typo the owner can fix by rereading it.
            detail = log_and_detail(
                "constraint_violation", exc, where="webpush.create_subscription"
            )
            raise HTTPException(status_code=422, detail=detail) from exc
        except webpush.SubscriptionError as exc:
            detail = log_and_detail("validation_error", exc, where="webpush.create_subscription")
            raise HTTPException(status_code=422, detail=detail) from exc
        return _subscription_dict(row)


@router.get("/subscriptions")
async def list_subscriptions(request: Request) -> dict[str, Any]:
    with _session_factory(request)() as db:
        rows = webpush.list_subscriptions(db)
        return {"subscriptions": [_subscription_dict(row) for row in rows]}


@router.delete("/subscriptions/{subscription_id}")
async def delete_subscription(request: Request, subscription_id: uuid.UUID) -> dict[str, Any]:
    with _session_factory(request)() as db:
        removed = webpush.unsubscribe(db, subscription_id)
        if not removed:
            raise HTTPException(status_code=404, detail="unknown subscription")
        return {"removed": True}


__all__ = ["router"]
