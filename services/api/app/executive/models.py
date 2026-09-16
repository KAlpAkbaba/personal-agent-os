"""``executive_runs`` / ``executive_steps``: Executive Autonomy's own durable record
(docs/M26_EXECUTIVE_AUTONOMY_SPEC.md §1, §3, ADR-0089). Expand-only migration:
``alembic/versions/20260908_0033_executive_runs.py``, chained from ``0032_scenes``.

Two tables, one run each:

* ``executive_runs`` — one row per ``TaskGraph`` the owner asked for. ``graph_json`` is
  the VALIDATED graph (canonical JSON, ``app.executive.spec.TaskGraph`` — never
  re-derived from prose, the same discipline ``app.creative3d.models.SceneRow.plan_json``
  already follows). ``state`` is one of :data:`app.uistate.contract.EXECUTIVE_RUN_STATES`
  DIRECTLY — see the note on that constant: this family's seven row states already are
  the seven wire words, so there is no separate ``wire_step()`` translation function the
  way the 3D family needs one.
* ``executive_steps`` — one row per step of one run, written ONLY by activities (the
  workflow itself holds no state, M13 discipline) and unique on ``(run_id, step_id)`` so
  an idempotent activity retried after a worker restart updates the SAME row rather than
  creating a duplicate. ``state`` is the STEP's own lifecycle (spec §3): a richer,
  internal vocabulary that is never published on the wire — the bus only ever names the
  RUN's state plus which step id is current (``app.executive.service.publish_run_state``).

Portable types throughout (generic ``Uuid``/``JSON`` with a ``JSONB`` variant on
Postgres) so the service and workflow layers unit-test on SQLite, the same discipline
every M13-M25 model in this codebase already follows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base
from app.uistate.contract import EXECUTIVE_RUN_STATES

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

#: The run's own lifecycle IS the wire vocabulary (module docstring); re-exported here
#: so callers that only need "the run states" do not have to reach into app.uistate.
EXECUTIVE_RUN_STATE_VALUES: tuple[str, ...] = EXECUTIVE_RUN_STATES
(
    STATE_PLANNED,
    STATE_RUNNING,
    STATE_PAUSED,
    STATE_COMPLETED,
    STATE_PARTIAL,
    STATE_CANCELLED,
    STATE_FAILED,
) = EXECUTIVE_RUN_STATE_VALUES

#: Terminal run states — a run in one of these never transitions again (service.py's
#: "<= 2 active runs" bound counts everything NOT in this set as active).
TERMINAL_RUN_STATES: frozenset[str] = frozenset(
    {STATE_COMPLETED, STATE_PARTIAL, STATE_CANCELLED, STATE_FAILED}
)

#: Step lifecycle (spec §3): ``pending -> ready -> running -> verified | failed_recoverable
#: -> (retrying -> running) | failed -> compensated | skipped | cancelled``. Internal only
#: — never published on the UI-state bus (module docstring); the receipt/explain sentence
#: is built from these plus the step's evidence, not sent verbatim to the wire.
STEP_STATE_PENDING = "pending"
STEP_STATE_READY = "ready"
STEP_STATE_RUNNING = "running"
STEP_STATE_VERIFIED = "verified"
STEP_STATE_FAILED_RECOVERABLE = "failed_recoverable"
STEP_STATE_RETRYING = "retrying"
STEP_STATE_FAILED = "failed"
STEP_STATE_COMPENSATED = "compensated"
STEP_STATE_SKIPPED = "skipped"
STEP_STATE_CANCELLED = "cancelled"

STEP_STATES: tuple[str, ...] = (
    STEP_STATE_PENDING,
    STEP_STATE_READY,
    STEP_STATE_RUNNING,
    STEP_STATE_VERIFIED,
    STEP_STATE_FAILED_RECOVERABLE,
    STEP_STATE_RETRYING,
    STEP_STATE_FAILED,
    STEP_STATE_COMPENSATED,
    STEP_STATE_SKIPPED,
    STEP_STATE_CANCELLED,
)

#: A step in one of these is DONE contributing to the run's progress count — it will
#: never run again on this attempt sequence (spec §6's ``done``/``total`` counters).
#: B10 req 558/559: terminal AND not successful. `STEP_TERMINAL_STATES` answers "will this
#: step move again?"; this answers "did it work?", and conflating the two is what made a run
#: of three failures report four steps done.
STEP_UNSUCCESSFUL_STATES: frozenset[str] = frozenset(
    {
        STEP_STATE_FAILED,
        STEP_STATE_COMPENSATED,
        STEP_STATE_SKIPPED,
        STEP_STATE_CANCELLED,
    }
)

STEP_TERMINAL_STATES: frozenset[str] = frozenset(
    {
        STEP_STATE_VERIFIED,
        STEP_STATE_FAILED,
        STEP_STATE_COMPENSATED,
        STEP_STATE_SKIPPED,
        STEP_STATE_CANCELLED,
    }
)
#: A step that is genuinely IN FLIGHT - the workflow will act on it without anyone asking.
#: The distinction that matters for the run's own state, and it is NOT the complement of
#: ``STEP_TERMINAL_STATES``: `failed_recoverable` is in neither set. It is not terminal
#: (a retry can still move it) and it is not in flight (nothing will retry it unless the
#: OWNER asks). A run whose every step is stopped is `partial` even when one of them could
#: be retried - see the M26 runtime verification, where treating "retryable" as "in
#: progress" told the owner "Çalışıyorum efendim" about a run that had stopped completely.
STEP_IN_FLIGHT_STATES: frozenset[str] = frozenset(
    {STEP_STATE_PENDING, STEP_STATE_READY, STEP_STATE_RUNNING, STEP_STATE_RETRYING}
)

#: A step that did NOT verify, for the run's ``partial`` naming (spec §3): finished, but
#: not with the evidence its postcondition asked for.
STEP_UNVERIFIED_TERMINAL_STATES: frozenset[str] = frozenset(
    {STEP_STATE_FAILED, STEP_STATE_COMPENSATED, STEP_STATE_SKIPPED, STEP_STATE_CANCELLED}
)

#: spec §5, §6: where a run was started from — provenance only, mirrors
#: app.research.service.SOURCE_REST / SOURCE_VOICE.
SOURCE_REST = "rest"
SOURCE_VOICE = "voice"


class ExecutiveRunRow(Base):
    __tablename__ = "executive_runs"
    __table_args__ = (
        Index("ix_executive_runs_state", "state"),
        Index("ix_executive_runs_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: The owner's own words, bounded (spec §1) — never a paraphrase.
    goal: Mapped[str] = mapped_column(String(2000), nullable=False)
    #: The VALIDATED TaskGraph, canonical JSON (module docstring).
    graph_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=STATE_PLANNED)
    #: The step id the run is currently on (running or paused there); None before the
    #: first step starts and after the run reaches a terminal state.
    current_step: Mapped[str | None] = mapped_column(String(8), nullable=True)
    steps_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: B10 req 558/559: steps that WORKED. It used to count every step in a terminal state
    #: - failures, cancellations and compensations included - while being spoken as "adım
    #: tamam", so a run of three failures and one success reported "4/4 adım tamam".
    steps_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Steps that did not work. The other half of the count, so a caller never has to infer
    #: failure by subtracting and never has to guess whether "not done" means "failed" or
    #: "still going".
    steps_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: The final owner-facing summary (spec §1's ``synthesis`` step output) — what was
    #: made, where it is, what is missing. Populated even on a ``partial`` run (spec §3:
    #: "the synthesis still runs over what exists").
    synthesis_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: {step_id: reason} for a ``partial`` run — every step that did not verify, and why
    #: (spec §3). Empty/None on any other state.
    partial_reasons_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: B38 (req 544): {step_id: iso timestamp} of the owner's approvals, and the step the
    #: run is parked on (None when nothing waits).
    approvals_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    awaiting_step: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: "rest" | "voice" (module docstring) — provenance only, behaviour is identical.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=SOURCE_VOICE)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExecutiveStepRow(Base):
    __tablename__ = "executive_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_id", name="uq_executive_steps_run_step"),
        Index("ix_executive_steps_run_id", "run_id"),
        Index("ix_executive_steps_state", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("executive_runs.id", ondelete="CASCADE"), nullable=False
    )
    #: The graph's own step id ("s1", "s2", ...), never the row's own uuid — every
    #: cross-reference (inputs, precondition.step_done, the workflow's own bookkeeping)
    #: speaks in this token.
    step_id: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: A snapshot of this step's own spec fields (spec.Step, minus id/kind) — read by the
    #: activity that runs it, never re-derived from the run's graph_json at execution
    #: time (an amend appends new rows; the graph_json on the run row stays the ORIGINAL
    #: plan plus whatever amend_json history the service keeps, so a step's own row is
    #: the single source of truth for what it was asked to do).
    inputs_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    precondition_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    postcondition_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)
    retry_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    #: B38 (req 555): {max_rounds} - the step's bounded loop; empty = one round.
    repeat_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_class: Mapped[str] = mapped_column(String(24), nullable=False)
    compensation: Mapped[str] = mapped_column(String(24), nullable=False, default="none")
    state: Mapped[str] = mapped_column(String(24), nullable=False, default=STEP_STATE_PENDING)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: The postcondition evidence the activity READ BACK — never the call's own return
    #: value trusted as-is (spec §3's central rule). Shape depends on evidence_kind:
    #: {"artifact_id": "...", "count": N} etc. None until a real read-back happened.
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "EXECUTIVE_RUN_STATE_VALUES",
    "ExecutiveRunRow",
    "ExecutiveStepRow",
    "SOURCE_REST",
    "SOURCE_VOICE",
    "STATE_CANCELLED",
    "STATE_COMPLETED",
    "STATE_FAILED",
    "STATE_PARTIAL",
    "STATE_PAUSED",
    "STATE_PLANNED",
    "STATE_RUNNING",
    "STEP_STATES",
    "STEP_STATE_CANCELLED",
    "STEP_STATE_COMPENSATED",
    "STEP_STATE_FAILED",
    "STEP_STATE_FAILED_RECOVERABLE",
    "STEP_STATE_PENDING",
    "STEP_STATE_READY",
    "STEP_STATE_RETRYING",
    "STEP_STATE_RUNNING",
    "STEP_STATE_SKIPPED",
    "STEP_STATE_VERIFIED",
    "STEP_IN_FLIGHT_STATES",
    "STEP_TERMINAL_STATES",
    "STEP_UNVERIFIED_TERMINAL_STATES",
    "TERMINAL_RUN_STATES",
]
