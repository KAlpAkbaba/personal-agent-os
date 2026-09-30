"""``/v1/team/approvals``: the Onay Merkezi's REST surface.

GET lists what waits at the owner's two gates; POST ``/decision`` takes Onayla / Reddet.
The voice path ("fikri onayla" / "yayını onayla") is a later task and calls the same POST with
``channel: "voice"`` and the gate it heard; the rules that refuse a voice approval live in
``approvals.decide``, not here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.identity.dependencies import require_owner_session
from app.ledger import service as ledger_service
from app.ledger.vocabulary import InvalidVocabulary
from app.team import approvals

router = APIRouter(prefix="/v1/team/approvals", dependencies=[Depends(require_owner_session)])

#: services/api/app/team/routes.py -> the repository root's ``team/``.
DEFAULT_TEAM_ROOT = Path(__file__).resolve().parents[4] / "team"


def _team_root(request: Request) -> Path:
    return getattr(request.app.state, "team_root", None) or DEFAULT_TEAM_ROOT


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    decision: Literal["approve", "reject"]
    reason: str | None = None
    #: "fikir" | "yayin" (or "yayın"). Required for a voice decision, optional for the shell
    #: (where it guards against a page that is out of date).
    gate: str | None = None
    channel: Literal["shell", "voice"] = "shell"


@router.get("")
async def list_approvals(request: Request) -> dict[str, Any]:
    root = _team_root(request)

    def load() -> dict[str, Any]:
        try:
            queue = approvals.load_queue(root / "queue.json")
        except (OSError, ValueError) as error:
            raise HTTPException(503, {"code": "queue_unreadable", "message": str(error)}) from error
        return {
            "approvals": approvals.list_pending(queue, root),
            "cycle_report": approvals.newest_cycle_report(root),
            "cycle_running": approvals.cycle_running(root),
        }

    return await asyncio.to_thread(load)


@router.post("/decision")
async def decide(body: DecisionRequest, request: Request) -> dict[str, Any]:
    root = _team_root(request)
    artifacts = request.app.state.artifacts

    def record(facts: dict[str, Any]) -> None:
        approved = facts["decision"] == "approve"
        verb = "onayladı" if approved else "reddetti"
        event = ledger_service.ActivityEvent(
            event_type=approvals.EVENT_TASK_APPROVED if approved else approvals.EVENT_TASK_REJECTED,
            subsystem=approvals.SUBSYSTEM_TEAM,
            action=f"team.approvals.{body.decision}",
            factual_summary=(
                f"Sahip {facts['gate']} kapısında {facts['task_id']} görevini {verb} "
                f"({facts['from_state']} -> {facts['to_state']}, {facts['channel']})."
            ),
            source="team.approvals",
            source_ref=f"{facts['task_id']}:{facts['from_state']}:{facts['updated_at']}:{body.decision}",
            detail_json=dict(facts),
        )
        try:
            with artifacts.session() as session:
                ledger_service.record(session, event)
        except InvalidVocabulary as error:
            raise approvals.Refused(
                503,
                "ledger_refused",
                "Ledger bu olayı kabul etmedi; karar yazılmadı.",
                {"why": str(error)},
            ) from error

    def run() -> dict[str, Any]:
        return approvals.decide(
            root,
            task_id=body.task_id,
            decision=body.decision,
            reason=body.reason,
            gate=body.gate,
            channel=body.channel,
            record=record,
        )

    try:
        return await asyncio.to_thread(run)
    except approvals.Refused as refused:
        raise HTTPException(refused.status, refused.detail()) from refused
