"""Unit tests: the nine auditability answers (ACCEPTANCE_TESTS M7 "Auditability").

    why the capability was needed; what request/incident triggered it; what
    changed; what code/dependencies were introduced; what permissions were
    granted; what tests ran; who/what reviewed it; why it was promoted; what the
    rollback target is.
"""

import uuid

import pytest

from app.evolution.audit import AUDIT_QUESTIONS, build_audit
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from tests.unit.test_evolution_pipeline import Stack, make_stack


@pytest.fixture()
def stack(tmp_path) -> Stack:
    return make_stack(tmp_path)

COMPONENT_GAP = {
    "requested_capability": "text.word_count",
    "request_text": "Metindeki kelime sayisini dondur.",
    "required_inputs": ["text"],
    "required_outputs": ["count"],
    "spec": {
        "capability_id": "text.word_count",
        "operation": "word_count",
        "version": "0.1.0",
        "summary": "count the words in a text",
    },
}


@pytest.fixture()
def completed(stack: Stack):
    task_id = stack.resumer.record_capability_missing("slug lutfen", "text.slugify")
    gap_id = stack.open_gap(task_id=str(task_id), resume_payload={"text": "Agent OS"})
    result = stack.pipeline().run(gap_id)
    assert result.status == "registered", result.summary
    return gap_id, result


def audit_for(stack: Stack, gap_id):
    return build_audit(gap_id, stack.gaps, stack.registry)


# ------------------------------------------------------- all nine answered


def test_every_question_has_a_non_empty_answer(stack: Stack, completed) -> None:
    gap_id, _ = completed
    audit = audit_for(stack, gap_id)
    assert audit["questions"] == list(AUDIT_QUESTIONS)
    assert set(audit["answers"]) == set(AUDIT_QUESTIONS)
    for question in AUDIT_QUESTIONS:
        assert audit["answers"][question], question


def test_why_needed_names_the_request_and_the_ruled_out_options(
    stack: Stack, completed
) -> None:
    gap_id, _ = completed
    answer = audit_for(stack, gap_id)["answers"]["why_needed"]
    assert answer["requested_capability"] == "text.slugify"
    assert answer["request_text"]
    assert answer["required_outputs"] == ["slug"]
    assert answer["resolution"] == "generation"
    assert answer["cheaper_options_ruled_out"]["composition"]
    assert answer["cheaper_options_ruled_out"]["component_adaptation"]


def test_what_triggered_names_the_task_and_the_trail(stack: Stack, completed) -> None:
    gap_id, _ = completed
    answer = audit_for(stack, gap_id)["answers"]["what_triggered"]
    assert answer["trigger"] == "capability_gap"
    assert answer["gap_id"] == str(gap_id)
    assert answer["task_id"]
    assert answer["created_at"]
    assert answer["intent"] == "operational_capability"
    assert answer["depth"] == 0


def test_what_changed_reports_version_and_lifecycle(stack: Stack, completed) -> None:
    gap_id, _ = completed
    answer = audit_for(stack, gap_id)["answers"]["what_changed"]
    assert answer["capability_id"] == "text.slugify"
    assert answer["version"] == "0.1.0"
    assert answer["capability_status"] == "production"
    assert answer["resolution_path"] == "generation_from_scratch"
    assert answer["lifecycle_stage"] == "active"
    assert answer["lifecycle_history"] == [
        "sandbox",
        "validated",
        "shadow",
        "canary",
        "active",
    ]


def test_code_and_dependencies_are_named_with_digests(stack: Stack, completed) -> None:
    gap_id, _ = completed
    answer = audit_for(stack, gap_id)["answers"]["code_and_dependencies"]
    assert answer["skill"] == "text_slugify"
    assert answer["entrypoint"] == "run"
    assert answer["source_ref"]
    assert answer["manifest_digest"]
    assert answer["generated_files"]["unit"].endswith("test_text_slugify.py")
    assert answer["generated_files"]["evals"].endswith("eval_text_slugify.py")
    assert answer["dependencies"] == []  # generated from scratch, stdlib only
    assert answer["supply_chain"]["ok"] is True
    assert answer["provenance"]["generator"] == "deterministic"
    assert answer["builder_identity"]["kind"] == "generated"


def test_permissions_answer_states_the_deny_by_default_posture(
    stack: Stack, completed
) -> None:
    gap_id, _ = completed
    answer = audit_for(stack, gap_id)["answers"]["permissions_granted"]
    assert answer["granted"] == {}
    assert answer["deny_by_default"] is True
    assert answer["risk_class"] == "low"
    assert answer["side_effects"] == ["pure"]
    assert set(answer["declared_blocks"]) == {
        "network_permissions",
        "filesystem_permissions",
        "device_permissions",
        "secret_requirements",
    }
    assert answer["reviewer_approved"] is True


def test_tests_answer_carries_the_real_numbers(stack: Stack, completed) -> None:
    gap_id, _ = completed
    answer = audit_for(stack, gap_id)["answers"]["tests_that_ran"]
    assert answer["unit"]["total"] >= 8
    assert answer["unit"]["failed"] == 0
    assert answer["evals"]["total"] == answer["evals"]["passed"] >= 3
    assert answer["release_score"]["functional_success_rate"] == 1.0
    assert answer["failed_gates"] == []
    assert answer["evaluator"] == "deterministic"


def test_who_reviewed_names_the_independent_reviewer(stack: Stack, completed) -> None:
    gap_id, _ = completed
    answer = audit_for(stack, gap_id)["answers"]["who_reviewed"]
    assert answer["reviewer"] == "independent-skill-reviewer"
    assert answer["approved"] is True
    names = {check["name"] for check in answer["checks"]}
    assert {"tests_rerun_pass", "tests_detect_regression", "supply_chain_clean"} <= names
    assert answer["builder_is_not_reviewer"] is True


def test_why_promoted_shows_evidence_at_every_edge(stack: Stack, completed) -> None:
    gap_id, _ = completed
    answer = audit_for(stack, gap_id)["answers"]["why_promoted"]
    assert answer["gates"]["evaluation_passed"] is True
    assert answer["gates"]["review_approved"] is True
    assert answer["gates"]["registered_at"]
    evidence = answer["promotion_evidence"]
    assert evidence["validated"]["evaluation_passed"] is True
    assert evidence["shadow"]["mismatches"] == 0
    assert evidence["canary"]["not_worse_than_incumbent"] is True
    assert answer["evaluation_metrics"]["functional_success_rate"] == 1.0


def test_rollback_target_is_explicit_even_for_a_first_version(
    stack: Stack, completed
) -> None:
    gap_id, _ = completed
    answer = audit_for(stack, gap_id)["answers"]["rollback_target"]
    assert answer["version"] is None
    assert "capability_missing" in answer["note"]


# --------------------------------------------------- other resolution paths


def test_audit_of_a_component_adaptation_names_the_component(stack: Stack) -> None:
    gap_id = stack.open_gap(**COMPONENT_GAP)
    result = stack.pipeline().run(gap_id)
    assert result.status == "registered", result.summary

    audit = audit_for(stack, gap_id)
    answers = audit["answers"]
    assert answers["what_changed"]["resolution_path"] == "component_adaptation"
    components = answers["code_and_dependencies"]["components_adapted"]
    assert [c["name"] for c in components] == ["text_metrics_kit"]
    assert components[0]["digest"].startswith("sha256:")
    assert answers["code_and_dependencies"]["supply_chain"]["ok"] is True
    assert answers["why_needed"]["cheaper_options_ruled_out"]["component_adaptation"]


def test_audit_of_a_refused_core_change_still_answers(stack: Stack) -> None:
    gap_id = stack.open_gap(
        requested_capability="recovery.patch_supervisor",
        request_text="Supervisor'i guncelle.",
        target_component="recovery-supervisor",
        spec=None,
    )
    audit = audit_for(stack, gap_id)
    assert audit["status"] == "abandoned"
    assert audit["answers"]["why_needed"]["resolution"] == "product_change_required"
    assert audit["answers"]["what_changed"]["lifecycle_stage"] == "candidate"
    for question in AUDIT_QUESTIONS:
        assert audit["answers"][question], question


def test_audit_of_an_unknown_gap_is_not_found(stack: Stack) -> None:
    with pytest.raises(EvolutionError) as excinfo:
        audit_for(stack, uuid.uuid4())
    assert excinfo.value.error_class == EvolutionErrorClass.NOT_FOUND
