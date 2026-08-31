"""Self-healing pipeline: EVOLUTION_ENGINE_SPEC §7 as a deterministic orchestrator.

    incident -> reproduce (isolated copy of the broken release)
             -> regression test (must FAIL on broken)
             -> patch via CodingBackend
             -> regression test PASSES on candidate
             -> independent review gate (builder can never promote alone)
             -> build candidate release dir (bumped version + manifest digest)
             -> staging deploy via the Recovery Supervisor (staging workspace/port)
             -> health green -> production promote (production workspace)
             -> incident marked fixed with fixed_release_id

Deployment goes through the SAME stdlib supervisor CLI that guards production
(services/recovery-supervisor/supervisor.py): the pipeline never repoints a
release by hand and never touches production before staging is green. A failed
staging/production deploy is auto-rolled-back BY THE SUPERVISOR and the
candidate is marked rejected.

ADR-0023 constraint: this engineering loop may read memory but must NEVER
mutate explicit owner memories or recovery roots — this module therefore has
no import of, and no call into, the owner-actor memory mutation surface (a
grep-level guard test enforces it).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.logging import get_logger
from app.selfhealing.backends import (
    CodingBackend,
    DeterministicReviewer,
    run_regression_test,
)
from app.selfhealing.errors import SelfHealingError, SelfHealingErrorClass
from app.selfhealing.service import SelfHealingService, build_manifest, compute_manifest_digest

logger = get_logger("app.selfhealing.pipeline")


# ------------------------------------------------------------------ deployers


@dataclass(slots=True)
class DeployResult:
    healthy: bool
    detail: dict[str, Any] = field(default_factory=dict)


class StagingDeployer(Protocol):
    """Seam so the pipeline is unit-testable without real processes."""

    def deploy_staging(self, release_source: Path, version: str) -> DeployResult: ...

    def promote_production(self, release_source: Path, version: str) -> DeployResult: ...


class SupervisorDeployer:
    """Drives the stdlib Recovery Supervisor CLI for staging and production.

    Staging and production are SEPARATE workspaces and ports; a candidate only
    reaches the production workspace after the staging health policy passed.
    ``--promote-on-healthy`` makes the supervisor itself move last-known-good,
    so every workspace always has a rollback target for the NEXT candidate.
    """

    def __init__(
        self,
        *,
        supervisor_script: Path,
        target_service_script: Path,
        staging_workspace: Path,
        staging_port: int,
        production_workspace: Path,
        production_port: int,
        component: str,
        python: str = sys.executable,
        interval_s: float = 0.2,
        failure_threshold: int = 2,
        window_s: float = 10.0,
        startup_timeout_s: float = 30.0,
        max_cycles: int = 3,
        run_timeout_s: float = 120.0,
    ) -> None:
        self.supervisor_script = Path(supervisor_script)
        self.target_service_script = Path(target_service_script)
        self.staging_workspace = Path(staging_workspace)
        self.staging_port = staging_port
        self.production_workspace = Path(production_workspace)
        self.production_port = production_port
        self.component = component
        self.python = python
        self.interval_s = interval_s
        self.failure_threshold = failure_threshold
        self.window_s = window_s
        self.startup_timeout_s = startup_timeout_s
        self.max_cycles = max_cycles
        self.run_timeout_s = run_timeout_s

    def _supervisor(self, workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
        command = [
            self.python,
            str(self.supervisor_script),
            "--workspace",
            str(workspace),
            "--json",
            *args,
        ]
        return subprocess.run(  # noqa: S603 - repo-local supervisor script
            command, capture_output=True, text=True, timeout=self.run_timeout_s
        )

    def _deploy(
        self, workspace: Path, port: int, release_source: Path, version: str
    ) -> DeployResult:
        activated = self._supervisor(
            workspace, "activate", "--version", version, "--source", str(release_source)
        )
        if activated.returncode != 0:
            output = (activated.stdout + activated.stderr)[-2000:]
            return DeployResult(healthy=False, detail={"stage": "activate", "output": output})
        run = self._supervisor(
            workspace,
            "run",
            "--component",
            self.component,
            "--health-url",
            f"http://127.0.0.1:{port}/health",
            "--selftest-url",
            f"http://127.0.0.1:{port}/selftest",
            f"--command-arg={self.python}",
            f"--command-arg={self.target_service_script}",
            "--command-arg=--workspace",
            f"--command-arg={workspace}",
            "--command-arg=--port",
            f"--command-arg={port}",
            "--interval",
            str(self.interval_s),
            "--failure-threshold",
            str(self.failure_threshold),
            "--window",
            str(self.window_s),
            "--startup-timeout",
            str(self.startup_timeout_s),
            "--max-cycles",
            str(self.max_cycles),
            "--promote-on-healthy",
        )
        try:
            verdict = json.loads(run.stdout)
        except ValueError:
            verdict = {"raw": run.stdout[-2000:], "stderr": run.stderr[-2000:]}
        healthy = run.returncode == 0 and verdict.get("result") == "healthy"
        return DeployResult(
            healthy=healthy,
            detail={"workspace": str(workspace), "verdict": verdict, "exit_code": run.returncode},
        )

    def deploy_staging(self, release_source: Path, version: str) -> DeployResult:
        return self._deploy(self.staging_workspace, self.staging_port, release_source, version)

    def promote_production(self, release_source: Path, version: str) -> DeployResult:
        return self._deploy(
            self.production_workspace, self.production_port, release_source, version
        )


# ------------------------------------------------------------------- pipeline


@dataclass(slots=True)
class PipelineStep:
    name: str
    status: str  # "ok" | "failed"
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PipelineResult:
    incident_id: str
    status: str  # "fixed" | "failed"
    steps: list[PipelineStep] = field(default_factory=list)
    candidate_version: str | None = None
    release_id: str | None = None
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "status": self.status,
            "steps": [
                {"name": s.name, "status": s.status, "detail": s.detail} for s in self.steps
            ],
            "candidate_version": self.candidate_version,
            "release_id": self.release_id,
            "summary": self.summary,
        }


class SelfHealingPipeline:
    def __init__(
        self,
        service: SelfHealingService,
        backend: CodingBackend,
        deployer: StagingDeployer,
        *,
        reviewer: DeterministicReviewer | None = None,
        work_root: Path | None = None,
        allowed_roots: list[Path] | None = None,
    ) -> None:
        self.service = service
        self.backend = backend
        self.deployer = deployer
        # Builder/reviewer separation: the reviewer instance is independent of
        # the coding backend and is the only acceptance authority.
        self.reviewer = reviewer or DeterministicReviewer()
        self.work_root = work_root
        # When given, the broken-release path taken from incident evidence must
        # live under one of these roots (defense against a forged report).
        self.allowed_roots = [Path(root).resolve() for root in (allowed_roots or [])]

    def run(self, incident_id: uuid.UUID) -> PipelineResult:
        result = PipelineResult(incident_id=str(incident_id), status="failed")
        steps = result.steps
        if self.work_root is not None:
            Path(self.work_root).mkdir(parents=True, exist_ok=True)
        work_dir = Path(
            tempfile.mkdtemp(
                prefix="selfhealing-", dir=str(self.work_root) if self.work_root else None
            )
        )
        release_row: dict[str, Any] | None = None
        try:
            # 1. Load the incident.
            incident = self.service.get_incident(incident_id)
            if incident["status"] not in ("open", "recovered"):
                raise SelfHealingError(
                    SelfHealingErrorClass.VALIDATION_ERROR,
                    f"incident is {incident['status']}; pipeline needs open/recovered",
                )
            component = incident["component"]
            evidence = incident.get("evidence") or {}
            broken_version = evidence.get("active_version")
            broken_dir = self._resolve_broken_release(evidence)
            self.service.set_incident_status(incident_id, "fix_in_progress")
            steps.append(
                PipelineStep(
                    "load_incident",
                    "ok",
                    {"component": component, "broken_version": broken_version},
                )
            )

            # 2. Analyze the evidence (CodingBackend.analyze_issue).
            analysis = self.backend.analyze_issue(incident)
            steps.append(
                PipelineStep(
                    "analyze_issue", "ok", {"fault_kind": analysis.fault_kind,
                                            "summary": analysis.summary}
                )
            )

            # 3. Reproduce in isolation: copy the broken release into a temp
            # workspace; the failing counterexample must actually fail there.
            isolated_broken = work_dir / "broken"
            shutil.copytree(
                broken_dir,
                isolated_broken,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            patch = self.backend.implement_change(analysis, isolated_broken, work_dir)
            reproduced_pass, reproduce_out = run_regression_test(
                patch.regression_test_path, isolated_broken
            )
            if reproduced_pass:
                raise SelfHealingError(
                    SelfHealingErrorClass.REPRODUCTION_FAILED,
                    "could not reproduce the bug: the failing check passes on the broken release",
                    details={"output": reproduce_out[-500:]},
                )
            steps.append(
                PipelineStep("reproduce", "ok", {"regression_on_broken": "failed_as_expected"})
            )
            steps.append(
                PipelineStep(
                    "write_regression_test",
                    "ok",
                    {"path": patch.regression_test_path.name},
                )
            )
            steps.append(
                PipelineStep(
                    "patch", "ok", {"changed_files": patch.changed_files, "notes": patch.notes}
                )
            )

            # 4. Regression test must pass on the candidate.
            candidate_pass, candidate_out = run_regression_test(
                patch.regression_test_path, patch.candidate_dir
            )
            if not candidate_pass:
                raise SelfHealingError(
                    SelfHealingErrorClass.PIPELINE_STAGE_FAILED,
                    "regression test still fails on the candidate",
                    details={"output": candidate_out[-500:]},
                )
            steps.append(PipelineStep("regression_on_candidate", "ok", {}))

            # 5. Independent review gate (never the builder's own verdict).
            review = self.reviewer.review_change(analysis, patch, isolated_broken)
            if not review.approved:
                steps.append(PipelineStep("review_change", "failed", review.to_dict()))
                raise SelfHealingError(
                    SelfHealingErrorClass.REVIEW_REJECTED,
                    f"independent reviewer rejected the candidate: {review.summary}",
                )
            steps.append(PipelineStep("review_change", "ok", review.to_dict()))

            # 6. Build the candidate release directory (new version + manifest).
            candidate_version = self._next_version(component, broken_version)
            release_dir = work_dir / "release" / candidate_version
            shutil.copytree(patch.candidate_dir, release_dir)
            manifest = build_manifest(release_dir, candidate_version)
            (release_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            digest = manifest["digest"]
            assert digest == compute_manifest_digest(release_dir)  # manifest.json excluded
            release_row = self.service.record_release(
                component, candidate_version, digest, status="candidate"
            )
            result.candidate_version = candidate_version
            result.release_id = release_row["id"]
            steps.append(
                PipelineStep(
                    "build_release",
                    "ok",
                    {"version": candidate_version, "manifest_digest": digest},
                )
            )

            # 7. Staging deploy via the supervisor; unhealthy => supervisor has
            # already rolled staging back — candidate is rejected, production
            # untouched.
            staging = self.deployer.deploy_staging(release_dir, candidate_version)
            if not staging.healthy:
                steps.append(PipelineStep("staging_deploy", "failed", staging.detail))
                self.service.set_release_status(
                    uuid.UUID(release_row["id"]), "rejected", health=staging.detail
                )
                raise SelfHealingError(
                    SelfHealingErrorClass.PIPELINE_STAGE_FAILED,
                    "candidate failed staging health policy; staging rolled back, "
                    "candidate rejected",
                )
            self.service.set_release_status(
                uuid.UUID(release_row["id"]), "staging", health=staging.detail
            )
            steps.append(PipelineStep("staging_deploy", "ok", staging.detail))

            # 8. Production promote via the supervisor.
            production = self.deployer.promote_production(release_dir, candidate_version)
            if not production.healthy:
                steps.append(PipelineStep("production_promote", "failed", production.detail))
                self.service.set_release_status(
                    uuid.UUID(release_row["id"]), "rejected", health=production.detail
                )
                raise SelfHealingError(
                    SelfHealingErrorClass.PIPELINE_STAGE_FAILED,
                    "candidate failed production health policy; production rolled back, "
                    "candidate rejected",
                )
            release_id = uuid.UUID(release_row["id"])
            self.service.set_release_status(release_id, "active", health=production.detail)
            self.service.supersede_active_releases(component, except_release_id=release_id)
            steps.append(PipelineStep("production_promote", "ok", production.detail))

            # 9. Close the loop.
            self.service.set_incident_status(
                incident_id, "fixed", fixed_release_id=release_id
            )
            result.status = "fixed"
            result.summary = self.backend.summarize_patch(analysis, patch)
            steps.append(PipelineStep("mark_incident_fixed", "ok", {}))
            logger.info(
                "pipeline_fixed",
                incident_id=str(incident_id),
                candidate_version=candidate_version,
            )
            return result
        except SelfHealingError as exc:
            steps.append(
                PipelineStep("pipeline_error", "failed", exc.to_dict())
            )
            result.summary = exc.message
            # The service was already restored by the supervisor's rollback;
            # the incident goes back to 'recovered' so the fix can be retried.
            try:
                current = self.service.get_incident(incident_id)
                if current["status"] == "fix_in_progress":
                    self.service.set_incident_status(incident_id, "recovered")
            except SelfHealingError:
                pass
            logger.info(
                "pipeline_failed", incident_id=str(incident_id), error=str(exc.error_class)
            )
            return result

    # ------------------------------------------------------------------ utils

    def _resolve_broken_release(self, evidence: dict[str, Any]) -> Path:
        workspace = evidence.get("workspace")
        version = evidence.get("active_version")
        if not workspace or not version:
            raise SelfHealingError(
                SelfHealingErrorClass.VALIDATION_ERROR,
                "incident evidence lacks workspace/active_version; cannot locate broken release",
            )
        broken_dir = (Path(workspace) / "releases" / str(version)).resolve()
        if self.allowed_roots and not any(
            broken_dir.is_relative_to(root) for root in self.allowed_roots
        ):
            raise SelfHealingError(
                SelfHealingErrorClass.VALIDATION_ERROR,
                "broken release path is outside the allowed workspace roots",
                details={"path": str(broken_dir)},
            )
        if not broken_dir.is_dir():
            raise SelfHealingError(
                SelfHealingErrorClass.NOT_FOUND,
                f"broken release directory not found: {broken_dir}",
            )
        return broken_dir

    def _next_version(self, component: str, broken_version: str | None) -> str:
        parts = [0, 0, 0]
        if broken_version:
            try:
                parsed = [int(p) for p in str(broken_version).split(".")]
                parts = (parsed + [0, 0, 0])[:3]
            except ValueError:
                parts = [0, 0, 0]
        candidate = [parts[0], parts[1], parts[2] + 1]
        for _ in range(1000):
            version = ".".join(str(p) for p in candidate)
            if self.service.get_release(component, version) is None:
                return version
            candidate[2] += 1
        raise SelfHealingError(
            SelfHealingErrorClass.INTERNAL_BUG, "could not allocate a candidate version"
        )


__all__ = [
    "DeployResult",
    "PipelineResult",
    "PipelineStep",
    "SelfHealingPipeline",
    "StagingDeployer",
    "SupervisorDeployer",
]
