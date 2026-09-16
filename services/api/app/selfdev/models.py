"""The self-development queue (B35 req 581, 583, 609, 615, 620, 621).

One row per defect the system was asked to fix or feature it was asked to add - by the
owner's voice, by the Cockpit, or by the bridge from an evolution opportunity - carrying
the run's outcome once a worker has run it and the owner's decision once the owner has
looked. The row IS the product surface for ``app.selfdev``: before B35 a defect was a JSON
file a person wrote by hand and a run was a folder only that person could find.

States, in the order a row moves through them::

    queued -> claimed -> running -> awaiting_owner -> approved | rejected
                                 -> refused | quarantined | failed      (terminal, no candidate)

``approved`` is a recorded decision, never a promotion: the constitution's promote step
stays outside this table (req 624 - no autonomous high-risk promotion, ever).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

DEFECT_KIND_BUG = "bug"
DEFECT_KIND_FEATURE = "feature"
DEFECT_KINDS: tuple[str, ...] = (DEFECT_KIND_BUG, DEFECT_KIND_FEATURE)

SOURCE_OWNER_VOICE = "owner_voice"
SOURCE_OWNER_REST = "owner_rest"
SOURCE_OPPORTUNITY = "opportunity"
SOURCE_CI_FAILURE = "ci_failure"
SOURCES: tuple[str, ...] = (
    SOURCE_OWNER_VOICE,
    SOURCE_OWNER_REST,
    SOURCE_OPPORTUNITY,
    SOURCE_CI_FAILURE,
)

STATE_QUEUED = "queued"
STATE_CLAIMED = "claimed"
STATE_RUNNING = "running"
STATE_AWAITING_OWNER = "awaiting_owner"
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
STATE_REFUSED = "refused"
STATE_QUARANTINED = "quarantined"
STATE_FAILED = "failed"
STATES: tuple[str, ...] = (
    STATE_QUEUED,
    STATE_CLAIMED,
    STATE_RUNNING,
    STATE_AWAITING_OWNER,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_REFUSED,
    STATE_QUARANTINED,
    STATE_FAILED,
)
#: A worker holds one of these; the parallel bound (req 620) counts them.
ACTIVE_STATES: frozenset[str] = frozenset({STATE_CLAIMED, STATE_RUNNING})
#: The owner has something to look at.
PENDING_STATES: frozenset[str] = frozenset({STATE_AWAITING_OWNER})
TERMINAL_STATES: frozenset[str] = frozenset(
    {STATE_APPROVED, STATE_REJECTED, STATE_REFUSED, STATE_QUARANTINED, STATE_FAILED}
)

DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"


class SelfDevDefectRow(Base):
    __tablename__ = "selfdev_defects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=DEFECT_KIND_BUG)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    #: The voice/owner session that asked, when one did.
    session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scope_json: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    failing_test: Mapped[str | None] = mapped_column(String(400), nullable=True)
    #: The bridge (req 583): which opportunity this defect came from.
    opportunity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: The CI fix loop (req 603): which defect's candidate CI failed to make this one.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: The class the supervisor derives from the paths (req 585) - consumed when the run
    #: ends: it decides what "awaiting the owner" means for this row.
    promotion_class: Mapped[str | None] = mapped_column(String(40), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default=STATE_QUEUED, index=True)
    #: Which worker holds it and since when.
    claimed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The engine's run: id, branch, candidate sha, the checks, the security review, the
    #: gate, the shadow, the risk, the promotion class - the RunRecord, minus the diff.
    run_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    run_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    candidate_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: The owner's decision (req 609): recorded, dated, attributed - and NOT a promotion.
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_selfdev_defects_state_created", "state", "created_at"),)
