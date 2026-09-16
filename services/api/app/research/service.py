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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.artifacts import service as artifact_service
from app.artifacts.models import TASK_STATUS_CREATED, TASK_STATUS_FAILED_TERMINAL, Task
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
    from app.research.models import ResearchRunRow

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


#: Said when the durable-workflow service (Temporal) does not answer. The owner hears what
#: happened; the task opened for the run is closed rather than left CREATED for ever.
WORKFLOW_UNAVAILABLE_TR = "İş akışı servisine ulaşamadım efendim; araştırma başlamadı."
ERROR_WORKFLOW_START_FAILED = "workflow_start_failed"


def fail_unstarted_research(db: Session, task_id: uuid.UUID, *, detail: str) -> bool:
    """Close a research task whose workflow never started (Phase 8, 2026-09-11).

    ``start_browser_research`` opens the task and its run row BEFORE the workflow starts
    (device selection must fail fast, see its docstring). When the start then fails -
    Temporal down, unreachable, refusing - every caller completed the owner-facing call as
    failed and left the task CREATED and the run PLANNED for ever: an orphan the task list,
    the ledger backfill and the announcers all read as work still to come.

    Only a task still in its opening state is touched; one the workflow already moved on is
    left to the workflow. Returns whether it closed the task.
    """
    task = db.get(Task, task_id)
    if task is None or task.status != TASK_STATUS_CREATED:
        return False
    artifact_service.transition_task(
        db,
        task_id,
        TASK_STATUS_FAILED_TERMINAL,
        error_class=ERROR_WORKFLOW_START_FAILED,
        error_message=detail[:1000],
    )
    runs_service.update_run(
        db,
        task_id,
        stage=STAGE_FAILED,
        error=detail[:1000],
        event={"stage": STAGE_FAILED, "detail": detail[:500]},
    )
    logger.info("research_unstarted_task_closed", task_id=str(task_id))
    return True


#: A run nothing has moved for this long has no workflow behind it any more. Generous on
#: purpose: the interactive stages wait on a PERSON (a challenge page the owner clears), and a
#: day is long enough that "the owner is still on it" has stopped being true.
ABANDONED_RUN_AFTER: Final[timedelta] = timedelta(hours=24)
ERROR_RUN_ABANDONED = "workflow_abandoned"
ABANDONED_RUN_TR = "Araştırma yarıda kaldı efendim; iş akışı geri dönmedi."


def sweep_abandoned_runs(
    db: Session,
    *,
    now: datetime | None = None,
    abandoned_after: timedelta | None = None,
    limit: int = 50,
) -> list[uuid.UUID]:
    """Close research runs whose workflow is gone (B06 req 10/13/11/206). Returns what it closed.

    ``fail_unstarted_research`` covers the run that never STARTED. This is the other orphan:
    one that started, and then the workflow went away - a worker restart, a killed process, a
    Temporal that came back without its history. Nothing swept those: on 2026-09-12 a run was
    still in ``discovering`` three days after its last event, the task list read it as work in
    progress, and the world model counted it among the running.

    A task is only reconciled together with its run, and only when the task is still in a
    non-terminal state: a task the workflow already closed is left exactly as it is.
    """
    from app.artifacts.state import TASK_TERMINAL_STATUSES
    from app.research.models import TERMINAL_STAGES, ResearchRunRow

    moment = now or datetime.now(UTC)
    horizon = moment - (abandoned_after or ABANDONED_RUN_AFTER)
    rows = list(
        db.execute(
            select(ResearchRunRow)
            .where(ResearchRunRow.stage.notin_(tuple(TERMINAL_STAGES)))
            .where(ResearchRunRow.updated_at <= horizon)
            .order_by(ResearchRunRow.updated_at.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    closed: list[uuid.UUID] = []
    for row in rows:
        task = db.get(Task, row.task_id)
        if task is not None and task.status not in TASK_TERMINAL_STATUSES:
            artifact_service.transition_task(
                db,
                row.task_id,
                TASK_STATUS_FAILED_TERMINAL,
                error_class=ERROR_RUN_ABANDONED,
                error_message=ABANDONED_RUN_TR,
            )
        runs_service.update_run(
            db,
            row.task_id,
            stage=STAGE_FAILED,
            error=ERROR_RUN_ABANDONED,
            event={"stage": STAGE_FAILED, "detail": ABANDONED_RUN_TR},
        )
        closed.append(row.task_id)
        logger.info("research_abandoned_run_closed", task_id=str(row.task_id), was=row.stage)
    return closed


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
    mode: str = "quick",
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
                mode=mode,
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


#: B27 req 732. The owner cancelled a research that was still running - the run's own
#: terminal stage, and the task's error class so the task list says WHY it ended.
ERROR_RESEARCH_CANCELLED = "cancelled_by_owner"
CANCELLED_BY_OWNER_TR: Final = "Sahip iptal etti."


def active_research(db: Session) -> ResearchRunRow | None:
    """The most recent research still in flight, or ``None``.

    "In flight" is the run's own vocabulary (``TERMINAL_STAGES``), read from the run row
    rather than the task status: a task can sit READY for days while its run is long
    finished, and the world model already learned that lesson once (ADR-0123).
    """
    from app.research.models import TERMINAL_STAGES, ResearchRunRow

    return (
        db.execute(
            select(ResearchRunRow)
            .where(ResearchRunRow.stage.notin_(tuple(TERMINAL_STAGES)))
            .order_by(ResearchRunRow.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def mark_research_cancelled(
    db: Session, task_id: uuid.UUID, *, detail: str = CANCELLED_BY_OWNER_TR
) -> bool:
    """The durable half of a cancellation: the run to CANCELLED, the task closed.

    ONE function for both callers - the REST route (``POST /v1/research/{id}/cancel``)
    and the voice tool (``research.cancel``) - so the two halves of req 732 cannot record
    a cancellation differently. Returns False for a run that is already terminal: a
    research that finished is finished, and cancelling it would rewrite history.
    """
    from app.artifacts.state import TASK_TERMINAL_STATUSES
    from app.research.models import STAGE_CANCELLED, TERMINAL_STAGES

    run = runs_service.get_run(db, task_id)
    if run is None or run.stage in TERMINAL_STAGES:
        return False
    task = db.get(Task, task_id)
    if task is not None and task.status not in TASK_TERMINAL_STATUSES:
        artifact_service.transition_task(
            db,
            task_id,
            TASK_STATUS_FAILED_TERMINAL,
            error_class=ERROR_RESEARCH_CANCELLED,
            error_message=detail[:1000],
        )
    runs_service.update_run(
        db,
        task_id,
        stage=STAGE_CANCELLED,
        event={"stage": STAGE_CANCELLED, "detail": detail[:500]},
    )
    logger.info("research_cancelled", task_id=str(task_id), was=run.stage)
    return True


# B31 req 203/204. A research the owner paused waits where it is - the Temporal workflow
# holds at its next stage boundary (BrowserResearchWorkflow.pause) and the durable record
# says so - and resumes on the owner's word. Pausing is not a stage: the run keeps the stage
# it was in, the flag lives in progress_json, so a paused run is still "in flight" to
# active_research() and to the orphan sweep alike.
PAUSED_BY_OWNER_TR: Final = "Sahip duraklattı."
RESUMED_BY_OWNER_TR: Final = "Sahip devam ettirdi."
PROGRESS_PAUSED_KEY: Final = "paused"
PROGRESS_PAUSED_AT_KEY: Final = "paused_at"
PROGRESS_PAUSED_SECONDS_KEY: Final = "paused_seconds"


def research_is_paused(run: Any) -> bool:
    progress = getattr(run, "progress_json", None) or {}
    return bool(progress.get(PROGRESS_PAUSED_KEY))


def mark_research_paused(
    db: Session, task_id: uuid.UUID, *, detail: str = PAUSED_BY_OWNER_TR
) -> bool:
    """The durable half of a pause: the run keeps its stage and is flagged paused. ONE
    function for the REST route and the voice tool, like ``mark_research_cancelled``.
    Returns False for a terminal run or one already paused."""
    from app.research.models import TERMINAL_STAGES

    run = runs_service.get_run(db, task_id)
    if run is None or run.stage in TERMINAL_STAGES or research_is_paused(run):
        return False
    now = datetime.now(UTC).isoformat()
    runs_service.update_run(
        db,
        task_id,
        progress={PROGRESS_PAUSED_KEY: True, PROGRESS_PAUSED_AT_KEY: now},
        event={"stage": run.stage, "detail": detail[:500], PROGRESS_PAUSED_KEY: True},
    )
    logger.info("research_paused", task_id=str(task_id), stage=run.stage)
    return True


def mark_research_resumed(
    db: Session, task_id: uuid.UUID, *, detail: str = RESUMED_BY_OWNER_TR
) -> bool:
    """The durable half of a resume; the paused seconds are kept so the report's elapsed
    time can be honest about what the run itself spent. False unless the run is paused."""
    from app.research.models import TERMINAL_STAGES

    run = runs_service.get_run(db, task_id)
    if run is None or run.stage in TERMINAL_STAGES or not research_is_paused(run):
        return False
    progress = dict(run.progress_json or {})
    paused_seconds = float(progress.get(PROGRESS_PAUSED_SECONDS_KEY) or 0.0)
    paused_at = progress.get(PROGRESS_PAUSED_AT_KEY)
    if isinstance(paused_at, str):
        try:
            started = datetime.fromisoformat(paused_at)
            paused_seconds += max(0.0, (datetime.now(UTC) - started).total_seconds())
        except ValueError:
            pass
    runs_service.update_run(
        db,
        task_id,
        progress={
            PROGRESS_PAUSED_KEY: False,
            PROGRESS_PAUSED_AT_KEY: None,
            PROGRESS_PAUSED_SECONDS_KEY: round(paused_seconds, 3),
        },
        event={"stage": run.stage, "detail": detail[:500], PROGRESS_PAUSED_KEY: False},
    )
    logger.info("research_resumed", task_id=str(task_id), stage=run.stage)
    return True


async def connect_temporal(artifacts: ArtifactRuntime) -> Client:
    """The same connection recipe ``app.research.routes`` has always used, factored
    out so the voice follow-up (a different call site, same settings) does not
    duplicate it."""
    settings = artifacts.settings
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


__all__ = [
    "CANCELLED_BY_OWNER_TR",
    "ERROR_RESEARCH_CANCELLED",
    "PAUSED_BY_OWNER_TR",
    "PROGRESS_PAUSED_KEY",
    "RESUMED_BY_OWNER_TR",
    "active_research",
    "mark_research_cancelled",
    "mark_research_paused",
    "mark_research_resumed",
    "research_is_paused",
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
