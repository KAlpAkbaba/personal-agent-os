"""Unit tests: the generated tests and evals really RUN, in isolated
subprocesses, and produce a §9 release score.

Acceptance coverage: "tests/evals generated" (the execution half) and the §9
rule that promotion needs deterministic evidence, never a reviewer's opinion.

Also covers the INDEPENDENT reviewer (§6): it re-runs everything itself and
proves the generated tests are not vacuous via a mutation probe.
"""

import json
import sys

import pytest

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.evaluation import (
    SkillEvaluator,
    parse_eval_output,
    parse_test_output,
    run_skill_script,
    static_findings,
)
from app.evolution.review import MUTANT_SUFFIX, IndependentSkillReviewer
from app.evolution.sandbox import SandboxPolicy
from app.evolution.skills import DeterministicSkillGenerator, SkillSpec

BASE_SPEC = {
    "capability_id": "text.slugify",
    "operation": "slugify",
    "version": "0.1.0",
    "summary": "turn a title into a url slug",
}


def build(tmp_path, **overrides):
    spec = SkillSpec.parse({**BASE_SPEC, **overrides})
    return DeterministicSkillGenerator().generate(spec, tmp_path)


# ------------------------------------------------------- execution evidence


def test_generated_tests_and_evals_actually_run(tmp_path) -> None:
    layout = build(tmp_path)
    evaluator = SkillEvaluator()

    test_run, test_summary = evaluator.run_tests(layout)
    assert test_run.exit_code == 0
    assert test_summary["total"] >= 8  # cases + 3 contract checks
    assert test_summary["failed"] == 0

    eval_run, eval_summary = evaluator.run_evals(layout)
    assert eval_run.exit_code == 0
    assert eval_summary["total"] == eval_summary["passed"] >= 3
    assert set(eval_summary["metrics"]) == {"success_rate", "p95_latency_ms", "case_count"}


def test_release_score_carries_every_section9_dimension(tmp_path) -> None:
    result = SkillEvaluator().evaluate(build(tmp_path))
    assert result.passed is True
    assert result.failed_gates == []
    score = result.score.to_dict()
    assert score["functional_success_rate"] == 1.0
    assert score["regression_count"] == 0
    assert score["test_pass_rate"] == 1.0
    assert score["security_findings"] == 0
    assert score["eval_cases"] >= 3
    assert score["p95_latency_ms"] >= 0.0
    # Skill-declared health metrics are part of the score.
    assert set(score["metrics"]) == {"success_rate", "p95_latency_ms", "case_count"}
    assert result.to_dict()["evaluator"] == "deterministic"


def test_evaluation_fails_when_the_requested_cases_contradict_the_operation(
    tmp_path,
) -> None:
    """A caller-supplied acceptance case the implementation cannot satisfy is a
    failing gate — not something the pipeline can talk itself past."""
    layout = build(
        tmp_path,
        cases=[
            {"input": "Personal Agent OS", "expected": "personal-agent-os"},
            {"input": "Agent", "expected": "definitely-not-this"},
        ],
    )
    result = SkillEvaluator().evaluate(layout)
    assert result.passed is False
    assert "regressions_present" in result.failed_gates
    assert "functional_success_rate_below_threshold" in result.failed_gates
    assert result.score.functional_success_rate < 1.0


def test_evaluation_detects_a_sabotaged_entrypoint(tmp_path) -> None:
    layout = build(tmp_path)
    with layout.module_path.open("a", encoding="utf-8") as handle:
        handle.write(MUTANT_SUFFIX)
    result = SkillEvaluator().evaluate(layout)
    assert result.passed is False
    assert "generated_tests_failed" in result.failed_gates


def test_evaluation_requires_a_complete_layout(tmp_path) -> None:
    layout = build(tmp_path)
    layout.eval_path.unlink()
    with pytest.raises(EvolutionError) as excinfo:
        SkillEvaluator().evaluate(layout)
    assert excinfo.value.error_class == EvolutionErrorClass.EVALUATION_FAILED


def test_evaluation_flags_declared_metrics_the_eval_set_never_emits(tmp_path) -> None:
    layout = build(tmp_path)
    manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
    manifest["health_metrics"] = [*manifest["health_metrics"], "owner_correction_rate"]
    layout.manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    result = SkillEvaluator().evaluate(layout)
    assert result.passed is False
    assert "declared_health_metrics_missing" in result.failed_gates
    assert result.static["missing_health_metrics"] == ["owner_correction_rate"]


def test_subprocess_isolation_hides_the_api_environment(tmp_path) -> None:
    """Generated code runs with a rebuilt environment and -I, so it cannot
    import the app package or read API settings."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os, sys, json\n"
        "try:\n"
        "    import app  # noqa: F401\n"
        "    reachable = True\n"
        "except Exception:\n"
        "    reachable = False\n"
        'print(json.dumps({"app": reachable, '
        '"secretish": [k for k in os.environ if "PAGENTOS" in k or "DATABASE" in k]}))\n',
        encoding="utf-8",
    )
    run = run_skill_script(probe, tmp_path, python=sys.executable)
    assert run.exit_code == 0
    payload = parse_test_output(run.stdout)
    assert payload["app"] is False
    assert payload["secretish"] == ["PAGENTOS_SKILL_DIR"]


def test_missing_script_is_a_typed_error(tmp_path) -> None:
    with pytest.raises(EvolutionError) as excinfo:
        run_skill_script(tmp_path / "nope.py", tmp_path)
    assert excinfo.value.error_class == EvolutionErrorClass.NOT_FOUND


def test_eval_output_parsing_is_strict() -> None:
    assert parse_eval_output('EVAL-RESULT {"total": 1}')["total"] == 1
    with pytest.raises(EvolutionError):
        parse_eval_output("nothing here")
    with pytest.raises(EvolutionError):
        parse_eval_output("EVAL-RESULT not-json")
    with pytest.raises(EvolutionError):
        parse_eval_output("EVAL-RESULT [1,2]")


def test_static_scan_flags_dangerous_generated_source() -> None:
    assert static_findings("x = 1\n") == []
    assert "forbidden_import:subprocess" in static_findings("import subprocess\n")
    assert "forbidden_import:socket" in static_findings("from socket import socket\n")
    assert "forbidden_call:eval" in static_findings("y = eval('1')\n")
    assert "forbidden_call:__import__" in static_findings("__import__('os')\n")
    assert static_findings("'''eval( in a docstring'''\n") == []
    assert static_findings("def f(:\n")[0].startswith("syntax_error")


# --------------------------------------------------- independent reviewer


def test_independent_review_approves_a_sound_candidate(tmp_path) -> None:
    layout = build(tmp_path)
    review = IndependentSkillReviewer(sandbox=SandboxPolicy(tmp_path)).review(layout)
    assert review.approved is True
    names = {check.name for check in review.checks}
    assert {
        "layout_complete",
        "sandbox_confined",
        "generated_sources_parse",
        "entrypoint_present",
        "manifest_valid",
        "no_forbidden_constructs",
        "tests_rerun_pass",
        "evals_rerun_pass",
        "tests_detect_regression",
    } <= names


def test_independent_review_rejects_vacuous_tests(tmp_path) -> None:
    """If the generated tests would pass on a sabotaged entrypoint, the mutation
    probe fails and the candidate is rejected."""
    layout = build(tmp_path)
    layout.test_path.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    review = IndependentSkillReviewer().review(layout)
    assert review.approved is False
    failed = {c.name for c in review.checks if not c.passed}
    assert "tests_detect_regression" in failed


def test_independent_review_rejects_dangerous_source(tmp_path) -> None:
    layout = build(tmp_path)
    layout.module_path.write_text(
        layout.module_path.read_text(encoding="utf-8") + "\nimport subprocess\n",
        encoding="utf-8",
    )
    review = IndependentSkillReviewer().review(layout)
    assert review.approved is False
    assert "no_forbidden_constructs" in {c.name for c in review.checks if not c.passed}


def test_independent_review_rejects_a_candidate_outside_the_sandbox(tmp_path) -> None:
    layout = build(tmp_path / "outside")
    review = IndependentSkillReviewer(sandbox=SandboxPolicy(tmp_path / "inside")).review(layout)
    assert review.approved is False
    assert "sandbox_confined" in {c.name for c in review.checks if not c.passed}


def test_independent_review_rejects_an_incomplete_candidate(tmp_path) -> None:
    layout = build(tmp_path)
    layout.module_path.unlink()
    review = IndependentSkillReviewer().review(layout)
    assert review.approved is False
    failed = {c.name for c in review.checks if not c.passed}
    assert {"layout_complete", "entrypoint_present"} <= failed


def test_reviewer_does_not_hold_the_generator(tmp_path) -> None:
    """Builder/reviewer separation is structural: the reviewer has no reference
    to any generator and its evaluator is its own."""
    reviewer = IndependentSkillReviewer()
    assert not any(
        isinstance(value, DeterministicSkillGenerator) for value in vars(reviewer).values()
    )
    evaluator = SkillEvaluator()
    assert IndependentSkillReviewer(evaluator=evaluator).evaluator is evaluator
    assert IndependentSkillReviewer().evaluator is not evaluator
