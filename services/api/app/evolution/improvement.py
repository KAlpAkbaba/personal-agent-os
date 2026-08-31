"""Telemetry-driven improvement of an EXISTING skill (ACCEPTANCE_TESTS M7
"Improvement of an existing skill"; EVOLUTION_ENGINE_SPEC §8).

    recorded weakness -> candidate improved version -> deterministic old-vs-new
    benchmark -> promote ONLY when objectively superior -> old version stays
    rollback-capable

Nothing here is a hunch: the weakness comes from telemetry recorded against the
REGISTERED version (``skill_versions.evaluation_json["telemetry"]``, appended by
``CapabilityRegistry.record_telemetry`` on every dispatch outcome), and the
promotion decision comes from running BOTH versions over the same case set in
isolated subprocesses and comparing the numbers.

Benchmark semantics: the shared case set is the IMPROVED candidate's eval set —
i.e. the behaviour the telemetry failures showed to be desired. The incumbent is
scored against exactly the same expectations, so "superior" means "gets more of
the cases the owner actually hit right", not "was measured on an easier set".
The candidate must also not regress latency beyond a bounded factor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.evolution import lifecycle as lifecycle_module
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.evaluation import SkillEvaluator
from app.evolution.registry import CapabilityRegistry
from app.evolution.resources import ResourceBudget
from app.evolution.review import IndependentSkillReviewer
from app.evolution.rollout import RolloutScore, load_eval_cases, run_cases
from app.evolution.sandbox import SandboxPolicy
from app.evolution.skills import (
    DeterministicSkillGenerator,
    SkillGenerator,
    SkillLayout,
    SkillSpec,
)
from app.evolution.supply_chain import scan_dependencies
from app.logging import get_logger
from app.selfhealing.service import compute_manifest_digest

logger = get_logger("app.evolution.improvement")

DEFAULT_MAX_FAILURE_RATE = 0.2
DEFAULT_MAX_P95_LATENCY_MS = 250.0
DEFAULT_MIN_SAMPLES = 5
# A candidate may be at most this much slower than the incumbent and still count
# as an improvement when it is functionally better.
LATENCY_REGRESSION_FACTOR = 2.0


@dataclass(slots=True)
class WeaknessThresholds:
    max_failure_rate: float = DEFAULT_MAX_FAILURE_RATE
    max_p95_latency_ms: float = DEFAULT_MAX_P95_LATENCY_MS
    min_samples: int = DEFAULT_MIN_SAMPLES


@dataclass(slots=True)
class ImprovementProposal:
    capability_id: str
    current_version: str
    skill_version_id: str
    weakness: str
    metric: str
    observed: float
    threshold: float
    telemetry: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "current_version": self.current_version,
            "skill_version_id": self.skill_version_id,
            "weakness": self.weakness,
            "metric": self.metric,
            "observed": self.observed,
            "threshold": self.threshold,
            "telemetry": {
                key: value for key, value in self.telemetry.items() if key != "samples"
            },
        }


@dataclass(slots=True)
class BenchmarkResult:
    incumbent: RolloutScore
    candidate: RolloutScore
    superior: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incumbent": self.incumbent.to_dict(),
            "candidate": self.candidate.to_dict(),
            "superior": self.superior,
            "reasons": list(self.reasons),
        }


@dataclass(slots=True)
class ImprovementResult:
    status: str  # "promoted" | "rejected" | "failed"
    capability_id: str
    proposal: dict[str, Any] = field(default_factory=dict)
    candidate_version: str | None = None
    skill_version_id: str | None = None
    benchmark: dict[str, Any] | None = None
    rollback_version: str | None = None
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "capability_id": self.capability_id,
            "proposal": self.proposal,
            "candidate_version": self.candidate_version,
            "skill_version_id": self.skill_version_id,
            "benchmark": self.benchmark,
            "rollback_version": self.rollback_version,
            "summary": self.summary,
        }


# ------------------------------------------------------------------ detector


class ImprovementDetector:
    """Turns recorded telemetry into an objective improvement proposal."""

    def __init__(
        self, registry: CapabilityRegistry, thresholds: WeaknessThresholds | None = None
    ) -> None:
        self.registry = registry
        self.thresholds = thresholds or WeaknessThresholds()

    def detect(self, capability_id: str) -> ImprovementProposal | None:
        resolved = self.registry.resolve(capability_id)
        if resolved is None:
            return None
        version_row = resolved.get("skill_version") or {}
        telemetry = (version_row.get("evaluation") or {}).get("telemetry") or {}
        samples = int(telemetry.get("invocations") or 0)
        if samples < self.thresholds.min_samples:
            return None
        failure_rate = float(telemetry.get("failure_rate") or 0.0)
        p95 = float(telemetry.get("p95_latency_ms") or 0.0)
        if failure_rate > self.thresholds.max_failure_rate:
            weakness, metric = "recurring_failures", "failure_rate"
            observed, threshold = failure_rate, self.thresholds.max_failure_rate
        elif p95 > self.thresholds.max_p95_latency_ms:
            weakness, metric = "latency_regression", "p95_latency_ms"
            observed, threshold = p95, self.thresholds.max_p95_latency_ms
        else:
            return None
        logger.info(
            "improvement_proposed",
            capability_id=capability_id,
            weakness=weakness,
            observed=observed,
        )
        return ImprovementProposal(
            capability_id=capability_id,
            current_version=resolved["version"],
            skill_version_id=version_row.get("id", ""),
            weakness=weakness,
            metric=metric,
            observed=observed,
            threshold=threshold,
            telemetry=telemetry,
        )


# ----------------------------------------------------------------- benchmark


def benchmark(
    incumbent: SkillLayout,
    candidate: SkillLayout,
    *,
    evaluator: SkillEvaluator | None = None,
    latency_factor: float = LATENCY_REGRESSION_FACTOR,
) -> BenchmarkResult:
    """Deterministic old-vs-new comparison over the candidate's case set."""
    evaluator = evaluator or SkillEvaluator()
    payload = load_eval_cases(candidate)
    candidate_score = run_cases(candidate, payload, evaluator=evaluator)
    incumbent_payload = {**payload, "module": incumbent.skill_name}
    incumbent_score = run_cases(incumbent, incumbent_payload, evaluator=evaluator)

    reasons: list[str] = []
    functionally_better = candidate_score.success_rate > incumbent_score.success_rate
    if not functionally_better:
        reasons.append(
            f"candidate success rate {candidate_score.success_rate:.3f} is not better than "
            f"incumbent {incumbent_score.success_rate:.3f}"
        )
    latency_ok = candidate_score.p95_latency_ms <= max(
        incumbent_score.p95_latency_ms * latency_factor, 1.0
    )
    if not latency_ok:
        reasons.append(
            f"candidate p95 {candidate_score.p95_latency_ms:.3f}ms regresses beyond "
            f"{latency_factor}x the incumbent's {incumbent_score.p95_latency_ms:.3f}ms"
        )
    if candidate_score.errors:
        reasons.append(f"candidate raised {candidate_score.errors} errors")
    superior = functionally_better and latency_ok and not candidate_score.errors
    logger.info(
        "benchmark_completed",
        capability_id=candidate.capability_id,
        superior=superior,
        candidate=candidate_score.success_rate,
        incumbent=incumbent_score.success_rate,
    )
    return BenchmarkResult(
        incumbent=incumbent_score,
        candidate=candidate_score,
        superior=superior,
        reasons=reasons,
    )


# ------------------------------------------------------------------ improver


class SkillImprover:
    """Builds, benchmarks and (only if superior) promotes an improved version."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        sandbox: SandboxPolicy,
        skills_root: Path,
        generator: SkillGenerator | None = None,
        evaluator: SkillEvaluator | None = None,
        reviewer: IndependentSkillReviewer | None = None,
        budget: ResourceBudget | None = None,
    ) -> None:
        self.registry = registry
        self.sandbox = sandbox
        self.skills_root = Path(skills_root)
        self.budget = budget or ResourceBudget()
        self.generator = generator or DeterministicSkillGenerator()
        self.evaluator = evaluator or SkillEvaluator(budget=self.budget)
        self.reviewer = reviewer or IndependentSkillReviewer(sandbox=sandbox)

    def improve(
        self, proposal: ImprovementProposal, spec_overrides: dict[str, Any]
    ) -> ImprovementResult:
        import shutil
        import tempfile

        result = ImprovementResult(
            status="failed",
            capability_id=proposal.capability_id,
            proposal=proposal.to_dict(),
            rollback_version=proposal.current_version,
        )
        incumbent = self._incumbent_layout(proposal.capability_id)
        if incumbent is None:
            raise EvolutionError(
                EvolutionErrorClass.NOT_FOUND,
                f"{proposal.capability_id} has no registered version to improve",
            )
        self.sandbox.prepare()
        work_dir = Path(tempfile.mkdtemp(prefix="evolution-improve-", dir=str(self.sandbox.root)))
        skill_version_id: uuid.UUID | None = None
        try:
            spec_dict = {
                **spec_overrides,
                "capability_id": proposal.capability_id,
                "version": self._next_version(proposal.capability_id, proposal.current_version),
            }
            spec = SkillSpec.parse(spec_dict)
            layout = self.generator.generate(spec, work_dir)
            self.sandbox.ensure_within(layout.root, label="improved skill root")

            created = self.registry.create_skill_version(
                spec.capability_id, spec.version, status="draft"
            )
            skill_version_id = uuid.UUID(created["id"])
            result.skill_version_id = created["id"]
            result.candidate_version = spec.version
            digest = compute_manifest_digest(layout.root)
            self.registry.record_build(
                skill_version_id, source_ref=str(layout.root), manifest_digest=digest
            )
            self.registry.advance_lifecycle(
                skill_version_id,
                lifecycle_module.STAGE_SANDBOX,
                {"workspace": work_dir.name, "manifest_digest": digest},
            )

            manifest = spec.capability_manifest(
                creation_reason={
                    "trigger": "improvement",
                    "resolution_path": "generation_from_scratch",
                },
                rollback_version=proposal.current_version,
                resource_budget=self.budget.to_dict(),
            )
            supply = scan_dependencies(
                list(manifest.get("dependencies") or []) + list(manifest.get("components") or [])
            )
            if not supply.ok:
                return self._reject(result, skill_version_id, "supply chain rejected")

            evaluation = self.evaluator.evaluate(layout)
            self.registry.record_evaluation(skill_version_id, evaluation.to_dict())
            if not evaluation.passed:
                return self._reject(
                    result,
                    skill_version_id,
                    "release gates failed: " + ", ".join(evaluation.failed_gates),
                )
            review, review_payload = self.reviewer.review_record(layout)
            self.registry.record_review(skill_version_id, review_payload)
            if not review.approved:
                return self._reject(
                    result, skill_version_id, f"independent review: {review.summary}"
                )
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

            # THE decision: objectively superior, or nothing happens.
            comparison = benchmark(incumbent, layout, evaluator=self.evaluator)
            result.benchmark = comparison.to_dict()
            if not comparison.superior:
                return self._reject(
                    result,
                    skill_version_id,
                    "benchmark: candidate is not objectively superior — "
                    + "; ".join(comparison.reasons),
                )
            self.registry.advance_lifecycle(
                skill_version_id,
                lifecycle_module.STAGE_SHADOW,
                {
                    "samples": comparison.candidate.samples,
                    "mismatches": comparison.candidate.failed,
                    "benchmark": comparison.to_dict(),
                },
            )
            self.registry.advance_lifecycle(
                skill_version_id,
                lifecycle_module.STAGE_CANARY,
                {
                    "samples": comparison.candidate.samples,
                    "not_worse_than_incumbent": True,
                    "benchmark": comparison.to_dict(),
                },
            )

            published = self._publish(layout)
            published_digest = compute_manifest_digest(published)
            self.registry.record_source_ref(
                skill_version_id, source_ref=str(published), manifest_digest=published_digest
            )
            manifest["source_ref"] = str(published)
            manifest["provenance"] = {
                "generator": self.generator.name,
                "trigger": "improvement",
                "benchmark": comparison.to_dict(),
                "published_digest": published_digest,
            }
            manifest["evaluation_metrics"] = evaluation.score.to_dict()
            self.registry.register(proposal.capability_id, skill_version_id, manifest)
            result.status = "promoted"
            result.summary = (
                f"{proposal.capability_id} {proposal.current_version} -> {spec.version}: "
                f"success rate {comparison.incumbent.success_rate:.3f} -> "
                f"{comparison.candidate.success_rate:.3f}"
            )
            logger.info(
                "improvement_promoted",
                capability_id=proposal.capability_id,
                from_version=proposal.current_version,
                to_version=spec.version,
            )
            return result
        except EvolutionError as exc:
            if skill_version_id is not None:
                with_reason = exc.message
                try:
                    self.registry.reject_skill_version(skill_version_id, with_reason)
                except EvolutionError:
                    pass
            result.status = "failed"
            result.summary = exc.message
            return result
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def rollback(self, capability_id: str, target_version: str) -> dict[str, Any]:
        """Restore a previously registered version to production."""
        return self.registry.rollback_to(capability_id, target_version)

    # ------------------------------------------------------------------ utils

    def _reject(
        self, result: ImprovementResult, skill_version_id: uuid.UUID, reason: str
    ) -> ImprovementResult:
        self.registry.reject_skill_version(skill_version_id, reason)
        result.status = "rejected"
        result.summary = reason
        logger.info(
            "improvement_rejected",
            capability_id=result.capability_id,
            skill_version_id=str(skill_version_id),
        )
        return result

    def _incumbent_layout(self, capability_id: str) -> SkillLayout | None:
        resolved = self.registry.resolve(capability_id)
        if resolved is None:
            return None
        source_ref = (resolved.get("skill_version") or {}).get("source_ref")
        skill_name = (resolved.get("manifest") or {}).get("skill")
        if not source_ref or not skill_name or not Path(source_ref).is_dir():
            return None
        return SkillLayout(
            root=Path(source_ref),
            skill_name=skill_name,
            capability_id=capability_id,
            version=resolved["version"],
        )

    def _next_version(self, capability_id: str, current: str) -> str:
        parts = [int(p) for p in current.split(".")]
        parts[1] += 1
        parts[2] = 0
        existing = {
            row["version"]
            for row in self.registry.list_skill_versions(capability_id=capability_id, limit=500)
        }
        for _ in range(1000):
            candidate = ".".join(str(p) for p in parts)
            if candidate not in existing:
                return candidate
            parts[2] += 1
        raise EvolutionError(
            EvolutionErrorClass.INTERNAL_BUG, "could not allocate an improved version"
        )

    def _publish(self, layout: SkillLayout) -> Path:
        import shutil

        target = self.skills_root / layout.skill_name / layout.version
        if target.exists():
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                f"published skill {layout.skill_name}/{layout.version} already exists",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            layout.root, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        return target


__all__ = [
    "DEFAULT_MAX_FAILURE_RATE",
    "DEFAULT_MAX_P95_LATENCY_MS",
    "DEFAULT_MIN_SAMPLES",
    "LATENCY_REGRESSION_FACTOR",
    "BenchmarkResult",
    "ImprovementDetector",
    "ImprovementProposal",
    "ImprovementResult",
    "SkillImprover",
    "WeaknessThresholds",
    "benchmark",
]
