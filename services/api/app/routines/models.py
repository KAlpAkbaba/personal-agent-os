"""Routine Engine ORM rows (M18).

Canonical schema: ``alembic/versions/20260905_0019_routines.py`` +
``alembic/versions/20260906_0020_routine_dispatch.py``. Same discipline as
``app.goals.models`` / ``app.selfhealing.models``: portable column types (generic ``Uuid``,
``JSON`` with a ``JSONB`` variant) so the service layer unit-tests on SQLite, plus
``CheckConstraint``s spelling out the closed vocabularies (``app.selfhealing.models``,
``app.security.models`` house pattern) so an invalid status can never reach the database
even from a path that skips the Python-side validation.

Two tables:

``Routine`` is the durable TRIGGER -> CONDITIONS -> ACTIONS descriptor. It is armed once
(``status="armed"``) and stays that way until it is cancelled or — for a one-shot ``at``
trigger only — resolved by its single occurrence (``app.routines.service``). Conditions and
actions are stored as plain JSON lists of ``{"kind": ..., "detail": {...}}`` descriptors
(mirrors ``app.goals.models.Goal.success_criteria``): this package only ever reads and
writes those two shapes through ``app.routines.conditions`` / ``app.routines.actions``, so a
column here never becomes an ad-hoc second schema.

``RoutineFiring`` is the append-only record of one evaluated occurrence: it exists
specifically so a routine "must not double-fire" (task brief) is a database constraint
(``uq_routine_firings_routine_occurrence``), not a hope about caller discipline — a second
``evaluate_due`` call for the same occurrence finds the existing row and does nothing, the
same idempotency shape as ``app.ledger.models.ActivityEventRow`` on ``(source, source_ref)``.
It also carries ``conditions_result`` so a skip is never a silent drop: the reason a routine
did not fire is a durable, queryable fact, not just a ledger sentence.

``dispatch_results``/``dispatch_status`` (ADR-0060) give the same durability to EXECUTION:
before them, a failed or refused dispatch lived only in the ``routine.executed`` ledger
event's ``detail_json`` — readable, but not queryable, and not exposed on the firing itself.
Each ``dispatch_results`` entry is ``{"kind", "status", "ok", "reason", "detail"}`` — one per
action in ``actions_snapshot``, same order. ``dispatch_status`` is the AGGREGATE across all of
them for this firing (``succeeded`` | ``partial`` | ``failed`` | ``refused`` | ``none``,
computed by ``app.routines.service._aggregate_dispatch_status``) — nullable because a
``skipped`` firing never dispatches anything at all, which is a different fact from
"dispatched zero actions successfully" (``none``, e.g. an action-less routine that still
triggered).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


# ------------------------------------------------------------------- triggers

TRIGGER_KIND_AT = "at"
TRIGGER_KIND_SCHEDULE = "schedule"
TRIGGER_KIND_PRESENCE = "presence"

TRIGGER_KINDS: tuple[str, ...] = (TRIGGER_KIND_AT, TRIGGER_KIND_SCHEDULE, TRIGGER_KIND_PRESENCE)

# -------------------------------------------------------------------- statuses

#: A routine is created already armed (task brief: creation and arming are both recorded,
#: but nothing in this package leaves a routine unarmed in between). ``completed`` is
#: reached only by a one-shot ``at`` trigger resolving its single occurrence
#: (triggered or skipped — either way the moment has passed); ``schedule``/``presence``
#: routines stay ``armed`` across every occurrence until cancelled.
ROUTINE_STATUS_ARMED = "armed"
ROUTINE_STATUS_COMPLETED = "completed"
ROUTINE_STATUS_CANCELLED = "cancelled"

ROUTINE_STATUSES: tuple[str, ...] = (
    ROUTINE_STATUS_ARMED,
    ROUTINE_STATUS_COMPLETED,
    ROUTINE_STATUS_CANCELLED,
)

ROUTINE_TERMINAL_STATUSES = frozenset({ROUTINE_STATUS_COMPLETED, ROUTINE_STATUS_CANCELLED})

FIRING_STATUS_TRIGGERED = "triggered"
FIRING_STATUS_SKIPPED = "skipped"

FIRING_STATUSES: tuple[str, ...] = (FIRING_STATUS_TRIGGERED, FIRING_STATUS_SKIPPED)

#: RoutineFiring.dispatch_status closed vocabulary (ADR-0060). Wider than
#: app.routines.actions.DISPATCH_STATUSES (succeeded|failed|refused) because this is the
#: AGGREGATE across every action dispatched for one firing, not one action's own verdict —
#: "partial" (a mix, at least one success alongside a non-success) and "none" (a triggered
#: firing with zero actions to dispatch — a legitimate, explicit configuration, same as an
#: empty conditions list) have no equivalent at the single-action level.
DISPATCH_STATUS_SUCCEEDED = "succeeded"
DISPATCH_STATUS_PARTIAL = "partial"
DISPATCH_STATUS_FAILED = "failed"
DISPATCH_STATUS_REFUSED = "refused"
DISPATCH_STATUS_NONE = "none"

FIRING_DISPATCH_STATUSES: tuple[str, ...] = (
    DISPATCH_STATUS_SUCCEEDED,
    DISPATCH_STATUS_PARTIAL,
    DISPATCH_STATUS_FAILED,
    DISPATCH_STATUS_REFUSED,
    DISPATCH_STATUS_NONE,
)


class Routine(Base):
    __tablename__ = "routines"
    __table_args__ = (
        CheckConstraint(_in_list("status", ROUTINE_STATUSES), name="ck_routines_status"),
        CheckConstraint(
            _in_list("trigger_kind", TRIGGER_KINDS), name="ck_routines_trigger_kind"
        ),
        UniqueConstraint("source", "source_ref", name="uq_routines_source_ref"),
        Index("ix_routines_status", "status"),
        Index("ix_routines_trigger_kind", "trigger_kind"),
    )

    routine_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ROUTINE_STATUS_ARMED
    )
    trigger_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: shape depends on trigger_kind — app.routines.triggers.validate_trigger is the only
    #: writer of this column's content; see that module for the three shapes.
    trigger_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    #: [{"kind": str, "detail": {...}}, ...] — app.routines.conditions is the closed
    #: vocabulary; evaluated at fire time, never at creation time.
    conditions_json: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: [{"kind": str, "detail": {...}}, ...] — declarative only (app.routines.actions);
    #: this package never executes one.
    actions_json: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: watermark into app.uistate.publisher's tail (its own monotonic ``sequence``) for a
    #: presence trigger, so the same underlying presence event is never re-scanned, let
    #: alone re-fired, on a later evaluate_due call. Meaningless for at/schedule triggers.
    last_presence_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    armed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: "owner" | caller-defined; who asked for this routine to exist (mirrors
    #: app.goals.models.Goal.source).
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="owner")
    #: idempotency key within `source` (mirrors app.goals.models.Goal.source_ref).
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)


class RoutineFiring(Base):
    """One evaluated occurrence of a routine — append-only (module docstring)."""

    __tablename__ = "routine_firings"
    __table_args__ = (
        CheckConstraint(_in_list("status", FIRING_STATUSES), name="ck_routine_firings_status"),
        CheckConstraint(
            _in_list("dispatch_status", FIRING_DISPATCH_STATUSES),
            name="ck_routine_firings_dispatch_status",
        ),
        UniqueConstraint(
            "routine_id", "occurrence_key", name="uq_routine_firings_routine_occurrence"
        ),
        Index("ix_routine_firings_routine_id", "routine_id"),
        Index("ix_routine_firings_occurred_at", "occurred_at"),
    )

    firing_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    routine_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("routines.routine_id", ondelete="CASCADE"), nullable=False
    )
    #: deterministic per-occurrence key: "once" for an ``at`` trigger, the local calendar
    #: date (owner's trigger timezone) for a ``schedule`` trigger, or the source uistate
    #: event's own ``event_id`` for a ``presence`` trigger — see app.routines.triggers.
    occurrence_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: [{"kind": str, "passed": bool, "reason": str}, ...] — never empty when status is
    #: "skipped" (task brief: "recorded as skipped WITH THE REASON, never silently dropped").
    conditions_result: Mapped[list[Any]] = mapped_column(
        JSONColumn, nullable=False, default=list
    )
    #: exact copies of the routine's actions_json at fire time — WHAT should happen,
    #: never executed here. Empty when status is "skipped".
    actions_snapshot: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    skip_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: [{"kind", "status", "ok", "reason", "detail"}, ...] — one per action_snapshot entry,
    #: same order (ADR-0060). A dispatch failure/refusal is visible here, not just in the
    #: ledger: this column is what "did that alarm actually ring?" answers directly, without
    #: re-reading a ledger event's detail_json. Empty when status is "skipped".
    dispatch_results: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: the AGGREGATE across dispatch_results (see FIRING_DISPATCH_STATUSES above). NULL for
    #: a "skipped" firing — dispatch never ran at all, which is a different fact from having
    #: dispatched and found nothing to do.
    dispatch_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: when this occurrence was decided (the evaluate_due caller's `now`), not when the
    #: row was written — same distinction as ActivityEventRow.occurred_at vs recorded_at.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "DISPATCH_STATUS_FAILED",
    "DISPATCH_STATUS_NONE",
    "DISPATCH_STATUS_PARTIAL",
    "DISPATCH_STATUS_REFUSED",
    "DISPATCH_STATUS_SUCCEEDED",
    "FIRING_DISPATCH_STATUSES",
    "FIRING_STATUSES",
    "FIRING_STATUS_SKIPPED",
    "FIRING_STATUS_TRIGGERED",
    "ROUTINE_STATUSES",
    "ROUTINE_STATUS_ARMED",
    "ROUTINE_STATUS_CANCELLED",
    "ROUTINE_STATUS_COMPLETED",
    "ROUTINE_TERMINAL_STATUSES",
    "TRIGGER_KINDS",
    "TRIGGER_KIND_AT",
    "TRIGGER_KIND_PRESENCE",
    "TRIGGER_KIND_SCHEDULE",
    "Routine",
    "RoutineFiring",
]
