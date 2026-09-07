"""The version model and the availability report as REST (M18.4 spec §2, §13).

- GET /v1/release/current      this Cloud Core: sha (or unknown), app version, contracts
- GET /v1/release/components   every component the record can name: cloud-core (live),
                               each enrolled device's reported software version and
                               capabilities, the web (unknown from the server, said so)
- GET /v1/release/slo          release windows and incidents from the ledger; availability
                               null with measurement "none" until a prober records samples

Owner-gated: the health endpoint already carries the cloud-core block unauthenticated;
the device inventory and the incident counts are the owner's.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.broker import service as broker_service
from app.identity.dependencies import require_owner_session
from app.release.slo import slo_report
from app.release.version import COMPONENT_CLOUD_CORE, release_model

router = APIRouter(
    prefix="/v1/release", tags=["release"], dependencies=[Depends(require_owner_session)]
)


@router.get("/current")
async def current(request: Request) -> dict[str, Any]:
    return release_model(request.app.state.settings)


@router.get("/components")
async def components(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    broker = getattr(request.app.state, "broker", None)

    def load() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = [
            {**release_model(settings), "observed": "live"},
        ]
        if broker is None:
            return rows
        connected = set(getattr(broker, "connections", {}) or {})
        with broker.session() as session:
            for device in broker_service.list_devices(session):
                rows.append(
                    {
                        "component": "windows-agent",
                        "device_id": str(device.id),
                        "name": device.name,
                        "platform": device.platform,
                        "version": device.software_version or "unknown",
                        "version_source": "device_row" if device.software_version else "unknown",
                        "capabilities": list(device.capabilities_json or []),
                        "connected": device.id in connected,
                        "observed": "device_row",
                    }
                )
        rows.append(
            {
                "component": "web",
                "version": "unknown_from_server",
                "version_source": "served_marker",
                "observed": "browser_only",
                "note": (
                    'the served <meta name="pagentos-core-build"> marker is known to the '
                    "browser, not to the API"
                ),
            }
        )
        return rows

    return {"components": await asyncio.to_thread(load), "primary": COMPONENT_CLOUD_CORE}


@router.get("/slo")
async def slo(request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts

    def load() -> dict[str, Any]:
        with artifacts.session() as session:
            return slo_report(session)

    return await asyncio.to_thread(load)
