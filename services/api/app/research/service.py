"""Starting a browser research run: the ONE code path, called from two callers
(M18.2 follow-up to ADR-0067).

``app.research.routes.create_research`` (the REST surface) and
``app.voice.realtime_sessions.tools.research_start`` (a spoken "araştır") used to be
two unrelated things linked only by the owner's own memory of having asked (ADR-0067,
Context, gap 1). This module is the extraction that closes that gap: the synchronous
DB part (create the task, pick a capable device, record the plan-adjacent run row) is
:func:`start_browser_research` — callable from a plain SQLAlchemy ``Session`` with no
``asyncio`` involved, so a synchronous voice tool handler running inside its own DB
transaction can call it directly. The asynchronous part (``Client.start_workflow``,
which only exists as a coroutine) is :func:`start_browser_research_workflow` — the
REST route awaits it inline exactly as before; the voice tool hands it to
``ToolContext.followups`` so the realtime-session route can await it once the tool
call's own transaction has committed.

Neither function knows about voice or REST: ``source`` is a caller-supplied string
("rest" | "voice") recorded on the run's own PLANNED/FAILED event so the durable
record says where a research run came from, and ``session_id``/``tool_call_id`` are
recorded the same way when the caller is a realtime session — the announcer already
finds its way back to the tool call through the call's own ``result_json['task_id']``
(``app.voice.realtime_sessions.service.find_running_tool_call_by_task_id``); this is
the human-readable provenance alongside that mechanism, not a second lookup path.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.artifacts import service as artifact_service
from app.artifacts.models import TASK_STATUS_FAILED_TERMINAL
from app.devices import service as devices_service
from app.devices.selection import NoCapableDeviceError, select_device
from app.logging import get_logger
from app.research import runs_service
from app.research.browser_workflow import BrowserResearchRequest, BrowserResearchWorkflow
from app.research.models import STAGE_FAILED, STAGE_PLANNED

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.artifacts.runtime import ArtifactRuntime
    from app.broker.runtime import BrokerRuntime

logger = get_logger("app.research.service")

#: spec §5a: interactive_wait_s bounds (60s = one browser.wait slice; 1800s = 30 minutes).
#: 30 s exists for owner QUALIFICATION runs (a 600 s wait is not a test); production
#: requests keep DEFAULT_INTERACTIVE_WAIT_S, and the owner smoke has -HandoffTimeoutSec.
MIN_INTERACTIVE_WAIT_S = 30
MAX_INTERACTIVE_WAIT_S = 1800
DEFAULT_INTERACTIVE_WAIT_S = 600

#: Where a research run's task/run row says it came from (provenance only; the
#: pipeline behaves identically either way).
SOURCE_REST = "rest"
SOURCE_VOICE = "voice"

RESEARCH_CAPABILITY = "browser.chrome"


def workflow_id_for(task_id: uuid.UUID) -> str:
    return f"research-browser-{task_id}"


def _device_summary(view: Any) -> dict[str, Any]:
    return {"device_id": str(view.id), "name": view.name}


@dataclass(frozen=True, slots=True)
class StartedResearch:
    """The outcome of the synchronous half (:func:`start_browser_research`).

    ``error`` is truthy exactly when no capable device existed: the task is already
    FAILED_TERMINAL and the run row already FAILED with the same Turkish detail — the
    caller reports it (409 for REST, an immediate failed tool call for voice) rather
    than starting a workflow that could never succeed.
    """

    task_id: uuid.UUID
    workflow_id: str
    device: dict[str, Any] | None
    error: str | None


def start_browser_research(
    db: Session,
    broker: BrokerRuntime,
    *,
    input: str,
    target_device: str | None = None,
    recency_days: int | None = None,
    max_sources: int = 12,
    trace_id: str | None = None,
    source: str = SOURCE_REST,
    session_id: uuid.UUID | None = None,
    tool_call_id: str | None = None,
) -> StartedResearch:
    """Create the task, pick a capable device, and record the PLANNED (or FAILED) run
    row — the synchronous DB half both callers share. Device selection happens HERE,
    before any workflow starts (see ``app.research.routes`` module docstring for why):
    a request that names an unreachable device, or when nothing is online at all,
    fails fast with a Turkish detail instead of starting a workflow certain to fail
    later.
    """
    task = artifact_service.create_task(db, intent=input, trace_id=trace_id)
    workflow_id = workflow_id_for(task.id)
    provenance = {"source": source}
    if session_id is not None:
        provenance["session_id"] = str(session_id)
    if tool_call_id is not None:
        provenance["tool_call_id"] = tool_call_id
    views = devices_service.list_device_views(db, broker)
    try:
        result = select_device(views, capability=RESEARCH_CAPABILITY, target=target_device)
    except NoCapableDeviceError as exc:
        artifact_service.transition_task(
            db,
            task.id,
            TASK_STATUS_FAILED_TERMINAL,
            error_class="no_capable_device",
            error_message=exc.detail_tr,
        )
        runs_service.update_run(
            db,
            task.id,
            stage=STAGE_FAILED,
            error=exc.detail_tr,
            event={"stage": STAGE_FAILED, "detail": exc.detail_tr, **provenance},
        )
        logger.info("research_no_capable_device", task_id=str(task.id), source=source)
        return StartedResearch(
            task_id=task.id, workflow_id=workflow_id, device=None, error=exc.detail_tr
        )
    runs_service.update_run(
        db,
        task.id,
        stage=STAGE_PLANNED,
        device_id=result.device.id,
        event={
            "stage": STAGE_PLANNED,
            "detail": f"selected {result.device.name}",
            **provenance,
        },
    )
    return StartedResearch(
        task_id=task.id,
        workflow_id=workflow_id,
        device=_device_summary(result.device),
        error=None,
    )


async def start_browser_research_workflow(
    client: Client,
    artifacts: ArtifactRuntime,
    *,
    task_id: uuid.UUID,
    workflow_id: str,
    input: str,
    target_device: str | None = None,
    recency_days: int | None = None,
    max_sources: int = 12,
    synthesis: str = "auto",
    interactive: bool = False,
    interactive_wait_s: int = DEFAULT_INTERACTIVE_WAIT_S,
    on_verification_timeout: str = "fallback",
    search_provider: str | None = None,
) -> None:
    """The asynchronous half: start the durable Temporal workflow and persist its id
    on the task. ``WorkflowAlreadyStartedError`` is swallowed (idempotent retry of the
    same task_id-derived workflow id) exactly as the REST route always has; a caller
    that wants a DIFFERENT failure to surface (the voice follow-up does, per
    ADR-0067 amendment) lets every other exception propagate.
    """
    try:
        await client.start_workflow(
            BrowserResearchWorkflow.run,
            BrowserResearchRequest(
                task_id=str(task_id),
                topic=input,
                target_device=target_device,
                recency_days=recency_days,
                max_sources=max_sources,
                synthesis=synthesis,
                interactive=interactive,
                interactive_wait_s=interactive_wait_s,
                on_verification_timeout=on_verification_timeout,
                search_provider=search_provider or "duckduckgo",
            ),
            id=workflow_id,
            task_queue=artifacts.settings.temporal_task_queue,
        )
    except WorkflowAlreadyStartedError:
        pass  # idempotent retry of the same request

    def persist_wf() -> None:
        with artifacts.session() as session:
            artifact_service.set_task_workflow_id(session, task_id, workflow_id)

    await asyncio.to_thread(persist_wf)


async def connect_temporal(artifacts: ArtifactRuntime) -> Client:
    """The same connection recipe ``app.research.routes`` has always used, factored
    out so the voice follow-up (a different call site, same settings) does not
    duplicate it."""
    settings = artifacts.settings
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


__all__ = [
    "DEFAULT_INTERACTIVE_WAIT_S",
    "MAX_INTERACTIVE_WAIT_S",
    "MIN_INTERACTIVE_WAIT_S",
    "RESEARCH_CAPABILITY",
    "SOURCE_REST",
    "SOURCE_VOICE",
    "StartedResearch",
    "connect_temporal",
    "start_browser_research",
    "start_browser_research_workflow",
    "workflow_id_for",
]
