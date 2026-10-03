"""``/v1/team/approvals`` (the Onay Merkezi) and ``/v1/team/queue`` (the team's shared state).

Approvals: GET lists what waits at the owner's two gates; POST ``/decision`` takes Onayla /
Reddet. The voice path ("fikri onayla" / "yayını onayla") calls the same POST with
``channel: "voice"`` and the gate it heard; the rules that refuse a voice approval live in
``approvals.decide``, not here.

Models: GET / PUT ``/v1/team/queue/models``, the model policy's setting (ADR-0214 addendum 7):
the cycle reads it, the Ofis page writes it; ``models_setting`` holds its rules.

Queue: the cycle's own surface (pilot-02). GET the whole queue; PUT one task by id with the
``updated_at`` the writer last saw (409 when it is stale); POST the lock (acquire / release,
the six-hour staleness rule), the cycle report as text and a proposal's text (what the Onay
Merkezi shows for the idea that names it). All of it under the owner session,
over whichever store ``app.state.team_store`` is: the database on the Cloud Core, otherwise
the files under ``app.state.team_root``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.identity.dependencies import require_owner_session
from app.ledger import service as ledger_service
from app.ledger.vocabulary import InvalidVocabulary
from app.team import approvals, models_setting, office
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
        lock, at = store.read_lock(), team_store.utcnow()
        return {
            "approvals": approvals.list_pending(queue, root, store),
            "cycle_report": store.newest_report(),
            # For information; whether a decision is taken now is ``decisions_open``.
            "cycle_running": team_store.lock_is_running(lock, at),
            "decisions_open": approvals.decisions_open(store, lock, at),
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


class ProposalRequest(BaseModel):
    """One file of ``team/proposals/``. The name's rules are the store's."""

    model_config = ConfigDict(extra="forbid")

    name: str
    text: str = Field(max_length=team_store.PROPOSAL_MAX_CHARS)


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


@router.post("/v1/team/queue/proposals")
async def post_proposal(body: ProposalRequest, request: Request) -> dict[str, Any]:
    """Keep (or replace) a proposal's text where the queue is kept."""
    store = _store(request)
    try:
        await asyncio.to_thread(store.put_proposal, body.name, body.text)
    except team_store.Invalid as error:
        raise _refuse(error) from error
    return {"ok": True}


# ------------------------------------------------------------------ the live status + the Ofis


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


_Role = Literal["lead", "researcher", "integrator", "worker", "inspector"]
#: A model id as a run names it. Not held to the three ids: the status is what the cycle SAW
#: (the tool may have run another model), and a refused heartbeat blanks the whole Ofis page.
_ModelId = Annotated[str, Field(max_length=64)]


class _Run(_Strict):
    task: str
    role: _Role
    started_at: str
    #: The model the run was started on. An older cycle sends none.
    model: _ModelId | None = None
    #: A worker run's seat number, kept for the run's life (office-stable-seats). An older
    #: cycle sends none; office_view seats a bad or duplicate one by the unseated rule.
    seat: int | None = None


class _UsageLimit(_Strict):
    state: Literal["ok", "waiting", "stopped"]
    resets_at: str | None = None


class _LimitWindow(_Strict):
    state: Literal["ok", "limited"]
    resets_at: str | None = None
    #: The tool's own number, or null: nobody computes or estimates a percentage.
    used_pct: float | None = Field(default=None, allow_inf_nan=False)


class _Lowered(_Strict):
    task: str
    role: _Role
    from_: _ModelId = Field(alias="from")
    to: _ModelId
    at: str


class _Limits(_Strict):
    fable: _LimitWindow
    all: _LimitWindow
    fallback: bool
    #: This cycle's downgrades, newest last.
    lowered: list[_Lowered] = Field(max_length=office.LOWERED_MAX)


class StatusRequest(_Strict):
    """The cycle's live status (``scripts/lib`` writes it; the Ofis reads it)."""

    cycle_id: str
    machine: str
    pid: int
    started_at: str
    runs: list[_Run]
    estimated_usd: float
    usage_limit: _UsageLimit
    #: ADR-0214 addendum 7. Optional: a cycle older than the model policy sends neither this
    #: nor a run's ``model``, and is still accepted.
    limits: _Limits | None = None
    updated_at: str


@router.get("/v1/team/queue/status")
async def read_status(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(_store(request).read_status) or {}


@router.put("/v1/team/queue/status")
async def put_status(body: StatusRequest, request: Request) -> dict[str, Any]:
    store = _store(request)
    # Kept as it was sent: what the cycle left out is not stored as null.
    document = body.model_dump(by_alias=True, exclude_unset=True)
    try:
        await asyncio.to_thread(store.put_status, document)
    except OSError as error:
        raise _refuse(error) from error
    return {"stored": True}


def _models_in_force(store: team_store.TeamStore) -> dict[str, Any]:
    return models_setting.effective(store.read_models(), team_store.stamp(team_store.utcnow()))


@router.get("/v1/team/queue/models")
async def read_models(request: Request) -> dict[str, Any]:
    """The model setting: what is stored, or the defaults. Exactly the document's three keys -
    the cycle refuses a setting with any other."""
    return await asyncio.to_thread(_models_in_force, _store(request))


@router.put("/v1/team/queue/models")
async def put_models(body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Replace the setting. ``updated_at`` is this server's: one a client sends is not kept."""
    problems = models_setting.problems(body, strict=True)
    if problems:
        raise HTTPException(
            422,
            {
                "code": problems[0][0],
                "message": "; ".join(text for _, text in problems),
                "problems": [text for _, text in problems],
            },
        )
    document = {
        "roles": {role: body["roles"][role] for role in models_setting.ROLES},
        "fallback": body["fallback"],
        "updated_at": team_store.stamp(team_store.utcnow()),
    }
    store = _store(request)
    try:
        await asyncio.to_thread(store.put_models, document)
    except (team_store.Invalid, OSError) as error:
        raise _refuse(error) from error
    return document


@router.get("/v1/team/office")
async def read_office(request: Request) -> dict[str, Any]:
    root = _team_root(request)
    store = _store(request)

    def load() -> dict[str, Any]:
        try:
            queue = store.read_queue()
        except (OSError, ValueError):
            queue = {"version": 1, "tasks": []}  # no store configured: the empty office
        return office.office_view(
            queue,
            store.read_lock(),
            store.read_status(),
            approvals.list_pending(queue, root, store),
            team_store.utcnow(),
            models=_models_in_force(store),
        )

    return await asyncio.to_thread(load)
