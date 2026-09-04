"""Evolution Engine service: evidence in, audited lifecycle out.

This is the layer that composes the other four:

- ``authority.py`` decides whether the caller may do the thing at all;
- ``backlog.py`` decides whether the lifecycle allows it;
- ``scoring.py`` decides how important it is;
- the Activity Ledger and the UI-state surface record that it happened.

Three properties are worth stating explicitly, because they are the ones a
future change is most likely to erode.

**Evidence only.** :meth:`EvolutionService.create_from_evidence` refuses an
opportunity with no evidence refs, and verifies every ref whose source it can
reach: a cited ledger event or incident that does not exist is a validation
error, not a note in a JSON blob. Evidence kinds whose subsystem is not
present in this build (experience lessons, while ``app.experience`` is still
being written) are recorded as ``verified: false`` rather than silently
accepted as true — the distinction is preserved instead of averaged away.

**The engine runs lab-scoped.** The service's default authority is
``LabAuthority.issue("evolution.engine")``. It therefore holds no production
grant, and the production-side transitions do not consult it: they require a
``ProductionAuthority`` argument, which can only be minted from a verified
owner session. There is no configuration that changes this.

**Owner approval is a different method, not a different argument.**
``advance()`` refuses ``OWNER_APPROVED`` outright and points the caller at
:meth:`approve`, which requires an :class:`~app.evolution.authority.OwnerCapability`.
Making it a separate entry point means an attacker (or a careless refactor)
cannot reach owner approval by varying a string in a request body.

Ledger vocabulary
-----------------

``app/ledger/vocabulary.py`` is a *closed* vocabulary owned by the ledger
module, and it reserves twelve ``evolution.*`` event types. Four backlog
statuses have no honest match among them (``researching``, ``qualifying``, and
``rejected``/``quarantined`` when they are not the result of a failed test
run). Rather than mislabel those events, this module records no ledger event
for them and says so in :data:`LEDGER_EVENT_FOR_STATUS`; every transition is
still captured in the opportunity's own ``detail_json["transitions"]`` history
and in the UI-state stream. Closing that gap needs three new reserved event
types in the ledger's vocabulary, which is another module's to change.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Final

from sqlalchemy.orm import Session

from app.evolution.authority import (
    Authority,
    AuthorityError,
    Grant,
    LabAuthority,
    OwnerCapability,
    Scope,
    guard_production_action,
    root_policies,
)
from app.evolution.backlog import (
    EVIDENCE_KINDS,
    LEGAL_TRANSITIONS,
    OPPORTUNITY_SOURCES,
    ActorKind,
    EvidenceRef,
    NullReleaseEvidenceProvider,
    OpportunityBacklog,
    OpportunityStatus,
    ReleaseEvidenceProvider,
    coerce_actor,
    coerce_status,
    parse_evidence,
    transition_table,
)
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.scoring import SCORE_FIELDS, score_from_mapping, weights
from app.ledger.service import ActivityEvent
from app.ledger.service import record as record_activity
from app.ledger.vocabulary import (
    EVENT_TYPE_EVOLUTION_BUILD_COMPLETED,
    EVENT_TYPE_EVOLUTION_BUILD_STARTED,
    EVENT_TYPE_EVOLUTION_DEPLOYED,
    EVENT_TYPE_EVOLUTION_IDEA_CREATED,
    EVENT_TYPE_EVOLUTION_MODULE_DESIGNED,
    EVENT_TYPE_EVOLUTION_OWNER_APPROVAL_REQUIRED,
    EVENT_TYPE_EVOLUTION_ROLLED_BACK,
    EVENT_TYPE_EVOLUTION_SHADOW_READY,
    EVENT_TYPE_EVOLUTION_TESTS_FAILED,
    EVENT_TYPE_EVOLUTION_TESTS_PASSED,
    PRODUCTION_STATE_APPROVAL_REQUIRED,
    PRODUCTION_STATE_BUILT,
    PRODUCTION_STATE_DEPLOYED,
    PRODUCTION_STATE_DESIGNED,
    PRODUCTION_STATE_IDEA,
    PRODUCTION_STATE_NA,
    PRODUCTION_STATE_ROLLED_BACK,
    PRODUCTION_STATE_SHADOW_READY,
    PRODUCTION_STATE_TESTED,
    SEVERITY_INFO,
    SEVERITY_NOTICE,
    SEVERITY_WARNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_STARTED,
    SUBSYSTEM_EVOLUTION,
)
from app.logging import get_logger

logger = get_logger("app.evolution.service")

SessionFactory = Callable[[], AbstractContextManager[Session]]

#: The version of this contract, surfaced by ``GET /v1/evolution/policy``.
EVOLUTION_VERSION: Final[int] = 1

#: The subject the engine runs as. It is not a credential; it is a label on a
#: lab-scoped authority that holds no production grant.
ENGINE_SUBJECT: Final[str] = "evolution.engine"

#: Statuses on the production side of the wall. Reaching one requires a
#: production authority, which is minted from an owner session and never held
#: by the engine.
PRODUCTION_SIDE_STATUSES: Final[frozenset[OpportunityStatus]] = frozenset(
    {
        OpportunityStatus.QUALIFYING,
        OpportunityStatus.LIVE,
        OpportunityStatus.ROLLED_BACK,
    }
)

#: The lab grant each lab-side transition consumes. Statuses on the production
#: side map to None: there is deliberately no lab grant that reaches them.
REQUIRED_GRANT_FOR_STATUS: Final[dict[OpportunityStatus, Grant | None]] = {
    OpportunityStatus.IDEA: Grant.PROPOSE_CANDIDATE,
    OpportunityStatus.RESEARCHING: Grant.READ_EVIDENCE,
    OpportunityStatus.DESIGN_READY: Grant.PROPOSE_CANDIDATE,
    OpportunityStatus.BUILDING: Grant.WRITE_LAB_WORKSPACE,
    OpportunityStatus.TESTING: Grant.RUN_LAB_TESTS,
    OpportunityStatus.EVALUATING: Grant.BENCHMARK_CANDIDATE,
    OpportunityStatus.SHADOW_READY: Grant.MARK_SHADOW_READY,
    OpportunityStatus.REJECTED: Grant.PROPOSE_CANDIDATE,
    OpportunityStatus.SUPERSEDED: Grant.PROPOSE_CANDIDATE,
    OpportunityStatus.QUARANTINED: Grant.PROPOSE_CANDIDATE,
    OpportunityStatus.OWNER_APPROVED: None,
    OpportunityStatus.QUALIFYING: None,
    OpportunityStatus.LIVE: None,
    OpportunityStatus.ROLLED_BACK: None,
}

#: The production action name each production-side status is guarded by.
PRODUCTION_ACTION_FOR_STATUS: Final[dict[OpportunityStatus, str]] = {
    OpportunityStatus.QUALIFYING: "write_production_database",
    OpportunityStatus.LIVE: "deploy_release",
    OpportunityStatus.ROLLED_BACK: "rollback_production",
}

#: UI-state keys published as the engine works, so the owner can watch progress
#: without being handed a wall of text (constitution §3).
UI_STATE_FOR_STATUS: Final[dict[OpportunityStatus, str]] = {
    OpportunityStatus.RESEARCHING: "evolution.researching",
    OpportunityStatus.DESIGN_READY: "evolution.designing",
    OpportunityStatus.BUILDING: "evolution.building",
    OpportunityStatus.TESTING: "evolution.testing",
    OpportunityStatus.EVALUATING: "evolution.testing",
    OpportunityStatus.SHADOW_READY: "evolution.shadow_ready",
}

#: (event_type, ledger status, production_state, severity) per backlog status.
#: ``None`` means the closed ledger vocabulary has no honest match — see the
#: module docstring.
LEDGER_EVENT_FOR_STATUS: Final[dict[OpportunityStatus, tuple[str, str, str, str] | None]] = {
    OpportunityStatus.IDEA: (
        EVENT_TYPE_EVOLUTION_IDEA_CREATED,
        STATUS_COMPLETED,
        PRODUCTION_STATE_IDEA,
        SEVERITY_INFO,
    ),
    OpportunityStatus.RESEARCHING: None,
    OpportunityStatus.DESIGN_READY: (
        EVENT_TYPE_EVOLUTION_MODULE_DESIGNED,
        STATUS_COMPLETED,
        PRODUCTION_STATE_DESIGNED,
        SEVERITY_INFO,
    ),
    OpportunityStatus.BUILDING: (
        EVENT_TYPE_EVOLUTION_BUILD_STARTED,
        STATUS_STARTED,
        PRODUCTION_STATE_DESIGNED,
        SEVERITY_INFO,
    ),
    OpportunityStatus.TESTING: (
        EVENT_TYPE_EVOLUTION_BUILD_COMPLETED,
        STATUS_COMPLETED,
        PRODUCTION_STATE_BUILT,
        SEVERITY_INFO,
    ),
    OpportunityStatus.EVALUATING: (
        EVENT_TYPE_EVOLUTION_TESTS_PASSED,
        STATUS_COMPLETED,
        PRODUCTION_STATE_TESTED,
        SEVERITY_INFO,
    ),
    OpportunityStatus.SHADOW_READY: (
        EVENT_TYPE_EVOLUTION_SHADOW_READY,
        STATUS_COMPLETED,
        PRODUCTION_STATE_SHADOW_READY,
        SEVERITY_NOTICE,
    ),
    OpportunityStatus.OWNER_APPROVED: (
        EVENT_TYPE_EVOLUTION_OWNER_APPROVAL_REQUIRED,
        STATUS_COMPLETED,
        PRODUCTION_STATE_APPROVAL_REQUIRED,
        SEVERITY_NOTICE,
    ),
    OpportunityStatus.QUALIFYING: None,
    OpportunityStatus.LIVE: (
        EVENT_TYPE_EVOLUTION_DEPLOYED,
        STATUS_COMPLETED,
        PRODUCTION_STATE_DEPLOYED,
        SEVERITY_NOTICE,
    ),
    OpportunityStatus.ROLLED_BACK: (
        EVENT_TYPE_EVOLUTION_ROLLED_BACK,
        STATUS_COMPLETED,
        PRODUCTION_STATE_ROLLED_BACK,
        SEVERITY_WARNING,
    ),
    # Only honest when the rejection came out of a test/eval run; see _ledger_spec.
    OpportunityStatus.REJECTED: None,
    OpportunityStatus.QUARANTINED: None,
    OpportunityStatus.SUPERSEDED: None,
}

_TEST_PHASE_STATUSES = frozenset({OpportunityStatus.TESTING, OpportunityStatus.EVALUATING})

_STATUS_TR: Final[dict[OpportunityStatus, str]] = {
    OpportunityStatus.IDEA: "fikir",
    OpportunityStatus.RESEARCHING: "araştırma",
    OpportunityStatus.DESIGN_READY: "tasarım hazır",
    OpportunityStatus.BUILDING: "geliştirme",
    OpportunityStatus.TESTING: "test",
    OpportunityStatus.EVALUATING: "değerlendirme",
    OpportunityStatus.SHADOW_READY: "gölge çalıştırmaya hazır",
    OpportunityStatus.OWNER_APPROVED: "sahip onayı verildi",
    OpportunityStatus.QUALIFYING: "yeterlilik testi",
    OpportunityStatus.LIVE: "yayında",
    OpportunityStatus.REJECTED: "reddedildi",
    OpportunityStatus.SUPERSEDED: "yerini yenisi aldı",
    OpportunityStatus.QUARANTINED: "karantinada",
    OpportunityStatus.ROLLED_BACK: "geri alındı",
}


# ------------------------------------------------------------ UI-state seam


class UiStatePublisher:
    """What the service needs from the UI-state subsystem: one method.

    ``app/uistate`` is being built by another agent, so this module resolves it
    lazily and degrades to a no-op rather than importing it at module load. A
    missing UI surface must never stop the engine from working or block a
    lifecycle transition — presentation and task completion are separate
    concepts (constitution §4).
    """

    name = "resolved"

    def publish(self, key: str, payload: Mapping[str, Any]) -> bool:
        publisher = _resolve_uistate_publisher()
        if publisher is None:
            return False
        try:
            publisher(key, dict(payload))
        except Exception:  # noqa: BLE001 - a UI failure never breaks the engine
            logger.warning("uistate_publish_failed", ui_state=key)
            return False
        return True


class NullUiStatePublisher:
    """Publishes nowhere. Used where a UI stream is meaningless (tests, jobs)."""

    name = "null"

    def publish(self, key: str, payload: Mapping[str, Any]) -> bool:
        return False


class RecordingUiStatePublisher:
    """Keeps published states in memory — for tests and for local inspection."""

    name = "recording"

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    def publish(self, key: str, payload: Mapping[str, Any]) -> bool:
        self.published.append((key, dict(payload)))
        return True


def _resolve_uistate_publisher() -> Callable[[str, dict[str, Any]], Any] | None:
    """Find a publish callable in ``app.uistate`` without importing it eagerly.

    Tries the conventional names in order. Returns ``None`` — never raises —
    when the package is absent or exposes none of them.
    """
    try:
        from app import uistate  # type: ignore[attr-defined]
    except ImportError:
        return None
    module: Any = uistate
    try:
        from app.uistate import service as uistate_service  # type: ignore[attr-defined]

        module = uistate_service
    except ImportError:
        pass
    for attribute in ("publish", "publish_state", "set_state"):
        candidate = getattr(module, attribute, None)
        if callable(candidate):
            return candidate
    return None


# ------------------------------------------------------- evidence resolution


def _verify_ledger_event(session: Session, ref: str) -> bool:
    from app.ledger.models import ActivityEventRow

    try:
        key = uuid.UUID(ref)
    except ValueError:
        return False
    return session.get(ActivityEventRow, key) is not None


def _verify_incident(session: Session, ref: str) -> bool | None:
    """True/False when the incidents table is reachable, None when it is not."""
    try:
        from app.selfhealing.models import Incident
    except ImportError:  # pragma: no cover - selfhealing is present in this build
        return None
    try:
        key = uuid.UUID(ref)
    except ValueError:
        return False
    return session.get(Incident, key) is not None


def _verify_capability_gap(session: Session, ref: str) -> bool:
    from app.evolution.models import CapabilityGap

    try:
        key = uuid.UUID(ref)
    except ValueError:
        return False
    return session.get(CapabilityGap, key) is not None


def _verify_lesson(session: Session, ref: str) -> bool | None:
    """Experience lessons — ``app.experience`` may not exist yet.

    Imported defensively: while the module is absent the ref is recorded as
    unverified (``None``) instead of being treated as either true or invalid.
    """
    try:
        from app import experience  # type: ignore[attr-defined]
    except ImportError:
        return None
    models: Any = None
    try:
        from app.experience import models as experience_models  # type: ignore

        models = experience_models
    except ImportError:
        models = getattr(experience, "models", None)
    if models is None:
        return None
    row_type = None
    for name in ("Lesson", "ExperienceLesson", "LessonRow"):
        candidate = getattr(models, name, None)
        if candidate is not None:
            row_type = candidate
            break
    if row_type is None:
        return None
    try:
        key: Any = uuid.UUID(ref)
    except ValueError:
        key = ref
    try:
        return session.get(row_type, key) is not None
    except Exception:  # noqa: BLE001 - an unfamiliar schema is "unverifiable"
        return None


# ------------------------------------------------------------------ service


class EvolutionService:
    """The Evolution Engine's own API. Lab-scoped unless told otherwise."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        authority: Authority | None = None,
        ui: UiStatePublisher | NullUiStatePublisher | RecordingUiStatePublisher | None = None,
        release_evidence: ReleaseEvidenceProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.backlog = OpportunityBacklog(session_factory)
        # Default and only sane value: the engine holds lab grants. An explicit
        # production authority here would be a bug, so it is refused.
        resolved = authority or LabAuthority.issue(ENGINE_SUBJECT)
        if resolved.scope is not Scope.LAB:
            raise AuthorityError(
                "the Evolution Engine service always runs lab-scoped; "
                "production actions take a ProductionAuthority per call",
                subject=resolved.subject,
            )
        self.authority = resolved
        self.ui = ui if ui is not None else UiStatePublisher()
        self.release_evidence = release_evidence or NullReleaseEvidenceProvider()

    # ------------------------------------------------------------- creation

    def create_from_evidence(
        self,
        *,
        title: str,
        statement: str,
        evidence_refs: Iterable[Mapping[str, Any]] | None,
        scores: Mapping[str, Any],
        source: str,
        source_ref: str,
        detail: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a new opportunity. Refuses anything not grounded in evidence."""
        self.authority.require(Grant.READ_EVIDENCE, action="create_from_evidence")
        self.authority.require(Grant.PROPOSE_CANDIDATE, action="create_from_evidence")

        parsed = parse_evidence(evidence_refs)
        verified = self._verify_evidence(parsed)
        score = score_from_mapping(dict(scores))

        payload = dict(detail or {})
        payload["scoring_rationale"] = list(score.rationale)
        payload["risk_vetoed"] = score.vetoed
        payload["evidence_verification"] = verified
        payload["evolution_version"] = EVOLUTION_VERSION

        opportunity = self.backlog.create(
            title=title,
            statement=statement,
            evidence=parsed,
            scores=score.to_dict(),
            source=source,
            source_ref=source_ref,
            detail=payload,
        )
        self._audit(opportunity, OpportunityStatus.IDEA, ActorKind.LAB)
        return opportunity

    def _verify_evidence(self, refs: list[EvidenceRef]) -> list[dict[str, Any]]:
        """Check every ref we can reach; refuse the ones we can reach and miss."""
        results: list[dict[str, Any]] = []
        checkers: dict[str, Callable[[Session, str], bool | None]] = {
            "ledger_event": _verify_ledger_event,
            "incident": _verify_incident,
            "capability_gap": _verify_capability_gap,
            "lesson": _verify_lesson,
        }
        with self._session_factory() as session:
            for ref in refs:
                checker = checkers[ref.kind]
                try:
                    outcome = checker(session, ref.ref)
                except Exception:  # noqa: BLE001 - a missing table is "unverifiable"
                    outcome = None
                if outcome is False:
                    raise EvolutionError(
                        EvolutionErrorClass.VALIDATION_ERROR,
                        "cited evidence does not exist; an opportunity may not "
                        "be created from a reference the system cannot confirm",
                        details={"kind": ref.kind, "ref": ref.ref},
                    )
                results.append({"kind": ref.kind, "ref": ref.ref, "verified": bool(outcome)})
        return results

    # ------------------------------------------------------------ lifecycle

    def advance(
        self,
        opportunity_id: uuid.UUID | str,
        *,
        target: str | OpportunityStatus,
        actor: str | ActorKind,
        reason: str | None = None,
        workspace_ref: str | None = None,
        candidate_ref: str | None = None,
        production_authority: Authority | None = None,
    ) -> dict[str, Any]:
        """Move an opportunity to ``target``, or refuse and say why.

        Lab-side targets consume a lab grant. Production-side targets consume a
        production action guard and ignore the engine's own authority entirely.
        ``OWNER_APPROVED`` is not reachable from here at all.
        """
        wanted = coerce_status(target, field_name="target")
        who = coerce_actor(actor)

        if wanted is OpportunityStatus.OWNER_APPROVED:
            raise AuthorityError(
                "owner approval is not a status change; it is an owner action. "
                "Use POST /v1/evolution/opportunities/{id}/approve, which "
                "requires a verified owner session.",
                target=str(wanted),
            )

        if wanted in PRODUCTION_SIDE_STATUSES:
            if who is ActorKind.LAB:
                raise AuthorityError(
                    "a lab actor may not drive a production-side transition",
                    target=str(wanted),
                    actor=str(who),
                )
            guard_production_action(PRODUCTION_ACTION_FOR_STATUS[wanted], production_authority)
        else:
            grant = REQUIRED_GRANT_FOR_STATUS[wanted]
            assert grant is not None  # every lab-side status maps to a grant
            self.authority.require(grant, action=f"advance:{wanted}")

        updated = self.backlog.apply_transition(
            opportunity_id,
            target=wanted,
            actor=who,
            reason=reason,
            workspace_ref=workspace_ref,
            candidate_ref=candidate_ref,
            release_lookup=self.release_evidence.approved_release,
        )
        self._audit(updated, wanted, who, reason=reason)
        return updated

    def approve(
        self,
        opportunity_id: uuid.UUID | str,
        capability: OwnerCapability,
        *,
        note: str | None = None,
    ) -> dict[str, Any]:
        """The human authority gate. The only path into ``OWNER_APPROVED``.

        ``capability`` is an :class:`OwnerCapability`, which can only be minted
        from a verified, unscoped owner session. There is no code path from a
        lab-scoped caller to a value of this type.
        """
        if not isinstance(capability, OwnerCapability):
            raise AuthorityError(
                "owner approval requires a verified owner session capability",
                reason="not_an_owner_capability",
            )
        approved_by = f"owner_session:{capability.session_id}"[:64]
        updated = self.backlog.apply_transition(
            opportunity_id,
            target=OpportunityStatus.OWNER_APPROVED,
            actor=ActorKind.OWNER,
            reason=note,
            approval=(approved_by, capability.issued_at),
            release_lookup=self.release_evidence.approved_release,
        )
        self._audit(updated, OpportunityStatus.OWNER_APPROVED, ActorKind.OWNER, reason=note)
        logger.info("opportunity_owner_approved", opportunity_id=updated["opportunity_id"])
        return updated

    # ------------------------------------------------------------- read side

    def get(self, opportunity_id: uuid.UUID | str) -> dict[str, Any]:
        return self.backlog.get(opportunity_id)

    def list_opportunities(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return self.backlog.list(status=status, limit=limit)

    def shadow_ready(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.backlog.list(status=str(OpportunityStatus.SHADOW_READY), limit=limit)

    def pending_owner_actions(self, *, limit: int = 100) -> dict[str, Any]:
        """What the engine has finished and the owner has not yet answered.

        This is the deliberate other half of the boundary: the engine can build
        a candidate all the way to shadow-ready on its own, and then it has to
        wait here. Everything in ``awaiting_approval`` is complete, tested and
        explainable — and none of it is running in production.
        """
        awaiting = self.backlog.pending_owner(limit=limit)
        return {
            "awaiting_approval": awaiting,
            "count": len(awaiting),
            "action": "POST /v1/evolution/opportunities/{opportunity_id}/approve",
            "note": (
                "Nothing here is deployed. Owner approval is the only transition "
                "out of shadow_ready, and no system or lab actor can perform it."
            ),
            "evolution_version": EVOLUTION_VERSION,
        }

    def policy(self) -> dict[str, Any]:
        """The engine's declared contract — lifecycle, weights, grants, roots."""
        return {
            "evolution_version": EVOLUTION_VERSION,
            "lifecycle": {
                "statuses": [str(s) for s in OpportunityStatus],
                "transitions": transition_table(),
                "owner_only_targets": [str(OpportunityStatus.OWNER_APPROVED)],
                "lab_forbidden_targets": sorted(str(s) for s in PRODUCTION_SIDE_STATUSES)
                + [str(OpportunityStatus.OWNER_APPROVED)],
                "release_required_targets": [str(OpportunityStatus.LIVE)],
                "terminal_statuses": sorted(
                    str(s) for s, targets in LEGAL_TRANSITIONS.items() if not targets
                ),
            },
            "scoring": {
                "inputs": list(SCORE_FIELDS),
                "weights": weights(),
            },
            "authority": {
                "engine_scope": str(Scope.LAB),
                "engine_subject": ENGINE_SUBJECT,
                "grants": self.authority.to_dict()["grants"],
                "lab_grants": sorted(str(g) for g in LabAuthority.DEFAULT_GRANTS),
                "production_grants_never_granted": sorted(
                    str(g) for g in Grant if g not in LabAuthority.DEFAULT_GRANTS
                ),
            },
            "root_policies": list(root_policies()),
            "evidence": {
                "kinds": sorted(EVIDENCE_KINDS),
                "sources": sorted(OPPORTUNITY_SOURCES),
                "rule": "an opportunity is only created from evidence that already exists",
            },
            "release_evidence_provider": self.release_evidence.name,
        }

    # ------------------------------------------------------------- auditing

    def _audit(
        self,
        opportunity: Mapping[str, Any],
        status: OpportunityStatus,
        actor: ActorKind,
        *,
        reason: str | None = None,
    ) -> None:
        """Write the ledger event (when the closed vocabulary has one) and
        publish the UI state. Neither failure is allowed to unwind a committed
        transition; both are logged and reported in the caller's result."""
        self._record_ledger(opportunity, status, actor, reason=reason)
        ui_state = UI_STATE_FOR_STATUS.get(status)
        if ui_state is not None:
            self.ui.publish(
                ui_state,
                {
                    "opportunity_id": opportunity["opportunity_id"],
                    "title": opportunity["title"],
                    "status": str(status),
                    "composite": opportunity["scores"]["composite"],
                },
            )

    def _ledger_spec(
        self, opportunity: Mapping[str, Any], status: OpportunityStatus
    ) -> tuple[str, str, str, str] | None:
        spec = LEDGER_EVENT_FOR_STATUS.get(status)
        if spec is not None:
            return spec
        if status in {OpportunityStatus.REJECTED, OpportunityStatus.QUARANTINED}:
            # Honest only when the failure really came out of a test/eval run.
            history = (opportunity.get("detail") or {}).get("transitions") or []
            previous = history[-1].get("from") if history else None
            if previous in {str(s) for s in _TEST_PHASE_STATUSES}:
                return (
                    EVENT_TYPE_EVOLUTION_TESTS_FAILED,
                    STATUS_FAILED,
                    PRODUCTION_STATE_NA,
                    SEVERITY_WARNING,
                )
        return None

    def _record_ledger(
        self,
        opportunity: Mapping[str, Any],
        status: OpportunityStatus,
        actor: ActorKind,
        *,
        reason: str | None = None,
    ) -> bool:
        spec = self._ledger_spec(opportunity, status)
        events: list[ActivityEvent] = []
        if spec is not None:
            events.append(self._activity_event(opportunity, status, actor, spec, reason=reason))
        if status is OpportunityStatus.SHADOW_READY:
            # Reaching the wall is also the moment the owner is asked.
            events.append(
                self._activity_event(
                    opportunity,
                    status,
                    actor,
                    (
                        EVENT_TYPE_EVOLUTION_OWNER_APPROVAL_REQUIRED,
                        STATUS_PENDING,
                        PRODUCTION_STATE_APPROVAL_REQUIRED,
                        SEVERITY_NOTICE,
                    ),
                    reason=reason,
                    suffix="approval_required",
                    summary=(
                        f"'{opportunity['title']}' gölge çalıştırmaya hazır; "
                        "yayına alınması için sahip onayı gerekiyor."
                    ),
                )
            )
        if not events:
            return False
        try:
            with self._session_factory() as session:
                for event in events:
                    record_activity(session, event)
        except Exception:  # noqa: BLE001 - audit failure is logged, never silent
            logger.error(
                "evolution_ledger_write_failed",
                opportunity_id=opportunity["opportunity_id"],
                to_status=str(status),
            )
            return False
        return True

    @staticmethod
    def _activity_event(
        opportunity: Mapping[str, Any],
        status: OpportunityStatus,
        actor: ActorKind,
        spec: tuple[str, str, str, str],
        *,
        reason: str | None = None,
        suffix: str | None = None,
        summary: str | None = None,
    ) -> ActivityEvent:
        event_type, ledger_status, production_state, severity = spec
        opportunity_id = opportunity["opportunity_id"]
        source_ref = f"evolution_opportunities:{opportunity_id}:{status}"
        if suffix:
            source_ref = f"{source_ref}:{suffix}"
        detail: dict[str, Any] = {
            "actor": str(actor),
            "status": str(status),
            "composite": opportunity["scores"]["composite"],
            "workspace_ref": opportunity.get("workspace_ref"),
            "candidate_ref": opportunity.get("candidate_ref"),
            "approved_by": opportunity.get("approved_by"),
        }
        if reason:
            detail["reason"] = reason[:512]
        return ActivityEvent(
            event_type=event_type,
            subsystem=SUBSYSTEM_EVOLUTION,
            action=f"opportunity.{status}",
            factual_summary=summary
            or (
                f"Evrim fırsatı '{opportunity['title']}' "
                f"{_STATUS_TR[status]} durumuna geçti ({actor})."
            ),
            source="live",
            source_ref=source_ref[:256],
            status=ledger_status,
            severity=severity,
            production_state=production_state,
            module="evolution.backlog",
            version=str(EVOLUTION_VERSION),
            related_module_id=str(opportunity_id)[:128],
            evidence_refs=list(opportunity.get("origin") or []),
            detail_json=detail,
            occurred_at=_parse_ts(opportunity.get("updated_at")),
        )


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


__all__ = [
    "ENGINE_SUBJECT",
    "EVOLUTION_VERSION",
    "LEDGER_EVENT_FOR_STATUS",
    "PRODUCTION_ACTION_FOR_STATUS",
    "PRODUCTION_SIDE_STATUSES",
    "REQUIRED_GRANT_FOR_STATUS",
    "UI_STATE_FOR_STATUS",
    "EvolutionService",
    "NullUiStatePublisher",
    "RecordingUiStatePublisher",
    "UiStatePublisher",
]
