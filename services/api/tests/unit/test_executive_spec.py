"""``app.executive.spec``: the TaskGraph/Step data model's own bounds (spec §1).

Every bound here is a construction-time refusal, not a runtime check — pydantic itself
is the thing under test: a graph that violates a bound must never come into existence at
all, which is what makes ``app.executive.graph.validate_graph`` (tested separately) able
to assume every field it inspects is already well-typed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.executive.spec import (
    COMPENSATION_NONE,
    EVIDENCE_ARTIFACT_ID,
    MAX_BACKOFF_S,
    MAX_RETRY_ATTEMPTS,
    MAX_STEP_ID_CHARS,
    MAX_STEPS,
    MAX_TIMEOUT_S,
    RISK_MUTATE_LOCAL,
    STEP_KIND_ARTIFACTS_CREATE,
    STEP_KIND_PROFILES,
    STEP_KINDS,
    Postcondition,
    Retry,
    Step,
    TaskGraph,
)


def _step(**overrides) -> Step:
    defaults = dict(
        id="s1",
        kind=STEP_KIND_ARTIFACTS_CREATE,
        postcondition=Postcondition(evidence=EVIDENCE_ARTIFACT_ID),
        risk_class=RISK_MUTATE_LOCAL,
    )
    defaults.update(overrides)
    return Step(**defaults)


def test_every_profiled_kind_is_a_member_of_step_kinds() -> None:
    assert set(STEP_KINDS) == set(STEP_KIND_PROFILES.keys())
    assert len(STEP_KINDS) == 15, (
        "spec §1's vocabulary is exactly 15 kinds — a count "
        "changing here means the vocabulary changed and every consumer needs re-checking"
    )


def test_step_id_over_the_bound_is_refused() -> None:
    with pytest.raises(ValidationError):
        _step(id="a" * (MAX_STEP_ID_CHARS + 1))


def test_step_timeout_over_the_bound_is_refused() -> None:
    with pytest.raises(ValidationError):
        _step(timeout_s=MAX_TIMEOUT_S + 1)


def test_step_timeout_at_the_bound_is_accepted() -> None:
    step = _step(timeout_s=MAX_TIMEOUT_S)
    assert step.timeout_s == MAX_TIMEOUT_S


def test_retry_max_attempts_over_the_bound_is_refused() -> None:
    with pytest.raises(ValidationError):
        Retry(max_attempts=MAX_RETRY_ATTEMPTS + 1)


def test_retry_backoff_over_the_bound_is_refused() -> None:
    with pytest.raises(ValidationError):
        Retry(backoff_s=MAX_BACKOFF_S + 1)


def test_graph_over_max_steps_is_refused() -> None:
    steps = [_step(id=f"s{i}") for i in range(MAX_STEPS + 1)]
    with pytest.raises(ValidationError):
        TaskGraph(goal="test", steps=steps)


def test_graph_at_max_steps_is_accepted() -> None:
    steps = [_step(id=f"s{i}") for i in range(MAX_STEPS)]
    graph = TaskGraph(goal="test", steps=steps)
    assert len(graph.steps) == MAX_STEPS


def test_graph_requires_at_least_one_step() -> None:
    with pytest.raises(ValidationError):
        TaskGraph(goal="test", steps=[])


def test_extra_field_on_step_is_refused() -> None:
    """``extra="forbid"`` (module docstring) — a stray field from a model proposal is
    refused at construction, never silently dropped."""
    with pytest.raises(ValidationError):
        Step.model_validate(
            {
                "id": "s1",
                "kind": STEP_KIND_ARTIFACTS_CREATE,
                "postcondition": {"evidence": EVIDENCE_ARTIFACT_ID},
                "risk_class": RISK_MUTATE_LOCAL,
                "compensation": COMPENSATION_NONE,
                "not_a_real_field": True,
            }
        )


def test_canonical_json_is_deterministic() -> None:
    """The SAME graph produces the SAME canonical JSON every time (module docstring:
    a retried plan must not drift from what already ran)."""
    graph_a = TaskGraph(goal="test", steps=[_step()])
    graph_b = TaskGraph(goal="test", steps=[_step()])
    assert graph_a.canonical_json() == graph_b.canonical_json()


def test_step_input_value_over_bound_is_refused() -> None:
    from app.executive.spec import MAX_INPUT_VALUE_CHARS

    with pytest.raises(ValidationError):
        _step(inputs={"x": "a" * (MAX_INPUT_VALUE_CHARS + 1)})
