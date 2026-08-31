"""Unit tests: the capability registry's two gating rules (EVOLUTION_ENGINE_SPEC §2).

Runs against SQLite (portable column types, like the other modules); the real
PostgreSQL schema is exercised by the M7 integration E2E.

Acceptance coverage: "capability registered only after gates".
"""

import contextlib
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.models import Capability, CapabilityGap, SkillVersion
from app.evolution.registry import (
    REQUIRED_MANIFEST_KEYS,
    CapabilityRegistry,
    gates_passed,
    validate_manifest,
)

TABLES = [Capability.__table__, SkillVersion.__table__, CapabilityGap.__table__]

PASSING_EVALUATION = {
    "passed": True,
    "score": {"functional_success_rate": 1.0, "regression_count": 0},
}
APPROVING_REVIEW = {"approved": True, "checks": [], "summary": "approved"}


def make_session_factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return session_scope


@pytest.fixture()
def registry() -> CapabilityRegistry:
    return CapabilityRegistry(make_session_factory())


def manifest_for(
    capability_id: str,
    version: str = "0.1.0",
    *,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    **extra,
) -> dict:
    body = {
        "id": capability_id,
        "version": version,
        "status": "experimental",
        "inputs": inputs if inputs is not None else ["text"],
        "outputs": outputs if outputs is not None else ["slug"],
        "permissions": [],
        "dependencies": [],
        "owner_scope": "normal",
        "health_metrics": ["success_rate"],
    }
    body.update(extra)
    return body


def register_fully(
    registry: CapabilityRegistry,
    capability_id: str,
    version: str = "0.1.0",
    **manifest_extra,
) -> tuple[dict, uuid.UUID]:
    created = registry.create_skill_version(capability_id, version)
    skill_version_id = uuid.UUID(created["id"])
    registry.record_evaluation(skill_version_id, PASSING_EVALUATION)
    registry.record_review(skill_version_id, APPROVING_REVIEW)
    capability = registry.register(
        capability_id, skill_version_id, manifest_for(capability_id, version, **manifest_extra)
    )
    return capability, skill_version_id


# ---------------------------------------------------------- manifest schema


def test_manifest_requires_every_section2_key() -> None:
    for key in REQUIRED_MANIFEST_KEYS:
        body = manifest_for("text.slugify")
        body.pop(key)
        with pytest.raises(EvolutionError) as excinfo:
            validate_manifest(body)
        assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR
        assert key in str(excinfo.value.details["missing"])


def test_manifest_rejects_unknown_keys_and_bad_tokens() -> None:
    with pytest.raises(EvolutionError):
        validate_manifest(manifest_for("text.slugify", surprise="x"))
    with pytest.raises(EvolutionError):
        validate_manifest(manifest_for("Text.Slugify"))
    with pytest.raises(EvolutionError):
        validate_manifest(manifest_for("text.slugify", version="1.0"))
    with pytest.raises(EvolutionError):
        validate_manifest(manifest_for("text.slugify", owner_scope="root"))
    with pytest.raises(EvolutionError):
        validate_manifest(manifest_for("text.slugify", entrypoint="__import__"))


def test_manifest_normalizes_and_keeps_engine_keys() -> None:
    normalized = validate_manifest(
        manifest_for(
            "text.slugify",
            summary="turn a title into a slug",
            skill="text_slugify",
            entrypoint="run",
            configurable_for=["text.slugify_tr"],
        )
    )
    assert normalized["id"] == "text.slugify"
    assert normalized["entrypoint"] == "run"
    assert normalized["configurable_for"] == ["text.slugify_tr"]


# ------------------------------------------------------------- registration


def test_register_promotes_and_resolves(registry: CapabilityRegistry) -> None:
    capability, skill_version_id = register_fully(registry, "text.slugify")
    assert capability["status"] == "production"
    assert capability["current_skill_version_id"] == str(skill_version_id)
    resolved = registry.resolve("text.slugify")
    assert resolved is not None
    assert resolved["skill_version"]["status"] == "registered"


def test_register_refuses_an_unevaluated_version(registry: CapabilityRegistry) -> None:
    created = registry.create_skill_version("text.slugify", "0.1.0")
    skill_version_id = uuid.UUID(created["id"])
    with pytest.raises(EvolutionError) as excinfo:
        registry.register("text.slugify", skill_version_id, manifest_for("text.slugify"))
    assert excinfo.value.error_class == EvolutionErrorClass.REGISTRATION_REFUSED
    assert registry.resolve("text.slugify") is None


def test_register_refuses_a_failed_evaluation(registry: CapabilityRegistry) -> None:
    created = registry.create_skill_version("text.slugify", "0.1.0")
    skill_version_id = uuid.UUID(created["id"])
    registry.record_evaluation(
        skill_version_id, {"passed": False, "failed_gates": ["regressions_present"]}
    )
    registry.record_review(skill_version_id, APPROVING_REVIEW)
    # A failing evaluation never reaches the 'evaluated' state...
    assert registry.get_skill_version(skill_version_id)["status"] == "tested"
    with pytest.raises(EvolutionError) as excinfo:
        registry.register("text.slugify", skill_version_id, manifest_for("text.slugify"))
    assert excinfo.value.error_class == EvolutionErrorClass.REGISTRATION_REFUSED
    assert registry.resolve("text.slugify") is None


def test_register_refuses_without_independent_review(registry: CapabilityRegistry) -> None:
    """§9: no promotion from green tests alone, and none from a reviewer alone."""
    created = registry.create_skill_version("text.slugify", "0.1.0")
    skill_version_id = uuid.UUID(created["id"])
    registry.record_evaluation(skill_version_id, PASSING_EVALUATION)
    with pytest.raises(EvolutionError) as excinfo:
        registry.register("text.slugify", skill_version_id, manifest_for("text.slugify"))
    assert "independent_review_not_approved" in excinfo.value.details["reasons"]

    ok, reasons = gates_passed({"passed": False}, {"approved": True})
    assert not ok and "evaluation_gates_not_passed" in reasons


def test_register_refuses_a_version_of_another_capability(
    registry: CapabilityRegistry,
) -> None:
    created = registry.create_skill_version("text.reverse", "0.1.0")
    skill_version_id = uuid.UUID(created["id"])
    registry.record_evaluation(skill_version_id, PASSING_EVALUATION)
    registry.record_review(skill_version_id, APPROVING_REVIEW)
    with pytest.raises(EvolutionError) as excinfo:
        registry.register("text.slugify", skill_version_id, manifest_for("text.slugify"))
    assert excinfo.value.error_class == EvolutionErrorClass.REGISTRATION_REFUSED


def test_production_status_is_unreachable_outside_register(
    registry: CapabilityRegistry,
) -> None:
    with pytest.raises(EvolutionError) as excinfo:
        registry.upsert_capability(
            "text.slugify", manifest_for("text.slugify"), status="production"
        )
    assert excinfo.value.error_class == EvolutionErrorClass.REGISTRATION_REFUSED
    registry.upsert_capability("text.slugify", manifest_for("text.slugify"))
    with pytest.raises(EvolutionError):
        registry.set_capability_status("text.slugify", "production")


# ------------------------------------------------------------------ resolve


def test_resolve_requires_production_and_registered(registry: CapabilityRegistry) -> None:
    registry.upsert_capability("text.slugify", manifest_for("text.slugify"))
    assert registry.resolve("text.slugify") is None  # proposed, no skill version

    capability, _ = register_fully(registry, "text.reverse")
    assert registry.resolve("text.reverse") is not None
    registry.set_capability_status("text.reverse", "deprecated")
    assert registry.resolve("text.reverse") is None  # registered but not production


def test_resolve_follows_the_current_version_status(registry: CapabilityRegistry) -> None:
    """A registered version that is later superseded stops being dispatchable
    unless a new one took its place."""
    _, first = register_fully(registry, "text.slugify", version="0.1.0")
    assert registry.resolve("text.slugify")["skill_version"]["id"] == str(first)
    _, second = register_fully(registry, "text.slugify", version="0.2.0")
    resolved = registry.resolve("text.slugify")
    assert resolved["skill_version"]["id"] == str(second)
    assert registry.get_skill_version(first)["status"] == "superseded"


def test_rejecting_a_registered_version_is_refused(registry: CapabilityRegistry) -> None:
    _, skill_version_id = register_fully(registry, "text.slugify")
    with pytest.raises(EvolutionError):
        registry.reject_skill_version(skill_version_id, "changed my mind")
    assert registry.resolve("text.slugify") is not None


def test_skill_versions_are_immutable_history(registry: CapabilityRegistry) -> None:
    registry.create_skill_version("text.slugify", "0.1.0")
    with pytest.raises(EvolutionError):
        registry.create_skill_version("text.slugify", "0.1.0")
    with pytest.raises(EvolutionError):
        registry.create_skill_version("text.slugify", "0.2.0", status="evaluated")


def test_resolve_never_raises_on_junk(registry: CapabilityRegistry) -> None:
    assert registry.resolve("../../etc/passwd") is None
    assert registry.resolve(None) is None  # type: ignore[arg-type]
