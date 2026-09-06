"""Device activity status (M18.3 spec §3.6).

- GET /v1/devices/{device_id}/status   the last heartbeat status this device reported

Its own router rather than another route in ``app.broker.routes`` because the fact is the
devices layer's, not the broker's: the broker moves frames, and this is the composed view of
what one of them said. Owner-gated like the rest of the devices surface.

A device that has never sent a ``status`` object — an older agent, or one with no
owner-session companion running — gets ``{"status": null}`` and a 200, not a 404: "this
device is not telling me" is a true and useful answer, and it is a different fact from "no
such device".
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.devices.status import DeviceStatusRegistry, get_status_registry
from app.identity.dependencies import require_owner_session

router = APIRouter(
    prefix="/v1/devices", tags=["devices"], dependencies=[Depends(require_owner_session)]
)


def _registry(request: Request) -> DeviceStatusRegistry:
    return getattr(request.app.state, "device_statuses", None) or get_status_registry()


@router.get("/{device_id}/status")
async def get_device_status(request: Request, device_id: uuid.UUID) -> dict[str, Any]:
    from app.broker.models import Device

    artifacts = request.app.state.artifacts

    def load() -> bool:
        with artifacts.session() as session:
            return session.get(Device, device_id) is not None

    known = await asyncio.to_thread(load)
    if not known:
        raise HTTPException(status_code=404, detail="device not found")
    status = _registry(request).get(device_id)
    return {"device_id": str(device_id), "status": status.as_dict() if status else None}


__all__ = ["router"]
