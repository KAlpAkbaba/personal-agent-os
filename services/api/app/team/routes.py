"""``/v1/team/approvals`` (the Onay Merkezi) and ``/v1/team/queue`` (the team's shared state).

Approvals: GET lists what waits at the owner's two gates; POST ``/decision`` takes Onayla /
Reddet. The voice path ("fikri onayla" / "yayını onayla") calls the same POST with
``channel: "voice"`` and the gate it heard; the rules that refuse a voice approval live in
``approvals.decide``, not here.

Queue: the cycle's own surface (pilot-02). GET the whole queue; PUT one task by id with the
``updated_at`` the writer last saw (409 when it is stale); POST the lock (acquire / release,
the six-hour staleness rule) and the cycle report as text. All of it under the owner session,
over whichever store ``app.state.team_store`` is: the database on the Cloud Core, otherwise
the files under ``app.state.team_root``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.identity.dependencies import require_owner_session
from app.ledger import service as ledger_service
from app.ledger.vocabulary import InvalidVocabulary
from app.team import approvals
from app.team import store as team_store

router = APIRouter(dependencies=[Depends(require_owner_session)])

#: services/api/app/team/routes.py -> the repository root's ``team/``.
DEFAULT_TEAM_ROOT = Path(__file__).resolve().parents[4] / "team"


def _team_root(request: Request) -> Path:
    return getattr(request.app.state, "team_root", None) or DEFAULT_TEAM_ROOT


def _store(request: Request) -> team_store.TeamStore:
    wired = getattr(request.app.state, "team_store", None)
    return wired if wired is not None else team_store.FileStore(_team_root(request))


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    decision: Literal["approve", "reject"]
    reason: str | None = None
    #: "fikir" | "yayin" (or "yayın"). Required for a voice decision, optional for the shell
    #: (where it guards against a page that is out of date).
    gate: str | None = None
    channel: Literal["shell", "voice"] = "shell"


@router.get("/v1/team/approvals")
async def list_approvals(request: Request) -> dict[str, Any]:
    root = _team_root(request)
    store = _store(request)

    def load() -> dict[str, Any]:
        try:
            queue = store.read_queue()
        except (OSError, ValueError) as error:
            raise HTTPException(503, {"code": "queue_unreadable", "message": str(error)}) from error
        return {
            "approvals": approvals.list_pending(queue, root),
            "cycle_report": store.newest_report(),
            "cycle_running": team_store.lock_is_running(store.read_lock(), team_store.utcnow()),
        }

    return await asyncio.to_thread(load)


@router.post("/v1/team/approvals/decision")
async def decide(body: DecisionRequest, request: Request) -> dict[str, Any]:
    root = _team_root(request)
    store = _store(request)
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
            store=store,
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


# ------------------------------------------------------------------ the cycle's surface


class PutTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: dict[str, Any]
    #: The ``updated_at`` the writer last read; ``None`` creates the task.
    expected_updated_at: str | None = None


class LockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["acquire", "release"]
    machine: str = Field(default="", max_length=64)
    cycle_id: str = Field(default="", max_length=64)
    pid: int = 0
    #: Only for ``acquire``: the caller checked and the process that took ITS lock is gone.
    takeover_dead: bool = False


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    text: str = Field(max_length=200_000)


def _refuse(error: Exception) -> HTTPException:
    if isinstance(error, team_store.Stale):
        return HTTPException(409, {"code": "stale_write", "message": str(error)})
    if isinstance(error, team_store.Invalid):
        return HTTPException(
            422, {"code": "invalid", "message": str(error), "problems": error.problems}
        )
    return HTTPException(503, {"code": "team_store_unavailable", "message": str(error)})


@router.get("/v1/team/queue")
async def read_queue(request: Request) -> dict[str, Any]:
    store = _store(request)
    try:
        return await asyncio.to_thread(store.read_queue)
    except (OSError, ValueError) as error:
        raise _refuse(error) from error


@router.put("/v1/team/queue/tasks/{task_id}")
async def put_task(task_id: str, body: PutTaskRequest, request: Request) -> dict[str, Any]:
    if body.task.get("id") != task_id:
        raise HTTPException(
            422,
            {
                "code": "invalid",
                "message": "the task's id is not the id in the path",
                "problems": [f"task.id must be {task_id!r}"],
            },
        )
    store = _store(request)
    try:
        return await asyncio.to_thread(store.put_task, body.task, body.expected_updated_at)
    except (team_store.Stale, team_store.Invalid) as error:
        raise _refuse(error) from error


@router.get("/v1/team/queue/lock")
async def read_lock(request: Request) -> dict[str, Any]:
    lock = await asyncio.to_thread(_store(request).read_lock)
    return lock or {"held": False}


@router.post("/v1/team/queue/lock")
async def post_lock(body: LockRequest, request: Request) -> dict[str, Any]:
    store = _store(request)
    if not body.machine:
        raise HTTPException(422, {"code": "invalid", "message": "machine is required"})
    if body.action == "acquire":
        return await asyncio.to_thread(
            lambda: store.acquire_lock(
                machine=body.machine,
                cycle_id=body.cycle_id,
                pid=body.pid,
                takeover_dead=body.takeover_dead,
            )
        )
    try:
        await asyncio.to_thread(
            lambda: store.release_lock(machine=body.machine, cycle_id=body.cycle_id)
        )
    except team_store.Stale as error:
        raise _refuse(error) from error
    return {"released": True}


@router.post("/v1/team/queue/reports")
async def post_report(body: ReportRequest, request: Request) -> dict[str, Any]:
    store = _store(request)
    try:
        await asyncio.to_thread(store.put_report, body.name, body.text)
    except team_store.Invalid as error:
        raise _refuse(error) from error
    return {"stored": body.name}
