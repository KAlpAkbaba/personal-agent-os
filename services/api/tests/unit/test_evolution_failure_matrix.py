"""The M7 FAILURE MATRIX — one named test per case (ACCEPTANCE_TESTS M7).

Every case asserts the same two things after the failure:

1. **production stays on last-known-good** — the previously registered version
   is still what ``registry.resolve`` returns, still dispatches, and the failed
   candidate is recorded as ``rejected`` and never published;
2. **the system stays usable** — the gap goes back to ``open`` (retryable), the
   registry keeps answering, and a fresh unrelated evolution still succeeds.

Cases: generated code does not compile; generated tests fail; security reviewer
rejects; candidate crashes at runtime; canary performs worse than the incumbent;
dependency unavailable; generated capability times out.
"""

import uuid
from pathlib import Path

import pytest

from app.evolution.errors import EvolutionErrorClass
from app.evolution.resources import ResourceBudget
from app.evolution.rollout import CanaryRunner
from app.evolution.skills import DeterministicSkillGenerator, SkillLayout, SkillSpec
from tests.unit.test_evolution_pipeline import SPEC, Stack, make_stack


@pytest.fixture()
def stack(tmp_path) -> Stack:
    return make_stack(tmp_path)

INCUMBENT_GAP = {
    "requested_capability": "text.slugify",
    "request_text": "Bir baslik verildiginde url slug uret.",
    "required_inputs": ["text"],
    "required_outputs": ["slug"],
    "spec": dict(SPEC),
}
CANDIDATE_GAP = {
    "requested_capability": "text.reverse_text",
    "request_text": "Metni ters cevir.",
    "required_inputs": ["text"],
    "required_outputs": ["reversed_text"],
    "spec": {
        "capability_id": "text.reverse_text",
        "operation": "reverse_text",
        "version": "0.1.0",
        "summary": "reverse a text",
    },
}


# ------------------------------------------------------------- generators


class BrokenSyntaxGenerator:
    """Emits a skill whose source does not compile."""

    name = "broken-syntax"

    def generate(self, spec: SkillSpec, workspace: Path) -> SkillLayout:
        layout = DeterministicSkillGenerator().generate(spec, workspace)
        layout.module_path.write_text("def run(payload:\n    return {\n", encoding="utf-8")
        return layout


class WrongAnswerGenerator:
    """Emits a skill whose entrypoint returns the wrong answer."""

    name = "wrong-answer"

    def generate(self, spec: SkillSpec, workspace: Path) -> SkillLayout:
        layout = DeterministicSkillGenerator().generate(spec, workspace)
        layout.module_path.write_text(
            'def run(payload):\n    return {"reversed_text": "always-wrong"}\n',
            encoding="utf-8",
        )
        return layout


class InsecureGenerator:
    """Emits a skill that reaches for capabilities it was never granted."""

    name = "insecure"

    def generate(self, spec: SkillSpec, workspace: Path) -> SkillLayout:
        layout = DeterministicSkillGenerator().generate(spec, workspace)
        source = layout.module_path.read_text(encoding="utf-8")
        layout.module_path.write_text(
            "import socket  # not granted by the manifest\n" + source, encoding="utf-8"
        )
        return layout


class CrashingGenerator:
    """Emits a skill whose entrypoint raises at runtime."""

    name = "crashing"

    def generate(self, spec: SkillSpec, workspace: Path) -> SkillLayout:
        layout = DeterministicSkillGenerator().generate(spec, workspace)
        layout.module_path.write_text(
            'def run(payload):\n    raise RuntimeError("candidate crashed")\n',
            encoding="utf-8",
        )
        return layout


class UnavailableDependencyGenerator:
    """Emits a skill pinning a component that is not in the local catalog."""

    name = "ghost-dependency"

    def generate(self, spec: SkillSpec, workspace: Path) -> SkillLayout:
        spec.components = [
            {
                "name": "ghost_package",
                "version": "9.9.9",
                "source": "local-component-catalog",
                "digest": "sha256:" + "cd" * 32,
            }
        ]
        return DeterministicSkillGenerator().generate(spec, workspace)


class SlowGenerator:
    """Emits a skill whose generated tests never finish."""

    name = "slow"

    def generate(self, spec: SkillSpec, workspace: Path) -> SkillLayout:
        layout = DeterministicSkillGenerator().generate(spec, workspace)
        layout.test_path.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        return layout


class AlwaysWorseCanary(CanaryRunner):
    """A canary that reports the candidate as worse than the incumbent."""

    def run(self, candidate, incumbent=None):
        report = super().run(candidate, incumbent)
        report.not_worse_than_incumbent = False
        report.detail["comparison"] = "forced-worse (failure-matrix case)"
        return report


# ------------------------------------------------------------- harness


def establish_incumbent(stack: Stack) -> dict:
    """Register a good capability that production will fall back to."""
    result = stack.pipeline().run(stack.open_gap(**INCUMBENT_GAP))
    assert result.status == "registered", result.summary
    return stack.registry.resolve("text.slugify")


def assert_production_intact(stack: Stack, incumbent: dict, bad_result) -> None:
    """The two invariants every failure-matrix case must hold."""
    # 1. production stays on last-known-good
    assert stack.registry.resolve("text.slugify") == incumbent
    assert stack.registry.resolve("text.reverse_text") is None
    dispatched = stack.resumer.dispatcher.dispatch("text.slugify", {"text": "Agent OS"})
    assert dispatched.output == {"slug": "agent-os"}
    assert dispatched.version == incumbent["version"]
    assert not (stack.skills_root / "text_reverse_text").exists()
    if bad_result.skill_version_id:
        rejected = stack.registry.get_skill_version(uuid.UUID(bad_result.skill_version_id))
        assert rejected["status"] == "rejected"
        assert rejected["rejected_reason"]
    assert "register_capability" not in [s.name for s in bad_result.stages]

    # 2. the system stays usable: a fresh, sound evolution still succeeds
    ok = stack.pipeline().run(
        stack.open_gap(
            requested_capability="text.char_checksum",
            request_text="Metnin saglama toplamini hesapla.",
            required_inputs=["text"],
            required_outputs=["checksum"],
            spec={
                "capability_id": "text.char_checksum",
                "operation": "char_checksum",
                "version": "0.1.0",
                "summary": "checksum a text",
            },
        )
    )
    assert ok.status == "registered", ok.summary
    assert stack.registry.resolve("text.char_checksum") is not None


def run_bad_candidate(stack: Stack, **pipeline_kwargs):
    gap_id = stack.open_gap(**CANDIDATE_GAP)
    result = stack.pipeline(**pipeline_kwargs).run(gap_id)
    assert stack.gaps.get(gap_id)["status"] == "open"  # retryable, not lost
    return result


# ------------------------------------------------------- the seven cases


def test_failure_generated_code_does_not_compile(stack: Stack) -> None:
    incumbent = establish_incumbent(stack)
    result = run_bad_candidate(stack, generator=BrokenSyntaxGenerator())
    assert result.status == "rejected"
    assert "release gates failed" in result.summary
    assert_production_intact(stack, incumbent, result)


def test_failure_generated_tests_fail(stack: Stack) -> None:
    incumbent = establish_incumbent(stack)
    result = run_bad_candidate(stack, generator=WrongAnswerGenerator())
    assert result.status == "rejected"
    evaluate = next(s for s in result.stages if s.name == "evaluate")
    assert evaluate.status == "failed"
    assert "regressions_present" in evaluate.detail["failed_gates"]
    assert_production_intact(stack, incumbent, result)


def test_failure_security_reviewer_rejects(stack: Stack) -> None:
    incumbent = establish_incumbent(stack)
    result = run_bad_candidate(stack, generator=InsecureGenerator())
    assert result.status == "rejected"
    # The deny-by-default permission scope is what catches it.
    evaluate = next(s for s in result.stages if s.name == "evaluate")
    assert "permission_scope_violation" in evaluate.detail["failed_gates"]
    assert_production_intact(stack, incumbent, result)


def test_failure_candidate_crashes_at_runtime(stack: Stack) -> None:
    incumbent = establish_incumbent(stack)
    result = run_bad_candidate(stack, generator=CrashingGenerator())
    assert result.status == "rejected"
    assert_production_intact(stack, incumbent, result)


def test_failure_canary_performs_worse_than_incumbent(stack: Stack) -> None:
    incumbent = establish_incumbent(stack)
    result = run_bad_candidate(stack, canary=AlwaysWorseCanary())
    assert result.status == "rejected"
    assert "canary performed worse" in result.summary
    names = [s.name for s in result.stages]
    # It got all the way to canary — and canary is what stopped it.
    assert names[-1] == "canary"
    assert "publish" not in names
    assert_production_intact(stack, incumbent, result)


def test_failure_dependency_unavailable(stack: Stack) -> None:
    incumbent = establish_incumbent(stack)
    result = run_bad_candidate(stack, generator=UnavailableDependencyGenerator())
    assert result.status == "rejected"
    supply = next(s for s in result.stages if s.name == "supply_chain")
    assert supply.status == "failed"
    assert "dependency_unavailable" in {f["rule"] for f in supply.detail["findings"]}
    assert_production_intact(stack, incumbent, result)


def test_failure_generated_capability_times_out(stack: Stack) -> None:
    incumbent = establish_incumbent(stack)
    budget = ResourceBudget(timeout_s=1.0, retry_limit=1)
    result = run_bad_candidate(stack, generator=SlowGenerator(), budget=budget)
    assert result.status == "rejected"
    evaluate = next(s for s in result.stages if s.name == "evaluate")
    assert "generated_tests_timed_out" in evaluate.detail["failed_gates"]
    # The retry limit was honoured: one attempt plus one retry, then stop.
    assert evaluate.detail["attempts"] == budget.retry_limit + 1
    assert_production_intact(stack, incumbent, result)


def test_a_failure_never_leaves_a_half_published_skill(stack: Stack) -> None:
    """Belt and braces across the matrix: the skills root only ever holds
    versions that were actually registered."""
    establish_incumbent(stack)
    for generator in (BrokenSyntaxGenerator(), WrongAnswerGenerator(), CrashingGenerator()):
        run_bad_candidate(stack, generator=generator)
    published = sorted(p.name for p in stack.skills_root.iterdir())
    assert published == ["text_slugify"]
    registered = {
        row["capability_id"]
        for row in stack.registry.list_skill_versions(status="registered")
    }
    assert registered == {"text.slugify"}


@pytest.mark.parametrize(
    "error_class",
    [
        EvolutionErrorClass.SUPPLY_CHAIN_REJECTED,
        EvolutionErrorClass.DEPENDENCY_UNAVAILABLE,
        EvolutionErrorClass.RESOURCE_BUDGET_EXCEEDED,
        EvolutionErrorClass.RECURSION_LIMIT_EXCEEDED,
        EvolutionErrorClass.PERMISSION_DENIED,
        EvolutionErrorClass.LIFECYCLE_VIOLATION,
        EvolutionErrorClass.NOT_SUPERIOR,
    ],
)
def test_every_new_failure_mode_has_a_typed_class(error_class) -> None:
    assert str(error_class)
