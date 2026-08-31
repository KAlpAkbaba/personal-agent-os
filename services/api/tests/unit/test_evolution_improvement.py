"""Unit tests: telemetry-driven improvement of an EXISTING skill
(ACCEPTANCE_TESTS M7 "Improvement of an existing skill").

The story, end to end and deterministic:

    text.slugify v0.1.0 is registered and serving. Turkish input starts failing
    in production (`ç/ğ/ı/ö/ş/ü` are dropped instead of transliterated), which
    telemetry records. The detector turns that into a proposal, an improved
    candidate (operation `slugify_tr`) is built, benchmarked old-vs-new on the
    SAME case set, and promoted ONLY because it is objectively better. The old
    version stays rollback-capable and a rollback restores it to serving.
"""

import uuid

import pytest

from app.evolution.errors import EvolutionError
from app.evolution.improvement import (
    ImprovementDetector,
    SkillImprover,
    WeaknessThresholds,
    benchmark,
)
from app.evolution.skills import DeterministicSkillGenerator, SkillLayout, SkillSpec
from tests.unit.test_evolution_pipeline import SPEC, Stack, make_stack


@pytest.fixture()
def stack(tmp_path) -> Stack:
    return make_stack(tmp_path)

TR_CASES = [
    {"input": "Türkçe Başlık", "expected": "turkce-baslik"},
    {"input": "IŞIK ve GÖLGE", "expected": "isik-ve-golge"},
    {"input": "Çoğunlukla", "expected": "cogunlukla"},
    {"input": "Personal Agent OS", "expected": "personal-agent-os"},
    {"input": "2026 Yol Haritası", "expected": "2026-yol-haritasi"},
]

IMPROVED_SPEC = {
    "capability_id": "text.slugify",
    "operation": "slugify_tr",
    "summary": "turkish-aware url slug",
    "cases": TR_CASES,
}


def improver(stack: Stack) -> SkillImprover:
    return SkillImprover(
        stack.registry, sandbox=stack.sandbox, skills_root=stack.skills_root
    )


def register_incumbent(stack: Stack) -> dict:
    result = stack.pipeline().run(stack.open_gap())
    assert result.status == "registered", result.summary
    return stack.registry.resolve("text.slugify")


def record_turkish_failures(stack: Stack, resolved: dict, count: int = 6) -> None:
    """What production telemetry would look like after Turkish requests."""
    version_id = uuid.UUID(resolved["skill_version"]["id"])
    for index in range(count):
        stack.registry.record_telemetry(
            version_id,
            {
                "ok": index % 3 == 0,  # 2 of every 3 Turkish requests are wrong
                "latency_ms": 1.2,
                "input_kind": "turkish_title",
            },
        )


# --------------------------------------------------------------- telemetry


def test_telemetry_accumulates_on_the_registered_version(stack: Stack) -> None:
    resolved = register_incumbent(stack)
    record_turkish_failures(stack, resolved)
    telemetry = stack.registry.get_skill_version(
        uuid.UUID(resolved["skill_version"]["id"])
    )["evaluation"]["telemetry"]
    assert telemetry["invocations"] == 6
    assert telemetry["failures"] == 4
    assert telemetry["failure_rate"] == pytest.approx(4 / 6)
    assert telemetry["p95_latency_ms"] >= 0


def test_detector_stays_quiet_without_evidence(stack: Stack) -> None:
    resolved = register_incumbent(stack)
    detector = ImprovementDetector(stack.registry)
    assert detector.detect("text.slugify") is None  # no telemetry at all
    assert detector.detect("text.nonexistent") is None

    # Too few samples is not evidence either.
    version_id = uuid.UUID(resolved["skill_version"]["id"])
    stack.registry.record_telemetry(version_id, {"ok": False, "latency_ms": 1.0})
    assert detector.detect("text.slugify") is None

    # A healthy capability is not "improvable" just because it is used.
    for _ in range(10):
        stack.registry.record_telemetry(version_id, {"ok": True, "latency_ms": 1.0})
    assert detector.detect("text.slugify") is None


def test_detector_reports_a_recurring_failure_weakness(stack: Stack) -> None:
    resolved = register_incumbent(stack)
    record_turkish_failures(stack, resolved)
    proposal = ImprovementDetector(stack.registry).detect("text.slugify")
    assert proposal is not None
    assert proposal.weakness == "recurring_failures"
    assert proposal.metric == "failure_rate"
    assert proposal.observed > proposal.threshold
    assert proposal.current_version == "0.1.0"
    assert "samples" not in proposal.to_dict()["telemetry"]  # raw samples not echoed


def test_detector_reports_a_latency_weakness(stack: Stack) -> None:
    resolved = register_incumbent(stack)
    version_id = uuid.UUID(resolved["skill_version"]["id"])
    for _ in range(8):
        stack.registry.record_telemetry(version_id, {"ok": True, "latency_ms": 900.0})
    proposal = ImprovementDetector(
        stack.registry, WeaknessThresholds(max_p95_latency_ms=250.0)
    ).detect("text.slugify")
    assert proposal is not None
    assert proposal.weakness == "latency_regression"
    assert proposal.metric == "p95_latency_ms"


# --------------------------------------------------------------- benchmark


def test_benchmark_scores_both_versions_on_the_same_cases(stack, tmp_path) -> None:
    generator = DeterministicSkillGenerator()
    old = generator.generate(
        SkillSpec.parse({**SPEC, "skill_name": "old_slugify"}), tmp_path / "old"
    )
    new = generator.generate(
        SkillSpec.parse({**IMPROVED_SPEC, "version": "0.2.0", "skill_name": "new_slugify"}),
        tmp_path / "new",
    )
    comparison = benchmark(old, new)
    assert comparison.superior is True
    assert comparison.candidate.success_rate == 1.0
    assert comparison.incumbent.success_rate < 1.0  # old drops Turkish characters
    assert comparison.reasons == []


def test_benchmark_refuses_an_equal_or_worse_candidate(stack, tmp_path) -> None:
    generator = DeterministicSkillGenerator()
    old = generator.generate(
        SkillSpec.parse({**SPEC, "skill_name": "old_one"}), tmp_path / "old"
    )
    same = generator.generate(
        SkillSpec.parse({**SPEC, "version": "0.2.0", "skill_name": "same_one"}),
        tmp_path / "same",
    )
    comparison = benchmark(old, same)
    assert comparison.superior is False
    assert any("not better" in reason for reason in comparison.reasons)


# ------------------------------------------------- the full improvement


def test_an_objectively_superior_candidate_is_promoted(stack: Stack) -> None:
    resolved = register_incumbent(stack)
    record_turkish_failures(stack, resolved)
    proposal = ImprovementDetector(stack.registry).detect("text.slugify")
    assert proposal is not None

    result = improver(stack).improve(proposal, IMPROVED_SPEC)
    assert result.status == "promoted", result.summary
    assert result.candidate_version == "0.2.0"
    assert result.benchmark["superior"] is True
    assert result.benchmark["candidate"]["success_rate"] == 1.0
    assert result.rollback_version == "0.1.0"

    now = stack.registry.resolve("text.slugify")
    assert now["version"] == "0.2.0"
    assert now["manifest"]["rollback_version"] == "0.1.0"
    assert now["manifest"]["creation_reason"]["trigger"] == "improvement"
    # The improvement actually fixes the recorded weakness.
    dispatched = stack.resumer.dispatcher.dispatch("text.slugify", {"text": "Türkçe Başlık"})
    assert dispatched.output == {"slug": "turkce-baslik"}


def test_a_candidate_that_is_not_superior_is_rejected(stack: Stack) -> None:
    resolved = register_incumbent(stack)
    record_turkish_failures(stack, resolved)
    proposal = ImprovementDetector(stack.registry).detect("text.slugify")

    # Same operation, same behaviour: nothing to promote.
    result = improver(stack).improve(proposal, {**SPEC, "summary": "no better at all"})
    assert result.status == "rejected"
    assert "not objectively superior" in result.summary
    assert result.benchmark["superior"] is False

    still = stack.registry.resolve("text.slugify")
    assert still["version"] == "0.1.0"
    assert still["skill_version"]["id"] == resolved["skill_version"]["id"]
    rejected = stack.registry.get_skill_version(uuid.UUID(result.skill_version_id))
    assert rejected["status"] == "rejected"
    assert not (stack.skills_root / "text_slugify" / "0.2.0").exists()


def test_the_old_version_remains_rollback_capable(stack: Stack) -> None:
    resolved = register_incumbent(stack)
    old_source_ref = resolved["skill_version"]["source_ref"]
    record_turkish_failures(stack, resolved)
    proposal = ImprovementDetector(stack.registry).detect("text.slugify")
    promoted = improver(stack).improve(proposal, IMPROVED_SPEC)
    assert promoted.status == "promoted"

    restored = improver(stack).rollback("text.slugify", "0.1.0")
    assert restored["version"] == "0.1.0"
    serving = stack.registry.resolve("text.slugify")
    assert serving["version"] == "0.1.0"
    assert serving["skill_version"]["source_ref"] == old_source_ref
    # ...and the rolled-back version is genuinely serving dispatch again.
    dispatched = stack.resumer.dispatcher.dispatch("text.slugify", {"text": "Agent OS"})
    assert dispatched.output == {"slug": "agent-os"}
    assert dispatched.version == "0.1.0"
    # Both versions survive on disk and in history — nothing was deleted.
    assert (stack.skills_root / "text_slugify" / "0.1.0").is_dir()
    assert (stack.skills_root / "text_slugify" / "0.2.0").is_dir()
    versions = {
        row["version"]: row["status"]
        for row in stack.registry.list_skill_versions(capability_id="text.slugify")
    }
    assert versions == {"0.1.0": "registered", "0.2.0": "superseded"}


def test_improving_an_unregistered_capability_is_refused(stack: Stack) -> None:
    from app.evolution.improvement import ImprovementProposal

    proposal = ImprovementProposal(
        capability_id="text.nothing",
        current_version="0.1.0",
        skill_version_id="",
        weakness="recurring_failures",
        metric="failure_rate",
        observed=1.0,
        threshold=0.2,
    )
    with pytest.raises(EvolutionError):
        improver(stack).improve(proposal, IMPROVED_SPEC)


def test_the_improved_candidate_still_passes_every_gate(stack: Stack) -> None:
    """An improvement is not a shortcut: it goes through evaluation, the
    independent review and the lifecycle just like a new capability."""
    resolved = register_incumbent(stack)
    record_turkish_failures(stack, resolved)
    proposal = ImprovementDetector(stack.registry).detect("text.slugify")
    result = improver(stack).improve(proposal, IMPROVED_SPEC)

    version = stack.registry.get_skill_version(uuid.UUID(result.skill_version_id))
    assert version["status"] == "registered"
    assert version["evaluation"]["passed"] is True
    assert version["review"]["approved"] is True
    assert [e["stage"] for e in version["evaluation"]["lifecycle"]["history"]] == [
        "sandbox",
        "validated",
        "shadow",
        "canary",
        "active",
    ]
    published = SkillLayout(
        __import__("pathlib").Path(version["source_ref"]),
        "text_slugify",
        "text.slugify",
        "0.2.0",
    )
    assert published.missing_paths() == []
