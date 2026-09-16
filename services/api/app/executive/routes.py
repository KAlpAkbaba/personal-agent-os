"""Executive Autonomy's REST surface (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md §6) for the
Cockpit's "Görevler" panel.

- POST /v1/executive/runs                    start a run from a directive (the SAME
                                              ``ExecutiveService.start_run`` the voice
                                              tool ``executive.start`` calls).
- GET  /v1/executive/runs                    the owner's own runs, with their state.
- GET  /v1/executive/runs/{id}                one run's status (spec §5's receipt shape).
- GET  /v1/executive/runs/{id}/explain        the current step, one sentence.
- POST /v1/executive/runs/{id}/pause|resume|cancel   the SAME signals the voice tools send.
- POST /v1/executive/runs/{id}/retry          {"step_id": "s2"}.
- POST /v1/executive/runs/{id}/amend          {"step": {...}} — a single, already-shaped
                                              ``app.executive.spec.Step``.

Owner-gated at the router level (``require_owner_session``) — starting or steering a
durable job that drives the owner's own device/mail/calendar/artifacts must never be one
unauthenticated HTTP call away. A refusal answers with the service's own error class and
sentence (422; 404 for an unknown run/step), never a bare 500.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from temporalio.client import Client

from app.executive import service as executive_service
from app.executive.models import ExecutiveRunRow
from app.executive.service import ExecutiveServiceError
from app.identity.dependencies import require_owner_session

router = APIRouter(
    prefix="/v1/executive", tags=["executive"], dependencies=[Depends(require_owner_session)]
)


def _artifacts(request: Request) -> Any:
    return request.app.state.artifacts


async def _temporal_client(request: Request) -> Client:
    """Mirrors ``app.research.routes._temporal_client`` — a fresh connection per call
    rather than a cached client on ``app.state`` (the same choice that module already
    made for the identical need)."""
    settings = request.app.state.artifacts.settings
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


def _run_dict(run: ExecutiveRunRow) -> dict[str, Any]:
    """The list route's row is the service's own shape plus how the run was started —
    one builder, so the list and the detail can never describe a run differently."""
    return {**executive_service.run_dict(run), "source": run.source}


def _error_response(exc: ExecutiveServiceError) -> HTTPException:
    status_code = 404 if exc.error_class == "not_found" else 422
    return HTTPException(
        status_code=status_code, detail={"code": exc.error_class, "message": exc.speech}
    )


@router.post("/runs")
async def start_run(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    directive = str(payload.get("directive") or "").strip()
    graph = payload.get("graph") if isinstance(payload.get("graph"), dict) else None
    if graph is not None and not directive:
        directive = str(graph.get("goal") or "").strip()
    if not directive:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_argument", "message": "directive is required"}
        )
    folder = payload.get("folder")
    artifacts = _artifacts(request)
    unavailable = HTTPException(
        status_code=503,
        detail={
            "code": "dependency_unavailable",
            "message": executive_service.WORKFLOW_UNAVAILABLE_TR,
        },
    )
    try:
        client = await _temporal_client(request)
    except Exception as exc:  # noqa: BLE001 - Phase 8: a typed refusal, not an untyped 500
        raise unavailable from exc
    owner_session_id = str(request.state.owner_session.session_id)
    try:
        with artifacts.session() as db:
            run = executive_service.start_run_db(
                db,
                directive=directive,
                folder=folder if isinstance(folder, str) else None,
                source="rest",
                session_id=owner_session_id,
                graph=graph,
            )
            try:
                await executive_service.start_run_workflow(
                    client,
                    run,
                    task_queue=artifacts.settings.temporal_task_queue,
                    artifacts=artifacts,
                )
            except Exception as exc:  # noqa: BLE001 - the run is closed, then refused typed
                executive_service.fail_unstarted_run(
                    db, run.id, detail=f"{type(exc).__name__}: {exc}"
                )
                raise unavailable from exc
            db.refresh(run)
            return _run_dict(run)
    except ExecutiveServiceError as exc:
        raise _error_response(exc) from exc


@router.get("/runs")
async def list_runs(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)
    with artifacts.session() as db:
        runs = executive_service.list_runs(db)
        return {"runs": [_run_dict(r) for r in runs]}


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)
    try:
        with artifacts.session() as db:
            status = executive_service.get_status(db, run_id)
            # The panel draws the current step's sentence from the detail route rather than
            # asking a second one for it (spec §6); the route that knows the run says it.
            status["explain"] = executive_service.get_explain(db, run_id).get("speech")
            steps = executive_service.list_steps(db, run_id)
            status["steps"] = [
                {
                    "step_id": s.step_id,
                    "kind": s.kind,
                    "state": s.state,
                    "error_class": s.error_class,
                }
                for s in steps
            ]
            return status
    except ExecutiveServiceError as exc:
        raise _error_response(exc) from exc


@router.get("/runs/{run_id}/explain")
async def explain_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)
    try:
        with artifacts.session() as db:
            return executive_service.get_explain(db, run_id)
    except ExecutiveServiceError as exc:
        raise _error_response(exc) from exc


#: Every mutating route below validates SYNCHRONOUSLY first (no Temporal connection
#: needed to be refused) and only reaches for a client once there is a real signal to
#: send — a request that was always going to be refused must never depend on a live
#: Temporal server to say so (found via this route's own unit tests: a refusal used to
#: fail on a connection attempt instead of answering 422).


@router.get("/runs/{run_id}/plan")
async def plan_of_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    """B38 (req 546): the whole plan with each step's rationale and state."""
    artifacts = _artifacts(request)
    try:
        with artifacts.session() as db:
            return executive_service.get_plan(db, run_id)
    except ExecutiveServiceError as exc:
        raise _error_response(exc) from exc


@router.post("/runs/{run_id}/approve")
async def approve_step(
    request: Request, run_id: uuid.UUID, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """B38 (req 544): the owner's yes for the step the run waits on. Recorded on the
    row first, then the workflow is told; the owner-session gate on this router is the
    authority."""
    step_id = (payload or {}).get("step_id")
    artifacts = _artifacts(request)
    try:
        with artifacts.session() as db:
            approved = executive_service.approve_step_db(
                db, run_id, str(step_id) if isinstance(step_id, str) and step_id else None
            )
            speech = executive_service.get_status(db, run_id)["speech"]
        client = await _temporal_client(request)
        await executive_service.approve_step_signal(client, run_id, approved)
        return {"run_id": str(run_id), "step_id": approved, "speech": speech}
    except ExecutiveServiceError as exc:
        raise _error_response(exc) from exc


@router.post("/runs/{run_id}/pause")
async def pause_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)
    try:
        with artifacts.session() as db:
            executive_service.pause_run_db(db, run_id)
            speech = executive_service.get_status(db, run_id)["speech"]
        client = await _temporal_client(request)
        await executive_service.pause_run_signal(client, run_id)
        return {"run_id": str(run_id), "speech": speech}
    except ExecutiveServiceError as exc:
        raise _error_response(exc) from exc


@router.post("/runs/{run_id}/resume")
async def resume_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)
    try:
        with artifacts.session() as db:
            executive_service.resume_run_db(db, run_id)
            speech = executive_service.get_status(db, run_id)["speech"]
        client = await _temporal_client(request)
        await executive_service.resume_run_signal(client, run_id)
        return {"run_id": str(run_id), "speech": speech}
    except ExecutiveServiceError as exc:
        raise _error_response(exc) from exc


@router.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)
    try:
        with artifacts.session() as db:
            executive_service.cancel_run_validate(db, run_id)
        client = await _temporal_client(request)
        with artifacts.session() as db:
            await executive_service.cancel_run_signal_and_sweep(client, run_id)
            speech = executive_service.get_status(db, run_id)["speech"]
        return {"run_id": str(run_id), "speech": speech}
    except ExecutiveServiceError as exc:
        raise _error_response(exc) from exc


@router.post("/runs/{run_id}/retry")
async def retry_step(
    request: Request, run_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    step_id = str(payload.get("step_id") or "").strip()
    if not step_id:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_argument", "message": "step_id is required"}
        )
    artifacts = _artifacts(request)
    try:
        with artifacts.session() as db:
            executive_service.retry_step_validate(db, run_id, step_id)
        client = await _temporal_client(request)
        await executive_service.retry_step_signal(client, run_id, step_id)
        return {
            "run_id": str(run_id),
            "step_id": step_id,
            "speech": f"{step_id} adımını tekrar deniyorum efendim.",
        }
    except ExecutiveServiceError as exc:
        raise _error_response(exc) from exc


@router.post("/runs/{run_id}/amend")
async def amend_run(request: Request, run_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    step = payload.get("step")
    if not isinstance(step, dict):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_argument", "message": "step is required"}
        )
    artifacts = _artifacts(request)
    try:
        with artifacts.session() as db:
            candidate = executive_service.amend_run_db(db, run_id, step)
        client = await _temporal_client(request)
        await executive_service.amend_run_signal(client, run_id, candidate)
        return {
            "run_id": str(run_id),
            "step_id": candidate.id,
            "speech": f"{candidate.id} adımını ekledim efendim.",
        }
    except ExecutiveServiceError as exc:
        raise _error_response(exc) from exc


__all__ = ["router"]
