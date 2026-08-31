"""Unit tests: SelfHealingPipeline orchestration with a fake deployer.

Real deterministic backend + real reviewer + real (SQLite) service; only the
supervisor deploy seam is faked, so every gate ordering rule is exercised
without spawning servers (the real supervisor path runs in the integration
E2E).
"""

import contextlib
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.selfhealing.backends import DeterministicCodingBackend
from app.selfhealing.models import Incident, Release
from app.selfhealing.monitoring import draft_from_supervisor_report
from app.selfhealing.pipeline import DeployResult, SelfHealingPipeline
from app.selfhealing.service import SelfHealingService

REPO_ROOT = Path(__file__).resolve().parents[4]
RELEASES_SRC = REPO_ROOT / "staging" / "target-service" / "releases-src"


@pytest.fixture()
def service() -> SelfHealingService:
    engine = create_engine("sqlite://")
    for table in (Release.__table__, Incident.__table__):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return SelfHealingService(session_scope)


class FakeDeployer:
    def __init__(self, *, staging_healthy: bool = True, production_healthy: bool = True):
        self.staging_healthy = staging_healthy
        self.production_healthy = production_healthy
        self.calls: list[tuple[str, str, Path]] = []

    def deploy_staging(self, release_source: Path, version: str) -> DeployResult:
        self.calls.append(("staging", version, Path(release_source)))
        return DeployResult(healthy=self.staging_healthy, detail={"env": "staging"})

    def promote_production(self, release_source: Path, version: str) -> DeployResult:
        self.calls.append(("production", version, Path(release_source)))
        return DeployResult(healthy=self.production_healthy, detail={"env": "production"})


def seed_incident(service: SelfHealingService, tmp_path: Path) -> uuid.UUID:
    """Stage the broken release into a workspace layout and ingest the incident
    the supervisor would have reported after rolling back."""
    workspace = tmp_path / "prod-ws"
    broken = workspace / "releases" / "1.1.0"
    broken.parent.mkdir(parents=True)
    shutil.copytree(RELEASES_SRC / "1.1.0", broken)
    report = {
        "schema": "pagentos.selfhealing.incident.v1",
        "component": "browser-agent-demo",
        "severity": "critical",
        "fingerprint_material": {
            "component": "browser-agent-demo",
            "error_class": "wrong_error_mapping",
            "failing_check": "selftest",
        },
        "workspace": str(workspace),
        "active_version": "1.1.0",
        "active_manifest_digest": "ab" * 32,
        "last_known_good": "1.0.0",
        "rolled_back_to": "1.0.0",
        "evidence": {
            "selftest": {
                "status": "fail",
                "error_class": "wrong_error_mapping",
                "check": "map_error",
                "input": "net::ERR_NAME_NOT_RESOLVED at https://example.invalid",
                "expected": "dependency_unavailable",
                "actual": "internal_bug",
            }
        },
        "detected_at": "2026-08-31T00:00:00+00:00",
        "recovered_at": "2026-08-31T00:00:05+00:00",
    }
    return service.ingest_incident(draft_from_supervisor_report(report)).incident_id


def make_pipeline(service, deployer, tmp_path: Path) -> SelfHealingPipeline:
    return SelfHealingPipeline(
        service,
        DeterministicCodingBackend(),
        deployer,
        work_root=tmp_path / "work",
        allowed_roots=[tmp_path],
    )


def step_status(result, name: str) -> str | None:
    for step in result.steps:
        if step.name == name:
            return step.status
    return None


def test_full_pipeline_fixes_incident(service, tmp_path: Path) -> None:
    incident_id = seed_incident(service, tmp_path)
    deployer = FakeDeployer()
    result = make_pipeline(service, deployer, tmp_path).run(incident_id)

    assert result.status == "fixed"
    assert result.candidate_version == "1.1.1"
    for name in (
        "load_incident",
        "analyze_issue",
        "reproduce",
        "write_regression_test",
        "patch",
        "regression_on_candidate",
        "review_change",
        "build_release",
        "staging_deploy",
        "production_promote",
        "mark_incident_fixed",
    ):
        assert step_status(result, name) == "ok", name
    # Staging strictly before production, same release dir both times.
    assert [c[0] for c in deployer.calls] == ["staging", "production"]
    assert deployer.calls[0][2] == deployer.calls[1][2]
    # Records: incident fixed + fixed_release link; candidate active; broken
    # release row untouched as rolled_back.
    incident = service.get_incident(incident_id)
    assert incident["status"] == "fixed"
    fixed = service.get_release("browser-agent-demo", "1.1.1")
    assert fixed["status"] == "active"
    assert incident["fixed_release_id"] == fixed["id"]
    assert len(fixed["manifest_digest"]) == 64
    assert service.get_release("browser-agent-demo", "1.1.0")["status"] == "rolled_back"
    # The shipped release dir carries a manifest.
    release_dir = deployer.calls[0][2]
    assert (release_dir / "manifest.json").is_file()
    assert (release_dir / "handler.py").is_file()


def test_reviewer_gate_blocks_unfixed_patch(service, tmp_path: Path) -> None:
    """Builder/reviewer separation: even a backend that lies about its own
    review can never promote — the pipeline consults ONLY the independent
    reviewer, and a patch that does not fix the regression test is refused."""

    class LyingBackend(DeterministicCodingBackend):
        def implement_change(self, analysis, broken_release_dir, output_dir):
            patch = super().implement_change(analysis, broken_release_dir, output_dir)
            # Sabotage: un-fix the candidate (put the broken handler back) but
            # keep a syntactically valid module.
            shutil.copyfile(
                Path(broken_release_dir) / "handler.py", patch.candidate_dir / "handler.py"
            )
            return patch

        def review_change(self, analysis, patch, broken_release_dir):  # self-approval
            raise AssertionError("pipeline must never ask the builder for review")

    incident_id = seed_incident(service, tmp_path)
    deployer = FakeDeployer()
    pipeline = SelfHealingPipeline(
        service,
        LyingBackend(),
        deployer,
        work_root=tmp_path / "work2",
        allowed_roots=[tmp_path],
    )
    result = pipeline.run(incident_id)
    assert result.status == "failed"
    # Failed before any deploy: production and staging untouched.
    assert deployer.calls == []
    # No release row was ever created for a rejected-at-review candidate.
    assert service.get_release("browser-agent-demo", "1.1.1") is None
    # Incident is back to recovered (retryable), not stuck in fix_in_progress.
    assert service.get_incident(incident_id)["status"] == "recovered"


def test_bad_candidate_on_staging_is_rejected_production_untouched(
    service, tmp_path: Path
) -> None:
    incident_id = seed_incident(service, tmp_path)
    deployer = FakeDeployer(staging_healthy=False)
    result = make_pipeline(service, deployer, tmp_path).run(incident_id)
    assert result.status == "failed"
    assert step_status(result, "staging_deploy") == "failed"
    # Production was never touched.
    assert [c[0] for c in deployer.calls] == ["staging"]
    # The candidate release record is marked rejected.
    assert service.get_release("browser-agent-demo", "1.1.1")["status"] == "rejected"
    assert service.get_incident(incident_id)["status"] == "recovered"


def test_production_failure_marks_rejected(service, tmp_path: Path) -> None:
    incident_id = seed_incident(service, tmp_path)
    deployer = FakeDeployer(production_healthy=False)
    result = make_pipeline(service, deployer, tmp_path).run(incident_id)
    assert result.status == "failed"
    assert step_status(result, "staging_deploy") == "ok"
    assert step_status(result, "production_promote") == "failed"
    assert service.get_release("browser-agent-demo", "1.1.1")["status"] == "rejected"
    assert service.get_incident(incident_id)["status"] == "recovered"


def test_pipeline_refuses_closed_incident(service, tmp_path: Path) -> None:
    incident_id = seed_incident(service, tmp_path)
    service.set_incident_status(incident_id, "closed")
    result = make_pipeline(service, FakeDeployer(), tmp_path).run(incident_id)
    assert result.status == "failed"
    assert result.steps[-1].name == "pipeline_error"
    assert service.get_incident(incident_id)["status"] == "closed"  # untouched


def test_pipeline_version_bump_skips_existing(service, tmp_path: Path) -> None:
    incident_id = seed_incident(service, tmp_path)
    service.record_release("browser-agent-demo", "1.1.1", "ee" * 32, status="rejected")
    result = make_pipeline(service, FakeDeployer(), tmp_path).run(incident_id)
    assert result.status == "fixed"
    assert result.candidate_version == "1.1.2"


def test_pipeline_rejects_workspace_outside_allowed_roots(service, tmp_path: Path) -> None:
    incident_id = seed_incident(service, tmp_path)
    pipeline = SelfHealingPipeline(
        service,
        DeterministicCodingBackend(),
        FakeDeployer(),
        work_root=tmp_path / "work3",
        allowed_roots=[tmp_path / "elsewhere"],
    )
    result = pipeline.run(incident_id)
    assert result.status == "failed"
    assert "allowed workspace roots" in result.summary
