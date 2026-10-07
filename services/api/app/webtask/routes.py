"""``/v1/web-tasks``: the REST path that starts a browser task and carries the owner's
words to it (card cloud-task-loop-core). Owner-gated like every other surface.

Where the task runs is chosen HERE, synchronously, before the workflow starts
(``app.webtask.target``): nowhere to run is ``409 no_capable_device`` with the Turkish
sentence, and nothing is started. The row is written next (``start_task_db``: one task at
a time, a cloud task unattended), then ``BrowserTaskWorkflow`` is started. If Temporal
cannot be reached the row is closed as ``failed`` / ``temporal_unavailable`` and the
answer is an honest 503 - never a row left "running" with nothing driving it.

The owner's word (confirm, decline, continue, cancel) is applied to the ROW first, so the
answer is true at once; the workflow is then woken by a signal that carries nothing
(``app.webtask.workflow``). A workflow that is already gone is logged, never a 500.

Mounted by ``app.main.create_app`` (one line, added by the lead at merge time).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from temporalio.client import Client

from app.actions.confirmation_gate import CONFIRM_SOURCE_REST, Confirmation
from app.identity.dependencies import require_owner_session
from app.logging import get_logger
from app.webtask import service
from app.webtask.models import SOURCE_REST
from app.webtask.service import WebTaskError
from app.webtask.target import choose_task_target
from app.webtask.workflow import BrowserTaskRequest, BrowserTaskWorkflow

logger = get_logger("app.webtask.routes")

router = APIRouter(prefix="/v1/web-tasks", dependencies=[Depends(require_owner_session)])

FAILURE_TEMPORAL_UNAVAILABLE: Final = "temporal_unavailable"
TEMPORAL_UNAVAILABLE_TR: Final = "Görev başlatılamadı: iş akışı sunucusuna ulaşılamıyor."
#: A Temporal that does not answer is not waited on for ever: the owner is told.
TEMPORAL_CONNECT_TIMEOUT_S: Final = 10.0
MAX_ALLOWED_HOSTS: Final = 10

#: ``WebTaskError.reason`` -> status. Anything else the task's state does not allow is 409.
_STATUS: Final[dict[str, int]] = {
    "not_found": 404,
    "empty_goal": 422,
    "goal_too_long": 422,
}


class StartWebTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=service.MAX_GOAL_CHARS)
    #: The owner's word for where: "bulutta", or a device's alias. None = the rule's chain.
    target_word: str | None = Field(default=None, max_length=64)
    allowed_hosts: list[str] = Field(default_factory=list, max_length=MAX_ALLOWED_HOSTS)


class ConfirmWebTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: This surface confirms as REST only; a spoken confirmation is the voice tools'.
    source: str = Field(default=CONFIRM_SOURCE_REST, pattern=f"^{CONFIRM_SOURCE_REST}$")


class ContinueWebTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(default="", max_length=600)


def _artifacts(request: Request) -> Any:
    return request.app.state.artifacts


async def _temporal_client(request: Request) -> Client:
    settings = _artifacts(request).settings
    return await asyncio.wait_for(
        Client.connect(settings.temporal_address, namespace=settings.temporal_namespace),
        timeout=TEMPORAL_CONNECT_TIMEOUT_S,
    )


def _refusal(exc: WebTaskError) -> HTTPException:
    detail: dict[str, Any] = {"error_class": exc.reason}
    message = str(exc)
    if message and message != exc.reason:
        detail["detail"] = message
    detail.update({k: v for k, v in exc.detail.items() if k == "task_id"})
    return HTTPException(status_code=_STATUS.get(exc.reason, 409), detail=detail)


#: The owner session the router's own dependency authenticated (resolved once a request).
OwnerSession = Annotated[Any, Depends(require_owner_session)]


def _session_id(session: Any) -> str:
    return str(getattr(session, "session_id", "") or CONFIRM_SOURCE_REST)


@router.post("", status_code=202)
async def start_web_task(request: Request, body: StartWebTask) -> JSONResponse:
    artifacts = _artifacts(request)
    broker = getattr(request.app.state, "broker", None)
    hosts = tuple(h.strip().lower() for h in body.allowed_hosts if h.strip())

    def choose_and_start() -> dict[str, Any]:
        with artifacts.session() as db:
            chosen = choose_task_target(
                db, broker, spoken_target=body.target_word, allowed_hosts=hosts
            )
            row = service.start_task_db(
                db,
                goal=body.goal,
                device_id=chosen.device_id,
                source=SOURCE_REST,
                allowed_hosts=hosts,
                target=chosen.target,
                spoken_target=body.target_word,
            )
            return {
                "task_id": str(row.id),
                "target": chosen.target,
                "device_id": str(chosen.device_id),
                "attended": row.attended,
            }

    try:
        started = await asyncio.to_thread(choose_and_start)
    except WebTaskError as exc:
        raise _refusal(exc) from exc

    task_id = uuid.UUID(started["task_id"])
    try:
        client = await _temporal_client(request)
        await client.start_workflow(
            BrowserTaskWorkflow.run,
            BrowserTaskRequest(task_id=str(task_id)),
            id=service.workflow_id_for(task_id),
            task_queue=artifacts.settings.temporal_task_queue,
        )
    except Exception as exc:  # noqa: BLE001 - a typed refusal, and the row closed
        error = f"{type(exc).__name__}: {exc}"

        def close() -> None:
            with artifacts.session() as db:
                service.fail_db(db, task_id, reason=FAILURE_TEMPORAL_UNAVAILABLE, detail=error)

        await asyncio.to_thread(close)
        logger.warning("web_task_workflow_start_failed", task_id=str(task_id), error=error[:300])
        raise HTTPException(
            status_code=503,
            detail={
                "error_class": FAILURE_TEMPORAL_UNAVAILABLE,
                "detail": TEMPORAL_UNAVAILABLE_TR,
                "task_id": str(task_id),
            },
        ) from exc
    logger.info("web_task_started", task_id=str(task_id), target=started["target"])
    return JSONResponse(status_code=202, content=started)


async def _apply(request: Request, task_id: uuid.UUID, word: Any) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            return service.task_dict(word(db))

    try:
        return await asyncio.to_thread(run)
    except WebTaskError as exc:
        raise _refusal(exc) from exc


async def _signal(request: Request, task_id: uuid.UUID, signal: Any) -> None:
    try:
        client = await _temporal_client(request)
        await client.get_workflow_handle(service.workflow_id_for(task_id)).signal(signal)
    except Exception as exc:  # noqa: BLE001 - the row holds the owner's word either way
        logger.warning(
            "web_task_signal_failed",
            task_id=str(task_id),
            signal=getattr(signal, "__name__", str(signal)),
            error=str(exc)[:300],
        )


@router.get("/{task_id}")
async def get_web_task(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    return await _apply(request, task_id, lambda db: service.get_task(db, task_id))


@router.post("/{task_id}/read-back")
async def read_back_web_task(
    request: Request, task_id: uuid.UUID, owner: OwnerSession
) -> dict[str, Any]:
    """The surface that SHOWED the pending step to the owner says so; a confirmation is
    judged against it (``app.actions.confirmation_gate``)."""
    session_id = _session_id(owner)
    return await _apply(
        request,
        task_id,
        lambda db: service.note_read_back_db(db, task_id, session_id=session_id, turn=None),
    )


@router.post("/{task_id}/confirm")
async def confirm_web_task(
    request: Request, task_id: uuid.UUID, owner: OwnerSession, body: ConfirmWebTask | None = None
) -> dict[str, Any]:
    confirmation = Confirmation(
        source=(body or ConfirmWebTask()).source,
        session_id=_session_id(owner),
        owner_intent_ok=True,
    )
    answer = await _apply(
        request, task_id, lambda db: service.confirm_db(db, task_id, confirmation)
    )
    await _signal(request, task_id, BrowserTaskWorkflow.woken)
    return answer


@router.post("/{task_id}/decline")
async def decline_web_task(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    answer = await _apply(request, task_id, lambda db: service.decline_db(db, task_id))
    await _signal(request, task_id, BrowserTaskWorkflow.woken)
    return answer


@router.post("/{task_id}/continue")
async def continue_web_task(
    request: Request, task_id: uuid.UUID, body: ContinueWebTask | None = None
) -> dict[str, Any]:
    answer_text = (body or ContinueWebTask()).answer
    answer = await _apply(
        request, task_id, lambda db: service.continue_db(db, task_id, answer=answer_text)
    )
    await _signal(request, task_id, BrowserTaskWorkflow.woken)
    return answer


@router.post("/{task_id}/cancel")
async def cancel_web_task(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    answer = await _apply(request, task_id, lambda db: service.request_cancel_db(db, task_id))
    await _signal(request, task_id, BrowserTaskWorkflow.cancel)
    return answer


ROUTERS = [router]

__all__ = ["router"]
