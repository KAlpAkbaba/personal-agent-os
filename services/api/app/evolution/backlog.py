"""The evolution backlog: lifecycle law and persistence.

This module owns two things and nothing else:

1. the **legal-transition table** for an opportunity, including the two
   transitions that no amount of engine confidence can produce;
2. the storage layer over ``evolution_opportunities``.

Everything that talks to the ledger, publishes UI state, checks authority or
answers HTTP lives in ``app/evolution/service.py`` and ``routes.py``. Keeping
the law here means it can be read, reviewed and tested without a database, a
FastAPI app or a running engine.

The lifecycle
-------------

::

    IDEA -> RESEARCHING -> DESIGN_READY -> BUILDING -> TESTING -> EVALUATING
         -> SHADOW_READY ==(owner)==> OWNER_APPROVED -> QUALIFYING -> LIVE

    off-ramps at (almost) any point: REJECTED, SUPERSEDED, QUARANTINED
    after LIVE or QUALIFYING:         ROLLED_BACK

Everything left of ``SHADOW_READY`` is lab work: research, design, code, tests,
benchmarks, security review, packaging. The engine drives all of it on its own
and that is the point of the phase — it should not need the owner to make a
candidate.

``SHADOW_READY`` is the wall. It means: *this candidate is built, tested,
benchmarked, reviewed and explainable, and it has touched nothing in
production.* The next state cannot be reached by the engine at all.

Two transitions are special
---------------------------

``* -> OWNER_APPROVED``
    Only ``ActorKind.OWNER`` may enter it. A system or lab actor attempting it
    raises, and the raise is not a policy check that a future refactor could
    forget: ``assert_transition`` refuses on the actor argument, and the
    service layer additionally requires an owner-session capability that lab
    code cannot construct (``app/evolution/authority.py``).

``QUALIFYING -> LIVE``
    Requires (a) the opportunity to carry a recorded owner approval, and (b) a
    :class:`ReleaseEvidenceProvider` to confirm an owner-approved release
    actually exists. The default provider,
    :class:`NullReleaseEvidenceProvider`, knows about no releases, so out of
    the box LIVE is unreachable. That is the fail-safe direction: a subsystem
    that has not been wired to real release evidence must refuse to declare
    something live, not assume it.

Why an explicit table rather than "any forward move"
----------------------------------------------------

An ordered list of states with "you may move forward" is one rename away from
allowing ``BUILDING -> LIVE``. The table is exhaustive and the states not in a
predecessor's set are refused, so adding a state to the enum without adding it
to the table makes it unreachable rather than universally reachable.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.models import OPPORTUNITY_STATUSES, EvolutionOpportunity
from app.logging import get_logger

logger = get_logger("app.evolution.backlog")

SessionFactory = Callable[[], AbstractContextManager[Session]]

MAX_TITLE = 200
MAX_STATEMENT = 8000
MAX_EVIDENCE_REFS = 32
MAX_REF = 256


class OpportunityStatus(StrEnum):
    """The backlog lifecycle. Values match ``models.OPPORTUNITY_STATUSES``."""

    IDEA = "idea"
    RESEARCHING = "researching"
    DESIGN_READY = "design_ready"
    BUILDING = "building"
    TESTING = "testing"
    EVALUATING = "evaluating"
    SHADOW_READY = "shadow_ready"
    #: The engine has finished and is ASKING. It may enter this itself - saying "I need you"
    #: is not an act of production authority - and it may go no further alone.
    OWNER_APPROVAL_REQUIRED = "owner_approval_required"
    #: Legacy spelling of OWNER_AUTHORIZED. Rows carry it; it means the same thing.
    OWNER_APPROVED = "owner_approved"
    #: The authenticated owner said yes. Only an owner actor may enter it.
    OWNER_AUTHORIZED = "owner_authorized"
    QUALIFYING = "qualifying"
    DEPLOYING = "deploying"
    VERIFYING = "verifying"
    LIVE = "live"
    #: Deployment or verification failed. The only way out is rollback.
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    QUARANTINED = "quarantined"
    ROLLED_BACK = "rolled_back"


assert {s.value for s in OpportunityStatus} == set(OPPORTUNITY_STATUSES), (
    "the lifecycle enum and the database CHECK constraint must not drift"
)


class ActorKind(StrEnum):
    """Who is asking for a transition.

    ``LAB`` is the Evolution Engine acting on its own. ``SYSTEM`` is a
    production-side supervisor (qualification, rollback) that holds production
    authority. ``OWNER`` is the single human authority, and is the only actor
    that may grant approval.
    """

    OWNER = "owner"
    SYSTEM = "system"
    LAB = "lab"


#: The exhaustive legal-transition table. A target not listed under the current
#: status is refused; a status with an empty set is terminal.
LEGAL_TRANSITIONS: Final[dict[OpportunityStatus, frozenset[OpportunityStatus]]] = {
    OpportunityStatus.IDEA: frozenset(
        {
            OpportunityStatus.RESEARCHING,
            OpportunityStatus.REJECTED,
            OpportunityStatus.SUPERSEDED,
        }
    ),
    OpportunityStatus.RESEARCHING: frozenset(
        {
            OpportunityStatus.DESIGN_READY,
            OpportunityStatus.REJECTED,
            OpportunityStatus.SUPERSEDED,
        }
    ),
    OpportunityStatus.DESIGN_READY: frozenset(
        {
            OpportunityStatus.BUILDING,
            OpportunityStatus.REJECTED,
            OpportunityStatus.SUPERSEDED,
        }
    ),
    OpportunityStatus.BUILDING: frozenset(
        {
            OpportunityStatus.TESTING,
            OpportunityStatus.REJECTED,
            OpportunityStatus.QUARANTINED,
            OpportunityStatus.SUPERSEDED,
        }
    ),
    # Tests can send a candidate back to the bench; that is normal, not failure.
    OpportunityStatus.TESTING: frozenset(
        {
            OpportunityStatus.EVALUATING,
            OpportunityStatus.BUILDING,
            OpportunityStatus.REJECTED,
            OpportunityStatus.QUARANTINED,
        }
    ),
    OpportunityStatus.EVALUATING: frozenset(
        {
            OpportunityStatus.SHADOW_READY,
            OpportunityStatus.BUILDING,
            OpportunityStatus.REJECTED,
            OpportunityStatus.QUARANTINED,
        }
    ),
    # The wall. Asking is allowed; crossing is not.
    OpportunityStatus.SHADOW_READY: frozenset(
        {
            OpportunityStatus.OWNER_APPROVAL_REQUIRED,
            OpportunityStatus.OWNER_APPROVED,
            OpportunityStatus.REJECTED,
            OpportunityStatus.QUARANTINED,
            OpportunityStatus.SUPERSEDED,
        }
    ),
    #: Waiting on the human. The engine may enter this state itself - "I am finished and I
    #: need you" is not an act of production authority - and it may go no further alone.
    OpportunityStatus.OWNER_APPROVAL_REQUIRED: frozenset(
        {
            OpportunityStatus.OWNER_AUTHORIZED,
            OpportunityStatus.REJECTED,
            OpportunityStatus.QUARANTINED,
            OpportunityStatus.SUPERSEDED,
        }
    ),
    OpportunityStatus.OWNER_APPROVED: frozenset(
        {
            OpportunityStatus.QUALIFYING,
            OpportunityStatus.REJECTED,
            OpportunityStatus.QUARANTINED,
        }
    ),
    OpportunityStatus.OWNER_AUTHORIZED: frozenset(
        {
            OpportunityStatus.QUALIFYING,
            OpportunityStatus.REJECTED,
            OpportunityStatus.QUARANTINED,
        }
    ),
    OpportunityStatus.QUALIFYING: frozenset(
        {
            OpportunityStatus.DEPLOYING,
            OpportunityStatus.FAILED,
            OpportunityStatus.ROLLED_BACK,
            OpportunityStatus.QUARANTINED,
            OpportunityStatus.REJECTED,
        }
    ),
    #: A deployment in flight can only finish, or fail. It can never jump to LIVE: LIVE is
    #: something VERIFYING concludes, not something DEPLOYING announces.
    OpportunityStatus.DEPLOYING: frozenset({OpportunityStatus.VERIFYING, OpportunityStatus.FAILED}),
    OpportunityStatus.VERIFYING: frozenset({OpportunityStatus.LIVE, OpportunityStatus.FAILED}),
    #: The only way out of a failed release is backwards. Not to LIVE, not to a retry that
    #: silently reuses the half-applied state.
    OpportunityStatus.FAILED: frozenset({OpportunityStatus.ROLLING_BACK}),
    OpportunityStatus.ROLLING_BACK: frozenset(
        {OpportunityStatus.ROLLED_BACK, OpportunityStatus.QUARANTINED}
    ),
    OpportunityStatus.LIVE: frozenset(
        {OpportunityStatus.ROLLED_BACK, OpportunityStatus.SUPERSEDED}
    ),
    OpportunityStatus.ROLLED_BACK: frozenset(
        {
            OpportunityStatus.QUARANTINED,
            OpportunityStatus.REJECTED,
            OpportunityStatus.SUPERSEDED,
        }
    ),
    # A quarantined candidate is parked, not deleted: it can be closed out but
    # never re-enter the build path without a new opportunity and new evidence.
    OpportunityStatus.QUARANTINED: frozenset(
        {OpportunityStatus.REJECTED, OpportunityStatus.SUPERSEDED}
    ),
    OpportunityStatus.REJECTED: frozenset(),
    OpportunityStatus.SUPERSEDED: frozenset(),
}

assert set(LEGAL_TRANSITIONS) == set(OpportunityStatus), (
    "every status needs a row in the transition table, even a terminal one"
)

#: Statuses only an owner action may enter.
OWNER_ONLY_STATUSES: Final[frozenset[OpportunityStatus]] = frozenset(
    {OpportunityStatus.OWNER_APPROVED, OpportunityStatus.OWNER_AUTHORIZED}
)

#: Statuses the lab actor may never enter — everything past the approval wall.
#: A lab actor asking for one of these is refused before the transition table
#: is even consulted.
LAB_FORBIDDEN_STATUSES: Final[frozenset[OpportunityStatus]] = frozenset(
    {
        OpportunityStatus.OWNER_APPROVED,
        OpportunityStatus.OWNER_AUTHORIZED,
        OpportunityStatus.QUALIFYING,
        OpportunityStatus.DEPLOYING,
        OpportunityStatus.VERIFYING,
        OpportunityStatus.LIVE,
        OpportunityStatus.ROLLING_BACK,
        OpportunityStatus.ROLLED_BACK,
    }
)

#: Statuses requiring proof that an owner-approved release exists.
RELEASE_REQUIRED_STATUSES: Final[frozenset[OpportunityStatus]] = frozenset(
    {OpportunityStatus.DEPLOYING, OpportunityStatus.LIVE}
)

#: Nothing leaves these.
TERMINAL_STATUSES: Final[frozenset[OpportunityStatus]] = frozenset(
    status for status, targets in LEGAL_TRANSITIONS.items() if not targets
)

#: The lab half of the lifecycle: everything the engine may drive by itself.
LAB_STATUSES: Final[tuple[OpportunityStatus, ...]] = (
    OpportunityStatus.IDEA,
    OpportunityStatus.RESEARCHING,
    OpportunityStatus.DESIGN_READY,
    OpportunityStatus.BUILDING,
    OpportunityStatus.TESTING,
    OpportunityStatus.EVALUATING,
    OpportunityStatus.SHADOW_READY,
)

#: What the owner is waiting on.
#: Everything that is genuinely waiting on the owner and on nothing else.
#:
#: OWNER_APPROVAL_REQUIRED was added to the lifecycle in M18 and means precisely this,
#: but was not listed here - so a candidate that had asked for the owner explicitly was
#: the one thing the Approval Center could not see. SHADOW_READY stays: a candidate that
#: has passed its gates is waiting on the owner whether or not anything has formally
#: asked yet.
PENDING_OWNER_STATUSES: Final[tuple[OpportunityStatus, ...]] = (
    OpportunityStatus.SHADOW_READY,
    OpportunityStatus.OWNER_APPROVAL_REQUIRED,
)

# --------------------------------------------------------------- evidence

#: Evidence kinds an opportunity may cite. An opportunity is only ever created
#: from something that already happened, which is why there is no "hunch" kind.
EVIDENCE_KINDS: Final[frozenset[str]] = frozenset(
    {"ledger_event", "incident", "lesson", "capability_gap"}
)

#: What may sit in the `source` column (the idempotency namespace).
OPPORTUNITY_SOURCES: Final[frozenset[str]] = frozenset(
    {"ledger_event", "incident", "lesson", "capability_gap", "owner"}
)


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """One pointer at something that actually happened."""

    kind: str
    ref: str
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind, "ref": self.ref}
        if self.note:
            payload["note"] = self.note
        return payload


def _validation(message: str, **details: Any) -> EvolutionError:
    return EvolutionError(EvolutionErrorClass.VALIDATION_ERROR, message, details=dict(details))


def parse_evidence(refs: Iterable[Mapping[str, Any]] | None) -> list[EvidenceRef]:
    """Normalise and bound the evidence list. Empty is an error, not a default.

    "Created from evidence only" is meaningless if the evidence list may be
    empty, so the refusal happens here, in the layer everything else calls.
    """
    if refs is None:
        raise _validation(
            "an opportunity must cite the evidence that produced it",
            expected_kinds=sorted(EVIDENCE_KINDS),
        )
    items = list(refs)
    if not items:
        raise _validation(
            "an opportunity must cite at least one piece of evidence; the "
            "engine does not invent work for itself",
            expected_kinds=sorted(EVIDENCE_KINDS),
        )
    if len(items) > MAX_EVIDENCE_REFS:
        raise _validation("too many evidence refs", maximum=MAX_EVIDENCE_REFS)

    parsed: list[EvidenceRef] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise _validation("evidence ref must be an object", index=index)
        kind = str(item.get("kind", ""))
        ref = str(item.get("ref", ""))
        if kind not in EVIDENCE_KINDS:
            raise _validation("unknown evidence kind", index=index, expected=sorted(EVIDENCE_KINDS))
        if not ref or len(ref) > MAX_REF:
            raise _validation("evidence ref must be a bounded non-empty string", index=index)
        note = item.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > MAX_REF):
            raise _validation("evidence note must be a bounded string", index=index)
        key = (kind, ref)
        if key in seen:
            continue
        seen.add(key)
        parsed.append(EvidenceRef(kind=kind, ref=ref, note=note))
    return parsed


# ------------------------------------------------------- release evidence


@runtime_checkable
class ReleaseEvidenceProvider(Protocol):
    """Confirms that an owner-approved release exists for an opportunity.

    Deliberately a seam rather than a direct query: the evolution package must
    not import the release/deployment modules at all (see
    ``app/evolution/authority.py``'s import guard). The integrator injects an
    implementation backed by the real release records; until then the null
    provider refuses, and LIVE is unreachable.
    """

    name: str

    def approved_release(self, opportunity: Mapping[str, Any]) -> str | None:
        """Return the release ref, or ``None`` when there is no approved one."""
        ...


class NullReleaseEvidenceProvider:
    """Knows about no releases, so nothing can be declared LIVE."""

    name = "null"

    def approved_release(self, opportunity: Mapping[str, Any]) -> str | None:
        return None


class StaticReleaseEvidenceProvider:
    """Explicit in-process mapping of opportunity id -> release ref.

    Used by tests and by a deliberate local configuration. It never reads a
    caller-supplied field to decide that a release exists.
    """

    name = "static"

    def __init__(self, releases: Mapping[str, str]) -> None:
        self._releases = {str(k): str(v) for k, v in releases.items()}

    def approved_release(self, opportunity: Mapping[str, Any]) -> str | None:
        return self._releases.get(str(opportunity.get("opportunity_id")))


# ----------------------------------------------------------- transition law


def coerce_status(value: Any, *, field_name: str = "status") -> OpportunityStatus:
    if isinstance(value, OpportunityStatus):
        return value
    try:
        return OpportunityStatus(str(value))
    except ValueError as exc:
        raise _validation(
            "unknown opportunity status",
            field=field_name,
            expected=[s.value for s in OpportunityStatus],
        ) from exc


def coerce_actor(value: Any, *, field_name: str = "actor") -> ActorKind:
    if isinstance(value, ActorKind):
        return value
    try:
        return ActorKind(str(value))
    except ValueError as exc:
        raise _validation(
            "unknown actor",
            field=field_name,
            expected=[a.value for a in ActorKind],
        ) from exc


def _lifecycle_violation(message: str, **details: Any) -> EvolutionError:
    return EvolutionError(EvolutionErrorClass.LIFECYCLE_VIOLATION, message, details=dict(details))


def assert_transition(
    current: OpportunityStatus,
    target: OpportunityStatus,
    actor: ActorKind,
    *,
    approved_by: str | None = None,
    release_ref: str | None = None,
) -> None:
    """Refuse anything the lifecycle does not allow. Raises or returns None.

    Order matters: actor rules are checked before the transition table so that
    a lab actor asking for ``LIVE`` is told it lacks the authority, not that
    the transition is out of order. The reasons are different and the audit
    trail should say which one applied.
    """
    if actor is ActorKind.LAB and target in LAB_FORBIDDEN_STATUSES:
        raise _lifecycle_violation(
            f"a lab actor may never move an opportunity to {target}; the "
            "Evolution Engine's authority stops at shadow_ready",
            current=str(current),
            target=str(target),
            actor=str(actor),
        )
    if target in OWNER_ONLY_STATUSES and actor is not ActorKind.OWNER:
        raise _lifecycle_violation(
            f"{target} can only be entered by an owner action",
            current=str(current),
            target=str(target),
            actor=str(actor),
            required_actor=str(ActorKind.OWNER),
        )
    if current is target:
        raise _lifecycle_violation(
            "an opportunity is already in that status",
            current=str(current),
            target=str(target),
        )
    allowed = LEGAL_TRANSITIONS[current]
    if target not in allowed:
        raise _lifecycle_violation(
            f"{current} -> {target} is not a legal transition",
            current=str(current),
            target=str(target),
            legal_targets=sorted(str(s) for s in allowed),
        )
    if target in RELEASE_REQUIRED_STATUSES:
        if not approved_by:
            raise _lifecycle_violation(
                f"{target} requires a recorded owner approval on the opportunity",
                current=str(current),
                target=str(target),
            )
        if not release_ref:
            raise _lifecycle_violation(
                f"{target} requires an owner-approved release; no release evidence was available",
                current=str(current),
                target=str(target),
            )


def legal_targets(current: OpportunityStatus, actor: ActorKind) -> list[str]:
    """What ``actor`` could legally ask for next — for the /policy surface."""
    targets = LEGAL_TRANSITIONS[current]
    if actor is ActorKind.LAB:
        targets = targets - LAB_FORBIDDEN_STATUSES
    if actor is not ActorKind.OWNER:
        targets = targets - OWNER_ONLY_STATUSES
    return sorted(str(s) for s in targets)


def transition_table() -> dict[str, list[str]]:
    return {
        str(status): sorted(str(t) for t in targets)
        for status, targets in LEGAL_TRANSITIONS.items()
    }


# ---------------------------------------------------------------- storage


def _utcnow() -> datetime:
    return datetime.now(UTC)


def opportunity_dict(row: EvolutionOpportunity) -> dict[str, Any]:
    return {
        "opportunity_id": str(row.opportunity_id),
        "title": row.title,
        "statement": row.statement,
        "origin": list(row.origin_json or []),
        "status": row.status,
        "scores": {
            "owner_relevance": row.owner_relevance,
            "expected_utility": row.expected_utility,
            "recurrence": row.recurrence,
            "confidence": row.confidence,
            "engineering_cost": row.engineering_cost,
            "operational_risk": row.operational_risk,
            "composite": row.composite,
        },
        "workspace_ref": row.workspace_ref,
        "candidate_ref": row.candidate_ref,
        "approved_by": row.approved_by,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "source": row.source,
        "source_ref": row.source_ref,
        "detail": dict(row.detail_json or {}),
    }


class OpportunityBacklog:
    """Persistence for ``evolution_opportunities``. No authority, no ledger.

    Every method takes and returns plain dicts so the service layer can hand
    them to the ledger, the UI-state publisher and FastAPI without dragging a
    detached ORM row across a session boundary.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    # -------------------------------------------------------------- create

    def create(
        self,
        *,
        title: str,
        statement: str,
        evidence: Sequence[EvidenceRef],
        scores: Mapping[str, Any],
        source: str,
        source_ref: str,
        detail: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not title or len(title) > MAX_TITLE:
            raise _validation("title must be a bounded non-empty string", maximum=MAX_TITLE)
        if not statement or len(statement) > MAX_STATEMENT:
            raise _validation("statement must be a bounded non-empty string", maximum=MAX_STATEMENT)
        if source not in OPPORTUNITY_SOURCES:
            raise _validation("unknown source", expected=sorted(OPPORTUNITY_SOURCES))
        if not source_ref or len(source_ref) > MAX_REF:
            raise _validation("source_ref must be a bounded non-empty string")
        if not evidence:
            raise _validation("an opportunity must cite the evidence that produced it")

        with self._session_factory() as session:
            existing = self._find(session, source, source_ref)
            if existing is not None:
                # Idempotent: the same incident does not spawn a second
                # opportunity when detection runs again.
                return opportunity_dict(existing)
            row = EvolutionOpportunity(
                title=title,
                statement=statement,
                origin_json=[ref.to_dict() for ref in evidence],
                status=str(OpportunityStatus.IDEA),
                owner_relevance=float(scores["owner_relevance"]),
                expected_utility=float(scores["expected_utility"]),
                recurrence=float(scores["recurrence"]),
                confidence=float(scores["confidence"]),
                engineering_cost=float(scores["engineering_cost"]),
                operational_risk=float(scores["operational_risk"]),
                composite=float(scores["composite"]),
                source=source,
                source_ref=source_ref,
                detail_json=dict(detail or {}),
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return opportunity_dict(row)

    # ---------------------------------------------------------------- read

    @staticmethod
    def _find(session: Session, source: str, source_ref: str) -> EvolutionOpportunity | None:
        return session.execute(
            select(EvolutionOpportunity).where(
                EvolutionOpportunity.source == source,
                EvolutionOpportunity.source_ref == source_ref,
            )
        ).scalar_one_or_none()

    def get(self, opportunity_id: uuid.UUID | str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._row(session, opportunity_id)
            return opportunity_dict(row)

    @staticmethod
    def _row(session: Session, opportunity_id: uuid.UUID | str) -> EvolutionOpportunity:
        try:
            key = (
                opportunity_id
                if isinstance(opportunity_id, uuid.UUID)
                else uuid.UUID(str(opportunity_id))
            )
        except ValueError as exc:
            raise EvolutionError(
                EvolutionErrorClass.NOT_FOUND,
                "unknown opportunity",
                details={"opportunity_id": str(opportunity_id)},
            ) from exc
        row = session.get(EvolutionOpportunity, key)
        if row is None:
            raise EvolutionError(
                EvolutionErrorClass.NOT_FOUND,
                "unknown opportunity",
                details={"opportunity_id": str(opportunity_id)},
            )
        return row

    def list(
        self,
        *,
        status: str | None = None,
        statuses: Iterable[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        wanted: list[str] = []
        if status is not None:
            wanted.append(str(coerce_status(status)))
        if statuses is not None:
            wanted.extend(str(coerce_status(s)) for s in statuses)
        stmt = select(EvolutionOpportunity)
        if wanted:
            stmt = stmt.where(EvolutionOpportunity.status.in_(sorted(set(wanted))))
        stmt = stmt.order_by(
            EvolutionOpportunity.composite.desc(), EvolutionOpportunity.created_at.desc()
        ).limit(max(1, min(int(limit), 500)))
        with self._session_factory() as session:
            rows = session.execute(stmt).scalars().all()
            return [opportunity_dict(row) for row in rows]

    # -------------------------------------------------------------- update

    def apply_transition(
        self,
        opportunity_id: uuid.UUID | str,
        *,
        target: OpportunityStatus,
        actor: ActorKind,
        reason: str | None = None,
        workspace_ref: str | None = None,
        candidate_ref: str | None = None,
        release_ref: str | None = None,
        approval: tuple[str, datetime] | None = None,
        release_lookup: Callable[[dict[str, Any]], str | None] | None = None,
        extra_detail: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate and persist one transition inside a single session.

        ``release_lookup`` is called with the *current* opportunity so the LIVE
        check consults release evidence for this specific opportunity rather
        than a globally cached answer. ``extra_detail`` is merged into the
        opportunity's ``detail`` blob (never into the transition-history
        entry) — used to carry a computed risk assessment or an execution
        report alongside the status change that produced it.
        """
        with self._session_factory() as session:
            row = self._row(session, opportunity_id)
            current = coerce_status(row.status)
            snapshot = opportunity_dict(row)

            approved_by = approval[0] if approval else row.approved_by
            resolved_release = release_ref
            if target in RELEASE_REQUIRED_STATUSES and resolved_release is None:
                resolved_release = release_lookup(snapshot) if release_lookup is not None else None

            assert_transition(
                current,
                target,
                actor,
                approved_by=approved_by,
                release_ref=resolved_release,
            )

            row.status = str(target)
            row.updated_at = _utcnow()
            if workspace_ref is not None:
                row.workspace_ref = workspace_ref[:512]
            if candidate_ref is not None:
                row.candidate_ref = candidate_ref[:512]
            if approval is not None:
                row.approved_by = approval[0][:64]
                row.approved_at = approval[1]

            detail = dict(row.detail_json or {})
            history = list(detail.get("transitions") or [])
            entry: dict[str, Any] = {
                "from": str(current),
                "to": str(target),
                "actor": str(actor),
                "at": row.updated_at.isoformat(),
            }
            if reason:
                entry["reason"] = reason[:512]
            if resolved_release:
                entry["release_ref"] = resolved_release[:256]
                detail["release_ref"] = resolved_release[:256]
            history.append(entry)
            detail["transitions"] = history[-100:]
            if extra_detail:
                detail.update(dict(extra_detail))
            row.detail_json = detail

            session.commit()
            session.refresh(row)
            result = opportunity_dict(row)
        logger.info(
            "opportunity_transitioned",
            opportunity_id=result["opportunity_id"],
            from_status=str(current),
            to_status=str(target),
            actor=str(actor),
        )
        return result

    def merge_detail(
        self,
        opportunity_id: uuid.UUID | str,
        fields: Mapping[str, Any],
        *,
        allowed_statuses: Iterable[OpportunityStatus] | None = None,
    ) -> dict[str, Any]:
        """Merge ``fields`` into ``detail_json`` without changing the status.

        Used to attach metadata computed ABOUT a candidate — its derived risk
        assessment, a preflight report — to the opportunity row it describes,
        at a point where no lifecycle transition is happening. Refuses when the
        opportunity's current status is not in ``allowed_statuses`` (when
        given): declaring a candidate's own footprint is only meaningful while
        the candidate is still the lab's to describe, not after the owner has
        already looked at a wall it no longer matches.
        """
        with self._session_factory() as session:
            row = self._row(session, opportunity_id)
            current = coerce_status(row.status)
            if allowed_statuses is not None and current not in set(allowed_statuses):
                raise _lifecycle_violation(
                    f"cannot annotate an opportunity in {current}",
                    current=str(current),
                    allowed=sorted(str(s) for s in allowed_statuses),
                )
            detail = dict(row.detail_json or {})
            detail.update(dict(fields))
            row.detail_json = detail
            row.updated_at = _utcnow()
            session.commit()
            session.refresh(row)
            return opportunity_dict(row)

    def pending_owner(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.list(statuses=[str(s) for s in PENDING_OWNER_STATUSES], limit=limit)


__all__ = [
    "EVIDENCE_KINDS",
    "LAB_FORBIDDEN_STATUSES",
    "LAB_STATUSES",
    "LEGAL_TRANSITIONS",
    "MAX_EVIDENCE_REFS",
    "MAX_STATEMENT",
    "MAX_TITLE",
    "OPPORTUNITY_SOURCES",
    "OWNER_ONLY_STATUSES",
    "PENDING_OWNER_STATUSES",
    "RELEASE_REQUIRED_STATUSES",
    "TERMINAL_STATUSES",
    "ActorKind",
    "EvidenceRef",
    "NullReleaseEvidenceProvider",
    "OpportunityBacklog",
    "OpportunityStatus",
    "ReleaseEvidenceProvider",
    "StaticReleaseEvidenceProvider",
    "assert_transition",
    "coerce_actor",
    "coerce_status",
    "legal_targets",
    "opportunity_dict",
    "parse_evidence",
    "transition_table",
]
