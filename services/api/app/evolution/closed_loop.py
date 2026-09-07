"""The M18.4 closed loop (docs/M18_4_SELF_EVOLUTION_SPEC.md §3, §5; ADR-0081):

    incident -> EvolutionOpportunity (the supervisor, deduplicated)
             -> this loop: the M6 self-healing pipeline's REAL phases, each moving the
                opportunity through its lifecycle as the gate lands -
                    load/analyze  -> researching / design_ready
                    patch         -> building        (an isolated work directory)
                    regression    -> testing         (fails on the broken release, passes on
                                                       the candidate)
                    review        -> evaluating      (the independent reviewer)
                    staging       -> shadow_ready    (the candidate ran under the health
                                                       policy on a staging workspace)
                    promote       -> the COMPONENT's release is promoted by the supervisor
                                     deployer; the OPPORTUNITY stops at
                                     owner_approval_required, because LIVE is the owner's
                                     (ADR-0055), whatever the promotion class says
             -> a failed gate: quarantined (from a build/test/evaluate phase) or rejected
                (from research/design), with the failing step in the reason; the incident
                stays recovered; the component's release stays untouched.

Nothing here decides an outcome. The pipeline's steps arrive through its observer seam
(``SelfHealingPipeline(on_step=...)``); the lifecycle table decides what is legal; the
service's authority decides what the lab may do. The loop only translates one into the
other and refuses to skip a step.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.evolution.backlog import ActorKind, OpportunityStatus
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.logging import get_logger
from app.selfhealing.errors import SelfHealingError
from app.selfhealing.pipeline import PipelineStep, SelfHealingPipeline

logger = get_logger("app.evolution.closed_loop")

CLOSED_LOOP_VERSION = 1

#: Which lifecycle status a landed pipeline step EARNS. Only "ok" steps move forward.
STATUS_FOR_STEP: dict[str, OpportunityStatus] = {
    "load_incident": OpportunityStatus.RESEARCHING,
    "analyze_issue": OpportunityStatus.DESIGN_READY,
    "patch": OpportunityStatus.BUILDING,
    "regression_on_candidate": OpportunityStatus.TESTING,
    "review_change": OpportunityStatus.EVALUATING,
    "staging_deploy": OpportunityStatus.SHADOW_READY,
}

#: A gate failing in a build/test/evaluate phase parks the candidate WITH its evidence
#: (quarantined: revisable); one failing while the incident is still being understood
#: rejects it (there is no candidate to park).
_QUARANTINE_FROM = frozenset(
    {OpportunityStatus.BUILDING, OpportunityStatus.TESTING, OpportunityStatus.EVALUATING}
)


@dataclass
class LoopOutcome:
    opportunity_id: str
    incident_id: str
    final_status: str
    transitions: list[dict[str, Any]] = field(default_factory=list)
    pipeline: dict[str, Any] = field(default_factory=dict)
    component_promoted: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "closed_loop_version": CLOSED_LOOP_VERSION,
            "opportunity_id": self.opportunity_id,
            "incident_id": self.incident_id,
            "final_status": self.final_status,
            "transitions": list(self.transitions),
            "pipeline": dict(self.pipeline),
            "component_promoted": self.component_promoted,
            "error": self.error,
        }


class ClosedLoop:
    """Drive ONE incident-born opportunity through the real pipeline."""

    def __init__(
        self,
        *,
        evolution_service: Any,
        pipeline_factory: Callable[[Callable[[PipelineStep], None]], SelfHealingPipeline],
    ) -> None:
        self._evolution = evolution_service
        self._pipeline_factory = pipeline_factory

    # ----------------------------------------------------------- transitions

    def _advance(
        self, outcome: LoopOutcome, target: OpportunityStatus, *, reason: str, **refs: Any
    ) -> str:
        current = self._evolution.backlog.get(outcome.opportunity_id)["status"]
        updated = self._evolution.advance(
            outcome.opportunity_id,
            target=target,
            actor=ActorKind.LAB,
            reason=reason[:500],
            **{k: v for k, v in refs.items() if v is not None},
        )
        outcome.transitions.append(
            {"from": current, "to": updated["status"], "reason": reason[:500]}
        )
        return updated["status"]

    def _on_step(self, outcome: LoopOutcome, step: PipelineStep) -> None:
        if step.status != "ok":
            return
        target = STATUS_FOR_STEP.get(step.name)
        if target is None:
            return
        detail = step.detail or {}
        reason = f"closed_loop: {step.name} ok"
        refs: dict[str, Any] = {}
        if step.name == "analyze_issue" and detail.get("fault_kind"):
            reason += f" ({detail['fault_kind']})"
        if step.name == "patch":
            changed = detail.get("changed_files") or []
            reason += f" ({len(changed)} file(s) changed in an isolated work directory)"
        if step.name == "staging_deploy":
            reason += " (candidate green under the staging health policy)"
        try:
            self._advance(outcome, target, reason=reason, **refs)
        except EvolutionError as exc:
            # A refused transition is a fact about the lifecycle, not a reason to abort the
            # pipeline: record it and let the pipeline finish its own story.
            outcome.transitions.append(
                {"from": None, "to": str(target), "refused": str(exc)[:300], "step": step.name}
            )

    # ------------------------------------------------------------------ run

    def heal(self, opportunity_id: uuid.UUID | str) -> LoopOutcome:
        opportunity = self._evolution.backlog.get(opportunity_id)
        if opportunity["source"] != "incident":
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                "the closed loop heals incident-born opportunities only",
                details={"source": opportunity["source"]},
            )
        try:
            incident_id = uuid.UUID(str(opportunity["source_ref"]))
        except ValueError as exc:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                "the opportunity's source_ref is not an incident id",
            ) from exc
        outcome = LoopOutcome(
            opportunity_id=str(opportunity["opportunity_id"]),
            incident_id=str(incident_id),
            final_status=str(opportunity["status"]),
        )
        if opportunity["status"] != str(OpportunityStatus.IDEA):
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                "the closed loop starts from an idea; this opportunity has already moved",
                details={"status": opportunity["status"]},
            )

        pipeline = self._pipeline_factory(lambda step: self._on_step(outcome, step))
        try:
            result = pipeline.run(incident_id)
        except SelfHealingError as exc:
            outcome.error = f"{exc.error_class}: {exc.message}"[:300]
            result = None
        if result is not None:
            outcome.pipeline = result.to_dict()

        current = OpportunityStatus(self._evolution.backlog.get(outcome.opportunity_id)["status"])
        if result is not None and result.status == "fixed":
            outcome.component_promoted = any(
                s.name == "production_promote" and s.status == "ok" for s in result.steps
            )
            if current is OpportunityStatus.SHADOW_READY:
                self._advance(
                    outcome,
                    OpportunityStatus.OWNER_APPROVAL_REQUIRED,
                    reason=(
                        f"closed_loop: candidate {result.candidate_version} promoted for the "
                        f"component by the supervisor deployer (release {result.release_id}); "
                        "LIVE for the opportunity is the owner's decision (ADR-0055)"
                    ),
                    candidate_ref=f"selfhealing/releases/{result.release_id}",
                )
        else:
            failed = next(
                (s for s in (result.steps if result is not None else []) if s.status != "ok"),
                None,
            )
            gate = failed.name if failed is not None else "pipeline"
            why = outcome.error or (
                str((failed.detail or {}).get("message") or "")[:200] if failed else ""
            )
            reason = f"closed_loop: gate '{gate}' failed; candidate parked with its evidence"
            if why:
                reason += f" - {why}"
            target = (
                OpportunityStatus.QUARANTINED
                if current in _QUARANTINE_FROM
                else OpportunityStatus.REJECTED
            )
            if current not in (OpportunityStatus.REJECTED, OpportunityStatus.QUARANTINED):
                try:
                    self._advance(outcome, target, reason=reason)
                except EvolutionError as exc:
                    outcome.transitions.append(
                        {"from": str(current), "to": str(target), "refused": str(exc)[:300]}
                    )
        outcome.final_status = self._evolution.backlog.get(outcome.opportunity_id)["status"]
        logger.info(
            "closed_loop_finished",
            opportunity_id=outcome.opportunity_id,
            incident_id=outcome.incident_id,
            final_status=outcome.final_status,
            transitions=len(outcome.transitions),
            component_promoted=outcome.component_promoted,
        )
        return outcome


__all__ = ["CLOSED_LOOP_VERSION", "STATUS_FOR_STEP", "ClosedLoop", "LoopOutcome"]
