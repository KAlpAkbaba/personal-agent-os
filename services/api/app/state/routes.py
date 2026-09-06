"""Live state REST surface (docs/M18_ACTION_CONTRACT.md §4): read-only, owner-gated.

- GET /v1/state/now?scope=all|eye|voice|presence|devices|release[&session_id=<realtime>]

Returns exactly what the ``state.now`` voice tool returns - the same composer,
:func:`app.state.now.compose_live_state`, over the same live runtimes - so the
qualification harness can hold the runtime's view of the eye next to the browser's own
and next to the receipt, without a realtime session in the loop. With ``session_id`` the
``voice.session`` fact is that realtime session's, as it is for the tool; without it that
fact is an honest ``no_session`` uncertainty.

No writes anywhere in this router: the tool's ``voice.state_answered`` ledger row marks
that the OWNER was told something by voice, and an HTTP probe is not that.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.artifacts.runtime import ArtifactRuntime
from app.identity.dependencies import require_owner_session
from app.state.now import SCOPE_ALL, SCOPES, compose_live_state

router = APIRouter(prefix="/v1/state", dependencies=[Depends(require_owner_session)])


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


def _broker_runtime(request: Request) -> Any | None:
    return getattr(request.app.state, "broker", None)


def _presence_runtime(request: Request) -> Any | None:
    # Same optional-injection shape as app.worldmodel.routes: the process-wide engine by
    # default, app.state.presence_engine when a test installs its own.
    injected = getattr(request.app.state, "presence_engine", None)
    if injected is not None:
        return injected
    from app.presence.engine import get_engine

    return get_engine()


@router.get("/now")
async def get_state_now(
    request: Request, scope: str = SCOPE_ALL, session_id: str | None = None
) -> dict[str, Any]:
    if scope not in SCOPES:
        raise HTTPException(status_code=422, detail=f"scope must be one of {', '.join(SCOPES)}")
    realtime_session: uuid.UUID | None = None
    if session_id:
        try:
            realtime_session = uuid.UUID(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="session_id must be a UUID") from exc
    artifacts = _artifacts(request)
    broker_runtime = _broker_runtime(request)
    presence_runtime = _presence_runtime(request)

    def compose() -> dict[str, Any]:
        with artifacts.session() as session:
            return compose_live_state(
                session,
                scope=scope,
                session_id=realtime_session,
                presence_runtime=presence_runtime,
                broker_runtime=broker_runtime,
            )

    return await asyncio.to_thread(compose)


__all__ = ["router"]
