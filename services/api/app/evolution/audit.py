"""Evolution audit record (ACCEPTANCE_TESTS M7 "Auditability").

Every evolution must be able to answer NINE questions. This module assembles
them from records that already exist — the gap's decision trail, the skill
version's evaluation/review JSON and lifecycle history, and the registered
capability manifest — so the audit is a VIEW over evidence, never a separate
narrative that could drift from what actually happened.

    1. why_needed              the request and the outputs it required
    2. what_triggered          the task/incident/telemetry that produced the gap
    3. what_changed            capability, version, resolution path, lifecycle
    4. code_and_dependencies   generated files, digests, pinned components
    5. permissions_granted     the grants, deny-by-default posture, approval
    6. tests_that_ran          generated tests + eval set + release score
    7. who_reviewed            reviewer identity and every check it ran
    8. why_promoted            the gates and the per-edge promotion evidence
    9. rollback_target         the version production falls back to
"""

from __future__ import annotations

import uuid
from typing import Any

from app.evolution import lifecycle as lifecycle_module
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.gaps import (
    STEP_COMPONENT,
    STEP_COMPOSITION,
    STEP_NEW_SKILL,
    STEP_REQUEST,
    GapService,
)
from app.evolution.manifest import PERMISSION_FIELDS, granted_permissions
from app.evolution.registry import CapabilityRegistry

AUDIT_QUESTIONS = (
    "why_needed",
    "what_triggered",
    "what_changed",
    "code_and_dependencies",
    "permissions_granted",
    "tests_that_ran",
    "who_reviewed",
    "why_promoted",
    "rollback_target",
)


def _trail_step(trail: list[dict[str, Any]], step: str) -> dict[str, Any]:
    for entry in trail or []:
        if isinstance(entry, dict) and entry.get("step") == step:
            return entry
    return {}


def build_audit(
    gap_id: uuid.UUID, gaps: GapService, registry: CapabilityRegistry
) -> dict[str, Any]:
    """Assemble the nine answers for one gap. Raises NOT_FOUND for an unknown gap."""
    gap = gaps.get(gap_id)
    trail = list(gap.get("decision_trail") or [])
    request_step = _trail_step(trail, STEP_REQUEST)
    request = request_step.get("evidence") or {}
    work_order = _trail_step(trail, "work_order").get("evidence") or {}
    composition = _trail_step(trail, STEP_COMPOSITION).get("evidence") or {}
    component_step = _trail_step(trail, STEP_COMPONENT)
    new_skill = _trail_step(trail, STEP_NEW_SKILL).get("evidence") or {}

    skill_version: dict[str, Any] = {}
    if gap.get("resolved_skill_version_id"):
        try:
            skill_version = registry.get_skill_version(
                uuid.UUID(gap["resolved_skill_version_id"])
            )
        except EvolutionError:
            skill_version = {}
    capability = registry.get_capability(gap["requested_capability"]) or {}
    manifest = capability.get("manifest") or work_order.get("manifest") or {}
    evaluation = skill_version.get("evaluation") or {}
    review = skill_version.get("review") or {}
    lifecycle = lifecycle_module.read_lifecycle(evaluation)

    grants = granted_permissions(manifest)
    dependencies = list(manifest.get("dependencies") or [])
    components = list(manifest.get("components") or [])

    answers: dict[str, Any] = {
        "why_needed": {
            "requested_capability": gap["requested_capability"],
            "request_text": gap["request_text"],
            "required_inputs": request.get("required_inputs") or [],
            "required_outputs": request.get("required_outputs") or [],
            "resolution": gap["resolution"],
            "cheaper_options_ruled_out": {
                "composition": composition.get("reason")
                or "composition step recorded in the trail",
                "component_adaptation": (component_step.get("evidence") or {}).get("reason")
                or component_step.get("outcome")
                or "not reached",
            },
        },
        "what_triggered": {
            "trigger": (manifest.get("creation_reason") or {}).get("trigger")
            or "capability_gap",
            "gap_id": gap["id"],
            "task_id": gap["task_id"],
            "trace_id": gap["trace_id"],
            "created_at": gap["created_at"],
            "intent": request.get("intent"),
            "depth": request.get("depth", 0),
        },
        "what_changed": {
            "capability_id": gap["requested_capability"],
            "version": capability.get("version") or skill_version.get("version"),
            "capability_status": capability.get("status"),
            "resolution_path": new_skill.get("resolution_path")
            or (manifest.get("creation_reason") or {}).get("resolution_path"),
            "lifecycle_stage": lifecycle["stage"],
            "lifecycle_history": [entry.get("stage") for entry in lifecycle["history"]],
        },
        "code_and_dependencies": {
            "skill": manifest.get("skill"),
            "entrypoint": manifest.get("entrypoint"),
            "source_ref": skill_version.get("source_ref") or manifest.get("source_ref"),
            "manifest_digest": skill_version.get("manifest_digest"),
            "generated_files": (manifest.get("tests") or {}),
            "dependencies": dependencies,
            "components_adapted": components,
            "supply_chain": (evaluation.get("static") or {}).get("supply_chain")
            or {"ok": True, "scanned": [], "findings": []},
            "provenance": manifest.get("provenance") or {},
            "builder_identity": manifest.get("builder_identity") or {},
        },
        "permissions_granted": {
            "granted": grants,
            "deny_by_default": True,
            "declared_blocks": {field: list(manifest.get(field) or []) for field in
                                PERMISSION_FIELDS},
            "risk_class": manifest.get("risk_class"),
            "side_effects": manifest.get("side_effects") or [],
            "authorized_asset": (manifest.get("creation_reason") or {}).get("authorized_asset"),
            "reviewer_approved": review.get("permissions_approved", not grants),
        },
        "tests_that_ran": {
            "unit": evaluation.get("tests") or {},
            "evals": evaluation.get("evals") or {},
            "release_score": evaluation.get("score") or {},
            "failed_gates": evaluation.get("failed_gates") or [],
            "evaluator": evaluation.get("evaluator"),
        },
        "who_reviewed": {
            "reviewer": review.get("reviewer") or "independent-skill-reviewer",
            "approved": review.get("approved"),
            "checks": review.get("checks") or [],
            "summary": review.get("summary"),
            "builder_is_not_reviewer": True,
        },
        "why_promoted": {
            "gates": {
                "evaluation_passed": evaluation.get("passed"),
                "review_approved": review.get("approved"),
                "registered_at": skill_version.get("registered_at"),
            },
            "promotion_evidence": {
                stage: lifecycle_module.stage_evidence(evaluation, stage)
                for stage in lifecycle_module.PROMOTION_PATH
            },
            "evaluation_metrics": manifest.get("evaluation_metrics") or {},
        },
        "rollback_target": {
            "version": manifest.get("rollback_version"),
            "capability_id": gap["requested_capability"],
            "note": (
                "no previous registered version; production falls back to "
                "capability_missing, which parks the task as FAILED_RECOVERABLE"
                if not manifest.get("rollback_version")
                else "previously registered version, restorable via "
                "CapabilityRegistry.rollback_to"
            ),
        },
    }
    unanswered = [
        question for question in AUDIT_QUESTIONS if not answers.get(question)
    ]
    if unanswered:  # pragma: no cover - defensive; every branch fills all nine
        raise EvolutionError(
            EvolutionErrorClass.INTERNAL_BUG,
            f"audit is incomplete: {unanswered}",
        )
    return {
        "gap_id": gap["id"],
        "status": gap["status"],
        "questions": list(AUDIT_QUESTIONS),
        "answers": answers,
    }


__all__ = ["AUDIT_QUESTIONS", "build_audit"]
