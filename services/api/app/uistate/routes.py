"""What a renderer reads: the contract, the current state and the recent tail.

Owner-gated like every other surface. There is no write endpoint: UI state is published by
the subsystems that are actually doing the work, never by a client claiming they are.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    now = datetime.now(UTC)
    # The age is computed HERE, against the server's clock, so a reader never compares a
    # server timestamp with its own clock (an owner harness did, 2026-09-06). A null
    # current is "nothing published since this process started" - after a release the
    # lifespan publishes agent.idle, so null means the startup itself did not run.
    current_age_s = None
    if current is not None:
        at = current.at if current.at.tzinfo else current.at.replace(tzinfo=UTC)
        current_age_s = round((now - at).total_seconds(), 3)
    return {
        "contract_version": ui_state_contract()["contract_version"],
        "current": current.as_dict() if current else None,
        "current_age_s": current_age_s,
        "now": now.isoformat().replace("+00:00", "Z"),
        "events": [e.as_dict() for e in events],
        "sequence": current.sequence if current else 0,
    }


__all__ = ["router"]
