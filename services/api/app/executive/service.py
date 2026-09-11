"""``ExecutiveService``: the owner-facing surface both REST (``routes.py``) and voice
(``tools_executive.py``) call — never the workflow or the activity directly (the same
"one code path" discipline every M13-M25 family in this codebase follows for its own
service layer).

Split into a synchronous DB half and an asynchronous Temporal half, mirroring
``app.research.service`` exactly (that module's own docstring explains why: a synchronous
voice-tool handler can call the DB half directly inside its own transaction; the async
half is awaited separately once that transaction has committed). ``start_run`` is the one
call that needs BOTH — see :func:`start_run` for how the two are composed.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.executive import activities
from app.executive.graph import GraphValidationError, validate_graph
from app.executive.models import (
    SOURCE_VOICE,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_PARTIAL,
    STATE_PAUSED,
    STATE_PLANNED,
    STATE_RUNNING,
    STEP_STATE_FAILED,
    STEP_STATE_FAILED_RECOVERABLE,
    STEP_STATE_PENDING,
    STEP_STATE_VERIFIED,
    TERMINAL_RUN_STATES,
    ExecutiveRunRow,
    ExecutiveStepRow,
)
from app.executive.planner import PlanningClarificationNeeded, RuleBasedExecutivePlanner
from app.executive.spec import MAX_ACTIVE_RUNS, STEP_KIND_PROFILES, Step, TaskGraph
from app.executive.workflow import ExecutiveRunRequest, ExecutiveWorkflow
from app.logging import get_logger

logger = get_logger("app.executive.service")



class ExecutiveServiceError(Exception):
    """Every refusal this layer produces — ``error_class`` for the REST status code,
    ``speech`` for the honest Turkish sentence (never a guess), the same shape every
    other M19-M25 service's own refusal path already uses."""

    def __init__(self, error_class: str, speech: str) -> None:
        self.error_class = error_class
        self.speech = speech
        super().__init__(speech)



def workflow_id_for(run_id: uuid.UUID) -> str:
    return f"executive-{run_id}"


def _get_run(db: Session, run_id: uuid.UUID) -> ExecutiveRunRow:
    run = db.get(ExecutiveRunRow, run_id)
    if run is None:
        raise ExecutiveServiceError("not_found", "Böyle bir iş bulamadım efendim.")
    return run


def get_step_row(db: Session, run_id: uuid.UUID, step_id: str) -> ExecutiveStepRow | None:
    return db.execute(
        select(ExecutiveStepRow).where(
            ExecutiveStepRow.run_id == run_id, ExecutiveStepRow.step_id == step_id
        )
    ).scalar_one_or_none()


def list_steps(db: Session, run_id: uuid.UUID) -> list[ExecutiveStepRow]:
    return list(
        db.execute(
            select(ExecutiveStepRow)
            .where(ExecutiveStepRow.run_id == run_id)
            .order_by(ExecutiveStepRow.step_id)
        ).scalars()
    )


def _too_many_active_runs() -> ExecutiveServiceError:
    """One refusal, one sentence, raised from both sides of the bound check."""
    return ExecutiveServiceError(
        "too_many_active_runs",
        "Aynı anda en fazla iki iş yürütebilirim efendim; önce birini bitirin ya da iptal edin.",
    )


def _count_active_runs(db: Session) -> int:
    return db.execute(
        select(func.count())
        .select_from(ExecutiveRunRow)
        .where(ExecutiveRunRow.state.notin_(TERMINAL_RUN_STATES))
    ).scalar_one()


# ------------------------------------------------------------------------------ start


def start_run_db(
    db: Session,
    *,
    directive: str,
    folder: str | None = None,
    source: str = SOURCE_VOICE,
    session_id: str | None = None,
) -> ExecutiveRunRow:
    """The synchronous half (module docstring): plans, validates (already inside the
    planner — ADR-0089 decision 1), enforces the <= 2 active runs bound (spec §4), and
    persists the run + every step row as ``pending``. Raises
    :class:`ExecutiveServiceError` for every refusal — a directive matching none of the
    three shapes, or the active-run bound — never starts a workflow for a graph that was
    refused."""
    # Checked twice, and the second one is the one that makes the bound TRUE. This
    # first check only saves the planning work when the answer is already no.
    if _count_active_runs(db) >= MAX_ACTIVE_RUNS:
        raise _too_many_active_runs()
    try:
        graph = RuleBasedExecutivePlanner().plan(directive, folder=folder)
    except PlanningClarificationNeeded as exc:
        raise ExecutiveServiceError("clarification_needed", exc.speech) from exc

    now = datetime.now(UTC)
    run = ExecutiveRunRow(
        id=uuid.uuid4(),
        goal=graph.goal,
        graph_json=graph.model_dump(mode="json"),
        # spec §3's state sequence starts at "planned", but the graph is ALREADY built
        # and validated by the time this row exists (planning is over by construction,
        # unlike research's own device-selection step, which can still fail) — so this
        # row goes straight to "running", the same "the owner is told 'running' before
        # the workflow is confirmed started" choice research_start's own synchronous
        # half already makes for its `plan["status"]`. The Temporal workflow itself is
        # still started only by the async half below/start_run_workflow.
        state=STATE_RUNNING,
        steps_total=len(graph.steps),
        steps_done=0,
        source=source,
        session_id=session_id,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    db.flush()
    # THE CHECK THAT COUNTS. Before the M26 security review there was only the one above,
    # and between it and this insert nothing held the count still: the reviewer ran two
    # threads on their own connections, both saw one active run, both inserted, and the
    # database ended with three against a bound of two. Counting again now that this
    # transaction's own row is flushed closes it on both engines — SQLite serializes write
    # transactions, and on Postgres each concurrent inserter counts its own row plus every
    # committed one, so the loser sees the bound broken and refuses rather than committing.
    if _count_active_runs(db) > MAX_ACTIVE_RUNS:
        db.rollback()
        raise _too_many_active_runs()
    for step in graph.steps:
        db.add(
            ExecutiveStepRow(
                id=uuid.uuid4(),
                run_id=run.id,
                step_id=step.id,
                kind=step.kind,
                inputs_json=dict(step.inputs),
                precondition_json=step.precondition.model_dump(),
                postcondition_json=step.postcondition.model_dump(),
                retry_json=step.retry.model_dump(),
                timeout_s=step.timeout_s,
                risk_class=step.risk_class,
                compensation=step.compensation,
                state=STEP_STATE_PENDING,
                attempt=0,
                created_at=now,
                updated_at=now,
            )
        )
    db.commit()
    activities.publish_run_state(run)
    return run


async def start_run_workflow(
    client: Client, run: ExecutiveRunRow, *, task_queue: str, artifacts: Any
) -> None:
    """The asynchronous half (module docstring): start the durable workflow and mark the
    run ``running``. ``WorkflowAlreadyStartedError`` is swallowed exactly as
    ``app.research.service.start_browser_research_workflow`` swallows it — an idempotent
    retry of the same run_id-derived workflow id.

    ``artifacts`` is the caller's OWN ``ArtifactRuntime`` (``request.app.state.artifacts``
    for REST, ``ctx.live["artifacts_runtime"]`` for the voice followup) — never a second
    engine built from global settings here: a unit test's harness binds ``artifacts`` to
    an in-memory SQLite engine, and a second, independently-built engine would instead
    reach for the real database url in ``Settings`` (found via the REST route's own unit
    tests: ``start_run`` failed hard against a real Postgres host that unit tests must
    never touch)."""
    workflow_id = workflow_id_for(run.id)
    request = ExecutiveRunRequest(run_id=str(run.id), graph_json=run.graph_json)
    try:
        await client.start_workflow(
            ExecutiveWorkflow.run, request, id=workflow_id, task_queue=task_queue
        )
    except WorkflowAlreadyStartedError:
        pass

    def persist() -> None:
        with artifacts.session() as db:
            fresh = db.get(ExecutiveRunRow, run.id)
            if fresh is None:
                return
            fresh.workflow_id = workflow_id
            if fresh.state == STATE_PLANNED:
                fresh.state = STATE_RUNNING
            fresh.updated_at = datetime.now(UTC)
            db.commit()
            activities.publish_run_state(fresh)

    await asyncio.to_thread(persist)


#: Said when the durable-workflow service (Temporal) does not answer for an executive run.
WORKFLOW_UNAVAILABLE_TR = "İş akışı servisine ulaşamadım efendim; iş başlamadı."
ERROR_WORKFLOW_START_FAILED = "workflow_start_failed"


def fail_unstarted_run(db: Session, run_id: uuid.UUID, *, detail: str) -> bool:
    """Close a run whose workflow never started (Phase 8, 2026-09-11).

    ``start_run_db`` records the run ``running`` before the workflow starts (its own comment
    says why), and ``start_run_workflow`` writes ``workflow_id`` only once the start
    succeeded - so a run with no workflow id is exactly a run whose workflow never started.
    When the start failed, the voice path only logged it - its own comment said the failure
    was "reported via the run row", and nothing wrote the row - so the run stayed
    ``running`` for ever. It counts as ACTIVE against the two-run bound
    (``_count_active_runs``): two Temporal outages and the owner could never start another
    run, told only that two were already going. Returns whether it closed the run.
    """
    run = db.get(ExecutiveRunRow, run_id)
    if run is None or run.workflow_id is not None or run.state in TERMINAL_RUN_STATES:
        return False
    run.state = STATE_FAILED
    run.error_class = ERROR_WORKFLOW_START_FAILED
    run.error_message = detail[:1000]
    run.updated_at = datetime.now(UTC)
    db.commit()
    activities.publish_run_state(run)
    logger.info("executive_unstarted_run_closed", run_id=str(run_id))
    return True


async def start_run(
    client: Client,
    db: Session,
    *,
    directive: str,
    folder: str | None = None,
    source: str = SOURCE_VOICE,
    session_id: str | None = None,
    task_queue: str,
    artifacts: Any,
) -> ExecutiveRunRow:
    """Convenience composition of both halves, for a caller (REST) that is already
    async end to end. The voice tool layer calls the two halves separately (module
    docstring) because its own handler is synchronous."""
    run = start_run_db(db, directive=directive, folder=folder, source=source, session_id=session_id)
    await start_run_workflow(client, run, task_queue=task_queue, artifacts=artifacts)
    db.refresh(run)
    return run


# ----------------------------------------------------------------------------- status


def _status_speech(run: ExecutiveRunRow) -> str:
    """The receipt sentences spec §5 names, verbatim in shape: what is done, what is
    left, and (partial) what is missing and why — read back from the ROWS, never a
    template that could disagree with what actually happened."""
    if run.state == STATE_PLANNED:
        return "Planı hazırladım, başlıyorum efendim."
    if run.state == STATE_RUNNING:
        return f"Çalışıyorum efendim: {run.steps_done}/{run.steps_total} adım tamam."
    if run.state == STATE_PAUSED:
        left = max(run.steps_total - run.steps_done, 0)
        return f"Duraklatıldı efendim: {run.steps_done} adım tamam, {left} bekliyor."
    if run.state == STATE_COMPLETED:
        tail = f" {run.synthesis_text}" if run.synthesis_text else ""
        return f"Tamamlandı efendim.{tail}".strip()
    if run.state == STATE_PARTIAL:
        reasons = run.partial_reasons_json or {}
        detail = "; ".join(f"{step_id} — {why}" for step_id, why in reasons.items())
        tail = f" {run.synthesis_text}" if run.synthesis_text else ""
        return f"Kısmen bitti efendim: {detail}.{tail}".strip()
    if run.state == STATE_CANCELLED:
        return f"İptal edildi efendim: {run.steps_done} adım tamam, geri kalanı durduruldu."
    if run.state == STATE_FAILED:
        return f"Başarısız oldu efendim: {run.error_message or 'bilinmeyen bir hata oluştu'}."
    return "Durumu bilmiyorum efendim."


def missing_steps(run: ExecutiveRunRow) -> list[dict[str, str | None]]:
    """The steps that did not verify, and why — the thing a `partial` run must be able to
    NAME rather than summarise (spec §6). The row has held this since the workflow wrote
    it; it was simply never sent to anyone."""
    reasons = run.partial_reasons_json or {}
    return [{"step": step_id, "reason": why} for step_id, why in reasons.items()]


def run_dict(run: ExecutiveRunRow) -> dict[str, Any]:
    """The ONE shape every client reads: the list route, the detail route and the Cockpit
    panel. The field names are the bus's own words (`run_id`, `step`, `done`, `total`), so
    one fact has one name wherever it is read.

    They did not, at first. The list route sent `id`/`current_step`/`steps_done`/
    `steps_total` while the detail route sent `run_id` for the same field, and the panel
    absorbed both with a `??` fallback chain — which is how M25's `/v1/scenes` row and its
    panel ended up in different languages while every suite stayed green. A cushion is not
    an agreement; `test_executive_row_shape.py` holds this to the panel's own source now.
    """
    return {
        "run_id": str(run.id),
        "goal": run.goal,
        "state": run.state,
        "step": run.current_step,
        "done": run.steps_done,
        "total": run.steps_total,
        "missing": missing_steps(run),
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
    }


def get_status(db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    run = _get_run(db, run_id)
    return {**run_dict(run), "speech": _status_speech(run)}


def get_explain(db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    """ "Şu an tam olarak ne yapıyorsun?" (spec §5): the CURRENT step, one sentence, what
    it is doing and what it waits for — never the ledger's whole history."""
    run = _get_run(db, run_id)
    if run.current_step is None:
        return {"run_id": str(run.id), "speech": _status_speech(run)}
    step = get_step_row(db, run_id, run.current_step)
    if step is None:
        return {"run_id": str(run.id), "speech": _status_speech(run)}
    profile = STEP_KIND_PROFILES.get(step.kind)
    description = profile.description if profile is not None else step.kind
    waits_for = ""
    precondition = step.precondition_json or {}
    if precondition.get("check") == "step_done" and precondition.get("arg"):
        waits_for = f" ({precondition['arg']} adımının bitmesini bekliyordum)"
    return {
        "run_id": str(run.id),
        "step_id": step.step_id,
        "speech": f"Şu an {description} işini yapıyorum efendim{waits_for}.",
    }


def list_runs(db: Session, *, limit: int = 50) -> list[ExecutiveRunRow]:
    return list(
        db.execute(
            select(ExecutiveRunRow).order_by(ExecutiveRunRow.created_at.desc()).limit(limit)
        ).scalars()
    )


# ------------------------------------------------------------------- signal-driven ops
#
# Each op splits sync (validate + DB transition) / async (send the Temporal signal) —
# the SAME split ``start_run_db``/``start_run_workflow`` use (module docstring), because
# a sync voice-tool handler needs to call the DB half directly inside its own
# transaction and defer the signal to ``ctx.followups`` (it cannot await anything
# itself); REST is async end to end and calls the combined convenience function. The DB
# half is authoritative for what the OWNER is told ("Duraklatıyorum efendim.") — the
# signal is best-effort delivery to the workflow that is actually running the steps,
# not a precondition for the owner's own record of having asked.


async def _signal(client: Client, run_id: uuid.UUID, method: Any, *args: Any) -> None:
    handle = client.get_workflow_handle(workflow_id_for(run_id))
    await handle.signal(method, *args)


def pause_run_db(db: Session, run_id: uuid.UUID) -> ExecutiveRunRow:
    run = _get_run(db, run_id)
    if run.state != STATE_RUNNING:
        raise ExecutiveServiceError(
            "invalid_state", "Bu iş şu anda çalışmıyor, duraklatacak bir şey yok efendim."
        )
    run.state = STATE_PAUSED
    run.updated_at = datetime.now(UTC)
    db.commit()
    activities.publish_run_state(run)
    return run


async def pause_run_signal(client: Client, run_id: uuid.UUID) -> None:
    await _signal(client, run_id, ExecutiveWorkflow.pause)


async def pause_run(client: Client, db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    run = pause_run_db(db, run_id)
    await pause_run_signal(client, run_id)
    return {"run_id": str(run.id), "speech": _status_speech(run)}


def resume_run_db(db: Session, run_id: uuid.UUID) -> ExecutiveRunRow:
    run = _get_run(db, run_id)
    if run.state != STATE_PAUSED:
        raise ExecutiveServiceError(
            "invalid_state", "Duraklatılmış bir iş yok, devam ettirecek bir şey yok efendim."
        )
    run.state = STATE_RUNNING
    run.updated_at = datetime.now(UTC)
    db.commit()
    activities.publish_run_state(run)
    return run


async def resume_run_signal(client: Client, run_id: uuid.UUID) -> None:
    await _signal(client, run_id, ExecutiveWorkflow.resume)


async def resume_run(client: Client, db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    run = resume_run_db(db, run_id)
    await resume_run_signal(client, run_id)
    return {"run_id": str(run.id), "speech": _status_speech(run)}


def cancel_run_validate(db: Session, run_id: uuid.UUID) -> ExecutiveRunRow:
    """Only the PRECONDITION check — the actual cancel (signal + the DB sweep +
    compensations) is ``activities.cancel_run_and_compensate``, idempotent and safe to
    call from a followup regardless of whether the signal itself lands in time."""
    run = _get_run(db, run_id)
    if run.state in TERMINAL_RUN_STATES:
        raise ExecutiveServiceError("invalid_state", "Bu iş zaten bitmiş efendim.")
    return run


async def cancel_run_signal_and_sweep(client: Client, run_id: uuid.UUID) -> None:
    try:
        await _signal(client, run_id, ExecutiveWorkflow.cancel)
    except Exception:  # noqa: BLE001 - the DB sweep below is authoritative regardless
        pass
    await asyncio.to_thread(activities.cancel_run_and_compensate, run_id)


async def cancel_run(client: Client, db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    cancel_run_validate(db, run_id)
    await cancel_run_signal_and_sweep(client, run_id)
    db.expire_all()
    fresh = _get_run(db, run_id)
    return {"run_id": str(fresh.id), "speech": _status_speech(fresh)}


def retry_step_validate(db: Session, run_id: uuid.UUID, step_id: str) -> ExecutiveStepRow:
    run = _get_run(db, run_id)
    if run.state == STATE_CANCELLED:
        raise ExecutiveServiceError(
            "invalid_state", "Bu iş iptal edildi, adım tekrar denenemez efendim."
        )
    step = get_step_row(db, run_id, step_id)
    if step is None:
        raise ExecutiveServiceError("not_found", "Böyle bir adım bulamadım efendim.")
    if step.state not in (STEP_STATE_FAILED, STEP_STATE_FAILED_RECOVERABLE):
        # Honest per actual state (found via the corpus: a PENDING step — one that
        # simply has not run yet — was answered "zaten başarılı" (already succeeded),
        # which is false; only a genuinely VERIFIED step earns that sentence).
        if step.state == STEP_STATE_VERIFIED:
            speech = "Bu adım zaten başarılı, tekrar denemeye gerek yok efendim."
        else:
            speech = (
                f"Bu adım henüz başarısız olmadı ({step.state}), tekrar denemeye gerek yok efendim."
            )
        raise ExecutiveServiceError("invalid_state", speech)
    return step


async def retry_step_signal(client: Client, run_id: uuid.UUID, step_id: str) -> None:
    await _signal(client, run_id, ExecutiveWorkflow.retry_step, step_id)


async def retry_step(
    client: Client, db: Session, run_id: uuid.UUID, step_id: str
) -> dict[str, Any]:
    retry_step_validate(db, run_id, step_id)
    await retry_step_signal(client, run_id, step_id)
    return {
        "run_id": str(run_id),
        "step_id": step_id,
        "speech": f"{step_id} adımını tekrar deniyorum efendim.",
    }


def amend_run_db(db: Session, run_id: uuid.UUID, new_step: dict[str, Any]) -> Step:
    run = _get_run(db, run_id)
    if run.state in (STATE_CANCELLED, STATE_FAILED):
        raise ExecutiveServiceError(
            "invalid_state", "Bu işe artık ekleme yapamam efendim; iş bitmiş durumda."
        )
    graph = TaskGraph.model_validate(run.graph_json)
    try:
        candidate = Step.model_validate(new_step)
    except ValidationError as exc:
        raise ExecutiveServiceError("invalid_step", "Bu eklemeyi anlayamadım efendim.") from exc
    if candidate.id in {s.id for s in graph.steps}:
        raise ExecutiveServiceError("invalid_step", "Bu adım zaten var efendim.")
    amended = TaskGraph(goal=graph.goal, steps=[*graph.steps, candidate])
    try:
        validate_graph(amended)
    except GraphValidationError as exc:
        raise ExecutiveServiceError(
            "invalid_step", f"Bu eklemeyi mevcut işe uygulayamadım efendim: {exc}"
        ) from exc

    now = datetime.now(UTC)
    db.add(
        ExecutiveStepRow(
            id=uuid.uuid4(),
            run_id=run.id,
            step_id=candidate.id,
            kind=candidate.kind,
            inputs_json=dict(candidate.inputs),
            precondition_json=candidate.precondition.model_dump(),
            postcondition_json=candidate.postcondition.model_dump(),
            retry_json=candidate.retry.model_dump(),
            timeout_s=candidate.timeout_s,
            risk_class=candidate.risk_class,
            compensation=candidate.compensation,
            state=STEP_STATE_PENDING,
            attempt=0,
            created_at=now,
            updated_at=now,
        )
    )
    run.graph_json = amended.model_dump(mode="json")
    run.steps_total += 1
    run.updated_at = now
    db.commit()
    activities.publish_run_state(run)
    return candidate


async def amend_run_signal(client: Client, run_id: uuid.UUID, candidate: Step) -> None:
    await _signal(client, run_id, ExecutiveWorkflow.amend, candidate.model_dump(mode="json"))


async def amend_run(
    client: Client, db: Session, run_id: uuid.UUID, new_step: dict[str, Any]
) -> dict[str, Any]:
    candidate = amend_run_db(db, run_id, new_step)
    await amend_run_signal(client, run_id, candidate)
    return {
        "run_id": str(run_id),
        "step_id": candidate.id,
        "speech": f"{candidate.id} adımını ekledim efendim.",
    }


__all__ = [
    "ExecutiveServiceError",
    "amend_run",
    "amend_run_db",
    "amend_run_signal",
    "cancel_run",
    "cancel_run_signal_and_sweep",
    "cancel_run_validate",
    "get_explain",
    "get_status",
    "get_step_row",
    "list_runs",
    "list_steps",
    "pause_run",
    "pause_run_db",
    "pause_run_signal",
    "resume_run",
    "resume_run_db",
    "resume_run_signal",
    "retry_step",
    "retry_step_signal",
    "retry_step_validate",
    "start_run",
    "start_run_db",
    "start_run_workflow",
    "workflow_id_for",
]
