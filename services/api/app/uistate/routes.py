"""What a renderer reads: the contract, the current state and the recent tail.

Owner-gated like every other surface. There is no write endpoint: UI state is published by
the subsystems that are actually doing the work, never by a client claiming they are.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.identity.dependencies import require_owner_session
from app.uistate.contract import ui_state_contract
from app.uistate.publisher import TAIL_SIZE, get_publisher

router = APIRouter(
    prefix="/v1/ui/state", tags=["ui-state"], dependencies=[Depends(require_owner_session)]
)


@router.get("/contract")
async def get_contract() -> dict[str, Any]:
    """The vocabulary and the metadata rules. Side-effect free."""
    return ui_state_contract()


@router.get("")
async def get_state(
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=TAIL_SIZE, ge=1, le=TAIL_SIZE),
) -> dict[str, Any]:
    """The current state plus the events after ``after_sequence``.

    A renderer polls (or later subscribes) with the last sequence it drew; an empty
    ``events`` list with an unchanged ``current`` means nothing happened, which is itself
    the truth to draw.
    """
    publisher = get_publisher()
    current = publisher.current()
    events = publisher.tail(limit=limit, after_sequence=after_sequence)
    return {
        "contract_version": ui_state_contract()["contract_version"],
        "current": current.as_dict() if current else None,
        "events": [e.as_dict() for e in events],
        "sequence": current.sequence if current else 0,
    }


__all__ = ["router"]
