"""Unit tests: release preflight (M18 spec §5) — refuse, never warn.

Each check is exercised individually so a failure report can be trusted:
every ``passed=False`` here corresponds to something genuinely missing.
"""

from __future__ import annotations

from app.release.preflight import (
    FakeGitTreeInspector,
    ReleaseCandidate,
    run_preflight,
)

GOOD_HISTORY = [{"to": "shadow_ready"}, {"to": "owner_authorized"}]
GOOD_EVIDENCE = {
    "tests": {"passed": True},
    "security_review": {"acceptable": True},
    "benchmark": {"passed": True},
    "dependencies": {"unpinned": []},
}
PREVIOUS_RELEASE = {
    "id": "11111111-1111-1111-1111-111111111111",
    "version": "1.0.0",
    "manifest_digest": "sha256:" + "a" * 64,
    "status": "active",
}


def candidate(**overrides) -> ReleaseCandidate:
    body = dict(
        opportunity={"detail": {"transitions": GOOD_HISTORY}},
        component="cloud_core",
        version="1.1.0",
        candidate_ref="deadbeef",
        manifest_digest="sha256:" + "b" * 64,
        changed_paths=["services/api/app/goals/service.py"],
        evidence=GOOD_EVIDENCE,
    )
    body.update(overrides)
    return ReleaseCandidate(**body)


def test_a_fully_evidenced_candidate_passes_every_check() -> None:
    report = run_preflight(
        candidate(), git_tree=FakeGitTreeInspector(clean=True), previous_release=PREVIOUS_RELEASE
    )
    assert report.passed, report.failed_checks
    assert report.failed_checks == []
    assert report.risk.tier >= 1


def test_all_ten_checks_run_even_when_several_fail() -> None:
    """A refused release should report EVERYTHING missing, not just the first."""
    report = run_preflight(
        candidate(
            opportunity={"detail": {"transitions": []}},
            version="",
            candidate_ref="",
            evidence={},
        ),
        git_tree=FakeGitTreeInspector(clean=False, detail="2 files dirty"),
        previous_release=None,
    )
    assert len(report.checks) == 10
    assert not report.passed
    assert {
        "candidate_is_shadow_ready",
        "version_diff_known",
        "source_committed_and_clean",
        "tests_passed",
        "security_review_acceptable",
        "benchmark_passed",
        "dependencies_known",
        "rollback_point_exists",
    } <= set(report.failed_checks)


def test_candidate_is_shadow_ready_requires_the_real_history_not_the_label() -> None:
    report = run_preflight(
        candidate(opportunity={"detail": {"transitions": [{"to": "researching"}]}}),
        git_tree=FakeGitTreeInspector(),
        previous_release=PREVIOUS_RELEASE,
    )
    check = next(c for c in report.checks if c.name == "candidate_is_shadow_ready")
    assert check.passed is False


def test_dirty_tree_refuses() -> None:
    report = run_preflight(
        candidate(), git_tree=FakeGitTreeInspector(clean=False, detail="1 file dirty"),
        previous_release=PREVIOUS_RELEASE,
    )
    check = next(c for c in report.checks if c.name == "source_committed_and_clean")
    assert check.passed is False
    assert "dirty" in check.detail


def test_tests_security_and_benchmark_each_refuse_independently() -> None:
    for key in ("tests", "security_review", "benchmark"):
        evidence = {k: v for k, v in GOOD_EVIDENCE.items() if k != key}
        report = run_preflight(
            candidate(evidence=evidence),
            git_tree=FakeGitTreeInspector(),
            previous_release=PREVIOUS_RELEASE,
        )
        assert not report.passed, key


def test_migration_impact_known_is_trivially_true_with_no_migrations() -> None:
    report = run_preflight(
        candidate(changed_paths=["services/api/app/goals/service.py"]),
        git_tree=FakeGitTreeInspector(),
        previous_release=PREVIOUS_RELEASE,
    )
    check = next(c for c in report.checks if c.name == "migration_impact_known")
    assert check.passed is True
    assert "no migrations" in check.detail


def test_migration_impact_known_requires_a_plan_when_migrations_are_touched() -> None:
    touching_migration = candidate(
        changed_paths=["services/api/alembic/versions/20260906_0019_x.py"]
    )
    refused = run_preflight(
        touching_migration, git_tree=FakeGitTreeInspector(), previous_release=PREVIOUS_RELEASE
    )
    check = next(c for c in refused.checks if c.name == "migration_impact_known")
    assert check.passed is False

    with_plan = candidate(
        changed_paths=["services/api/alembic/versions/20260906_0019_x.py"],
        evidence={
            **GOOD_EVIDENCE,
            "migration_plan": {"summary": "adds a nullable column", "reversible": True},
        },
    )
    accepted = run_preflight(
        with_plan, git_tree=FakeGitTreeInspector(), previous_release=PREVIOUS_RELEASE
    )
    check = next(c for c in accepted.checks if c.name == "migration_impact_known")
    assert check.passed is True


def test_dependencies_known_refuses_unpinned_and_missing_report() -> None:
    evidence_without_deps = {k: v for k, v in GOOD_EVIDENCE.items() if k != "dependencies"}
    missing_report = candidate(evidence=evidence_without_deps)
    report = run_preflight(
        missing_report, git_tree=FakeGitTreeInspector(), previous_release=PREVIOUS_RELEASE
    )
    assert not next(c for c in report.checks if c.name == "dependencies_known").passed

    unpinned = candidate(evidence={**GOOD_EVIDENCE, "dependencies": {"unpinned": ["requests"]}})
    report = run_preflight(
        unpinned, git_tree=FakeGitTreeInspector(), previous_release=PREVIOUS_RELEASE
    )
    assert not next(c for c in report.checks if c.name == "dependencies_known").passed


def test_rollback_point_exists_refuses_the_first_ever_deployment_of_a_component() -> None:
    """Deliberately conservative (ADR-0058): no prior active release means no
    rollback target, and the spec's precondition is literal."""
    report = run_preflight(candidate(), git_tree=FakeGitTreeInspector(), previous_release=None)
    check = next(c for c in report.checks if c.name == "rollback_point_exists")
    assert check.passed is False
    assert not report.passed


def test_component_genuinely_needs_deploying_refuses_a_no_op() -> None:
    identical_digest = candidate(manifest_digest=PREVIOUS_RELEASE["manifest_digest"])
    report = run_preflight(
        identical_digest, git_tree=FakeGitTreeInspector(), previous_release=PREVIOUS_RELEASE
    )
    check = next(c for c in report.checks if c.name == "component_genuinely_needs_deploying")
    assert check.passed is False
    assert "no-op" in check.detail
    assert not report.passed


def test_component_genuinely_needs_deploying_passes_with_no_baseline_to_compare() -> None:
    """The no-baseline case is its own honest state, not a false 'no-op'."""
    report = run_preflight(candidate(), git_tree=FakeGitTreeInspector(), previous_release=None)
    check = next(c for c in report.checks if c.name == "component_genuinely_needs_deploying")
    assert check.passed is True


def test_the_report_carries_the_derived_risk_tier() -> None:
    report = run_preflight(
        candidate(changed_paths=["services/api/app/identity/x.py"]),
        git_tree=FakeGitTreeInspector(),
        previous_release=PREVIOUS_RELEASE,
    )
    assert report.risk.tier == 5
    assert report.to_dict()["risk"]["tier"] == 5
