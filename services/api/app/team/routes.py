"""``/v1/team/approvals`` (the Onay Merkezi) and ``/v1/team/queue`` (the team's shared state).

Approvals: GET lists what waits at the owner's two gates; POST ``/decision`` takes Onayla /
Reddet. The voice path ("fikri onayla" / "yayını onayla") calls the same POST with
``channel: "voice"`` and the gate it heard; the rules that refuse a voice approval live in
``approvals.decide``, not here.

Models: GET / PUT ``/v1/team/queue/models``, the model policy's setting (ADR-0214 addendum 7):
the cycle reads it, the Ofis page writes it; ``models_setting`` holds its rules.

Queue: the cycle's own surface (pilot-02). GET the whole queue; PUT one task by id with the
``updated_at`` the writer last saw (409 when it is stale); POST the lock (acquire / release,
the six-hour staleness rule counted from the holder's last status - the store reads it beside
the lock), the cycle report as text and a proposal's text (what the Onay
Merkezi shows for the idea that names it). All of it under the owner session,
over whichever store ``app.state.team_store`` is: the database on the Cloud Core, otherwise
the files under ``app.state.team_root``.
"""

from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.identity.dependencies import require_owner_session
from app.ledger import service as ledger_service
from app.ledger.vocabulary import InvalidVocabulary
from app.team import approvals, models_setting, office, progress, trials
from app.team import store as team_store

router = APIRouter(dependencies=[Depends(require_owner_session)])

#: services/api/app/team/routes.py -> the repository root's ``team/``.
DEFAULT_TEAM_ROOT = Path(__file__).resolve().parents[4] / "team"
#: The repository tree whose roadmap and v1.0 matrix the Ofis's İlerleme strip reads.
DEFAULT_PROGRESS_ROOT = Path(__file__).resolve().parents[4]


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
            # The third gate: what the owner tries on a real device (``trials``).
            "trials": trials.list_open(queue),
            "cycle_report": store.newest_report(),
            # For information; whether a decision is taken now is ``decisions_open``.
            "cycle_running": team_store.lock_is_running(lock, at, store.read_status()),
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


class TrialDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    trial_id: str
    verdict: Literal["oldu", "olmadi"]
    #: The owner's own words; required for "olmadi" (``trials.decide`` holds the rule).
    said: str | None = None


@router.post("/v1/team/trials/decision")
async def decide_trial(body: TrialDecisionRequest, request: Request) -> dict[str, Any]:
    """Oldu / Olmadı on one trial (``trials``). Never writes QUALIFICATION."""
    store = _store(request)
    artifacts = request.app.state.artifacts

    def record(facts: dict[str, Any]) -> None:
        passed = facts["verdict"] == trials.PASSED
        opened = "" if passed else f"; {facts['fix_task_id']} düzeltme işi açıldı"
        event = ledger_service.ActivityEvent(
            event_type=trials.EVENT_TRIAL_PASSED if passed else trials.EVENT_TRIAL_FAILED,
            subsystem=approvals.SUBSYSTEM_TEAM,
            action=f"team.trials.{facts['verdict']}",
            factual_summary=(
                f"Sahip {facts['task_id']} görevinin {facts['trial_id']} denemesine "
                f"'{facts['verdict']}' dedi ({facts['machine']}){opened}."
            ),
            source="team.trials",
            source_ref=f"{facts['task_id']}:{facts['trial_id']}:{facts['updated_at']}",
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
        return trials.decide(
            store,
            task_id=body.task_id,
            trial_id=body.trial_id,
            verdict=body.verdict,
            said=body.said,
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
MODEL_ID_MAX = 64
#: A model id as a run names it. Not held to the three ids: the status is what the cycle SAW
#: (the tool may have run another model), and a refused heartbeat blanks the whole Ofis page.
_ModelId = Annotated[str, Field(max_length=MODEL_ID_MAX)]
#: Every timestamp of the status is held to the width of ``team_state.updated_at``
#: (VARCHAR(32)): longer was a 200 on SQLite and the file store and a 500 on PostgreSQL.
STAMP_MAX = 32
_Stamp = Annotated[str, Field(max_length=STAMP_MAX)]
#: The tool's own percentage, or null: nobody computes or estimates one.
_Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
_Usd = Annotated[float, Field(ge=0, allow_inf_nan=False)]

#: The status' own refusals (team-status-bounds): {detail: {code, message, problems}}. A code
#: is never renamed - the cycle's client may compare it. Any other broken field keeps the
#: framework's 422.
STATUS_REFUSALS = {
    "status_stamp_too_long": f"durum belgesinde bir zaman damgası {STAMP_MAX} karakteri aşıyor",
    "status_model_id_too_long": f"durumda bir model kimliği {MODEL_ID_MAX} karakteri aşıyor",
    "status_used_pct_invalid": "kullanım yüzdesi (used_pct) 0 ile 100 arası sonlu bir sayı olmalı",
    "status_estimated_usd_invalid": (
        "tahmini maliyet (estimated_usd) sıfır ya da pozitif sonlu bir sayı olmalı"
    ),
}


class _RunProgress(_Strict):
    """How far a live run has got, measured by the cycle from the run's own worktree (the
    owner, 2026-10-05): how many of the card's area entries have a change, and three marks."""

    area_total: int = Field(ge=0, le=500)
    area_touched: int = Field(ge=0, le=500)
    tests_changed: bool
    adr_draft: bool
    commits: int = Field(ge=0, le=10000)
    last_change_at: _Stamp | None = None


class _Run(_Strict):
    task: str
    role: _Role
    started_at: _Stamp
    #: The model the run was started on. An older cycle sends none.
    model: _ModelId | None = None
    #: An older cycle sends none.
    progress: _RunProgress | None = None


class _UsageLimit(_Strict):
    state: Literal["ok", "waiting", "stopped"]
    resets_at: _Stamp | None = None


class _LimitWindow(_Strict):
    state: Literal["ok", "limited"]
    resets_at: _Stamp | None = None
    used_pct: _Percent | None = None


class _Lowered(_Strict):
    task: str
    role: _Role
    from_: _ModelId = Field(alias="from")
    to: _ModelId
    at: _Stamp


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
    started_at: _Stamp
    runs: list[_Run]
    estimated_usd: _Usd
    usage_limit: _UsageLimit
    #: ADR-0214 addendum 7. Optional: a cycle older than the model policy sends neither this
    #: nor a run's ``model``, and is still accepted.
    limits: _Limits | None = None
    #: The Claude account the team runs under, as the team wrapper names it: the folder of
    #: CLAUDE_CONFIG_DIR (".claude-hesap3") or "varsayilan". A folder name, never an e-mail -
    #: the pattern refuses '@' and spaces. Optional: an older cycle does not send it.
    account: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"
    )
    updated_at: _Stamp


def _status_refusal(error: ValidationError) -> HTTPException | None:
    """The first broken bound as the route's own refusal; None when no bound is broken."""
    for item in error.errors(include_url=False):
        names = [step for step in item["loc"] if isinstance(step, str)]
        name = names[-1] if names else ""
        code = None
        if name == "used_pct":
            code = "status_used_pct_invalid"
        elif name == "estimated_usd":
            code = "status_estimated_usd_invalid"
        elif item["type"] == "string_too_long":
            width = (item.get("ctx") or {}).get("max_length")
            code = {
                STAMP_MAX: "status_stamp_too_long",
                MODEL_ID_MAX: "status_model_id_too_long",
            }.get(width)
        if code:
            where = ".".join(str(step) for step in item["loc"])
            return HTTPException(
                422,
                {"code": code, "message": f"{STATUS_REFUSALS[code]}: {where}", "problems": [where]},
            )
    return None


def _json_safe(value: Any) -> Any:
    """A refusal echoes the input; 1e999 or NaN in it would make the refusal itself a 500."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


@router.get("/v1/team/queue/status")
async def read_status(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(_store(request).read_status) or {}


@router.put("/v1/team/queue/status")
async def put_status(body: dict[str, Any], request: Request) -> dict[str, Any]:
    # Validated here, not by the signature: a broken bound is answered with its own code.
    try:
        status = StatusRequest.model_validate(body)
    except ValidationError as error:
        refusal = _status_refusal(error)
        if refusal is not None:
            raise refusal from None
        errors = [
            {**item, "loc": ("body", *item["loc"]), "input": _json_safe(item.get("input"))}
            for item in error.errors(include_url=False)
        ]
        raise RequestValidationError(errors) from None
    store = _store(request)
    # Kept as it was sent: what the cycle left out is not stored as null.
    document = status.model_dump(by_alias=True, exclude_unset=True)
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
        ) | {"progress": _progress(request)}

    return await asyncio.to_thread(load)


def _progress(request: Request) -> dict[str, Any]:
    """The İlerleme strip (office-progress): additive, so a page that predates it ignores it."""
    # The api image ships neither document: production mounts the two, read-only, under
    # PAGENTOS_PROGRESS_ROOT (infra/docker/docker-compose.prod.yml); a checkout reads its tree.
    root = (
        getattr(request.app.state, "progress_root", None)
        or os.environ.get("PAGENTOS_PROGRESS_ROOT", "").strip()
        or DEFAULT_PROGRESS_ROOT
    )
    settings = getattr(request.app.state, "settings", None)
    release = (getattr(settings, "release", None) or "").strip()
    return progress.progress(Path(root), as_of=release or None)
