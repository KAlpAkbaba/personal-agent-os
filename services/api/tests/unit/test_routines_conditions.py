"""Unit tests: app.routines.conditions.

Covers all five condition kinds, the "unknown fails closed" rule, and — the task brief's
own acceptance case — that a failing condition is always reported WITH a reason, never
silently dropped.
"""

from __future__ import annotations

import pytest

from app.routines.conditions import (
    InvalidCondition,
    RoutineConditionContext,
    evaluate_condition,
    evaluate_conditions,
    validate_condition,
)


def test_validate_condition_rejects_unknown_kind() -> None:
    with pytest.raises(InvalidCondition):
        validate_condition({"kind": "owner_mood", "detail": {}})


def test_validate_condition_normalizes_missing_detail() -> None:
    normalized = validate_condition({"kind": "owner_present"})
    assert normalized == {"kind": "owner_present", "detail": {}}


# ------------------------------------------------------------------ owner_present


def test_owner_present_fails_closed_when_unknown() -> None:
    passed, reason = evaluate_condition("owner_present", {}, RoutineConditionContext())
    assert passed is False
    assert reason == "owner_presence_unknown"


def test_owner_present_passes_when_matching() -> None:
    passed, reason = evaluate_condition(
        "owner_present", {}, RoutineConditionContext(owner_present=True)
    )
    assert passed is True


def test_owner_present_fails_with_reason_when_not_present() -> None:
    passed, reason = evaluate_condition(
        "owner_present", {}, RoutineConditionContext(owner_present=False)
    )
    assert passed is False
    assert "owner_present=False" in reason


# -------------------------------------------------------------------- quiet_hours


def test_quiet_hours_blocks_by_default_when_active() -> None:
    passed, reason = evaluate_condition(
        "quiet_hours", {}, RoutineConditionContext(quiet_hours_active=True)
    )
    assert passed is False
    assert reason == "quiet_hours_active"


def test_quiet_hours_allowed_override() -> None:
    passed, _ = evaluate_condition(
        "quiet_hours",
        {"allow_during_quiet_hours": True},
        RoutineConditionContext(quiet_hours_active=True),
    )
    assert passed is True


# ------------------------------------------------------------------ display_state


def test_display_state_requires_detail() -> None:
    with pytest.raises(InvalidCondition):
        evaluate_condition("display_state", {}, RoutineConditionContext(display_state="on"))


def test_display_state_matches() -> None:
    passed, _ = evaluate_condition(
        "display_state", {"required_state": "on"}, RoutineConditionContext(display_state="on")
    )
    assert passed is True


# -------------------------------------------------------------------- active_task


def test_active_task_default_requires_no_active_task() -> None:
    passed, _ = evaluate_condition(
        "active_task", {}, RoutineConditionContext(active_task_present=False)
    )
    assert passed is True
    passed, reason = evaluate_condition(
        "active_task", {}, RoutineConditionContext(active_task_present=True)
    )
    assert passed is False


# ------------------------------------------------------------- policy_permission


def test_policy_permission_unknown_policy_fails_closed() -> None:
    passed, reason = evaluate_condition(
        "policy_permission", {"policy": "media.play"}, RoutineConditionContext()
    )
    assert passed is False
    assert reason == "policy_permission_unknown:media.play"


def test_policy_permission_granted() -> None:
    passed, _ = evaluate_condition(
        "policy_permission",
        {"policy": "media.play"},
        RoutineConditionContext(policy_permissions={"media.play": True}),
    )
    assert passed is True


# ---------------------------------------------------------------- evaluate_conditions


def test_evaluate_conditions_empty_list_passes() -> None:
    all_passed, results = evaluate_conditions([], RoutineConditionContext())
    assert all_passed is True
    assert results == []


def test_evaluate_conditions_records_every_reason_never_short_circuits() -> None:
    """A routine whose conditions fail is recorded as skipped WITH THE REASON — this test
    pins that the reason names EVERY unmet condition, not just the first one hit."""
    conditions = [
        {"kind": "owner_present", "detail": {}},
        {"kind": "quiet_hours", "detail": {}},
    ]
    context = RoutineConditionContext(owner_present=False, quiet_hours_active=True)
    all_passed, results = evaluate_conditions(conditions, context)
    assert all_passed is False
    assert len(results) == 2
    assert results[0]["passed"] is False
    assert results[0]["reason"]
    assert results[1]["passed"] is False
    assert results[1]["reason"] == "quiet_hours_active"
