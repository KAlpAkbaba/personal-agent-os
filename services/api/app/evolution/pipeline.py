"""Self-extension pipeline (EVOLUTION_ENGINE_SPEC §4) — the M7 orchestrator.

    capability gap (composition already attempted and insufficient)
        -> requirement work order (validated SkillSpec + machine-readable manifest)
        -> ISOLATED workspace inside the evolution sandbox (§13)
        -> generate via the SkillGenerator seam
        -> RUN the generated tests + eval set, score per §9
        -> INDEPENDENT review (re-runs everything; the generator never self-approves)
        -> capability registration ONLY after every gate
        -> resume the original task

Hard invariants, each enforced by code rather than convention:

- ``require_composition_attempted`` runs BEFORE the generator is constructed, so
  code generation is unreachable unless the trail proves composition was tried
  first and reported insufficient;
- a gap resolved as ``product_change_required`` is refused here as well, so no
  caller can drive core/recovery work through the pipeline;
- every path the pipeline writes to is checked against the sandbox policy, which
  itself refuses roots overlapping the recovery supervisor or the API source;
- a rejected candidate is marked ``rejected`` with a reason, is never published
  to the skills root and never becomes a capability's current version — so
  ``registry.resolve`` keeps returning the previously registered version and
  production dispatch is bit-for-bit unchanged.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.evolution import lifecycle as lifecycle_module
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.evaluation import EvaluationResult, SkillEvaluator
from app.evolution.gaps import (
    STEP_COMPONENT,
    GapService,
    request_from_trail,
    require_composition_attempted,
)
from app.evolution.registry import CapabilityRegistry
from app.evolution.resources import ResourceBudget, require_within_depth
from app.evolution.review import IndependentSkillReviewer
from app.evolution.rollout import CanaryRunner, ShadowRunner
from app.evolution.sandbox import SandboxPolicy
from app.evolution.skills import (
    DeterministicSkillGenerator,
    SkillGenerator,
    SkillLayout,
    SkillSpec,
    read_manifest,
)
from app.evolution.supply_chain import scan_dependencies
from app.evolution.task_resumption import TaskResumer
from app.logging import get_logger
from app.selfhealing.service import compute_manifest_digest

logger = get_logger("app.evolution.pipeline")


@dataclass(slots=True)
class PipelineStage:
    name: str
    status: str  # "ok" | "failed" | "skipped"
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(slots=True)
class EvolutionResult:
    gap_id: str
    status: str  # "registered" | "rejected" | "refused" | "failed"
    stages: list[PipelineStage] = field(default_factory=list)
    capability_id: str | None = None
    version: str | None = None
    skill_version_id: str | None = None
    source_ref: str | None = None
    resumption: dict[str, Any] | None = None
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "status": self.status,
            "stages": [stage.to_dict() for stage in self.stages],
            "capability_id": self.capability_id,
            "version": self.version,
            "skill_version_id": self.skill_version_id,
            "source_ref": self.source_ref,
            "resumption": self.resumption,
            "summary": self.summary,
        }


class EvolutionPipeline:
    def __init__(
        self,
        registry: CapabilityRegistry,
        gaps: GapService,
        *,
        generator: SkillGenerator | None = None,
        evaluator: SkillEvaluator | None = None,
        reviewer: IndependentSkillReviewer | None = None,
        sandbox: SandboxPolicy,
        skills_root: Path,
        resumer: TaskResumer | None = None,
        budget: ResourceBudget | None = None,
        shadow: ShadowRunner | None = None,
        canary: CanaryRunner | None = None,
    ) -> None:
        self.registry = registry
        self.gaps = gaps
        self.budget = budget or ResourceBudget()
        self.generator = generator or DeterministicSkillGenerator()
        self.evaluator = evaluator or SkillEvaluator(budget=self.budget)
        self.shadow = shadow or ShadowRunner()
        self.canary = canary or CanaryRunner()
        # Builder/reviewer separation (§6): the reviewer is a DIFFERENT object
        # with its OWN evaluator; it never receives the generator.
        self.reviewer = reviewer or IndependentSkillReviewer(sandbox=sandbox)
        if self.reviewer is self.generator:
            raise EvolutionError(
                EvolutionErrorClass.INTERNAL_BUG,
                "the generator may never be its own reviewer",
            )
        self.sandbox = sandbox
        self.skills_root = Path(skills_root)
        self.resumer = resumer

    # ------------------------------------------------------------------- run

    def run(self, gap_id: uuid.UUID) -> EvolutionResult:
        result = EvolutionResult(gap_id=str(gap_id), status="failed")
        stages = result.stages
        work_dir: Path | None = None
        skill_version_id: uuid.UUID | None = None
        try:
            # 1. Load the gap and its decision trail.
            gap = self.gaps.get(gap_id)
            if gap["resolution"] == "product_change_required":
                stages.append(
                    PipelineStage(
                        "load_gap",
                        "failed",
                        {"resolution": gap["resolution"], "status": gap["status"]},
                    )
                )
                raise EvolutionError(
                    EvolutionErrorClass.PRODUCT_CHANGE_REQUIRED,
                    "this gap requires a product/core/recovery change; the evolution "
                    "engine never auto-generates those (constitution section 6)",
                    details={"gap_id": str(gap_id)},
                )
            if gap["resolution"] != "generation":
                raise EvolutionError(
                    EvolutionErrorClass.GENERATION_REFUSED,
                    f"gap resolution is {gap['resolution']!r}; nothing to generate",
                    details={"resolution": gap["resolution"]},
                )
            if gap["status"] not in ("open", "resolving"):
                raise EvolutionError(
                    EvolutionErrorClass.VALIDATION_ERROR,
                    f"gap is {gap['status']}; the pipeline needs open/resolving",
                )
            trail = list(gap["decision_trail"] or [])
            request = request_from_trail(trail)
            stages.append(
                PipelineStage(
                    "load_gap",
                    "ok",
                    {
                        "requested_capability": request.requested_capability,
                        "task_id": gap["task_id"],
                    },
                )
            )

            # 2. Composition-first gate. Runs BEFORE any generator work.
            composition = require_composition_attempted(trail)
            stages.append(
                PipelineStage(
                    "verify_composition_attempted",
                    "ok",
                    {
                        "step_index": composition.get("index"),
                        "outcome": composition.get("outcome"),
                        "missing_outputs": (composition.get("evidence") or {}).get(
                            "missing_outputs"
                        ),
                    },
                )
            )
            # 2b. Self-extension recursion limit (structural loop stop).
            require_within_depth(request.depth, self.budget)
            self.gaps.set_status(gap_id, "resolving")

            # 3. Requirement work order (choke-point validation of the spec).
            # If resolution step 4 matched a vetted component, the work order is
            # an ADAPTER around that pinned component, not code from scratch.
            component = self._matched_component(trail)
            spec = self._build_spec(request.requested_capability, request.spec, component)
            spec.version = self._next_version(spec.capability_id, spec.version)
            previous = self.registry.resolve(spec.capability_id)
            manifest = spec.capability_manifest(
                creation_reason={
                    "gap_id": str(gap_id),
                    "task_id": gap["task_id"],
                    "trigger": "capability_gap",
                    "request_digest": hashlib.sha256(
                        request.request_text.encode("utf-8")
                    ).hexdigest(),
                    "depth": request.depth,
                    "authorized_asset": request.authorized_asset,
                    "resolution_path": (
                        "component_adaptation" if component else "generation_from_scratch"
                    ),
                },
                rollback_version=previous["version"] if previous else None,
                resource_budget=self.budget.to_dict(),
            )
            self.gaps.append_step(
                gap_id,
                {
                    "index": len(trail),
                    "step": "work_order",
                    "question": "what exactly will be built?",
                    "outcome": "recorded",
                    "evidence": {
                        "manifest": manifest,
                        "generator": self.generator.name,
                        "component": component,
                        "resource_budget": self.budget.to_dict(),
                    },
                    "at": datetime.now(UTC).isoformat(),
                },
            )
            stages.append(
                PipelineStage(
                    "work_order",
                    "ok",
                    {
                        "capability_id": spec.capability_id,
                        "version": spec.version,
                        "resolution_path": manifest["creation_reason"]["resolution_path"],
                        "rollback_version": manifest["rollback_version"],
                    },
                )
            )

            # 4. Isolated workspace inside the sandbox (§13).
            self.sandbox.prepare()
            work_dir = Path(tempfile.mkdtemp(prefix="evolution-", dir=str(self.sandbox.root)))
            self.sandbox.ensure_within(work_dir, label="workspace")
            stages.append(PipelineStage("isolated_workspace", "ok", {"name": work_dir.name}))

            # 5. Generate.
            layout = self.generator.generate(spec, work_dir)
            self.sandbox.ensure_within(layout.root, label="generated skill root")
            missing = layout.missing_paths()
            if missing:
                raise EvolutionError(
                    EvolutionErrorClass.GENERATION_FAILED,
                    f"generator produced an incomplete skill; missing {missing}",
                    details={"missing": missing},
                )
            version_row = self.registry.create_skill_version(
                spec.capability_id, spec.version, status="draft"
            )
            skill_version_id = uuid.UUID(version_row["id"])
            result.skill_version_id = version_row["id"]
            result.capability_id = spec.capability_id
            result.version = spec.version
            workspace_digest = compute_manifest_digest(layout.root)
            self.registry.record_build(
                skill_version_id, source_ref=str(layout.root), manifest_digest=workspace_digest
            )
            # lifecycle: candidate -> sandbox
            self.registry.advance_lifecycle(
                skill_version_id,
                lifecycle_module.STAGE_SANDBOX,
                {"workspace": work_dir.name, "manifest_digest": workspace_digest},
            )
            stages.append(
                PipelineStage(
                    "generate",
                    "ok",
                    {
                        "generator": self.generator.name,
                        "files": sorted(p.name for p in layout.required_paths()),
                        "lifecycle_stage": lifecycle_module.STAGE_SANDBOX,
                    },
                )
            )

            # 5b. Supply chain: pinned name+version+source+digest, no install
            # scripts, and every component must exist in the local catalog.
            # Scanned from the manifest the GENERATOR actually emitted, not the
            # work-order draft — a generator cannot smuggle a dependency past
            # the gate by adding it after the work order was recorded.
            emitted_manifest = read_manifest(layout)
            supply = scan_dependencies(
                list(emitted_manifest.get("dependencies") or [])
                + list(emitted_manifest.get("components") or [])
            )
            if not supply.ok:
                stages.append(PipelineStage("supply_chain", "failed", supply.to_dict()))
                return self._reject(
                    result,
                    gap_id,
                    skill_version_id,
                    "supply chain rejected: "
                    + ", ".join(sorted({f.rule for f in supply.findings})),
                )
            stages.append(PipelineStage("supply_chain", "ok", supply.to_dict()))

            # 6. RUN the generated tests + eval set and score them (§9), with the
            # configured retry limit for transient timeouts.
            evaluation, attempts = self._evaluate_with_retries(layout)
            self.registry.record_evaluation(skill_version_id, evaluation.to_dict())
            if not evaluation.passed:
                stages.append(
                    PipelineStage(
                        "evaluate", "failed", evaluation.to_dict() | {"attempts": attempts}
                    )
                )
                return self._reject(
                    result,
                    gap_id,
                    skill_version_id,
                    "release gates failed: " + ", ".join(evaluation.failed_gates),
                )
            stages.append(
                PipelineStage("evaluate", "ok", evaluation.to_dict() | {"attempts": attempts})
            )

            # 7. INDEPENDENT review (re-runs everything itself) + the
            # deny-by-default decision on any requested permission grants.
            review, review_payload = self.reviewer.review_record(layout)
            self.registry.record_review(skill_version_id, review_payload)
            if not review.approved:
                stages.append(PipelineStage("independent_review", "failed", review_payload))
                return self._reject(
                    result, gap_id, skill_version_id, f"independent review: {review.summary}"
                )
            stages.append(PipelineStage("independent_review", "ok", review_payload))
            # lifecycle: sandbox -> validated (independent evidence at the edge)
            self.registry.advance_lifecycle(
                skill_version_id,
                lifecycle_module.STAGE_VALIDATED,
                {
                    "evaluation_passed": True,
                    "review_approved": True,
                    "supply_chain_ok": True,
                    "reviewer": self.reviewer.name,
                },
            )

            # 7b. SHADOW: mirror the case set; nothing is served.
            incumbent_layout = self._incumbent_layout(spec.capability_id)
            shadow_report = self.shadow.run(layout, incumbent_layout)
            if shadow_report.mismatches:
                stages.append(PipelineStage("shadow", "failed", shadow_report.to_dict()))
                return self._reject(
                    result,
                    gap_id,
                    skill_version_id,
                    f"shadow recorded {shadow_report.mismatches} mismatches",
                )
            self.registry.advance_lifecycle(
                skill_version_id,
                lifecycle_module.STAGE_SHADOW,
                {
                    "samples": shadow_report.candidate.samples,
                    "mismatches": shadow_report.mismatches,
                    "report": shadow_report.to_dict(),
                },
            )
            stages.append(PipelineStage("shadow", "ok", shadow_report.to_dict()))

            # 7c. CANARY: bounded sample, compared against the incumbent.
            canary_report = self.canary.run(layout, incumbent_layout)
            if not canary_report.not_worse_than_incumbent:
                stages.append(PipelineStage("canary", "failed", canary_report.to_dict()))
                return self._reject(
                    result,
                    gap_id,
                    skill_version_id,
                    "canary performed worse than the incumbent",
                )
            self.registry.advance_lifecycle(
                skill_version_id,
                lifecycle_module.STAGE_CANARY,
                {
                    "samples": canary_report.candidate.samples,
                    "not_worse_than_incumbent": True,
                    "report": canary_report.to_dict(),
                },
            )
            stages.append(PipelineStage("canary", "ok", canary_report.to_dict()))

            # 8. Publish the approved candidate into the skills root.
            published = self._publish(layout)
            digest = compute_manifest_digest(published)
            self.registry.record_source_ref(
                skill_version_id, source_ref=str(published), manifest_digest=digest
            )
            result.source_ref = str(published)
            stages.append(
                PipelineStage(
                    "publish",
                    "ok",
                    {"skill": layout.skill_name, "manifest_digest": digest},
                )
            )

            # 9. Registration — the gate. Refuses anything not evaluated+approved,
            # lacking lifecycle promotion evidence, or carrying unapproved grants.
            manifest["source_ref"] = str(published)
            manifest["provenance"] = {
                "generator": self.generator.name,
                "workspace_digest": workspace_digest,
                "published_digest": digest,
                "published_at": datetime.now(UTC).isoformat(),
                "gap_id": str(gap_id),
            }
            manifest["evaluation_metrics"] = evaluation.score.to_dict()
            manifest["dependencies"] = list(emitted_manifest.get("dependencies") or [])
            manifest["components"] = list(emitted_manifest.get("components") or [])
            registered = self.registry.register(spec.capability_id, skill_version_id, manifest)
            stages.append(
                PipelineStage(
                    "register_capability",
                    "ok",
                    {
                        "capability_id": registered["capability_id"],
                        "version": registered["version"],
                        "status": registered["status"],
                    },
                )
            )

            # 10. Resume the original task.
            result.resumption = self._resume(gap, request, spec.capability_id, stages)

            self.gaps.mark_resolved(
                gap_id, resolution="generation", skill_version_id=skill_version_id
            )
            result.status = "registered"
            result.summary = (
                f"generated, evaluated, reviewed and registered {spec.capability_id} "
                f"v{spec.version}"
            )
            logger.info(
                "evolution_pipeline_registered",
                gap_id=str(gap_id),
                capability_id=spec.capability_id,
                version=spec.version,
            )
            return result
        except EvolutionError as exc:
            stages.append(PipelineStage("pipeline_error", "failed", exc.to_dict()))
            result.summary = exc.message
            result.status = (
                "refused"
                if exc.error_class
                in (
                    EvolutionErrorClass.PRODUCT_CHANGE_REQUIRED,
                    EvolutionErrorClass.GENERATION_REFUSED,
                    EvolutionErrorClass.SANDBOX_VIOLATION,
                    EvolutionErrorClass.RECURSION_LIMIT_EXCEEDED,
                    EvolutionErrorClass.PERMISSION_DENIED,
                )
                else "failed"
            )
            if skill_version_id is not None:
                try:
                    self.registry.reject_skill_version(skill_version_id, result.summary)
                except EvolutionError:
                    pass
            try:
                if self.gaps.get(gap_id)["status"] == "resolving":
                    self.gaps.set_status(gap_id, "open")
            except EvolutionError:
                pass
            logger.info(
                "evolution_pipeline_failed",
                gap_id=str(gap_id),
                error_class=str(exc.error_class),
            )
            return result
        finally:
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)

    # ----------------------------------------------------------------- pieces

    def _reject(
        self,
        result: EvolutionResult,
        gap_id: uuid.UUID,
        skill_version_id: uuid.UUID,
        reason: str,
    ) -> EvolutionResult:
        """Terminal rejection: no publish, no registration, production untouched."""
        self.registry.reject_skill_version(skill_version_id, reason)
        try:
            self.gaps.set_status(gap_id, "open")
        except EvolutionError:
            pass
        result.status = "rejected"
        result.summary = reason
        logger.info(
            "evolution_candidate_rejected",
            gap_id=str(gap_id),
            skill_version_id=str(skill_version_id),
        )
        return result

    def _matched_component(self, trail: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The vetted component resolution step 4 chose, if any."""
        for entry in trail or []:
            if (
                isinstance(entry, dict)
                and entry.get("step") == STEP_COMPONENT
                and entry.get("outcome") == "satisfied"
            ):
                return (entry.get("evidence") or {}).get("component")
        return None

    def _build_spec(
        self,
        requested_capability: str,
        raw_spec: dict[str, Any] | None,
        component: dict[str, Any] | None = None,
    ) -> SkillSpec:
        spec_dict = dict(raw_spec or {})
        spec_dict.setdefault("capability_id", requested_capability)
        if spec_dict["capability_id"] != requested_capability:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                "skill spec capability_id does not match the requested capability",
            )
        if component:
            # Adapting a vetted component: the component decides the operation
            # and is recorded as a PINNED dependency of the resulting skill.
            spec_dict["operation"] = component["operation"]
            spec_dict["components"] = [
                {
                    "name": component["name"],
                    "version": component["version"],
                    "source": component["source"],
                    "digest": component["digest"],
                }
            ]
            spec_dict.setdefault("builder_name", "component_adapter")
        return SkillSpec.parse(spec_dict)

    def _evaluate_with_retries(self, layout: SkillLayout) -> tuple[EvaluationResult, int]:
        """Evaluate, retrying ONLY transient timeouts up to the retry limit."""
        attempts = 0
        evaluation: EvaluationResult | None = None
        for attempts in range(1, self.budget.retry_limit + 2):  # noqa: B007
            evaluation = self.evaluator.evaluate(layout)
            transient = {"generated_tests_timed_out"} & set(evaluation.failed_gates)
            if evaluation.passed or not transient:
                break
            logger.info(
                "evaluation_retry",
                capability_id=layout.capability_id,
                attempt=attempts,
                gates=evaluation.failed_gates,
            )
        assert evaluation is not None
        return evaluation, attempts

    def _incumbent_layout(self, capability_id: str) -> SkillLayout | None:
        """The currently registered version's skill directory, for comparison."""
        resolved = self.registry.resolve(capability_id)
        if resolved is None:
            return None
        source_ref = (resolved.get("skill_version") or {}).get("source_ref")
        manifest = resolved.get("manifest") or {}
        skill_name = manifest.get("skill")
        if not source_ref or not skill_name or not Path(source_ref).is_dir():
            return None
        return SkillLayout(
            root=Path(source_ref),
            skill_name=skill_name,
            capability_id=capability_id,
            version=resolved["version"],
        )

    def _next_version(self, capability_id: str, requested: str) -> str:
        parts = [int(p) for p in requested.split(".")]
        existing = {
            row["version"]
            for row in self.registry.list_skill_versions(
                capability_id=capability_id, limit=500
            )
        }
        for _ in range(1000):
            candidate = ".".join(str(p) for p in parts)
            if candidate not in existing:
                return candidate
            parts[2] += 1
        raise EvolutionError(
            EvolutionErrorClass.INTERNAL_BUG, "could not allocate a candidate skill version"
        )

    def _publish(self, layout: SkillLayout) -> Path:
        target = self.skills_root / layout.skill_name / layout.version
        if target.exists():
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                f"published skill {layout.skill_name}/{layout.version} already exists "
                "(published skill versions are immutable)",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            layout.root, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        return target

    def _resume(
        self,
        gap: dict[str, Any],
        request: Any,
        capability_id: str,
        stages: list[PipelineStage],
    ) -> dict[str, Any] | None:
        if self.resumer is None or not gap.get("task_id"):
            stages.append(
                PipelineStage(
                    "resume_task", "skipped", {"reason": "no task attached to this gap"}
                )
            )
            return None
        payload = request.resume_payload or {}
        try:
            resumption = self.resumer.resume(
                uuid.UUID(gap["task_id"]), capability_id, payload
            )
        except EvolutionError as exc:
            stages.append(PipelineStage("resume_task", "failed", exc.to_dict()))
            return {"status": "failed", "error": exc.to_dict()}
        stages.append(PipelineStage("resume_task", "ok", resumption.to_dict()))
        return resumption.to_dict()


__all__ = ["EvolutionPipeline", "EvolutionResult", "PipelineStage"]
