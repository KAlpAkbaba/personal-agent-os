"""``app.executive.graph.validate_graph`` (spec §1, §2, §8; ADR-0089 decision 1) — the
ONE gate every graph passes through, deterministic planner or model-proposed alike.
"""

from __future__ import annotations

import pytest

from app.executive.graph import GraphValidationError, validate_graph
from app.executive.planner import RuleBasedExecutivePlanner
from app.executive.spec import (
    COMPENSATION_NONE,
    EVIDENCE_ARTIFACT_ID,
    EVIDENCE_TEXT,
    PRECONDITION_STEP_DONE,
    RISK_HIGH_RISK,
    RISK_MUTATE_LOCAL,
    RISK_READ,
    STEP_KIND_ARTIFACTS_CREATE,
    STEP_KIND_SYNTHESIS,
    Postcondition,
    Precondition,
    Retry,
    Step,
    TaskGraph,
)

_RESEARCH_DIRECTIVE = (
    "Son üç gündeki AI gelişmelerini araştır, bana etkisini çıkar, Word raporu ve sunum hazırla."
)
_FOLDER_DIRECTIVE = "Bu klasördeki teklifleri karşılaştır, Excel oluştur ve yönetici özeti hazırla."
_MAIL_DIRECTIVE = "Bu mail zincirini analiz et, ilgili dosyaları bul ve cevap taslağı hazırla."


def _valid_step(**overrides) -> Step:
    defaults = dict(
        id="s1",
        kind=STEP_KIND_ARTIFACTS_CREATE,
        postcondition=Postcondition(evidence=EVIDENCE_ARTIFACT_ID),
        risk_class=RISK_MUTATE_LOCAL,
        compensation="delete_render",
    )
    defaults.update(overrides)
    return Step(**defaults)


def test_every_rule_based_shape_validates() -> None:
    """The planner's own output — every one of the three shapes — must pass this
    gate (ADR-0089 decision 1: no graph, deterministic or not, is exempt)."""
    planner = RuleBasedExecutivePlanner()
    for directive in (_RESEARCH_DIRECTIVE, _FOLDER_DIRECTIVE, _MAIL_DIRECTIVE):
        graph = planner.plan(directive)
        validate_graph(graph)  # must not raise


def test_every_rule_based_shape_with_presentation_validates() -> None:
    planner = RuleBasedExecutivePlanner()
    graph = planner.plan(_RESEARCH_DIRECTIVE + " Sunum da olsun.")
    validate_graph(graph)


def test_risk_class_mismatch_is_refused() -> None:
    """A planner claiming a risk_class the kind's own profile does not carry is a
    smuggled authority, never silently corrected (graph.py's own docstring)."""
    step = _valid_step(risk_class=RISK_READ)  # artifacts.create's real profile is mutate_local
    graph = TaskGraph(goal="test", steps=[step])
    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)
    assert "risk_class" in str(exc_info.value)


def test_high_risk_is_refused_even_if_claimed() -> None:
    step = _valid_step(risk_class=RISK_HIGH_RISK)
    graph = TaskGraph(goal="test", steps=[step])
    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)
    assert "high_risk" in str(exc_info.value)


def test_compensation_mismatch_is_refused() -> None:
    step = _valid_step(compensation=COMPENSATION_NONE)  # real profile is delete_render
    graph = TaskGraph(goal="test", steps=[step])
    with pytest.raises(GraphValidationError):
        validate_graph(graph)


def test_evidence_kind_mismatch_is_refused() -> None:
    step = _valid_step(postcondition=Postcondition(evidence=EVIDENCE_TEXT))
    graph = TaskGraph(goal="test", steps=[step])
    with pytest.raises(GraphValidationError):
        validate_graph(graph)


def test_unknown_kind_is_refused() -> None:
    step = Step.model_construct(
        id="s1",
        kind="mail.send",  # not in the closed vocabulary at all
        inputs={},
        precondition=Precondition(),
        postcondition=Postcondition(evidence=EVIDENCE_TEXT),
        timeout_s=60,
        retry=Retry(),
        risk_class=RISK_READ,
        compensation=COMPENSATION_NONE,
    )
    graph = TaskGraph.model_construct(goal="test", steps=[step])
    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)
    assert "closed vocabulary" in str(exc_info.value)


def test_forward_reference_is_a_dag_violation() -> None:
    """A step may depend only on an EARLIER step (spec §1) — s1 referencing s2, which
    comes AFTER it in the list, is refused."""
    s1 = _valid_step(id="s1", inputs={"x": "s2.artifact_id"})
    s2 = _valid_step(id="s2")
    graph = TaskGraph(goal="test", steps=[s1, s2])
    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)
    assert "DAG" in str(exc_info.value)


def test_self_reference_is_a_dag_violation() -> None:
    s1 = _valid_step(id="s1", inputs={"x": "s1.artifact_id"})
    graph = TaskGraph(goal="test", steps=[s1])
    with pytest.raises(GraphValidationError):
        validate_graph(graph)


def test_backward_reference_is_accepted() -> None:
    s1 = _valid_step(id="s1")
    s2 = _valid_step(id="s2", inputs={"x": "s1.artifact_id"})
    graph = TaskGraph(goal="test", steps=[s1, s2])
    validate_graph(graph)  # must not raise


def test_a_literal_input_value_that_looks_like_a_reference_but_names_no_step_is_a_literal() -> None:
    """ "nonexistent.thing" parses as reference-shaped but names no real step id in
    this graph — a literal, not a DAG violation (spec §1: "<step id>.<output name>" |
    literal; graph.py's own docstring on this exact ambiguity)."""
    s1 = _valid_step(id="s1", inputs={"x": "nonexistent.thing"})
    graph = TaskGraph(goal="test", steps=[s1])
    validate_graph(graph)  # must not raise


def test_step_done_precondition_naming_a_later_step_is_refused() -> None:
    s1 = _valid_step(id="s1", precondition=Precondition(check=PRECONDITION_STEP_DONE, arg="s2"))
    s2 = _valid_step(id="s2")
    graph = TaskGraph(goal="test", steps=[s1, s2])
    with pytest.raises(GraphValidationError):
        validate_graph(graph)


def test_retry_only_on_outside_the_closed_set_is_refused() -> None:
    step = _valid_step(retry=Retry(max_attempts=2, backoff_s=5.0, only_on=["validation_error"]))
    graph = TaskGraph(goal="test", steps=[step])
    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)
    assert "validation_error" in str(exc_info.value)


def test_retry_only_on_dependency_unavailable_and_timeout_is_accepted() -> None:
    step = _valid_step(
        retry=Retry(max_attempts=3, backoff_s=30.0, only_on=["dependency_unavailable", "timeout"])
    )
    graph = TaskGraph(goal="test", steps=[step])
    validate_graph(graph)  # must not raise


def test_duplicate_step_ids_are_refused() -> None:
    s1 = _valid_step(id="s1")
    s1_again = _valid_step(id="s1")
    graph = TaskGraph.model_construct(goal="test", steps=[s1, s1_again])
    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)
    assert "used more than once" in str(exc_info.value)


def test_multiple_violations_are_all_reported_together() -> None:
    """A planner bug usually breaks more than one rule at once (module docstring) —
    every violation is collected, not just the first."""
    step = _valid_step(risk_class=RISK_HIGH_RISK, compensation=COMPENSATION_NONE)
    graph = TaskGraph(goal="test", steps=[step])
    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)
    assert len(exc_info.value.reasons) >= 2


def test_synthesis_kind_has_no_compensation_or_external_risk() -> None:
    step = Step(
        id="s1",
        kind=STEP_KIND_SYNTHESIS,
        postcondition=Postcondition(evidence=EVIDENCE_TEXT),
        risk_class=RISK_READ,
        compensation=COMPENSATION_NONE,
    )
    graph = TaskGraph(goal="test", steps=[step])
    validate_graph(graph)  # must not raise
