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
module. It first reserved twelve ``evolution.*`` event types, and five backlog
statuses had no honest match among them (``researching``, ``qualifying``, and
``rejected``/``quarantined``/``superseded`` when they are not the result of a
failed test run). Rather than mislabel those events this module recorded
nothing for them, which left a real audit gap: a candidate could be parked in
quarantine and the ledger — and therefore the owner's briefing — would never
say so. The vocabulary now reserves the five missing types, and **every**
backlog transition writes exactly one ledger event.

The one place specificity beats the table is a rejection or quarantine that
came out of a test/eval run: that is ``evolution.tests_failed``, which says
*why*, so :meth:`_ledger_spec` consults the test-phase history first and falls
back to the generic closure event otherwise.
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
    LAB_FORBIDDEN_STATUSES,
    LAB_STATUSES,
    LEGAL_TRANSITIONS,
    OPPORTUNITY_SOURCES,
    OWNER_ONLY_STATUSES,
    RELEASE_REQUIRED_STATUSES,
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
from app.evolution.risk import SECOND_CONFIRMATION_FLOOR, RiskTier, derive_risk_tier
from app.evolution.scoring import SCORE_FIELDS, score_from_mapping, weights
from app.ledger import briefing as ledger_briefing
from app.ledger.service import ActivityEvent
from app.ledger.service import record as record_activity
from app.ledger.vocabulary import (
    EVENT_TYPE_EVOLUTION_BUILD_COMPLETED,
    EVENT_TYPE_EVOLUTION_BUILD_STARTED,
    EVENT_TYPE_EVOLUTION_DEPLOYED,
    EVENT_TYPE_EVOLUTION_DEPLOYING,
    EVENT_TYPE_EVOLUTION_FAILED,
    EVENT_TYPE_EVOLUTION_IDEA_CREATED,
    EVENT_TYPE_EVOLUTION_MODULE_DESIGNED,
    EVENT_TYPE_EVOLUTION_OWNER_APPROVAL_REQUIRED,
    EVENT_TYPE_EVOLUTION_OWNER_AUTHORIZED,
    EVENT_TYPE_EVOLUTION_QUALIFYING,
    EVENT_TYPE_EVOLUTION_QUARANTINED,
    EVENT_TYPE_EVOLUTION_REJECTED,
    EVENT_TYPE_EVOLUTION_RESEARCHING,
    EVENT_TYPE_EVOLUTION_ROLLED_BACK,
    EVENT_TYPE_EVOLUTION_ROLLING_BACK,
    EVENT_TYPE_EVOLUTION_SHADOW_READY,
    EVENT_TYPE_EVOLUTION_SUPERSEDED,
    EVENT_TYPE_EVOLUTION_TESTS_FAILED,
    EVENT_TYPE_EVOLUTION_TESTS_PASSED,
    EVENT_TYPE_EVOLUTION_VERIFYING,
    PRODUCTION_STATE_APPROVAL_REQUIRED,
    PRODUCTION_STATE_BUILT,
    PRODUCTION_STATE_DEPLOYED,
    PRODUCTION_STATE_DESIGNED,
    PRODUCTION_STATE_IDEA,
    PRODUCTION_STATE_NA,
    PRODUCTION_STATE_REVIEWED,
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
        OpportunityStatus.DEPLOYING,
        OpportunityStatus.VERIFYING,
        OpportunityStatus.LIVE,
        OpportunityStatus.ROLLING_BACK,
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
    # Asking for the owner is not an act of production authority: the engine is allowed to
    # say "I am finished and I need you", and allowed to do nothing further (ADR-0055 §1).
    OpportunityStatus.OWNER_APPROVAL_REQUIRED: Grant.MARK_SHADOW_READY,
    OpportunityStatus.REJECTED: Grant.PROPOSE_CANDIDATE,
    OpportunityStatus.SUPERSEDED: Grant.PROPOSE_CANDIDATE,
    OpportunityStatus.QUARANTINED: Grant.PROPOSE_CANDIDATE,
    OpportunityStatus.OWNER_APPROVED: None,
    OpportunityStatus.OWNER_AUTHORIZED: None,
    OpportunityStatus.QUALIFYING: None,
    OpportunityStatus.DEPLOYING: None,
    OpportunityStatus.VERIFYING: None,
    OpportunityStatus.LIVE: None,
    OpportunityStatus.FAILED: None,
    OpportunityStatus.ROLLING_BACK: None,
    OpportunityStatus.ROLLED_BACK: None,
}

#: The production action name each production-side status is guarded by.
PRODUCTION_ACTION_FOR_STATUS: Final[dict[OpportunityStatus, str]] = {
    OpportunityStatus.QUALIFYING: "write_production_database",
    # The mutation itself is guarded at DEPLOYING, where it actually happens - not at LIVE,
    # which is a conclusion drawn after verification succeeds.
    OpportunityStatus.DEPLOYING: "deploy_release",
    OpportunityStatus.VERIFYING: "write_production_database",
    OpportunityStatus.LIVE: "deploy_release",
    OpportunityStatus.FAILED: "rollback_production",
    OpportunityStatus.ROLLING_BACK: "rollback_production",
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
    # The release path, so an owner-authorised deployment is WATCHABLE and a failure
    # visibly becomes a rollback instead of a silent stop (M18 spec §15).
    OpportunityStatus.OWNER_APPROVAL_REQUIRED: "release.owner_approval_required",
    OpportunityStatus.OWNER_APPROVED: "release.owner_authorized",
    OpportunityStatus.OWNER_AUTHORIZED: "release.owner_authorized",
    OpportunityStatus.QUALIFYING: "release.qualifying",
    OpportunityStatus.DEPLOYING: "release.deploying",
    OpportunityStatus.VERIFYING: "release.verifying",
    OpportunityStatus.LIVE: "release.live",
    OpportunityStatus.FAILED: "release.rollback",
    OpportunityStatus.ROLLING_BACK: "release.rollback",
    OpportunityStatus.ROLLED_BACK: "release.rollback",
}

#: (event_type, ledger status, production_state, severity) per backlog status.
#: Every status maps to an event: an unrecorded transition is an audit gap, and
#: the ledger is the only durable place the owner's briefing reads from.
LEDGER_EVENT_FOR_STATUS: Final[dict[OpportunityStatus, tuple[str, str, str, str] | None]] = {
    OpportunityStatus.IDEA: (
        EVENT_TYPE_EVOLUTION_IDEA_CREATED,
        STATUS_COMPLETED,
        PRODUCTION_STATE_IDEA,
        SEVERITY_INFO,
    ),
    OpportunityStatus.RESEARCHING: (
        EVENT_TYPE_EVOLUTION_RESEARCHING,
        STATUS_STARTED,
        PRODUCTION_STATE_IDEA,
        SEVERITY_INFO,
    ),
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
    # Post-approval, pre-deployment: reviewed and approved, but nothing is live
    # yet, so the production_state must not say "deployed".
    OpportunityStatus.OWNER_APPROVAL_REQUIRED: (
        EVENT_TYPE_EVOLUTION_OWNER_APPROVAL_REQUIRED,
        STATUS_PENDING,
        PRODUCTION_STATE_APPROVAL_REQUIRED,
        SEVERITY_NOTICE,
    ),
    OpportunityStatus.OWNER_AUTHORIZED: (
        EVENT_TYPE_EVOLUTION_OWNER_AUTHORIZED,
        STATUS_COMPLETED,
        PRODUCTION_STATE_REVIEWED,
        SEVERITY_NOTICE,
    ),
    OpportunityStatus.QUALIFYING: (
        EVENT_TYPE_EVOLUTION_QUALIFYING,
        STATUS_STARTED,
        PRODUCTION_STATE_REVIEWED,
        SEVERITY_NOTICE,
    ),
    OpportunityStatus.DEPLOYING: (
        EVENT_TYPE_EVOLUTION_DEPLOYING,
        STATUS_STARTED,
        PRODUCTION_STATE_REVIEWED,
        SEVERITY_NOTICE,
    ),
    OpportunityStatus.VERIFYING: (
        EVENT_TYPE_EVOLUTION_VERIFYING,
        STATUS_STARTED,
        PRODUCTION_STATE_DEPLOYED,
        SEVERITY_NOTICE,
    ),
    OpportunityStatus.FAILED: (
        EVENT_TYPE_EVOLUTION_FAILED,
        STATUS_FAILED,
        PRODUCTION_STATE_DEPLOYED,
        SEVERITY_WARNING,
    ),
    OpportunityStatus.ROLLING_BACK: (
        EVENT_TYPE_EVOLUTION_ROLLING_BACK,
        STATUS_STARTED,
        PRODUCTION_STATE_ROLLED_BACK,
        SEVERITY_WARNING,
    ),
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
    # The generic closures. A rejection or quarantine that came out of a test or
    # eval run is more specific than these and wins in _ledger_spec.
    OpportunityStatus.REJECTED: (
        EVENT_TYPE_EVOLUTION_REJECTED,
        STATUS_COMPLETED,
        PRODUCTION_STATE_NA,
        SEVERITY_INFO,
    ),
    OpportunityStatus.QUARANTINED: (
        EVENT_TYPE_EVOLUTION_QUARANTINED,
        STATUS_FAILED,
        PRODUCTION_STATE_NA,
        SEVERITY_WARNING,
    ),
    OpportunityStatus.SUPERSEDED: (
        EVENT_TYPE_EVOLUTION_SUPERSEDED,
        STATUS_COMPLETED,
        PRODUCTION_STATE_NA,
        SEVERITY_INFO,
    ),
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
    # the owner-authorised release path (ADR-0055 §5)
    OpportunityStatus.OWNER_APPROVAL_REQUIRED: "sahip onayı bekleniyor",
    OpportunityStatus.OWNER_AUTHORIZED: "sahip yetkilendirdi",
    OpportunityStatus.DEPLOYING: "yayına alınıyor",
    OpportunityStatus.VERIFYING: "doğrulanıyor",
    OpportunityStatus.FAILED: "başarısız",
    OpportunityStatus.ROLLING_BACK: "geri alınıyor",
}


# ------------------------------------------------------------ UI-state seam


#: How far through the lab pipeline each status is, as real progress in [0, 1].
#: ``None`` where progress is genuinely unknown — the UI contract says a renderer
#: must not invent a bar for work whose length it cannot know.
_LAB_PROGRESS: Final[dict[OpportunityStatus, float]] = {
    OpportunityStatus.RESEARCHING: 0.2,
    OpportunityStatus.DESIGN_READY: 0.4,
    OpportunityStatus.BUILDING: 0.6,
    OpportunityStatus.TESTING: 0.75,
    OpportunityStatus.EVALUATING: 0.9,
    OpportunityStatus.SHADOW_READY: 1.0,
}


class UiStateBridge:
    """Publishes lab progress to ``app.uistate``, defensively.

    ``app/uistate`` is resolved lazily rather than imported at module load: it
    is a peer subsystem built on its own schedule, and a missing or changed UI
    surface must never stop the engine from working or block a lifecycle
    transition — presentation and task completion are separate concepts
    (constitution §4). Every failure degrades to ``False`` and a warning.

    Content-free by construction: the published event carries the opportunity
    id, its status and its composite score. The opportunity's title and
    statement are owner-authored prose and are deliberately not sent — the UI
    contract carries state, never content.
    """

    name = "uistate"

    def publish(self, key: str, payload: Mapping[str, Any]) -> bool:
        try:
            from app.uistate import UiState
            from app.uistate import publish as uistate_publish
        except ImportError:
            return False
        try:
            state = UiState(key)
        except ValueError:
            logger.warning("uistate_unknown_state", ui_state=key)
            return False
        status = str(payload.get("status") or "")
        metadata: dict[str, Any] = {"composite": payload.get("composite")}
        if payload.get("risk_tier") is not None:
            # `apps/web` reads this key verbatim as `metadata.risk_tier`;
            # never rename it (M18 spec §5).
            metadata["risk_tier"] = payload["risk_tier"]
        try:
            event = uistate_publish(
                state,
                # The UI contract has its own subsystem vocabulary; it happens
                # to agree with the ledger's here, but they are separate lists.
                subsystem="evolution",
                status=status or None,
                module_id=str(payload.get("opportunity_id") or "") or None,
                severity=(SEVERITY_NOTICE if key == "evolution.shadow_ready" else SEVERITY_INFO),
                progress=payload.get("progress"),
                metadata=metadata,
            )
        except Exception:  # noqa: BLE001 - a UI failure never breaks the engine
            logger.warning("uistate_publish_failed", ui_state=key)
            return False
        return event is not None


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
    for name in ("ExperienceLessonRow", "ExperienceLesson", "LessonRow", "Lesson"):
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



def _queue_briefing_quietly(session: Any, row: Any) -> None:
    """Never raises. The ledger row is the durable evidence; a briefing sits on top of it,
    and the courtesy failing must not cost the record."""
    try:
        ledger_briefing.queue_briefing(session, row)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("evolution_briefing_not_queued", event_type=getattr(row, "event_type", "?"))


class EvolutionService:
    """The Evolution Engine's own API. Lab-scoped unless told otherwise."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        authority: Authority | None = None,
        ui: UiStateBridge | NullUiStatePublisher | RecordingUiStatePublisher | None = None,
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
        self.ui = ui if ui is not None else UiStateBridge()
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

        The production-side check considers where the opportunity is COMING
        FROM as well as where it is going. ``QUARANTINED`` and ``REJECTED``
        are ordinary lab-reachable targets from the lab side of the wall (a
        candidate can be quarantined mid-BUILDING with no owner involved at
        all) — but both are *also* legal targets from ``QUALIFYING`` and
        ``ROLLING_BACK`` (an in-flight release can be parked/abandoned), and
        neither target is itself classified as production-side, so a naive
        "check only the target" guard lets a LAB actor divert an opportunity
        that is mid-deployment or mid-rollback into ``quarantined`` with only
        its own default ``propose_candidate`` grant — no production authority
        at all (found while building the M18 release executor; the lifecycle
        table alone does not prevent it, only this check does). Once an
        opportunity is on the production side of the wall, leaving it to
        ANYWHERE requires production authority, regardless of the target's own
        classification.
        """
        wanted = coerce_status(target, field_name="target")
        who = coerce_actor(actor)

        # Both spellings of the owner gate. Reachable only through approve(), which takes
        # an OwnerCapability - so no caller reaches owner authorisation by varying a string
        # in a request body, whichever name they use (ADR-0053 §5, ADR-0055 §4).
        if wanted in OWNER_ONLY_STATUSES:
            raise AuthorityError(
                "owner approval is not a status change; it is an owner action. "
                "Use POST /v1/evolution/opportunities/{id}/approve, which "
                "requires a verified owner session.",
                target=str(wanted),
            )

        current_status = coerce_status(self.backlog.get(opportunity_id)["status"])
        leaves_production_side = current_status in PRODUCTION_SIDE_STATUSES

        if wanted in PRODUCTION_SIDE_STATUSES or leaves_production_side:
            if who is ActorKind.LAB:
                raise AuthorityError(
                    "a lab actor may not drive a production-side transition",
                    target=str(wanted),
                    current=str(current_status),
                    actor=str(who),
                )
            action = PRODUCTION_ACTION_FOR_STATUS.get(wanted) or PRODUCTION_ACTION_FOR_STATUS.get(
                current_status
            )
            guard_production_action(action, production_authority)
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

    # ------------------------------------------------- risk tier (M18 §5)

    def record_release_footprint(
        self,
        opportunity_id: uuid.UUID | str,
        *,
        changed_paths: list[str],
    ) -> dict[str, Any]:
        """Attach the candidate's DERIVED risk tier to its own opportunity row.

        Lab-scoped (the engine knows what its own candidate touches; this is
        no more privileged than ``READ_SOURCE``). Refuses once the candidate
        has already crossed the wall: the footprint a risk tier is computed
        from must be the one the owner actually reviews, not one that can be
        edited out from under an approval that already happened.

        The tier itself is never accepted as an argument — only the path list
        is, and :func:`derive_risk_tier` computes the tier from it. There is
        no way to call this method and assert a tier by hand.
        """
        self.authority.require(Grant.MARK_SHADOW_READY, action="record_release_footprint")
        assessment = derive_risk_tier(changed_paths)
        updated = self.backlog.merge_detail(
            opportunity_id,
            {
                "changed_paths": list(changed_paths),
                "risk_assessment": assessment.to_dict(),
                "risk_tier": int(assessment.tier),
            },
            allowed_statuses=(
                *LAB_STATUSES,
                OpportunityStatus.OWNER_APPROVAL_REQUIRED,
            ),
        )
        logger.info(
            "release_footprint_recorded",
            opportunity_id=updated["opportunity_id"],
            risk_tier=int(assessment.tier),
            paths=len(changed_paths),
        )
        return updated

    def authorize(
        self,
        opportunity_id: uuid.UUID | str,
        capability: OwnerCapability,
        *,
        confirm_high_risk: bool = False,
        note: str | None = None,
    ) -> dict[str, Any]:
        """The owner action that completes the explicit chain into production.

        ``OWNER_APPROVAL_REQUIRED -> OWNER_AUTHORIZED`` (ADR-0055 §5, M18 spec
        §5). Like :meth:`approve`, this is a SEPARATE method rather than a
        status a caller can name through :meth:`advance`, and it requires a
        real :class:`OwnerCapability` — the same non-forgeable proof of a
        verified owner session.

        Tier 3 and above need a SECOND explicit confirmation: the first call
        (``confirm_high_risk=False``, the default) refuses and reports the
        tier and the reasons a human would need to decide; only a second,
        deliberate call with ``confirm_high_risk=True`` proceeds. Tiers 1-2
        proceed on the first call. The tier consulted is the one already
        recorded by :meth:`record_release_footprint` — this method never
        accepts a tier from its caller, so nothing here can be talked down.
        """
        if not isinstance(capability, OwnerCapability):
            raise AuthorityError(
                "owner authorisation requires a verified owner session capability",
                reason="not_an_owner_capability",
            )
        opportunity = self.backlog.get(opportunity_id)
        risk_tier = (opportunity.get("detail") or {}).get("risk_tier")
        if risk_tier is None:
            raise EvolutionError(
                EvolutionErrorClass.LIFECYCLE_VIOLATION,
                "this candidate's risk tier was never derived; call "
                "record_release_footprint before asking the owner to authorise it",
                details={"opportunity_id": str(opportunity_id)},
            )
        tier = RiskTier(int(risk_tier))
        if tier >= SECOND_CONFIRMATION_FLOOR and not confirm_high_risk:
            reasons = (
                (opportunity.get("detail") or {}).get("risk_assessment", {}).get("reasons", [])
            )
            raise AuthorityError(
                f"seviye {int(tier)} ({tier.name.lower()}) bir sürüm için ikinci, açık "
                "bir sahip onayı gerekiyor; bu çağrı confirm_high_risk=True ile "
                "tekrarlanmalı",
                reason="second_confirmation_required",
                risk_tier=int(tier),
                risk_reasons=list(reasons),
            )
        approved_by = f"owner_session:{capability.session_id}"[:64]
        updated = self.backlog.apply_transition(
            opportunity_id,
            target=OpportunityStatus.OWNER_AUTHORIZED,
            actor=ActorKind.OWNER,
            reason=note,
            approval=(approved_by, capability.issued_at),
            release_lookup=self.release_evidence.approved_release,
            extra_detail={"second_confirmation_given": bool(confirm_high_risk)}
            if tier >= SECOND_CONFIRMATION_FLOOR
            else None,
        )
        self._audit(updated, OpportunityStatus.OWNER_AUTHORIZED, ActorKind.OWNER, reason=note)
        logger.info(
            "opportunity_owner_authorized",
            opportunity_id=updated["opportunity_id"],
            risk_tier=int(tier),
        )
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
        awaiting = [self._with_risk(row) for row in self.backlog.pending_owner(limit=limit)]
        return {
            "awaiting_approval": awaiting,
            "count": len(awaiting),
            "action": "POST /v1/evolution/opportunities/{opportunity_id}/approve",
            "note": (
                "Nothing here is deployed. Every transition out of shadow_ready needs an "
                "authenticated owner capability, and no system or lab actor can perform "
                "one; a release at risk tier "
                f"{int(SECOND_CONFIRMATION_FLOOR)} or above needs a second, explicit "
                "confirmation on top of that."
            ),
            "second_confirmation_floor": int(SECOND_CONFIRMATION_FLOOR),
            "evolution_version": EVOLUTION_VERSION,
        }

    @staticmethod
    def _with_risk(row: dict[str, Any]) -> dict[str, Any]:
        """Surface the risk tier alongside the candidate.

        The tier is the single most decision-relevant fact for an owner deciding whether
        to authorise a release, and it was buried in ``detail`` where no reader would find
        it. ``None`` when it has never been derived - which is itself the answer, and the
        Approval Center says so rather than guessing a tier.
        """
        detail = row.get("detail") or {}
        raw = detail.get("risk_tier")
        if raw is None:
            return {
                **row,
                "risk_tier": None,
                "risk_tier_label": None,
                "requires_second_confirmation": None,
                "risk_reasons": [],
            }
        tier = RiskTier(int(raw))
        assessment = detail.get("risk_assessment") or {}
        return {
            **row,
            "risk_tier": int(tier),
            "risk_tier_label": tier.name.lower(),
            "requires_second_confirmation": tier >= SECOND_CONFIRMATION_FLOOR,
            "risk_reasons": list(assessment.get("reasons") or []),
        }

    def policy(self) -> dict[str, Any]:
        """The engine's declared contract — lifecycle, weights, grants, roots."""
        return {
            "evolution_version": EVOLUTION_VERSION,
            "lifecycle": {
                "statuses": [str(s) for s in OpportunityStatus],
                "transitions": transition_table(),
                # Derived from the constants the GUARDS consult, never re-listed here. A
                # published policy that is a second hand-maintained copy of the rule will
                # drift from the rule, and the copy is what the owner reads (the duplicate
                # Turkish pattern table cost a qualification run the same way, 2026-09-05).
                "owner_only_targets": sorted(str(s) for s in OWNER_ONLY_STATUSES),
                "lab_forbidden_targets": sorted(str(s) for s in LAB_FORBIDDEN_STATUSES),
                "release_required_targets": sorted(str(s) for s in RELEASE_REQUIRED_STATUSES),
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

    def _notify_owner(self, opportunity: Mapping[str, Any], status: OpportunityStatus) -> None:
        """B12 req 383/388: the two transitions that are ABOUT the owner.

        `owner_approval_required` means something is waiting on a decision only they can
        make; `shadow_ready` means a candidate is ready to look at. Everything else here is
        the engine talking to itself and does not earn an interruption.

        Best-effort and session-less: this service owns no session, so the notification is
        written through the artifact runtime's own factory. A fault here never unwinds a
        transition that has already committed.
        """
        events_for = {
            OpportunityStatus.OWNER_APPROVAL_REQUIRED: "approval_required",
            OpportunityStatus.SHADOW_READY: "candidate_ready",
        }
        which = events_for.get(status)
        if which is None:
            return
        try:
            from app.artifacts.runtime import build_artifact_context
            from app.config import get_settings
            from app.notifications import events

            factory, _store = build_artifact_context(get_settings())
            identifier = str(opportunity.get("opportunity_id") or opportunity.get("id") or "?")
            title = str(opportunity.get("title") or "Bir aday")
            with factory() as db:
                if which == "approval_required":
                    events.approval_required(db, subject=identifier, what=title)
                else:
                    events.candidate_ready(db, opportunity_id=identifier, title_text=title)
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning(
                "evolution_owner_notification_failed",
                status=str(status),
                error=f"{type(exc).__name__}: {exc}",
            )

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
        self._notify_owner(opportunity, status)
        ui_state = UI_STATE_FOR_STATUS.get(status)
        if ui_state is not None:
            payload: dict[str, Any] = {
                "opportunity_id": opportunity["opportunity_id"],
                "status": str(status),
                "composite": opportunity["scores"]["composite"],
                "progress": _LAB_PROGRESS.get(status),
            }
            # Once a candidate's footprint has been assessed, every release-path
            # UI state carries its tier — the owner should see the risk level
            # throughout the release, not only at the moment it was computed.
            # `apps/web` reads this key as `metadata.risk_tier` (do not rename).
            risk_tier = (opportunity.get("detail") or {}).get("risk_tier")
            if risk_tier is not None:
                payload["risk_tier"] = int(risk_tier)
            self.ui.publish(ui_state, payload)

    def _ledger_spec(
        self, opportunity: Mapping[str, Any], status: OpportunityStatus
    ) -> tuple[str, str, str, str] | None:
        if status in {OpportunityStatus.REJECTED, OpportunityStatus.QUARANTINED}:
            # tests_failed says WHY, so it beats the generic closure event — but
            # only when the failure really did come out of a test/eval run.
            history = (opportunity.get("detail") or {}).get("transitions") or []
            previous = history[-1].get("from") if history else None
            if previous in {str(s) for s in _TEST_PHASE_STATUSES}:
                return (
                    EVENT_TYPE_EVOLUTION_TESTS_FAILED,
                    STATUS_FAILED,
                    PRODUCTION_STATE_NA,
                    SEVERITY_WARNING,
                )
        return LEDGER_EVENT_FOR_STATUS.get(status)

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
                    row = record_activity(session, event)
                    # ...and then CARRY it. Recording is evidence; a queued briefing is the
                    # only thing the owner ever hears. Until 2026-09-10 queue_briefing had
                    # exactly two callers and both were in the research pipeline, so every
                    # finding this engine made died in a table: "operator.type_text
                    # (validation_error)" at 19:04:06 on 2026-09-09 was the Notepad defect,
                    # and "display.wake (no_capable_device)" at 04:32:43 the next morning
                    # was the alarm that had failed to wake anything two minutes earlier.
                    # The owner reported the first himself and was startled awake by the
                    # second at 08:10. The spec §4 policy table decides which of these is
                    # worth a sentence; most transitions are a digest, and shadow_ready --
                    # the moment the engine asks for permission -- is spoken once.
                    _queue_briefing_quietly(session, row)
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
                f"{_STATUS_TR.get(status, status)} durumuna geçti ({actor})."
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
    "UiStateBridge",
]
