"""Routine Engine service (M18).

Same discipline as ``app.goals.service`` / ``app.ledger.service``: every function takes an
open ``Session`` and owns its commit, so async routes run it via ``asyncio.to_thread`` and
nothing here is coupled to FastAPI.

``evaluate_due`` is the ONE explicit entry point that decides whether an armed routine
fires (task brief: "expose evaluation as an explicit call" — nothing in this package starts
a background timer). It is safe to call as often as a caller likes: idempotency is enforced
twice over — ``RoutineFiring`` has a database uniqueness constraint on
``(routine_id, occurrence_key)``, and this module checks for an existing firing before doing
any conditions/actions work at all, so a second call for the same occurrence is a cheap
no-op rather than a second ledger event or a second dispatch.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_ROUTINE_ARMED,
    EVENT_TYPE_ROUTINE_CANCELLED,
    EVENT_TYPE_ROUTINE_CREATED,
    EVENT_TYPE_ROUTINE_EXECUTED,
    EVENT_TYPE_ROUTINE_SKIPPED,
    EVENT_TYPE_ROUTINE_TRIGGERED,
    SUBSYSTEM_ROUTINE,
)
from app.logging import get_logger
from app.routines import conditions as conditions_mod
from app.routines import triggers as triggers_mod
from app.routines.actions import (
    ACTION_KIND_ALARM,
    DispatchOutcome,
    NoopDispatcher,
    RoutineDispatcher,
    validate_action,
)
from app.routines.conditions import RoutineConditionContext
from app.routines.models import (
    FIRING_STATUS_SKIPPED,
    FIRING_STATUS_TRIGGERED,
    ROUTINE_STATUS_ARMED,
    ROUTINE_STATUS_CANCELLED,
    ROUTINE_STATUS_COMPLETED,
    TRIGGER_KIND_AT,
    TRIGGER_KIND_PRESENCE,
    TRIGGER_KIND_SCHEDULE,
    TRIGGER_KINDS,
    Routine,
    RoutineFiring,
)
from app.routines.state import assert_routine_transition
from app.uistate.contract import UiState
from app.uistate.publisher import get_publisher

logger = get_logger("app.routines.service")


def utcnow() -> datetime:
    return datetime.now(UTC)


class RoutineNotFoundError(ValueError):
    pass


# ------------------------------------------------------------------------ ledger + uistate


def _record_ledger(
    session: Session,
    *,
    event_type: str,
    routine: Routine,
    action: str,
    factual_summary: str,
    source_ref: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Never fails the caller — the ledger is evidence, not a dependency (same rule
    ``app.goals.service._record_ledger`` follows)."""
    try:
        ledger_service.record(
            session,
            ledger_service.ActivityEvent(
                event_type=event_type,
                subsystem=SUBSYSTEM_ROUTINE,
                action=action,
                factual_summary=factual_summary,
                source="live",
                source_ref=source_ref,
                occurred_at=utcnow(),
                related_module_id=f"routine:{routine.routine_id}",
                detail_json=detail or {},
            ),
        )
    except Exception:  # noqa: BLE001 - ledger is evidence, never a hard dependency
        logger.warning(
            "routine_ledger_note_failed", event_type=event_type, routine_id=str(routine.routine_id)
        )


def _publish_ui_state(state: UiState, *, routine: Routine, **kwargs: Any) -> None:
    """Never fails the caller — a UI signal must not fail real work (same rule
    ``app.uistate.publisher.publish`` already enforces internally; this wrapper just
    supplies the routine identity consistently)."""
    from app.uistate.publisher import publish

    publish(
        state,
        subsystem=SUBSYSTEM_ROUTINE,
        label=routine.name,
        metadata={"routine_id": str(routine.routine_id), **kwargs},
    )


# ------------------------------------------------------------------------------- routines


def create_routine(
    session: Session,
    *,
    name: str,
    trigger_kind: str,
    trigger: dict[str, Any] | None = None,
    conditions: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
    source: str = "owner",
    source_ref: str | None = None,
    detail_json: dict[str, Any] | None = None,
) -> Routine:
    """Idempotent on ``(source, source_ref)`` like ``app.goals.service.create_goal``: a
    second create with the same key returns the existing routine unchanged.

    Validates and normalizes the trigger, every condition and every action BEFORE writing
    anything — a routine is either created fully valid and armed, or not created at all.
    """
    if trigger_kind not in TRIGGER_KINDS:
        raise triggers_mod.InvalidTrigger(f"unknown trigger_kind: {trigger_kind!r}")
    normalized_trigger = triggers_mod.validate_trigger(trigger_kind, trigger)
    normalized_conditions = [conditions_mod.validate_condition(c) for c in (conditions or [])]
    normalized_actions = [validate_action(a) for a in (actions or [])]

    source_ref = source_ref or f"{uuid.uuid4()}"
    existing = session.execute(
        select(Routine).where(Routine.source == source, Routine.source_ref == source_ref)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    now = utcnow()
    routine = Routine(
        name=name,
        status=ROUTINE_STATUS_ARMED,
        trigger_kind=trigger_kind,
        trigger_json=normalized_trigger,
        conditions_json=normalized_conditions,
        actions_json=normalized_actions,
        armed_at=now,
        source=source,
        source_ref=source_ref,
        detail_json=dict(detail_json or {}),
    )
    session.add(routine)
    session.commit()

    _record_ledger(
        session,
        event_type=EVENT_TYPE_ROUTINE_CREATED,
        routine=routine,
        action="routine_created",
        factual_summary=f"Rutin oluşturuldu: {name}",
        source_ref=f"routines:{routine.routine_id}:created",
        detail={"trigger_kind": trigger_kind},
    )
    _record_ledger(
        session,
        event_type=EVENT_TYPE_ROUTINE_ARMED,
        routine=routine,
        action="routine_armed",
        factual_summary=f"Rutin devrede: {name}",
        source_ref=f"routines:{routine.routine_id}:armed",
        detail={"trigger_kind": trigger_kind},
    )
    _publish_ui_state(UiState.ROUTINE_ARMED, routine=routine, trigger_kind=trigger_kind)
    return routine


def get_routine(session: Session, routine_id: uuid.UUID) -> Routine | None:
    return session.get(Routine, routine_id)


def _require_routine(session: Session, routine_id: uuid.UUID) -> Routine:
    routine = get_routine(session, routine_id)
    if routine is None:
        raise RoutineNotFoundError(f"unknown routine: {routine_id}")
    return routine


def list_routines(
    session: Session,
    *,
    status: str | None = None,
    trigger_kind: str | None = None,
    limit: int = 100,
) -> list[Routine]:
    stmt = select(Routine)
    if status is not None:
        stmt = stmt.where(Routine.status == status)
    if trigger_kind is not None:
        stmt = stmt.where(Routine.trigger_kind == trigger_kind)
    stmt = stmt.order_by(Routine.created_at.desc()).limit(max(1, min(limit, 200)))
    return list(session.execute(stmt).scalars().all())


def cancel_routine(
    session: Session, routine_id: uuid.UUID, *, reason: str | None = None
) -> Routine:
    """Idempotent: cancelling an already-cancelled routine is a no-op success (same
    "re-assert is legal" rule as ``app.goals.state``); cancelling a completed one is a
    409-shaped ``IllegalRoutineTransition`` — a resolved one-shot cannot be un-resolved."""
    routine = _require_routine(session, routine_id)
    if routine.status == ROUTINE_STATUS_CANCELLED:
        return routine
    assert_routine_transition(routine.status, ROUTINE_STATUS_CANCELLED)
    routine.status = ROUTINE_STATUS_CANCELLED
    routine.cancelled_at = utcnow()
    routine.cancel_reason = reason
    routine.updated_at = utcnow()
    session.commit()
    _record_ledger(
        session,
        event_type=EVENT_TYPE_ROUTINE_CANCELLED,
        routine=routine,
        action="routine_cancelled",
        factual_summary=f"Rutin iptal edildi: {routine.name}",
        source_ref=f"routines:{routine.routine_id}:cancelled",
        detail={"reason": reason},
    )
    return routine


# ------------------------------------------------------------------------- evaluation


@dataclasses.dataclass(frozen=True, slots=True)
class FiringOutcome:
    routine_id: uuid.UUID
    status: str  # "triggered" | "skipped"
    occurrence_key: str
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "routine_id": str(self.routine_id),
            "status": self.status,
            "occurrence_key": self.occurrence_key,
            "reason": self.reason,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class EvaluateDueResult:
    checked: int
    outcomes: tuple[FiringOutcome, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"checked": self.checked, "outcomes": [o.as_dict() for o in self.outcomes]}


def _find_existing_firing(
    session: Session, routine_id: uuid.UUID, occurrence_key: str
) -> RoutineFiring | None:
    return session.execute(
        select(RoutineFiring).where(
            RoutineFiring.routine_id == routine_id,
            RoutineFiring.occurrence_key == occurrence_key,
        )
    ).scalar_one_or_none()


def _check_due(
    routine: Routine, now: datetime
) -> tuple[bool, str | None, int | None]:
    """Returns (due, occurrence_key, new_presence_watermark). The watermark is only
    meaningful (non-None) for a presence trigger."""
    if routine.trigger_kind == TRIGGER_KIND_AT:
        due, key = triggers_mod.check_at_due(routine.trigger_json, now)
        return due, key, None
    if routine.trigger_kind == TRIGGER_KIND_SCHEDULE:
        due, key = triggers_mod.check_schedule_due(routine.trigger_json, now)
        return due, key, None
    if routine.trigger_kind == TRIGGER_KIND_PRESENCE:
        tail = get_publisher().tail(after_sequence=routine.last_presence_sequence)
        due, key, watermark = triggers_mod.check_presence_due(
            routine.trigger_json, tail, after_sequence=routine.last_presence_sequence
        )
        return due, key, watermark
    raise ValueError(f"unknown trigger_kind: {routine.trigger_kind!r}")  # pragma: no cover


def _insert_firing(session: Session, firing: RoutineFiring) -> RoutineFiring:
    session.add(firing)
    try:
        session.commit()
    except IntegrityError:
        # a concurrent evaluate_due call won the race on (routine_id, occurrence_key) —
        # the row now exists; treat this call as the no-op it should have been
        # (idempotent firing, module docstring).
        session.rollback()
        existing = _find_existing_firing(session, firing.routine_id, firing.occurrence_key)
        if existing is None:
            raise
        return existing
    return firing


def _resolve_one_shot(session: Session, routine: Routine) -> None:
    """An 'at' trigger's single occurrence has now been decided (fired or skipped) — the
    moment has passed either way, so it moves to completed (module docstring: TRIGGER_KIND_AT
    never fires twice, and RoutineFiring's own uniqueness constraint would refuse a second
    attempt regardless — this just keeps the routine's own status honest)."""
    if routine.trigger_kind != TRIGGER_KIND_AT:
        return
    routine.status = ROUTINE_STATUS_COMPLETED
    routine.updated_at = utcnow()
    session.commit()


def evaluate_due(
    session: Session,
    *,
    now: datetime | None = None,
    context: RoutineConditionContext | None = None,
    dispatcher: RoutineDispatcher | None = None,
) -> EvaluateDueResult:
    """The explicit "due now" entry point (task brief). Nothing calls this on a timer —
    a caller (an owner command, a future scheduler, a test) decides when to ask."""
    now = now or utcnow()
    context = context or RoutineConditionContext()
    dispatcher = dispatcher or NoopDispatcher()

    armed = list_routines(session, status=ROUTINE_STATUS_ARMED, limit=500)
    outcomes: list[FiringOutcome] = []

    for routine in armed:
        due, occurrence_key, new_watermark = _check_due(routine, now)
        if new_watermark is not None and new_watermark != routine.last_presence_sequence:
            routine.last_presence_sequence = new_watermark
            session.commit()
        if not due or occurrence_key is None:
            continue

        if _find_existing_firing(session, routine.routine_id, occurrence_key) is not None:
            continue  # already resolved this occurrence — idempotent no-op

        all_passed, conditions_result = conditions_mod.evaluate_conditions(
            routine.conditions_json, context
        )

        if not all_passed:
            reason = "; ".join(
                f"{r['kind']}:{r['reason']}" for r in conditions_result if not r["passed"]
            )
            firing = _insert_firing(
                session,
                RoutineFiring(
                    routine_id=routine.routine_id,
                    occurrence_key=occurrence_key,
                    status=FIRING_STATUS_SKIPPED,
                    conditions_result=conditions_result,
                    actions_snapshot=[],
                    skip_reason=reason,
                    occurred_at=now,
                ),
            )
            _record_ledger(
                session,
                event_type=EVENT_TYPE_ROUTINE_SKIPPED,
                routine=routine,
                action="routine_skipped",
                factual_summary=f"Rutin atlandı: {routine.name} ({reason})",
                source_ref=f"routines:{routine.routine_id}:skipped:{occurrence_key}",
                detail={"reason": reason, "conditions": conditions_result},
            )
            _resolve_one_shot(session, routine)
            outcomes.append(
                FiringOutcome(
                    routine_id=routine.routine_id,
                    status=firing.status,
                    occurrence_key=occurrence_key,
                    reason=reason,
                )
            )
            continue

        actions_snapshot = [dict(a) for a in routine.actions_json]
        firing = _insert_firing(
            session,
            RoutineFiring(
                routine_id=routine.routine_id,
                occurrence_key=occurrence_key,
                status=FIRING_STATUS_TRIGGERED,
                conditions_result=conditions_result,
                actions_snapshot=actions_snapshot,
                occurred_at=now,
            ),
        )
        if firing.status != FIRING_STATUS_TRIGGERED:
            # a concurrent call already resolved this occurrence (_insert_firing returned
            # the pre-existing row) — nothing left for this call to do.
            outcomes.append(
                FiringOutcome(
                    routine_id=routine.routine_id,
                    status=firing.status,
                    occurrence_key=occurrence_key,
                    reason=firing.skip_reason,
                )
            )
            continue

        _record_ledger(
            session,
            event_type=EVENT_TYPE_ROUTINE_TRIGGERED,
            routine=routine,
            action="routine_triggered",
            factual_summary=f"Rutin tetiklendi: {routine.name}",
            source_ref=f"routines:{routine.routine_id}:triggered:{occurrence_key}",
            detail={"conditions": conditions_result},
        )
        _publish_ui_state(UiState.ROUTINE_TRIGGERED, routine=routine, occurrence_key=occurrence_key)
        if any(a.get("kind") == ACTION_KIND_ALARM for a in actions_snapshot):
            _publish_ui_state(
                UiState.ALARM_TRIGGERED, routine=routine, occurrence_key=occurrence_key
            )

        dispatch_results: list[dict[str, Any]] = []
        for action in actions_snapshot:
            try:
                outcome = dispatcher.dispatch(
                    routine_id=routine.routine_id, firing_id=firing.firing_id, action=action
                )
            except Exception as exc:  # noqa: BLE001 - a broken dispatcher must not break evaluation
                outcome = DispatchOutcome(
                    ok=False, detail={"error": f"{type(exc).__name__}: {exc}"}
                )
            dispatch_results.append(
                {"kind": action.get("kind"), "ok": outcome.ok, "detail": outcome.detail}
            )

        _record_ledger(
            session,
            event_type=EVENT_TYPE_ROUTINE_EXECUTED,
            routine=routine,
            action="routine_executed",
            factual_summary=f"Rutin çalıştırıldı: {routine.name}",
            source_ref=f"routines:{routine.routine_id}:executed:{occurrence_key}",
            detail={"dispatch_results": dispatch_results},
        )
        _resolve_one_shot(session, routine)
        outcomes.append(
            FiringOutcome(
                routine_id=routine.routine_id,
                status=firing.status,
                occurrence_key=occurrence_key,
            )
        )

    return EvaluateDueResult(checked=len(armed), outcomes=tuple(outcomes))


def list_firings(
    session: Session, routine_id: uuid.UUID, *, limit: int = 100
) -> list[RoutineFiring]:
    stmt = (
        select(RoutineFiring)
        .where(RoutineFiring.routine_id == routine_id)
        .order_by(RoutineFiring.occurred_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    return list(session.execute(stmt).scalars().all())


__all__ = [
    "EvaluateDueResult",
    "FiringOutcome",
    "RoutineNotFoundError",
    "cancel_routine",
    "create_routine",
    "evaluate_due",
    "get_routine",
    "list_firings",
    "list_routines",
    "utcnow",
]
