"""Unit tests: the generated-skill lifecycle state machine and its mapping onto
the FROZEN skill_versions.status values (ACCEPTANCE_TESTS M7 "Generated-skill
lifecycle").

The headline guarantee: `active` is unreachable because the agent that generated
the skill says so — every promotion edge needs independent evidence.
"""

import uuid

import pytest

from app.evolution import lifecycle as lc
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.models import SKILL_VERSION_STATUSES
from app.evolution.registry import CapabilityRegistry
from tests.unit.test_evolution_registry import (
    APPROVING_REVIEW,
    PASSING_EVALUATION,
    make_session_factory,
    manifest_for,
    promote_lifecycle,
)

SANDBOX_EVIDENCE = {"workspace": "ws", "manifest_digest": "ab" * 32}
VALIDATED_EVIDENCE = {
    "evaluation_passed": True,
    "review_approved": True,
    "supply_chain_ok": True,
}
SHADOW_EVIDENCE = {"samples": 5, "mismatches": 0}
CANARY_EVIDENCE = {"samples": 5, "not_worse_than_incumbent": True}


@pytest.fixture()
def registry() -> CapabilityRegistry:
    return CapabilityRegistry(make_session_factory())


def walk_to_canary(evaluation=None):
    state = evaluation or {}
    state = lc.advance(state, lc.STAGE_SANDBOX, SANDBOX_EVIDENCE)
    state = lc.advance(state, lc.STAGE_VALIDATED, VALIDATED_EVIDENCE)
    state = lc.advance(state, lc.STAGE_SHADOW, SHADOW_EVIDENCE)
    return lc.advance(state, lc.STAGE_CANARY, CANARY_EVIDENCE)


# ----------------------------------------------------------- the machine


def test_the_owner_stage_sequence_exists() -> None:
    assert lc.STAGES[:6] == (
        "candidate",
        "sandbox",
        "validated",
        "shadow",
        "canary",
        "active",
    )
    assert {"deprecated", "rolled_back"} <= set(lc.STAGES)


def test_every_stage_maps_to_a_legal_frozen_db_status() -> None:
    for stage in lc.STAGES:
        assert lc.STAGE_TO_DB_STATUS[stage] in SKILL_VERSION_STATUSES
        assert lc.db_status_for(stage) == lc.STAGE_TO_DB_STATUS[stage]
    # The mapping the frozen CHECK forces: shadow/canary share `evaluated`.
    assert lc.STAGE_TO_DB_STATUS["shadow"] == lc.STAGE_TO_DB_STATUS["canary"] == "evaluated"
    assert lc.STAGE_TO_DB_STATUS["active"] == "registered"
    assert lc.STAGE_TO_DB_STATUS["rolled_back"] == "superseded"


def test_a_fresh_version_starts_as_candidate() -> None:
    assert lc.current_stage({}) == lc.STAGE_CANDIDATE
    assert lc.current_stage(None) == lc.STAGE_CANDIDATE
    assert lc.current_stage({"lifecycle": {"stage": "bogus"}}) == lc.STAGE_CANDIDATE


def test_stages_advance_in_order_with_history() -> None:
    state = walk_to_canary()
    assert lc.current_stage(state) == lc.STAGE_CANARY
    assert [e["stage"] for e in state["lifecycle"]["history"]] == [
        "sandbox",
        "validated",
        "shadow",
        "canary",
    ]
    assert lc.stage_evidence(state, lc.STAGE_SHADOW) == SHADOW_EVIDENCE


@pytest.mark.parametrize(
    "stage", [lc.STAGE_VALIDATED, lc.STAGE_SHADOW, lc.STAGE_CANARY, lc.STAGE_ACTIVE]
)
def test_stages_cannot_be_skipped(stage) -> None:
    with pytest.raises(EvolutionError) as excinfo:
        lc.advance({}, stage, {"samples": 1})
    assert excinfo.value.error_class == EvolutionErrorClass.LIFECYCLE_VIOLATION


def test_each_edge_demands_its_evidence() -> None:
    with pytest.raises(EvolutionError) as excinfo:
        lc.advance({}, lc.STAGE_SANDBOX, {})
    assert set(excinfo.value.details["missing"]) == {"workspace", "manifest_digest"}

    sandboxed = lc.advance({}, lc.STAGE_SANDBOX, SANDBOX_EVIDENCE)
    with pytest.raises(EvolutionError) as excinfo:
        lc.advance(sandboxed, lc.STAGE_VALIDATED, {"evaluation_passed": True})
    assert "review_approved" in excinfo.value.details["missing"]


def test_rejection_is_reachable_from_any_pre_active_stage() -> None:
    state: dict = {}
    for stage, evidence in (
        (lc.STAGE_SANDBOX, SANDBOX_EVIDENCE),
        (lc.STAGE_VALIDATED, VALIDATED_EVIDENCE),
        (lc.STAGE_SHADOW, SHADOW_EVIDENCE),
        (lc.STAGE_CANARY, CANARY_EVIDENCE),
    ):
        rejected = lc.advance(state, lc.STAGE_REJECTED, {"reason": "x"})
        assert lc.current_stage(rejected) == lc.STAGE_REJECTED
        state = lc.advance(state, stage, evidence)
    # ...but a rejected version is terminal.
    rejected = lc.advance({}, lc.STAGE_REJECTED, {"reason": "x"})
    with pytest.raises(EvolutionError):
        lc.advance(rejected, lc.STAGE_SANDBOX, SANDBOX_EVIDENCE)


def test_active_can_only_be_deprecated_or_rolled_back() -> None:
    active = lc.advance(walk_to_canary(), lc.STAGE_ACTIVE, {"registered_by": "registry"})
    for stage in (lc.STAGE_DEPRECATED, lc.STAGE_ROLLED_BACK):
        assert lc.current_stage(lc.advance(active, stage, {})) == stage
    with pytest.raises(EvolutionError):
        lc.advance(active, lc.STAGE_SHADOW, SHADOW_EVIDENCE)
    # A rolled-back / deprecated version can be restored (rollback path).
    rolled = lc.advance(active, lc.STAGE_ROLLED_BACK, {})
    assert lc.current_stage(lc.advance(rolled, lc.STAGE_ACTIVE, {"registered_by": "rollback"}))


# --------------------------------- active needs independent evidence


def test_promotion_requires_the_whole_path() -> None:
    assert lc.require_promotion_evidence(walk_to_canary())["stage"] == lc.STAGE_CANARY

    sandboxed = lc.advance({}, lc.STAGE_SANDBOX, SANDBOX_EVIDENCE)
    with pytest.raises(EvolutionError) as excinfo:
        lc.require_promotion_evidence(sandboxed)
    assert "canary stage" in excinfo.value.message


def test_promotion_rejects_a_self_asserted_validation() -> None:
    state = lc.advance({}, lc.STAGE_SANDBOX, SANDBOX_EVIDENCE)
    state = lc.advance(
        state,
        lc.STAGE_VALIDATED,
        {"evaluation_passed": True, "review_approved": False, "supply_chain_ok": True},
    )
    state = lc.advance(state, lc.STAGE_SHADOW, SHADOW_EVIDENCE)
    state = lc.advance(state, lc.STAGE_CANARY, CANARY_EVIDENCE)
    with pytest.raises(EvolutionError) as excinfo:
        lc.require_promotion_evidence(state)
    assert "independent evaluation/review/supply-chain evidence" in excinfo.value.message


def test_promotion_rejects_a_dirty_shadow_or_a_worse_canary() -> None:
    state = lc.advance({}, lc.STAGE_SANDBOX, SANDBOX_EVIDENCE)
    state = lc.advance(state, lc.STAGE_VALIDATED, VALIDATED_EVIDENCE)
    dirty = lc.advance(state, lc.STAGE_SHADOW, {"samples": 5, "mismatches": 3})
    dirty = lc.advance(dirty, lc.STAGE_CANARY, CANARY_EVIDENCE)
    with pytest.raises(EvolutionError) as excinfo:
        lc.require_promotion_evidence(dirty)
    assert "shadow stage" in excinfo.value.message

    worse = lc.advance(state, lc.STAGE_SHADOW, SHADOW_EVIDENCE)
    worse = lc.advance(
        worse, lc.STAGE_CANARY, {"samples": 5, "not_worse_than_incumbent": False}
    )
    with pytest.raises(EvolutionError) as excinfo:
        lc.require_promotion_evidence(worse)
    assert "canary stage" in excinfo.value.message


# ------------------------------------------------ registry integration


def test_registry_keeps_db_status_in_sync_with_the_stage(registry) -> None:
    created = registry.create_skill_version("text.slugify", "0.1.0")
    version_id = uuid.UUID(created["id"])
    assert created["status"] == "draft"

    row = registry.advance_lifecycle(version_id, lc.STAGE_SANDBOX, SANDBOX_EVIDENCE)
    assert row["status"] == "built"
    assert row["evaluation"]["lifecycle"]["stage"] == lc.STAGE_SANDBOX

    registry.record_evaluation(version_id, PASSING_EVALUATION)
    registry.record_review(version_id, APPROVING_REVIEW)
    row = registry.advance_lifecycle(version_id, lc.STAGE_VALIDATED, VALIDATED_EVIDENCE)
    assert row["status"] == "evaluated"
    # record_evaluation must not clobber the lifecycle it found on the row
    assert row["evaluation"]["lifecycle"]["stage"] == lc.STAGE_VALIDATED
    assert row["evaluation"]["passed"] is True


def test_registration_refuses_a_version_that_never_reached_canary(registry) -> None:
    created = registry.create_skill_version("text.slugify", "0.1.0")
    version_id = uuid.UUID(created["id"])
    registry.advance_lifecycle(version_id, lc.STAGE_SANDBOX, SANDBOX_EVIDENCE)
    registry.record_evaluation(version_id, PASSING_EVALUATION)
    registry.record_review(version_id, APPROVING_REVIEW)
    registry.advance_lifecycle(version_id, lc.STAGE_VALIDATED, VALIDATED_EVIDENCE)

    with pytest.raises(EvolutionError) as excinfo:
        registry.register("text.slugify", version_id, manifest_for("text.slugify"))
    assert excinfo.value.error_class == EvolutionErrorClass.LIFECYCLE_VIOLATION
    assert registry.resolve("text.slugify") is None


def test_registration_records_active_and_deprecates_the_previous(registry) -> None:
    first = registry.create_skill_version("text.slugify", "0.1.0")
    first_id = uuid.UUID(first["id"])
    promote_lifecycle(registry, first_id)
    registry.register("text.slugify", first_id, manifest_for("text.slugify", "0.1.0"))
    assert lc.current_stage(registry.get_skill_version(first_id)["evaluation"]) == lc.STAGE_ACTIVE

    second = registry.create_skill_version("text.slugify", "0.2.0")
    second_id = uuid.UUID(second["id"])
    promote_lifecycle(registry, second_id)
    registry.register("text.slugify", second_id, manifest_for("text.slugify", "0.2.0"))

    old = registry.get_skill_version(first_id)
    assert old["status"] == "superseded"
    assert lc.current_stage(old["evaluation"]) == lc.STAGE_DEPRECATED
    assert registry.resolve("text.slugify")["version"] == "0.2.0"


def test_rollback_restores_the_previous_version(registry) -> None:
    first = registry.create_skill_version("text.slugify", "0.1.0")
    first_id = uuid.UUID(first["id"])
    promote_lifecycle(registry, first_id)
    registry.register("text.slugify", first_id, manifest_for("text.slugify", "0.1.0"))
    second = registry.create_skill_version("text.slugify", "0.2.0")
    second_id = uuid.UUID(second["id"])
    promote_lifecycle(registry, second_id)
    registry.register("text.slugify", second_id, manifest_for("text.slugify", "0.2.0"))

    restored = registry.rollback_to("text.slugify", "0.1.0")
    assert restored["version"] == "0.1.0"
    assert registry.resolve("text.slugify")["version"] == "0.1.0"
    assert registry.get_skill_version(first_id)["status"] == "registered"
    demoted = registry.get_skill_version(second_id)
    assert demoted["status"] == "superseded"
    assert lc.current_stage(demoted["evaluation"]) == lc.STAGE_ROLLED_BACK


def test_rollback_refuses_a_never_registered_target(registry) -> None:
    first = registry.create_skill_version("text.slugify", "0.1.0")
    promote_lifecycle(registry, uuid.UUID(first["id"]))
    registry.register("text.slugify", uuid.UUID(first["id"]), manifest_for("text.slugify"))
    registry.create_skill_version("text.slugify", "0.9.0")

    with pytest.raises(EvolutionError):
        registry.rollback_to("text.slugify", "0.9.0")
    with pytest.raises(EvolutionError) as excinfo:
        registry.rollback_to("text.slugify", "9.9.9")
    assert excinfo.value.error_class == EvolutionErrorClass.NOT_FOUND
    assert registry.resolve("text.slugify")["version"] == "0.1.0"
