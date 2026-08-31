"""Unit tests: CodingBackend seam, deterministic patch derivation, the
independent reviewer, and the inert-without-configuration Claude backend.

The broken handler used here is the actual checked-in injected-bug release
template (staging/target-service/releases-src/1.1.0), so these tests pin the
acceptance scenario end to end.
"""

import shutil
from pathlib import Path

import pytest

from app.selfhealing.backends import (
    ClaudeCodingBackend,
    CodingBackend,
    DeterministicCodingBackend,
    DeterministicReviewer,
    run_regression_test,
)
from app.selfhealing.errors import SelfHealingError, SelfHealingErrorClass
from app.selfhealing.service import compute_fingerprint

REPO_ROOT = Path(__file__).resolve().parents[4]
RELEASES_SRC = REPO_ROOT / "staging" / "target-service" / "releases-src"

FINGERPRINT = compute_fingerprint("browser-agent-demo", "wrong_error_mapping", "selftest")


def make_incident() -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "component": "browser-agent-demo",
        "fingerprint": FINGERPRINT,
        "status": "recovered",
        "evidence": {
            "fingerprint_material": {
                "component": "browser-agent-demo",
                "error_class": "wrong_error_mapping",
                "failing_check": "selftest",
            },
            "selftest": {
                "status": "fail",
                "error_class": "wrong_error_mapping",
                "check": "map_error",
                "input": "net::ERR_NAME_NOT_RESOLVED at https://example.invalid",
                "expected": "dependency_unavailable",
                "actual": "internal_bug",
            },
            "workspace": "unused-here",
            "active_version": "1.1.0",
        },
    }


@pytest.fixture()
def broken_dir(tmp_path: Path) -> Path:
    target = tmp_path / "broken"
    shutil.copytree(RELEASES_SRC / "1.1.0", target)
    return target


def test_backends_conform_to_protocol() -> None:
    assert isinstance(DeterministicCodingBackend(), CodingBackend)
    assert isinstance(ClaudeCodingBackend(), CodingBackend)


def test_analyze_issue_extracts_counterexample() -> None:
    analysis = DeterministicCodingBackend().analyze_issue(make_incident())
    assert analysis.fault_kind == "wrong_error_mapping"
    assert analysis.input_value.startswith("net::ERR_NAME_NOT_RESOLVED")
    assert analysis.expected == "dependency_unavailable"
    assert analysis.actual == "internal_bug"
    assert analysis.fingerprint == FINGERPRINT


def test_analyze_issue_rejects_unsupported_fault() -> None:
    incident = make_incident()
    incident["evidence"]["fingerprint_material"]["error_class"] = "cosmic_rays"
    with pytest.raises(SelfHealingError) as excinfo:
        DeterministicCodingBackend().analyze_issue(incident)
    assert excinfo.value.error_class == SelfHealingErrorClass.PATCH_DERIVATION_FAILED


def test_analyze_issue_requires_counterexample_evidence() -> None:
    incident = make_incident()
    del incident["evidence"]["selftest"]["expected"]
    with pytest.raises(SelfHealingError) as excinfo:
        DeterministicCodingBackend().analyze_issue(incident)
    assert excinfo.value.error_class == SelfHealingErrorClass.PATCH_DERIVATION_FAILED


def test_implement_change_derives_fix_from_evidence(broken_dir: Path, tmp_path: Path) -> None:
    backend = DeterministicCodingBackend()
    analysis = backend.analyze_issue(make_incident())
    patch = backend.implement_change(analysis, broken_dir, tmp_path / "out")
    patched = (patch.candidate_dir / "handler.py").read_text(encoding="utf-8")
    assert '("net::ERR_", "dependency_unavailable")' in patched
    assert "INJECTED-FAULT" not in patched
    assert patch.changed_files == ["handler.py"]
    # The generated regression test fails on the broken release...
    passed_broken, out_broken = run_regression_test(patch.regression_test_path, broken_dir)
    assert passed_broken is False
    assert "REGRESSION-FAIL" in out_broken
    # ...and passes on the candidate.
    passed_candidate, out_candidate = run_regression_test(
        patch.regression_test_path, patch.candidate_dir
    )
    assert passed_candidate is True
    assert "REGRESSION-PASS" in out_candidate


def test_implement_change_fails_when_fault_not_locatable(tmp_path: Path) -> None:
    weird = tmp_path / "weird"
    weird.mkdir()
    (weird / "handler.py").write_text("def map_error(m):\n    return 'zzz'\n", encoding="utf-8")
    backend = DeterministicCodingBackend()
    analysis = backend.analyze_issue(make_incident())
    with pytest.raises(SelfHealingError) as excinfo:
        backend.implement_change(analysis, weird, tmp_path / "out")
    assert excinfo.value.error_class == SelfHealingErrorClass.PATCH_DERIVATION_FAILED


def test_reviewer_approves_good_candidate(broken_dir: Path, tmp_path: Path) -> None:
    backend = DeterministicCodingBackend()
    analysis = backend.analyze_issue(make_incident())
    patch = backend.implement_change(analysis, broken_dir, tmp_path / "out")
    review = DeterministicReviewer().review_change(analysis, patch, broken_dir)
    assert review.approved is True
    assert {c.name for c in review.checks} == {
        "regression_fails_on_broken",
        "regression_passes_on_candidate",
        "candidate_compiles",
        "required_api_surface",
        "no_fault_markers",
    }


def test_reviewer_refuses_patch_that_does_not_fix(broken_dir: Path, tmp_path: Path) -> None:
    """A candidate identical to the broken release must never be approved."""
    backend = DeterministicCodingBackend()
    analysis = backend.analyze_issue(make_incident())
    patch = backend.implement_change(analysis, broken_dir, tmp_path / "out")
    # Sabotage: replace the candidate with the unpatched broken handler.
    shutil.copyfile(broken_dir / "handler.py", patch.candidate_dir / "handler.py")
    review = DeterministicReviewer().review_change(analysis, patch, broken_dir)
    assert review.approved is False
    failed = {c.name for c in review.checks if not c.passed}
    assert "regression_passes_on_candidate" in failed
    assert "no_fault_markers" in failed


def test_reviewer_refuses_meaningless_regression_test(
    broken_dir: Path, tmp_path: Path
) -> None:
    """A regression test that also passes on the broken release proves nothing."""
    backend = DeterministicCodingBackend()
    analysis = backend.analyze_issue(make_incident())
    patch = backend.implement_change(analysis, broken_dir, tmp_path / "out")
    patch.regression_test_path.write_text("print('ok')\n", encoding="utf-8")
    review = DeterministicReviewer().review_change(analysis, patch, broken_dir)
    assert review.approved is False
    assert any(
        c.name == "regression_fails_on_broken" and not c.passed for c in review.checks
    )


def test_reviewer_requires_full_api_surface(broken_dir: Path, tmp_path: Path) -> None:
    backend = DeterministicCodingBackend()
    analysis = backend.analyze_issue(make_incident())
    patch = backend.implement_change(analysis, broken_dir, tmp_path / "out")
    source = (patch.candidate_dir / "handler.py").read_text(encoding="utf-8")
    (patch.candidate_dir / "handler.py").write_text(
        source.replace("def run_task", "def renamed_task"), encoding="utf-8"
    )
    review = DeterministicReviewer().review_change(analysis, patch, broken_dir)
    assert review.approved is False
    assert any(c.name == "required_api_surface" and not c.passed for c in review.checks)


def test_summarize_patch_is_deterministic(broken_dir: Path, tmp_path: Path) -> None:
    backend = DeterministicCodingBackend()
    analysis = backend.analyze_issue(make_incident())
    patch = backend.implement_change(analysis, broken_dir, tmp_path / "out")
    summary = backend.summarize_patch(analysis, patch)
    assert "browser-agent-demo" in summary
    assert "wrong_error_mapping" in summary
    assert summary == backend.summarize_patch(analysis, patch)


def test_claude_backend_inert_without_configuration() -> None:
    backend = ClaudeCodingBackend()
    for call in (
        lambda: backend.analyze_issue(make_incident()),
        lambda: backend.implement_change(None, Path("."), Path(".")),
        lambda: backend.review_change(None, None, Path(".")),
        lambda: backend.summarize_patch(None, None),
    ):
        with pytest.raises(SelfHealingError) as excinfo:
            call()
        assert excinfo.value.error_class == SelfHealingErrorClass.BACKEND_NOT_CONFIGURED


def test_claude_backend_command_construction_is_pure() -> None:
    backend = ClaudeCodingBackend(cli_path="C:/tools/claude.exe", model="claude-x")
    command = backend.build_command("fix it")
    assert command[0] == "C:/tools/claude.exe"
    assert "--output-format" in command and "json" in command
    assert "--model" in command and "claude-x" in command
