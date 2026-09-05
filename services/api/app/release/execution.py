"""The release execution orchestrator (M18 spec §5, ADR-0055 §5).

::

    QUALIFYING -> preflight -> DEPLOYING -> VERIFYING -> LIVE
                            \\-> FAILED -> ROLLING_BACK -> ROLLED_BACK

Every lifecycle transition is driven through ``EvolutionService.advance()``
with a ``production_authority`` the CALLER supplies — this module mints no
authority of its own, holds none, and cannot construct an
:class:`~app.evolution.authority.OwnerCapability` or a
:class:`~app.evolution.authority.Authority`. It is exactly as authorised as
the ``Authority`` object it is handed, and every production-side call still
passes through ``guard_production_action`` inside ``EvolutionService`` —
this orchestrator adds an execution plan on top of that kernel, not a
parallel gate around it.

Nothing here performs a real deployment. Every effectful step goes through
:class:`~app.release.backend.DeploymentBackend`; the task that commissioned
this module is explicit that no real Hetzner/ssh path may run yet, so only
:class:`~app.release.backend.FakeDeploymentBackend` exists today.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.evolution.authority import Authority
from app.evolution.backlog import ActorKind, OpportunityStatus
from app.evolution.service import EvolutionService
from app.logging import get_logger
from app.release.backend import DeploymentBackend, RollbackOutcome
from app.release.preflight import GitTreeInspector, ReleaseCandidate, run_preflight
from app.selfhealing.errors import SelfHealingError
from app.selfhealing.service import SelfHealingService

logger = get_logger("app.release.execution")


class DynamicReleaseEvidenceProvider:
    """A :class:`~app.evolution.backlog.ReleaseEvidenceProvider` the executor
    can update as it runs.

    ``EvolutionService`` fixes its release-evidence provider at construction;
    the executor needs to say "an owner-approved release now exists for THIS
    opportunity" at the exact moment it is about to enter ``DEPLOYING`` — not
    before (a candidate must not appear release-ready before it has passed
    preflight) and not via a caller-supplied ``release_ref`` string that could
    be asserted for any opportunity. ``set()`` is the only way to add an
    entry, and only :class:`ReleaseExecutor` calls it, right after preflight
    passes and a real ``releases`` row has been recorded.
    """

    name = "dynamic"

    def __init__(self) -> None:
        self._refs: dict[str, str] = {}

    def set(self, opportunity_id: uuid.UUID | str, release_ref: str) -> None:
        self._refs[str(opportunity_id)] = release_ref

    def approved_release(self, opportunity: Mapping[str, Any]) -> str | None:
        return self._refs.get(str(opportunity.get("opportunity_id")))


@dataclass(slots=True)
class ReleaseExecutionReport:
    opportunity_id: str
    component: str
    version: str
    outcome: str = "refused"  # "live" | "rolled_back" | "quarantined"
    final_status: str = ""
    preflight: dict[str, Any] | None = None
    deploy: dict[str, Any] | None = None
    verify: dict[str, Any] | None = None
    rollback: dict[str, Any] | None = None
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "component": self.component,
            "version": self.version,
            "outcome": self.outcome,
            "final_status": self.final_status,
            "preflight": self.preflight,
            "deploy": self.deploy,
            "verify": self.verify,
            "rollback": self.rollback,
            "summary": self.summary,
        }


class ReleaseExecutor:
    """Composes the evolution lifecycle, the release ledger and a deployment
    backend into the ADR-0055 §5 execution plan.

    ``evolution.release_evidence`` MUST be a :class:`DynamicReleaseEvidenceProvider`
    (checked at construction) — this is what lets ``DEPLOYING``/``LIVE`` be
    entered honestly, with the release_ref this executor itself just recorded,
    rather than a provider wired for some other purpose.
    """

    def __init__(
        self,
        *,
        evolution: EvolutionService,
        selfhealing: SelfHealingService,
        backend: DeploymentBackend,
        git_tree: GitTreeInspector,
    ) -> None:
        if not isinstance(evolution.release_evidence, DynamicReleaseEvidenceProvider):
            raise TypeError(
                "ReleaseExecutor requires an EvolutionService constructed with "
                "release_evidence=DynamicReleaseEvidenceProvider(); got "
                f"{type(evolution.release_evidence).__name__}"
            )
        self.evolution = evolution
        self.selfhealing = selfhealing
        self.backend = backend
        self.git_tree = git_tree

    # ------------------------------------------------------------------ api

    def execute(
        self,
        opportunity_id: uuid.UUID | str,
        candidate: ReleaseCandidate,
        *,
        production_authority: Authority,
    ) -> ReleaseExecutionReport:
        oid = str(opportunity_id)
        report = ReleaseExecutionReport(
            opportunity_id=oid, component=candidate.component, version=candidate.version
        )

        previous_release = self._active_release(candidate.component)

        self.evolution.advance(
            oid,
            target=OpportunityStatus.QUALIFYING,
            actor=ActorKind.SYSTEM,
            production_authority=production_authority,
        )

        preflight = run_preflight(
            candidate, git_tree=self.git_tree, previous_release=previous_release
        )
        report.preflight = preflight.to_dict()
        if not preflight.passed:
            logger.warning(
                "release_preflight_refused",
                opportunity_id=oid,
                failed_checks=preflight.failed_checks,
            )
            return self._fail(
                report,
                oid,
                production_authority,
                reason=f"preflight refused: {', '.join(preflight.failed_checks)}",
                previous_release=previous_release,
                release_id=None,
            )

        try:
            release_row = self.selfhealing.record_release(
                candidate.component,
                candidate.version,
                candidate.manifest_digest,
                status="candidate",
                git_commit=candidate.candidate_ref,
            )
        except SelfHealingError as exc:
            # (component, version) was already recorded (releases are
            # immutable) — most likely a retried version number. This is a
            # real failure, not a crash: it must still visibly become a
            # rollback rather than propagate past the state machine.
            logger.warning("release_record_failed", opportunity_id=oid, error=exc.message)
            return self._fail(
                report,
                oid,
                production_authority,
                reason=f"could not record the release: {exc.message}",
                previous_release=previous_release,
                release_id=None,
            )
        release_id = release_row["id"]
        release_ref = f"{candidate.component}:{candidate.version}"
        self.evolution.release_evidence.set(oid, release_ref)

        self.evolution.advance(
            oid,
            target=OpportunityStatus.DEPLOYING,
            actor=ActorKind.SYSTEM,
            production_authority=production_authority,
        )

        deploy_outcome = self.backend.deploy(
            component=candidate.component,
            version=candidate.version,
            candidate_ref=candidate.candidate_ref,
        )
        report.deploy = deploy_outcome.to_dict()
        if not deploy_outcome.succeeded:
            self.selfhealing.set_release_status(
                uuid.UUID(release_id), "rejected", health=deploy_outcome.detail
            )
            logger.warning(
                "release_deploy_failed", opportunity_id=oid, detail=deploy_outcome.detail
            )
            return self._fail(
                report,
                oid,
                production_authority,
                reason="deploy failed",
                previous_release=previous_release,
                release_id=release_id,
            )

        self.evolution.advance(
            oid,
            target=OpportunityStatus.VERIFYING,
            actor=ActorKind.SYSTEM,
            production_authority=production_authority,
        )

        verify_outcome = self.backend.verify(
            component=candidate.component,
            version=candidate.version,
            installed_digest=candidate.manifest_digest,
        )
        report.verify = verify_outcome.to_dict()
        if not verify_outcome.passed:
            self.selfhealing.set_release_status(
                uuid.UUID(release_id), "rolled_back", health=verify_outcome.to_dict()
            )
            logger.warning(
                "release_verification_failed",
                opportunity_id=oid,
                healthy=verify_outcome.healthy,
                provenance_ok=verify_outcome.provenance_ok,
            )
            return self._fail(
                report,
                oid,
                production_authority,
                reason=(
                    "verification failed: "
                    f"healthy={verify_outcome.healthy} provenance_ok={verify_outcome.provenance_ok}"
                ),
                previous_release=previous_release,
                release_id=release_id,
            )

        live = self.evolution.advance(
            oid,
            target=OpportunityStatus.LIVE,
            actor=ActorKind.SYSTEM,
            production_authority=production_authority,
        )
        self.selfhealing.set_release_status(
            uuid.UUID(release_id), "active", health=verify_outcome.to_dict()
        )
        self.selfhealing.supersede_active_releases(
            candidate.component, except_release_id=uuid.UUID(release_id)
        )
        report.outcome = "live"
        report.final_status = live["status"]
        report.summary = (
            f"{candidate.component} {candidate.version} is live "
            f"(release {release_id}); previous release superseded."
        )
        logger.info(
            "release_live",
            opportunity_id=oid,
            component=candidate.component,
            version=candidate.version,
        )
        return report

    # ------------------------------------------------------------ rollback

    def _fail(
        self,
        report: ReleaseExecutionReport,
        opportunity_id: str,
        production_authority: Authority,
        *,
        reason: str,
        previous_release: Mapping[str, Any] | None,
        release_id: str | None,
    ) -> ReleaseExecutionReport:
        """FAILED -> ROLLING_BACK -> {ROLLED_BACK, QUARANTINED}. Never a silent stop.

        Every failure path — preflight, deploy, or verify — ends up here, so a
        release that never even reached the infrastructure and one that had to
        be torn back out both leave the SAME kind of durable, ledgered trail.
        """
        self.evolution.advance(
            opportunity_id,
            target=OpportunityStatus.FAILED,
            actor=ActorKind.SYSTEM,
            reason=reason,
            production_authority=production_authority,
        )
        self.evolution.advance(
            opportunity_id,
            target=OpportunityStatus.ROLLING_BACK,
            actor=ActorKind.SYSTEM,
            reason=reason,
            production_authority=production_authority,
        )

        if previous_release is not None:
            rollback_outcome = self.backend.rollback(
                component=report.component, target_version=previous_release["version"]
            )
        else:
            # Preflight refused before anything was recorded or deployed (most
            # commonly because rollback_point_exists itself failed) — there is
            # nothing on the infrastructure to undo.
            rollback_outcome = RollbackOutcome(
                succeeded=True,
                restored_version=None,
                detail={"note": "nothing was deployed; no rollback target existed"},
            )
        report.rollback = rollback_outcome.to_dict()

        if rollback_outcome.succeeded:
            final = self.evolution.advance(
                opportunity_id,
                target=OpportunityStatus.ROLLED_BACK,
                actor=ActorKind.SYSTEM,
                reason=reason,
                production_authority=production_authority,
            )
            report.outcome = "rolled_back"
            report.final_status = final["status"]
            report.summary = (
                f"release refused/rolled back ({reason}); "
                f"restored version: {rollback_outcome.restored_version!r}"
                + (f"; release {release_id} marked accordingly" if release_id else "")
            )
        else:
            # The rollback itself failed. Reporting ROLLED_BACK here would be a
            # false "recovered" claim; QUARANTINED honestly says "this needs a
            # human", per the same lifecycle law that already allows
            # ROLLING_BACK -> QUARANTINED.
            final = self.evolution.advance(
                opportunity_id,
                target=OpportunityStatus.QUARANTINED,
                actor=ActorKind.SYSTEM,
                reason=f"rollback failed: {reason}",
                production_authority=production_authority,
            )
            report.outcome = "quarantined"
            report.final_status = final["status"]
            report.summary = (
                f"rollback FAILED after {reason}; quarantined for manual recovery "
                f"({rollback_outcome.detail})"
            )
            logger.error("release_rollback_failed", opportunity_id=opportunity_id, reason=reason)

        # The release row's OWN status ("rejected" for a deploy that never
        # took, "rolled_back" for one that did and had to be undone) is set by
        # the caller before invoking this method — it depends on WHICH stage
        # failed, not on whether the rollback itself succeeded, so it is not
        # duplicated or overwritten here.
        return report

    # --------------------------------------------------------------- lookup

    def _active_release(self, component: str) -> dict[str, Any] | None:
        for release in self.selfhealing.list_releases(component=component, limit=50):
            if release["status"] == "active":
                return release
        return None


__all__ = [
    "DynamicReleaseEvidenceProvider",
    "ReleaseExecutionReport",
    "ReleaseExecutor",
]
