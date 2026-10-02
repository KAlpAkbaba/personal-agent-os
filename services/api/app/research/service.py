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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.artifacts import service as artifact_service
from app.artifacts.models import TASK_STATUS_CREATED, TASK_STATUS_FAILED_TERMINAL, Task
from app.config import get_settings
from app.devices import service as devices_service
from app.devices.selection import (
    REASON_AUTO,
    REASON_EXPLICIT_ALIAS,
    REASON_SESSION_AFFINITY,
    NoCapableDeviceError,
    SelectionResult,
    named_device_target,
    select_device,
)
from app.execution import wiring
from app.execution.rule import Decision, Target, forced_target_of
from app.logging import get_logger
from app.research import runs_service
from app.research.browser_workflow import BrowserResearchRequest, BrowserResearchWorkflow
from app.research.models import STAGE_FAILED, STAGE_PLANNED
from app.research.target import RESEARCH_OPERATIONS, choose_research_target

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


#: Said beside the device's name when the session's own machine was chosen (ADR-0208).
SESSION_AFFINITY_TR: Final = "Komutu verdiğiniz cihaz seçildi."


#: ...and when the owner NAMED the machine in the sentence (ADR-0212).
NAMED_DEVICE_TR: Final = "Söylediğiniz cihaz seçildi."


def _device_summary(view: Any, reason: str | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {"device_id": str(view.id), "name": view.name}
    if reason:
        # ADR-0208: WHY this machine ("session_affinity" = the one the owner is on), and the
        # same in the owner's language for whatever shows this summary to him.
        summary["reason"] = reason
        summary["reason_tr"] = (
            SESSION_AFFINITY_TR if reason == REASON_SESSION_AFFINITY else NAMED_DEVICE_TR
        )
    return summary


#: The rule chose a target and the registry no longer shows it (it went away between the
#: two reads). Refused rather than quietly run somewhere the rule did not choose.
TARGET_GONE_TR: Final = "Seçilen hedef şu anda çevrimiçi değil."


def _machines(views: Sequence[Any]) -> list[Any]:
    """The views ``select_device`` may choose among: the cloud worker is never the
    ``device`` target (ADR-0213)."""
    return [v for v in views if v.platform != wiring.CLOUD_PLATFORM]


def _select_for(
    decision: Decision, views: list[Any], *, target: str | None, hint: dict[str, Any]
) -> SelectionResult:
    """The device the rule's decision names (ADR-0213). ``cloud`` and ``owner_chrome`` are
    one registry view each; ``device`` is the selection it always was, over the machines."""
    if decision.target is Target.DEVICE:
        return select_device(
            _machines(views), capability=RESEARCH_CAPABILITY, target=target, **hint
        )
    # The same test the rule applied when it called the target available (the operations
    # research sends, advertised and policy-allowed), over the same views: a target the rule
    # selected has a view here. Not ``select_device``'s family marker - the cloud worker
    # advertises its operations by name and no ``browser.chrome``.
    view = wiring.device_for(decision, views, capabilities=RESEARCH_OPERATIONS)
    if view is None:
        raise NoCapableDeviceError(
            TARGET_GONE_TR, capability=RESEARCH_CAPABILITY, target=target, reason="target_gone"
        )
    if decision.forced:
        return SelectionResult(device=view, reason=REASON_EXPLICIT_ALIAS, explicit=True)
    return SelectionResult(device=view, reason=REASON_AUTO, explicit=False)


def _refusal(
    refusal: NoCapableDeviceError,
    views: list[Any],
    *,
    spoken: str | None,
    target: str | None,
    hint: dict[str, Any],
) -> NoCapableDeviceError:
    """What a run the rule refused is reported with. A refusal about MACHINES ("'ofis'
    cihazı şu anda çevrimiçi değil.", "Şu anda çevrimiçi bir cihaz bulunamadı.") keeps the
    sentence the selection has always said for it; "bulutta" keeps the rule's own."""
    if forced_target_of(spoken) is Target.CLOUD:
        return refusal
    try:
        select_device(_machines(views), capability=RESEARCH_CAPABILITY, target=target, **hint)
    except NoCapableDeviceError as precise:
        return precise
    return refusal


def _execution_fields(decision: Decision | None) -> dict[str, Any]:
    if decision is None or decision.target is None:
        return {}
    return {
        "execution_target": decision.target.value,
        "execution_chain": [t.value for t in decision.chain],
        "execution_skipped": [
            {"target": s.target.value, "reason": s.reason} for s in decision.skipped
        ],
    }


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
    session_device_ids: Sequence[uuid.UUID] | None = None,
    named_devices: Sequence[str] = (),
    needs_signed_in_session: bool = False,
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
    decision: Decision | None = None
    refused: str | None = None
    # Off (the default), the device is chosen exactly as it was before the rule had a call
    # site: turning it on waits for the cloud worker's image (``Settings``, the release order).
    rule_enabled = get_settings().research_execution_rule_enabled
    try:
        # The session hint is passed only when there is one: with none this is the call it
        # always was (ADR-0208).
        hint = {"session_device_ids": session_device_ids} if session_device_ids else {}
        # ADR-0212: the device the owner SAID in the sentence ("ofis bilgisayarında ...
        # araştır") is the owner's latest word, ahead of a REST caller's ``target_device``
        # and - inside ``select_device``, which ignores the session hint once a device is
        # named - ahead of the session's own. It refuses rather than falling back.
        target = (
            named_device_target(views, named_devices, RESEARCH_CAPABILITY)
            if named_devices
            else target_device
        )
        if not rule_enabled or (target_device and not named_devices):
            # The selection research always made (the setting off). With it on: a REST
            # caller's own ``target_device`` names a device by id, name or alias - it is
            # that device, and the rule is not asked to overrule it.
            result = select_device(views, capability=RESEARCH_CAPABILITY, target=target, **hint)
        else:
            # ADR-0213: WHERE the run executes is the execution_target rule's answer - cloud
            # first, "bulutta" forces it, a named machine is never the cloud - decided and
            # written to the ledger before any device is picked.
            spoken = named_devices[0] if named_devices else None
            try:
                decision = choose_research_target(
                    db,
                    broker,
                    spoken_target=spoken,
                    needs_signed_in_session=needs_signed_in_session,
                    task_id=task.id,
                    views=views,
                )
            except NoCapableDeviceError as exc:
                refused = exc.reason
                raise _refusal(exc, views, spoken=spoken, target=target, hint=hint) from exc
            result = _select_for(decision, views, target=target, hint=hint)
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
            event={
                "stage": STAGE_FAILED,
                "detail": exc.detail_tr,
                **({"execution_reason": refused} if refused else {}),
                **provenance,
            },
        )
        logger.info("research_no_capable_device", task_id=str(task.id), source=source)
        return StartedResearch(
            task_id=task.id, workflow_id=workflow_id, device=None, error=exc.detail_tr
        )
    # ADR-0208: said only when the session's own device was chosen, so what a research run
    # reported before this change is reported byte for byte the same.
    say_why = result.reason == REASON_SESSION_AFFINITY or bool(named_devices)
    runs_service.update_run(
        db,
        task.id,
        stage=STAGE_PLANNED,
        device_id=result.device.id,
        event={
            "stage": STAGE_PLANNED,
            "detail": f"selected {result.device.name}",
            **({"selection_reason": result.reason} if say_why else {}),
            **_execution_fields(decision),
            **provenance,
        },
    )
    return StartedResearch(
        task_id=task.id,
        workflow_id=workflow_id,
        device=_device_summary(result.device, result.reason if say_why else None),
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
