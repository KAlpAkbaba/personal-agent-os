"""``MissionService``: the owner-facing surface both REST and voice call for operator
missions (B39 req 128-130) - never the workflow or the loop directly.

Split into a synchronous DB half and an asynchronous Temporal half exactly as
``app.executive.service`` is, and for the same reason: a synchronous voice-tool handler
starts the row inside its own transaction and hands the workflow start to a followup.

The row is the truth. ``run_step_db`` (called by the workflow's activity, or inline by a
test) loads the mission, runs ONE step's closed loop over the real ports, and writes the
mission back - including a pause or cancel the owner asked for between rounds, which the
activity reads from the row before every round through ``MissionPorts``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_OPERATOR_MISSION_ESCALATED,
    EVENT_TYPE_OPERATOR_MISSION_FINISHED,
    EVENT_TYPE_OPERATOR_MISSION_STARTED,
    SUBSYSTEM_OPERATOR,
)
from app.logging import get_logger
from app.operator import mission as mission_module
from app.operator.capabilities import CAPABILITY_MISSION
from app.operator.mission import (
    MISSION_AWAITING_APPROVAL,
    MISSION_CANCELLED,
    MISSION_FAILED,
    MISSION_PAUSED,
    MISSION_PLANNED,
    MISSION_RUNNING,
    MISSION_SUCCEEDED,
    TERMINAL_MISSION_STATUSES,
    Mission,
    MissionClarificationNeeded,
    MissionPorts,
    plan_mission,
)
from app.operator.mission_models import SOURCE_VOICE, OperatorMissionRow
from app.uistate import UiState
from app.uistate import publish as publish_ui_state

logger = get_logger("app.operator.mission_service")

WORKFLOW_UNAVAILABLE_TR = "İş akışı altyapısına şu an ulaşamıyorum efendim; görevi başlatamadım."
ACTIVE_MISSION_STATUSES = frozenset(
    {MISSION_PLANNED, MISSION_AWAITING_APPROVAL, MISSION_RUNNING, MISSION_PAUSED}
)


class MissionServiceError(Exception):
    def __init__(self, error_class: str, speech: str) -> None:
        self.error_class = error_class
        self.speech = speech
        super().__init__(speech)


def workflow_id_for(mission_id: uuid.UUID) -> str:
    return f"operator-mission-{mission_id}"


def _now() -> datetime:
    return datetime.now(UTC)


def _get(db: Session, mission_id: uuid.UUID) -> OperatorMissionRow:
    row = db.get(OperatorMissionRow, mission_id)
    if row is None:
        raise MissionServiceError("not_found", "Böyle bir görev bulamadım efendim.")
    return row


def _write(row: OperatorMissionRow, mission: Mission) -> None:
    row.status = mission.status
    row.approved = mission.approved
    row.current_step = mission.current_step
    row.step_count = len(mission.steps)
    row.error_class = mission.error_class[:64]
    row.message = mission.message[:600]
    row.mission_json = mission.as_dict()
    row.started_at = mission.started_at
    row.completed_at = mission.completed_at
    row.updated_at = _now()


def load(row: OperatorMissionRow) -> Mission:
    mission = Mission.from_dict(row.mission_json)
    mission.pause_requested = bool(row.pause_requested)
    mission.cancel_requested = bool(row.cancel_requested)
    return mission


def mission_dict(row: OperatorMissionRow) -> dict[str, Any]:
    return {
        **row.mission_json,
        "status": row.status,
        "source": row.source,
        "session_id": row.session_id,
        "pause_requested": bool(row.pause_requested),
        "cancel_requested": bool(row.cancel_requested),
    }


# ------------------------------------------------------------------ DB half


def active_mission(db: Session) -> OperatorMissionRow | None:
    """The one mission in flight (planned, awaiting the owner, running or paused) - a
    single-owner desktop runs one mission at a time."""
    return (
        db.execute(
            select(OperatorMissionRow)
            .where(OperatorMissionRow.status.in_(sorted(ACTIVE_MISSION_STATUSES)))
            .order_by(OperatorMissionRow.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def start_mission_db(
    db: Session,
    *,
    text: str,
    preview: bool | None = None,
    source: str = SOURCE_VOICE,
    session_id: str | None = None,
) -> OperatorMissionRow:
    """Plan the owner's sentence and persist the mission (planned, or awaiting approval
    when they asked to see the plan first). Refuses a second mission while one is in
    flight - honestly, naming it."""
    running = active_mission(db)
    if running is not None:
        raise MissionServiceError(
            "mission_in_flight",
            f"Şu an başka bir görev üzerindeyim efendim ({running.goal[:80]}); "
            "önce onu bitireyim ya da iptal edin.",
        )
    try:
        mission = plan_mission(text)
    except MissionClarificationNeeded as exc:
        raise MissionServiceError("clarification_needed", exc.speech) from exc
    if preview is not None:
        mission.preview = bool(preview)
    if mission.preview:
        mission.status = MISSION_AWAITING_APPROVAL
    now = _now()
    row = OperatorMissionRow(
        id=mission.id,
        goal=mission.goal,
        status=mission.status,
        preview=mission.preview,
        approved=False,
        current_step=0,
        step_count=len(mission.steps),
        source=source,
        session_id=session_id,
        mission_json=mission.as_dict(),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    _ledger(
        db,
        mission,
        EVENT_TYPE_OPERATOR_MISSION_STARTED,
        f"görev planlandı: {mission.goal[:80]}",
        {"steps": [s.label_tr for s in mission.steps], "preview": mission.preview},
    )
    return row


def _ledger(
    db: Session, mission: Mission, event_type: str, summary: str, detail: dict[str, Any]
) -> None:
    """One ``operator.mission.*`` row per lifecycle transition, under the mission tool's
    capability - the same discipline ``OperatorService._ledger`` follows for a task."""
    try:
        ledger_service.record(
            db,
            ledger_service.ActivityEvent(
                event_type=event_type,
                subsystem=SUBSYSTEM_OPERATOR,
                action=CAPABILITY_MISSION,
                factual_summary=f"{CAPABILITY_MISSION} -> {summary}",
                occurred_at=_now(),
                detail_json={"mission_id": str(mission.id), **detail},
                source="live",
                source_ref=f"operator_mission:{mission.id}:{event_type}:{mission.current_step}:{mission.status}",
            ),
        )
    except Exception:  # noqa: BLE001 - evidence, never a dependency of the action
        logger.warning(
            "operator_mission_ledger_failed", mission_id=str(mission.id), event_type=event_type
        )


def approve_db(db: Session, mission_id: uuid.UUID) -> OperatorMissionRow:
    row = _get(db, mission_id)
    if row.status != MISSION_AWAITING_APPROVAL:
        raise MissionServiceError("not_awaiting", "Bu görev onayınızı beklemiyor efendim.")
    mission = load(row)
    mission.approved = True
    mission.status = MISSION_PLANNED
    _write(row, mission)
    db.commit()
    return row


def pause_db(db: Session, mission_id: uuid.UUID) -> OperatorMissionRow:
    row = _get(db, mission_id)
    if row.status not in (MISSION_RUNNING, MISSION_PLANNED):
        raise MissionServiceError("not_running", "Duraklatacak bir görev yürümüyor efendim.")
    row.pause_requested = True
    row.updated_at = _now()
    db.commit()
    return row


def resume_db(db: Session, mission_id: uuid.UUID) -> OperatorMissionRow:
    row = _get(db, mission_id)
    if row.status not in (MISSION_PAUSED, MISSION_AWAITING_APPROVAL):
        raise MissionServiceError(
            "not_paused", "Devam ettirecek duraklatılmış bir görev yok efendim."
        )
    mission = load(row)
    mission_module.resume(mission)
    row.pause_requested = False
    _write(row, mission)
    db.commit()
    return row


def cancel_db(db: Session, mission_id: uuid.UUID) -> OperatorMissionRow:
    row = _get(db, mission_id)
    if row.status in TERMINAL_MISSION_STATUSES:
        raise MissionServiceError("already_finished", "Bu görev zaten bitmiş efendim.")
    mission = load(row)
    if row.status in (MISSION_AWAITING_APPROVAL, MISSION_PAUSED, MISSION_PLANNED):
        # Nothing is between rounds: the cancel is final here, no activity will read it.
        mission.status = MISSION_CANCELLED
        mission.error_class = "cancelled"
        mission.message = "iptal edildi"
        mission.completed_at = _now()
        _write(row, mission)
        _finished_event(db, row, mission)
    row.cancel_requested = True
    row.updated_at = _now()
    db.commit()
    return row


def _outcome(mission: Mission) -> dict[str, Any]:
    return {
        "status": mission.status,
        "current_step": mission.current_step,
        "step_count": len(mission.steps),
    }


def run_step_db(
    db: Session,
    mission_id: uuid.UUID,
    ports: MissionPorts,
    *,
    on_task: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """The NEXT step's closed loop over the real ports: the row read before, written
    after. A step that verified advances ``current_step``; the last one ends the mission.
    Called by the workflow's activity and, inline, by tests and by the corpus harness."""
    row = _get(db, mission_id)
    mission = load(row)
    if mission.status in TERMINAL_MISSION_STATUSES:
        return _outcome(mission)
    if mission.cancel_requested:
        mission.status = MISSION_CANCELLED
        mission.error_class = "cancelled"
        mission.message = "iptal edildi"
        mission.completed_at = _now()
        _write(row, mission)
        db.commit()
        _finished_event(db, row, mission)
        return _outcome(mission)
    if mission.preview and not mission.approved:
        mission.status = MISSION_AWAITING_APPROVAL
        _write(row, mission)
        db.commit()
        return _outcome(mission)
    if mission.status == MISSION_PAUSED:
        return _outcome(mission)
    if mission.pause_requested:
        mission.status = MISSION_PAUSED
        mission.pause_requested = False
        row.pause_requested = False
        _write(row, mission)
        db.commit()
        return _outcome(mission)
    index = mission.current_step
    if index >= len(mission.steps):
        mission.status = MISSION_SUCCEEDED
        mission.completed_at = _now()
        _write(row, mission)
        db.commit()
        _finished_event(db, row, mission)
        return _outcome(mission)

    publish_ui_state(
        UiState.OPERATOR_RUNNING,
        subsystem=SUBSYSTEM_OPERATOR,
        task_id=str(mission.id),
        label=mission.steps[index].label_tr[:64],
        metadata={"step": index, "step_count": len(mission.steps), "mission": True},
    )
    mission_module.run_mission_step(mission, index, ports, on_task=on_task)
    if mission.status == MISSION_RUNNING:
        mission.current_step = index + 1
        if mission.current_step >= len(mission.steps):
            mission.status = MISSION_SUCCEEDED
            mission.completed_at = _now()
    _write(row, mission)
    db.commit()
    if mission.status == MISSION_PAUSED and mission.escalation:
        _ledger(
            db,
            mission,
            EVENT_TYPE_OPERATOR_MISSION_ESCALATED,
            f"sahibe döndü: {mission.escalation.get('speech', '')[:120]}",
            dict(mission.escalation),
        )
        publish_ui_state(
            UiState.OPERATOR_FAILED,
            subsystem=SUBSYSTEM_OPERATOR,
            task_id=str(mission.id),
            label=mission.steps[index].label_tr[:64],
            severity="warning",
            metadata={
                "error_class": (mission.escalation.get("error_class") or "")[:64],
                "escalated": True,
            },
        )
    elif mission.status in TERMINAL_MISSION_STATUSES:
        _finished_event(db, row, mission)
    return _outcome(mission)


def run_inline(db: Session, mission_id: uuid.UUID, ports: MissionPorts) -> OperatorMissionRow:
    """Every step in order, synchronously (tests, the corpus harness, and a host with
    no Temporal that still wants to drive one - the flag is the caller's). Stops where
    the loop stops: the owner's approval, a pause, an escalation, the end."""
    row = _get(db, mission_id)
    for _ in range(len(load(row).steps) + 1):
        outcome = run_step_db(db, mission_id, ports)
        if outcome["status"] != MISSION_RUNNING:
            break
    db.refresh(row)
    return row


def _finished_event(db: Session, row: OperatorMissionRow, mission: Mission) -> None:
    del row
    _ledger(
        db,
        mission,
        EVENT_TYPE_OPERATOR_MISSION_FINISHED,
        f"{mission.status}: {mission.goal[:60]}",
        {
            "status": mission.status,
            "error_class": mission.error_class,
            "rounds": sum(s.rounds for s in mission.steps),
            "levels": sorted({s.level for s in mission.steps if s.level}),
        },
    )
    if mission.status != MISSION_SUCCEEDED:
        publish_ui_state(
            UiState.OPERATOR_FAILED,
            subsystem=SUBSYSTEM_OPERATOR,
            task_id=str(mission.id),
            label=mission.goal[:64],
            severity="warning" if mission.status == MISSION_FAILED else "info",
            metadata={"error_class": mission.error_class[:64]},
        )


def list_missions(db: Session, *, limit: int = 50) -> list[OperatorMissionRow]:
    return list(
        db.execute(
            select(OperatorMissionRow).order_by(OperatorMissionRow.created_at.desc()).limit(limit)
        ).scalars()
    )


def get_mission(db: Session, mission_id: uuid.UUID) -> OperatorMissionRow:
    return _get(db, mission_id)


def fail_unstarted(db: Session, mission_id: uuid.UUID, *, detail: str) -> None:
    row = db.get(OperatorMissionRow, mission_id)
    if row is None or row.status in TERMINAL_MISSION_STATUSES:
        return
    mission = load(row)
    mission.status = MISSION_FAILED
    mission.error_class = "dependency_unavailable"
    mission.message = f"iş akışı başlatılamadı: {detail[:200]}"
    mission.completed_at = _now()
    _write(row, mission)
    db.commit()
    _finished_event(db, row, mission)


# ------------------------------------------------------------- Temporal half


async def start_mission_workflow(client: Client, mission_id: uuid.UUID, *, task_queue: str) -> None:
    from app.operator.mission_workflow import MissionRequest, OperatorMissionWorkflow

    try:
        await client.start_workflow(
            OperatorMissionWorkflow.run,
            MissionRequest(mission_id=str(mission_id)),
            id=workflow_id_for(mission_id),
            task_queue=task_queue,
        )
    except WorkflowAlreadyStartedError:
        logger.info("operator_mission_workflow_already_started", mission_id=str(mission_id))


async def _signal(client: Client, mission_id: uuid.UUID, signal: Any, *args: Any) -> None:
    handle = client.get_workflow_handle(workflow_id_for(mission_id))
    await handle.signal(signal, *args)


async def approve_signal(client: Client, mission_id: uuid.UUID) -> None:
    from app.operator.mission_workflow import OperatorMissionWorkflow

    await _signal(client, mission_id, OperatorMissionWorkflow.approve)


async def resume_signal(client: Client, mission_id: uuid.UUID) -> None:
    from app.operator.mission_workflow import OperatorMissionWorkflow

    await _signal(client, mission_id, OperatorMissionWorkflow.resume)


async def cancel_signal(client: Client, mission_id: uuid.UUID) -> None:
    from app.operator.mission_workflow import OperatorMissionWorkflow

    await _signal(client, mission_id, OperatorMissionWorkflow.cancel)


async def pause_signal(client: Client, mission_id: uuid.UUID) -> None:
    from app.operator.mission_workflow import OperatorMissionWorkflow

    await _signal(client, mission_id, OperatorMissionWorkflow.pause)


__all__ = [
    "ACTIVE_MISSION_STATUSES",
    "WORKFLOW_UNAVAILABLE_TR",
    "MissionServiceError",
    "active_mission",
    "approve_db",
    "approve_signal",
    "cancel_db",
    "cancel_signal",
    "fail_unstarted",
    "get_mission",
    "list_missions",
    "load",
    "mission_dict",
    "pause_db",
    "pause_signal",
    "resume_db",
    "resume_signal",
    "run_inline",
    "run_step_db",
    "start_mission_db",
    "start_mission_workflow",
    "workflow_id_for",
]
