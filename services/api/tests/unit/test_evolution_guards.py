"""ADR-0025 boundary guards, enforced by tests rather than by convention.

1. MEMORY (ADR-0023): the evolution engine may READ memory but must never reach
   the owner-actor memory mutation surface. Mirrors
   tests/unit/test_selfhealing_memory_guard.py.
2. RECOVERY/CORE (constitution §6, EVOLUTION_ENGINE_SPEC §12): the sandbox root
   can never overlap the recovery supervisor or the product's own source, and a
   gap whose resolution would require a core/recovery change is refused with
   ``product_change_required`` — never auto-generated.
"""

from pathlib import Path

import pytest

from app.config import Settings
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.gaps import (
    PRODUCT_CHANGE_KINDS,
    PROTECTED_CAPABILITY_PREFIXES,
    PROTECTED_COMPONENTS,
    STEP_NEW_SKILL,
    STEP_PRODUCT_CHANGE,
    CapabilityRequest,
    GapDetector,
    GapService,
    implies_product_change,
)
from app.evolution.registry import CapabilityRegistry
from app.evolution.runtime import EvolutionRuntime
from app.evolution.sandbox import REPO_ROOT, SandboxPolicy, protected_trees
from tests.unit.test_evolution_registry import make_session_factory

EVOLUTION_DIR = Path(__file__).resolve().parents[2] / "app" / "evolution"

# Owner-authority memory mutation surface (see app.memory.routes/service).
FORBIDDEN_SUBSTRINGS = (
    "Actor.OWNER",
    "/remember",
    ".remember(",
    ".forget(",
    ".supersede_memory(",
    "MemoryLinks",
    "explicit=True",
)
FORBIDDEN_IMPORTS = ("from app.memory", "import app.memory")


# ------------------------------------------------------------ 1. memory guard


def test_evolution_sources_exist() -> None:
    sources = sorted(p.name for p in EVOLUTION_DIR.glob("*.py"))
    assert {
        "errors.py",
        "evaluation.py",
        "gaps.py",
        "models.py",
        "pipeline.py",
        "registry.py",
        "review.py",
        "routes.py",
        "runtime.py",
        "sandbox.py",
        "skills.py",
        "task_resumption.py",
        "tokens.py",
    } <= set(sources)


def test_evolution_never_touches_owner_memory_mutation() -> None:
    offenders: list[str] = []
    for source_file in EVOLUTION_DIR.glob("*.py"):
        text = source_file.read_text(encoding="utf-8")
        for needle in FORBIDDEN_SUBSTRINGS + FORBIDDEN_IMPORTS:
            if needle in text:
                offenders.append(f"{source_file.name}: {needle}")
    assert offenders == [], (
        "the evolution engine must never use the owner-actor memory mutation "
        f"surface (ADR-0023); found: {offenders}"
    )


# ------------------------------------------------- 2a. sandbox cannot target core


def test_sandbox_refuses_protected_roots() -> None:
    for protected in protected_trees():
        with pytest.raises(EvolutionError) as excinfo:
            SandboxPolicy(protected)
        assert excinfo.value.error_class == EvolutionErrorClass.SANDBOX_VIOLATION


def test_sandbox_refuses_the_recovery_supervisor_specifically() -> None:
    supervisor = REPO_ROOT / "services" / "recovery-supervisor"
    assert supervisor in protected_trees()
    with pytest.raises(EvolutionError):
        SandboxPolicy(supervisor)
    with pytest.raises(EvolutionError):
        SandboxPolicy(supervisor / "recovery_supervisor")
    # A root that CONTAINS a protected tree is refused too.
    with pytest.raises(EvolutionError):
        SandboxPolicy(REPO_ROOT / "services")
    with pytest.raises(EvolutionError):
        SandboxPolicy(REPO_ROOT)


def test_sandbox_refuses_the_products_own_source() -> None:
    for target in (
        REPO_ROOT / "services" / "api" / "app",
        REPO_ROOT / "services" / "api" / "app" / "evolution",
        REPO_ROOT / "services" / "api" / "alembic",
        REPO_ROOT / "services" / "api" / "tests",
        REPO_ROOT / "state",
        REPO_ROOT / "docs",
        REPO_ROOT / "config",
    ):
        with pytest.raises(EvolutionError):
            SandboxPolicy(target)


def test_sandbox_confines_paths(tmp_path) -> None:
    policy = SandboxPolicy(tmp_path / "work")
    inside = policy.ensure_within(tmp_path / "work" / "candidate")
    assert inside.is_relative_to(policy.root)
    for outside in (
        tmp_path / "elsewhere",
        tmp_path / "work" / ".." / "escape",
        REPO_ROOT / "services" / "recovery-supervisor",
    ):
        with pytest.raises(EvolutionError) as excinfo:
            policy.ensure_within(outside)
        assert excinfo.value.error_class == EvolutionErrorClass.SANDBOX_VIOLATION


def test_default_runtime_roots_are_outside_every_protected_tree(monkeypatch) -> None:
    for name in (
        "PAGENTOS_EVOLUTION_SKILLS_ROOT",
        "PAGENTOS_EVOLUTION_WORK_ROOT",
        "PAGENTOS_EVOLUTION_GENERATOR",
    ):
        monkeypatch.delenv(name, raising=False)
    runtime = EvolutionRuntime(Settings(_env_file=None))
    assert runtime.skills_root == (REPO_ROOT / "skills" / "generated").resolve()
    assert runtime.work_root.is_relative_to(runtime.skills_root)
    for protected in protected_trees():
        assert not runtime.sandbox.root.is_relative_to(protected)
        assert not protected.is_relative_to(runtime.sandbox.root)
    health = runtime.health_check()
    assert health["status"] == "ok"
    assert health["skill_generator"] == "deterministic"
    assert health["sandbox_isolated_from_core"] is True


def test_a_protected_root_from_configuration_fails_fast(monkeypatch) -> None:
    monkeypatch.setenv(
        "PAGENTOS_EVOLUTION_SKILLS_ROOT",
        str(REPO_ROOT / "services" / "recovery-supervisor" / "generated"),
    )
    with pytest.raises(EvolutionError) as excinfo:
        EvolutionRuntime(Settings(_env_file=None))
    assert excinfo.value.error_class == EvolutionErrorClass.SANDBOX_VIOLATION


# ---------------------------------------- 2b. core/recovery gaps are refused


@pytest.fixture()
def detector() -> tuple[GapDetector, GapService]:
    factory = make_session_factory()
    return GapDetector(CapabilityRegistry(factory)), GapService(factory)


def core_request(**overrides) -> CapabilityRequest:
    body = {
        "requested_capability": "text.slugify",
        "request_text": "Bir sey yap.",
        "required_inputs": ["text"],
        "required_outputs": ["slug"],
    }
    body.update(overrides)
    return CapabilityRequest.parse(body)


@pytest.mark.parametrize("component", sorted(PROTECTED_COMPONENTS))
def test_a_protected_component_target_is_refused(detector, component) -> None:
    gap_detector, gaps = detector
    request = core_request(target_component=component)
    decision = gap_detector.detect(request)
    assert decision.resolution == "product_change_required"
    steps = {entry["step"]: entry for entry in decision.trail}
    assert steps[STEP_NEW_SKILL]["outcome"] == "not_applicable"
    assert steps[STEP_PRODUCT_CHANGE]["outcome"] == "refused"
    assert decision.refusal_reason
    # ...and the recorded gap is abandoned, not queued for generation.
    gap = gaps.record(request, decision)
    assert gap["status"] == "abandoned"
    assert gap["resolution"] == "product_change_required"


@pytest.mark.parametrize("prefix", PROTECTED_CAPABILITY_PREFIXES)
def test_a_protected_capability_namespace_is_refused(detector, prefix) -> None:
    gap_detector, _ = detector
    capability_id = f"{prefix}{'x' if prefix.endswith('.') else '_x'}"
    decision = gap_detector.detect(core_request(requested_capability=capability_id))
    assert decision.resolution == "product_change_required"


@pytest.mark.parametrize("kind", sorted(PRODUCT_CHANGE_KINDS))
def test_a_product_change_kind_is_refused(detector, kind) -> None:
    gap_detector, _ = detector
    decision = gap_detector.detect(core_request(change_kind=kind))
    assert decision.resolution == "product_change_required"


def test_the_core_detector_is_fail_safe() -> None:
    """Every signal can only push towards refusal — including a mere mention of
    a protected component in the request text."""
    refused, evidence = implies_product_change(
        core_request(request_text="patch the recovery-supervisor rollback logic")
    )
    assert refused is True
    assert any("request_text_mentions" in reason for reason in evidence["reasons"])

    allowed, evidence = implies_product_change(core_request())
    assert allowed is False
    assert evidence["reasons"] == []


def test_an_ordinary_skill_request_is_not_refused(detector) -> None:
    gap_detector, _ = detector
    decision = gap_detector.detect(core_request())
    assert decision.resolution == "generation"
    assert decision.refusal_reason is None
