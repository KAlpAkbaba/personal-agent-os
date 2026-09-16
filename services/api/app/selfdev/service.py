"""The self-development queue service (B35 req 581, 583, 585, 603, 609, 615, 618-620).

One object over the ``selfdev_defects`` table:

* **intake** - a defect from the owner's voice, the Cockpit, the bridge or the CI fix loop
  becomes a ``queued`` row (req 581, 583, 622, 623);
* **claim** - a worker takes the oldest queued row, if the parallel bound (req 620), the
  daily token budget (req 618) and the disk floor (req 619) allow; each refusal is a
  recorded reason, never silence;
* **finish** - the engine's record lands on the row; the promotion class decides what the
  row now is (req 585): every candidate ``awaiting_owner``, a NEVER_AUTO_PROMOTE one saying
  so on the row; a candidate whose CI is red spawns ONE bounded follow-up defect (req 603);
* **approve / reject** - the owner's decision, from a verified owner session capability
  and nothing else (req 609). Approval is a recorded decision. It promotes nothing: the
  constitution's promote step is the release pipeline's, behind its own owner gate, and
  req 624 (no autonomous high-risk promotion) is not a setting.

Every state change is a ledger event. The service takes a ``Session`` per call, as the
document mutation service does, so the REST routes, the voice tools and the clock share
one code path.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors.owner import log_and_detail
from app.evolution.authority import AuthorityError, OwnerCapability
from app.evolution.supervisor import (
    PROMOTION_CLASSES,
    PROMOTION_NEVER_AUTO_PROMOTE,
)
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_SELFDEV_CANDIDATE_READY,
    EVENT_TYPE_SELFDEV_DECIDED,
    EVENT_TYPE_SELFDEV_DEFECT_QUEUED,
    EVENT_TYPE_SELFDEV_RUN_ENDED,
    SUBSYSTEM_SELFDEV,
)
from app.logging import get_logger
from app.selfdev.bridge import defect_from_opportunity
from app.selfdev.models import (
    ACTIVE_STATES,
    DECISION_APPROVED,
    DECISION_REJECTED,
    DEFECT_KIND_BUG,
    DEFECT_KINDS,
    PENDING_STATES,
    SOURCE_CI_FAILURE,
    SOURCE_OPPORTUNITY,
    SOURCES,
    STATE_APPROVED,
    STATE_AWAITING_OWNER,
    STATE_CLAIMED,
    STATE_FAILED,
    STATE_QUARANTINED,
    STATE_QUEUED,
    STATE_REFUSED,
    STATE_REJECTED,
    STATE_RUNNING,
    SelfDevDefectRow,
)

logger = get_logger("app.selfdev.service")

ERROR_SELFDEV_DISABLED = "selfdev_disabled"
ERROR_DEFECT_NOT_FOUND = "defect_not_found"
ERROR_NOT_AWAITING_OWNER = "not_awaiting_owner"
ERROR_NOT_CLAIMABLE = "not_claimable"
ERROR_BUDGET_EXHAUSTED = "budget_exhausted"
ERROR_PARALLEL_LIMIT = "parallel_limit"
ERROR_DISK_FLOOR = "disk_floor"
ERROR_OPPORTUNITY_ALREADY_QUEUED = "opportunity_already_queued"
ERROR_INVALID_DEFECT = "invalid_defect"

#: The engine's statuses -> the row's states.
_STATE_BY_ENGINE_STATUS: dict[str, str] = {
    "STOPPED_AT_POLICY_BOUNDARY": STATE_AWAITING_OWNER,
    "REFUSED": STATE_REFUSED,
    "QUARANTINED": STATE_QUARANTINED,
}
#: Keys of the engine record kept on the row (the diff stays in the runs folder).
_RUN_KEYS: tuple[str, ...] = (
    "run_id",
    "status",
    "reason",
    "branch",
    "worktree",
    "worktree_kept",
    "base_sha",
    "base_ci",
    "candidate_sha",
    "candidate_ci",
    "changed_paths",
    "risk",
    "promotion_class",
    "next_step",
    "security_review",
    "gate",
    "ci_trigger",
    "shadow",
    "explanation",
    "model",
    "seconds",
    "started_at",
    "finished_at",
    "attempts",
)
MAX_RUN_JSON_ATTEMPT_DETAIL = 1500


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """The bounds (req 618-620), all settings, all named in the status."""

    max_parallel: int = 1
    daily_token_budget: int = 1_000_000
    min_free_bytes: int = 5 * 1024**3
    worktrees_root: Path | None = None
    claim_ttl_s: float = 7200.0
    max_ci_fix_rounds: int = 2


@dataclass(slots=True)
class ClaimVerdict:
    row: SelfDevDefectRow | None
    reason: str = ""

    @property
    def claimed(self) -> bool:
        return self.row is not None


class SelfDevService:
    def __init__(
        self,
        *,
        enabled: bool | Callable[[], bool] = True,
        policy: BudgetPolicy | None = None,
        disk_free: Callable[[Path], int] | None = None,
    ) -> None:
        self._enabled = enabled
        self.policy = policy or BudgetPolicy()
        self._disk_free = disk_free or (lambda path: shutil.disk_usage(path).free)
        self.last_refusal: str = ""

    # ------------------------------------------------------------------ helpers

    @property
    def enabled(self) -> bool:
        return self._enabled() if callable(self._enabled) else bool(self._enabled)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise AuthorityError(
                "self-development is disabled (PAGENTOS_SELFDEV_ENABLED=false)",
                reason=ERROR_SELFDEV_DISABLED,
            )

    def _ledger(
        self, db: Session, *, event_type: str, action: str, summary: str, detail: dict[str, Any]
    ) -> None:
        try:
            ledger_service.record(
                db,
                ledger_service.ActivityEvent(
                    event_type=event_type,
                    subsystem=SUBSYSTEM_SELFDEV,
                    action=action,
                    factual_summary=summary,
                    occurred_at=_utcnow(),
                    detail_json=detail,
                    source="live",
                    source_ref=f"{action}:{uuid.uuid4()}",
                ),
            )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the action
            logger.warning("selfdev_ledger_failed", action=action)

    @staticmethod
    def entry(row: SelfDevDefectRow) -> dict[str, Any]:
        run = dict(row.run_json or {})
        return {
            "defect_id": str(row.id),
            "kind": row.kind,
            "source": row.source,
            "session_id": row.session_id,
            "title": row.title,
            "evidence": row.evidence,
            "scope": list(row.scope_json or []),
            "failing_test": row.failing_test,
            "opportunity_id": row.opportunity_id,
            "parent_id": str(row.parent_id) if row.parent_id else None,
            "promotion_class": row.promotion_class,
            "never_auto_promote": row.promotion_class == PROMOTION_NEVER_AUTO_PROMOTE,
            "priority": row.priority,
            "state": row.state,
            "claimed_by": row.claimed_by,
            "claimed_at": _iso(row.claimed_at),
            "run_id": row.run_id,
            "branch": row.branch,
            "candidate_sha": row.candidate_sha,
            "tokens_used": row.tokens_used,
            "risk_tier": (run.get("risk") or {}).get("tier"),
            "security_review": run.get("security_review") or {},
            "gate": run.get("gate") or {},
            "shadow": run.get("shadow") or {},
            "ci": run.get("candidate_ci") or {},
            "ci_trigger": run.get("ci_trigger") or {},
            "explanation": run.get("explanation") or "",
            "reason": run.get("reason") or "",
            "decision": row.decision,
            "decided_at": _iso(row.decided_at),
            "decided_by": row.decided_by,
            "decision_note": row.decision_note,
            "error_class": row.error_class,
            "error_message": row.error_message,
            "created_at": _iso(row.created_at),
            "started_at": _iso(row.started_at),
            "finished_at": _iso(row.finished_at),
        }

    def get(self, db: Session, defect_id: uuid.UUID | str) -> SelfDevDefectRow:
        try:
            key = uuid.UUID(str(defect_id))
        except ValueError as exc:
            raise LookupError(ERROR_DEFECT_NOT_FOUND) from exc
        row = db.get(SelfDevDefectRow, key)
        if row is None:
            raise LookupError(ERROR_DEFECT_NOT_FOUND)
        return row

    # ------------------------------------------------------------------ intake

    def intake(
        self,
        db: Session,
        *,
        title: str,
        evidence: str,
        source: str,
        kind: str = DEFECT_KIND_BUG,
        scope: list[str] | tuple[str, ...] | None = None,
        failing_test: str | None = None,
        session_id: str | None = None,
        opportunity_id: str | None = None,
        parent_id: uuid.UUID | None = None,
        promotion_class: str | None = None,
        priority: int = 2,
    ) -> SelfDevDefectRow:
        self._require_enabled()
        title = (title or "").strip()
        if not title:
            raise ValueError(ERROR_INVALID_DEFECT)
        if kind not in DEFECT_KINDS or source not in SOURCES:
            raise ValueError(ERROR_INVALID_DEFECT)
        if promotion_class is not None and promotion_class not in PROMOTION_CLASSES:
            raise ValueError(ERROR_INVALID_DEFECT)
        row = SelfDevDefectRow(
            id=uuid.uuid4(),
            kind=kind,
            source=source,
            session_id=session_id,
            title=title[:200],
            evidence=(evidence or "").strip()[:8000],
            scope_json=[str(p) for p in (scope or ()) if str(p).strip()],
            failing_test=failing_test,
            opportunity_id=opportunity_id,
            parent_id=parent_id,
            promotion_class=promotion_class,
            priority=max(0, min(int(priority), 3)),
            state=STATE_QUEUED,
            run_json={},
            tokens_used=0,
            created_at=_utcnow(),
        )
        db.add(row)
        db.flush()
        self._ledger(
            db,
            event_type=EVENT_TYPE_SELFDEV_DEFECT_QUEUED,
            action="selfdev.queue",
            summary=f"{kind} kuyruğa alındı: {row.title}",
            detail={
                "defect_id": str(row.id),
                "source": source,
                "kind": kind,
                "opportunity_id": opportunity_id,
                "parent_id": str(parent_id) if parent_id else None,
            },
        )
        db.commit()
        db.refresh(row)
        return row

    def intake_opportunity(
        self, db: Session, opportunity: dict[str, Any], *, session_id: str | None = None
    ) -> SelfDevDefectRow:
        """The bridge (req 583): one defect per opportunity, never two live ones."""
        self._require_enabled()
        spec = defect_from_opportunity(opportunity)
        live = db.scalar(
            select(SelfDevDefectRow).where(
                SelfDevDefectRow.opportunity_id == spec["opportunity_id"],
                SelfDevDefectRow.state.in_(
                    (STATE_QUEUED, STATE_CLAIMED, STATE_RUNNING, STATE_AWAITING_OWNER)
                ),
            )
        )
        if live is not None:
            raise ValueError(ERROR_OPPORTUNITY_ALREADY_QUEUED)
        return self.intake(
            db,
            title=spec["title"],
            evidence=spec["evidence"],
            source=SOURCE_OPPORTUNITY,
            kind=spec["kind"],
            scope=spec["scope"],
            failing_test=spec["failing_test"],
            session_id=session_id,
            opportunity_id=spec["opportunity_id"],
            promotion_class=spec["promotion_class"],
            priority=spec["priority"],
        )

    # ------------------------------------------------------------------ queries

    def list(
        self, db: Session, *, state: str | None = None, limit: int = 50
    ) -> list[SelfDevDefectRow]:
        query = select(SelfDevDefectRow).order_by(SelfDevDefectRow.created_at.desc())
        if state:
            query = query.where(SelfDevDefectRow.state == state)
        return list(db.scalars(query.limit(limit)))

    def pending(self, db: Session) -> list[SelfDevDefectRow]:
        return list(
            db.scalars(
                select(SelfDevDefectRow)
                .where(SelfDevDefectRow.state.in_(tuple(PENDING_STATES)))
                .order_by(SelfDevDefectRow.finished_at.desc())
            )
        )

    def active_count(self, db: Session) -> int:
        return int(
            db.scalar(
                select(func.count())
                .select_from(SelfDevDefectRow)
                .where(SelfDevDefectRow.state.in_(tuple(ACTIVE_STATES)))
            )
            or 0
        )

    def tokens_used_since(self, db: Session, since: datetime) -> int:
        return int(
            db.scalar(
                select(func.coalesce(func.sum(SelfDevDefectRow.tokens_used), 0)).where(
                    SelfDevDefectRow.finished_at >= since
                )
            )
            or 0
        )

    def budget_report(self, db: Session, *, now: datetime | None = None) -> dict[str, Any]:
        """Req 618-620 in one dict: what is spent, what is left, what is running, what the
        disk has. Every number a bound the owner can read beside the bound itself."""
        now = now or _utcnow()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        used = self.tokens_used_since(db, day_start)
        policy = self.policy
        free: int | None = None
        disk_ok = True
        if policy.worktrees_root is not None:
            probe = policy.worktrees_root
            while not probe.exists() and probe.parent != probe:
                probe = probe.parent
            try:
                free = int(self._disk_free(probe))
                disk_ok = free >= policy.min_free_bytes
            except OSError:
                free = None
                disk_ok = False
        active = self.active_count(db)
        return {
            "tokens_used_today": used,
            "daily_token_budget": policy.daily_token_budget,
            "tokens_remaining": max(0, policy.daily_token_budget - used),
            "token_budget_ok": used < policy.daily_token_budget,
            "active": active,
            "max_parallel": policy.max_parallel,
            "parallel_ok": active < policy.max_parallel,
            "disk_free_bytes": free,
            "min_free_bytes": policy.min_free_bytes,
            "disk_ok": disk_ok,
            "worktrees_root": str(policy.worktrees_root) if policy.worktrees_root else None,
        }

    def status(self, db: Session, *, now: datetime | None = None) -> dict[str, Any]:
        counts = {
            state: int(count)
            for state, count in db.execute(
                select(SelfDevDefectRow.state, func.count()).group_by(SelfDevDefectRow.state)
            )
        }
        return {
            "enabled": self.enabled,
            "counts": counts,
            "queued": counts.get(STATE_QUEUED, 0),
            "awaiting_owner": counts.get(STATE_AWAITING_OWNER, 0),
            "budget": self.budget_report(db, now=now),
            "last_refusal": self.last_refusal,
        }

    # ------------------------------------------------------------------ the worker's side

    def expire_stale_claims(self, db: Session, *, now: datetime | None = None) -> int:
        """A worker that died mid-run leaves a claim; past the TTL the row is queued again
        (req 615: the scheduler's one housekeeping duty)."""
        now = now or _utcnow()
        cutoff = now - timedelta(seconds=self.policy.claim_ttl_s)
        stale = list(
            db.scalars(
                select(SelfDevDefectRow).where(
                    SelfDevDefectRow.state.in_(tuple(ACTIVE_STATES)),
                    SelfDevDefectRow.claimed_at < cutoff,
                )
            )
        )
        for row in stale:
            row.state = STATE_QUEUED
            row.claimed_by = None
            row.claimed_at = None
            row.error_class = "claim_expired"
            row.error_message = f"the worker did not finish within {self.policy.claim_ttl_s:.0f}s"
        if stale:
            db.commit()
        return len(stale)

    def claim(self, db: Session, *, worker_id: str, now: datetime | None = None) -> ClaimVerdict:
        """The oldest queued defect, if every bound allows one more run."""
        if not self.enabled:
            self.last_refusal = ERROR_SELFDEV_DISABLED
            return ClaimVerdict(None, ERROR_SELFDEV_DISABLED)
        now = now or _utcnow()
        report = self.budget_report(db, now=now)
        if not report["parallel_ok"]:
            self.last_refusal = ERROR_PARALLEL_LIMIT
            return ClaimVerdict(None, ERROR_PARALLEL_LIMIT)
        if not report["token_budget_ok"]:
            self.last_refusal = ERROR_BUDGET_EXHAUSTED
            return ClaimVerdict(None, ERROR_BUDGET_EXHAUSTED)
        if not report["disk_ok"]:
            self.last_refusal = ERROR_DISK_FLOOR
            return ClaimVerdict(None, ERROR_DISK_FLOOR)
        row = db.scalar(
            select(SelfDevDefectRow)
            .where(SelfDevDefectRow.state == STATE_QUEUED)
            .order_by(SelfDevDefectRow.priority.asc(), SelfDevDefectRow.created_at.asc())
            .limit(1)
        )
        if row is None:
            self.last_refusal = ""
            return ClaimVerdict(None, "queue_empty")
        row.state = STATE_CLAIMED
        row.claimed_by = worker_id[:120]
        row.claimed_at = now
        db.commit()
        db.refresh(row)
        self.last_refusal = ""
        return ClaimVerdict(row)

    def start(self, db: Session, defect_id: uuid.UUID | str, *, run_id: str) -> SelfDevDefectRow:
        row = self.get(db, defect_id)
        if row.state != STATE_CLAIMED:
            raise ValueError(ERROR_NOT_CLAIMABLE)
        row.state = STATE_RUNNING
        row.run_id = run_id
        row.started_at = _utcnow()
        db.commit()
        db.refresh(row)
        return row

    def finish(
        self, db: Session, defect_id: uuid.UUID | str, record: dict[str, Any]
    ) -> SelfDevDefectRow:
        """The engine's record lands on the row and the promotion class is consumed
        (req 585): the row's class is the supervisor's when the bridge set one, the
        engine's derivation otherwise; NEVER_AUTO_PROMOTE is kept on the row and the
        entry says ``never_auto_promote``. A red candidate CI opens ONE follow-up (req 603)."""
        row = self.get(db, defect_id)
        if row.state not in ACTIVE_STATES:
            raise ValueError(ERROR_NOT_CLAIMABLE)
        run = {key: record.get(key) for key in _RUN_KEYS if key in record}
        for attempt in run.get("attempts") or []:
            for check in attempt.get("checks") or []:
                detail = check.get("detail")
                if isinstance(detail, str):
                    check["detail"] = detail[-MAX_RUN_JSON_ATTEMPT_DETAIL:]
        row.run_json = run
        row.run_id = str(record.get("run_id") or row.run_id or "")
        row.candidate_sha = record.get("candidate_sha") or None
        row.branch = record.get("branch") or None
        model = record.get("model") or {}
        row.tokens_used = int(model.get("input_tokens", 0) or 0) + int(
            model.get("output_tokens", 0) or 0
        )
        engine_class = record.get("promotion_class")
        if row.promotion_class is None and engine_class in PROMOTION_CLASSES:
            row.promotion_class = engine_class
        status = str(record.get("status") or "")
        row.state = _STATE_BY_ENGINE_STATUS.get(status, STATE_FAILED)
        row.finished_at = _utcnow()
        if row.state != STATE_AWAITING_OWNER:
            row.error_class = status.lower() or "failed"
            row.error_message = str(record.get("reason") or record.get("error") or "")[:2000]
        db.flush()
        self._ledger(
            db,
            event_type=EVENT_TYPE_SELFDEV_RUN_ENDED,
            action="selfdev.run_ended",
            summary=f"koşu bitti: {row.title} → {row.state}",
            detail={
                "defect_id": str(row.id),
                "run_id": row.run_id,
                "status": status,
                "candidate_sha": row.candidate_sha,
                "promotion_class": row.promotion_class,
                "security_review_passed": (run.get("security_review") or {}).get("passed"),
                "gate": (run.get("gate") or {}).get("state"),
                "shadow": (run.get("shadow") or {}).get("state"),
            },
        )
        if row.state == STATE_AWAITING_OWNER:
            self._ledger(
                db,
                event_type=EVENT_TYPE_SELFDEV_CANDIDATE_READY,
                action="selfdev.candidate_ready",
                summary=f"aday sahip onayı bekliyor: {row.title}",
                detail={
                    "defect_id": str(row.id),
                    "candidate_sha": row.candidate_sha,
                    "promotion_class": row.promotion_class,
                    "never_auto_promote": row.promotion_class == PROMOTION_NEVER_AUTO_PROMOTE,
                },
            )
        db.commit()
        db.refresh(row)
        ci = run.get("candidate_ci") or {}
        if row.state == STATE_AWAITING_OWNER and str(ci.get("state")) == "failure":
            self.reconcile_ci(db, row.id, state="failure", detail=str(ci.get("detail") or ""))
            db.refresh(row)
        return row

    def ci_round_of(self, db: Session, row: SelfDevDefectRow) -> int:
        rounds = 0
        current = row
        while current.parent_id is not None and rounds <= self.policy.max_ci_fix_rounds:
            parent = db.get(SelfDevDefectRow, current.parent_id)
            if parent is None:
                break
            rounds += 1
            current = parent
        return rounds

    def reconcile_ci(
        self, db: Session, defect_id: uuid.UUID | str, *, state: str, detail: str = ""
    ) -> SelfDevDefectRow | None:
        """The CI failure -> fix loop (req 603): a red CI on a waiting candidate becomes a
        NEW queued defect that names the parent, its branch and CI's words - bounded by
        ``max_ci_fix_rounds`` so a red that never turns green cannot spawn for ever."""
        row = self.get(db, defect_id)
        run = dict(row.run_json or {})
        run["candidate_ci"] = {"state": state, "detail": detail[:2000]}
        row.run_json = run
        if state != "failure" or row.state != STATE_AWAITING_OWNER:
            db.commit()
            return None
        rounds = self.ci_round_of(db, row)
        if rounds >= self.policy.max_ci_fix_rounds:
            row.error_class = "ci_fix_rounds_exhausted"
            row.error_message = f"CI red after {rounds} fix rounds; the owner decides"
            db.commit()
            return None
        existing = db.scalar(
            select(SelfDevDefectRow).where(
                SelfDevDefectRow.parent_id == row.id,
                SelfDevDefectRow.source == SOURCE_CI_FAILURE,
            )
        )
        if existing is not None:
            db.commit()
            return existing
        db.commit()
        return self.intake(
            db,
            title=f"CI kırmızı: {row.title}"[:200],
            evidence=(
                f"candidate {row.candidate_sha} on {row.branch} failed CI (round {rounds + 1}):\n"
                f"{detail}"
            ),
            source=SOURCE_CI_FAILURE,
            kind=DEFECT_KIND_BUG,
            scope=list(row.scope_json or []),
            failing_test=row.failing_test,
            session_id=row.session_id,
            opportunity_id=row.opportunity_id,
            parent_id=row.id,
            promotion_class=row.promotion_class,
            priority=row.priority,
        )

    # ------------------------------------------------------------------ the owner's side

    def _decide(
        self,
        db: Session,
        defect_id: uuid.UUID | str,
        capability: OwnerCapability,
        *,
        decision: str,
        note: str | None,
    ) -> SelfDevDefectRow:
        if not isinstance(capability, OwnerCapability):
            raise AuthorityError(
                "a self-development decision requires a verified owner session capability",
                reason="not_an_owner_capability",
            )
        row = self.get(db, defect_id)
        if row.state != STATE_AWAITING_OWNER:
            raise ValueError(ERROR_NOT_AWAITING_OWNER)
        row.decision = decision
        row.decided_at = _utcnow()
        row.decided_by = f"owner_session:{capability.session_id}"[:64]
        row.decision_note = (note or "").strip()[:2000] or None
        row.state = STATE_APPROVED if decision == DECISION_APPROVED else STATE_REJECTED
        db.flush()
        self._ledger(
            db,
            event_type=EVENT_TYPE_SELFDEV_DECIDED,
            action=f"selfdev.{decision}",
            summary=(
                f"sahip adayı {'onayladı' if decision == DECISION_APPROVED else 'reddetti'}: "
                f"{row.title}"
            ),
            detail={
                "defect_id": str(row.id),
                "candidate_sha": row.candidate_sha,
                "branch": row.branch,
                "promotion_class": row.promotion_class,
                "decision": decision,
                # The sentence that keeps req 624 a fact and not a hope.
                "promoted": False,
            },
        )
        db.commit()
        db.refresh(row)
        return row

    def approve(
        self,
        db: Session,
        defect_id: uuid.UUID | str,
        capability: OwnerCapability,
        *,
        note: str | None = None,
    ) -> SelfDevDefectRow:
        """The owner's yes, recorded. Promotes nothing (req 624)."""
        return self._decide(db, defect_id, capability, decision=DECISION_APPROVED, note=note)

    def reject(
        self,
        db: Session,
        defect_id: uuid.UUID | str,
        capability: OwnerCapability,
        *,
        note: str | None = None,
    ) -> SelfDevDefectRow:
        return self._decide(db, defect_id, capability, decision=DECISION_REJECTED, note=note)


def owner_error(error_class: str, exc: Exception, *, where: str) -> Any:
    """The owner-language detail for a refusal, through the one helper every route uses."""
    return log_and_detail(error_class, exc, where=where)
