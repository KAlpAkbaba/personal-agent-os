"""Condition vocabulary and evaluation (M18 task brief §Conditions).

Five closed kinds: owner present, quiet hours, display state, an active task, policy
permission. Conditions are evaluated at FIRE time, from a ``RoutineConditionContext`` the
caller of ``app.routines.service.evaluate_due`` supplies — this package imports nothing from
``app.devices``, ``app.voice`` or any presence/policy subsystem to build one, on purpose:
those subsystems are owned by other tracks (some not built yet), and a routine's condition
logic must stay testable from plain data regardless of what eventually supplies it.

A context field left at its default (``None`` / empty) means "unknown", and every evaluator
here fails CLOSED on unknown — a condition that cannot be checked is not satisfied, never
silently assumed true. This mirrors ``app.identity.dependencies``' "fail closed... refuses"
rule: the alternative (treating "unknown" as "pass") would let a routine act on stale or
inconsistent context read as an okay from a caller that just hasn't supplied real data yet.

Every evaluation returns a reason string alongside the bool — a failing condition is
recorded as skipped WITH THE REASON (task brief), never silently dropped; that reason is
what a caller writes into ``RoutineFiring.skip_reason`` / the ledger's ``routine.skipped``
event and, later, what "neden çalmadı?" answers from.
"""

from __future__ import annotations

import dataclasses
from typing import Any

CONDITION_KIND_OWNER_PRESENT = "owner_present"
CONDITION_KIND_QUIET_HOURS = "quiet_hours"
CONDITION_KIND_DISPLAY_STATE = "display_state"
CONDITION_KIND_ACTIVE_TASK = "active_task"
CONDITION_KIND_POLICY_PERMISSION = "policy_permission"
#: "is a greeting warranted right now?", answered by the Presence Engine's own
#: four-gate policy rather than reimplemented here. A morning briefing is then a
#: routine like any other: trigger owner.awake, condition greeting_allowed, action
#: voice_briefing - and the "is this actually a morning" reasoning stays in one place.
CONDITION_KIND_GREETING_ALLOWED = "greeting_allowed"

CONDITION_KINDS: tuple[str, ...] = (
    CONDITION_KIND_OWNER_PRESENT,
    CONDITION_KIND_QUIET_HOURS,
    CONDITION_KIND_DISPLAY_STATE,
    CONDITION_KIND_ACTIVE_TASK,
    CONDITION_KIND_POLICY_PERMISSION,
    CONDITION_KIND_GREETING_ALLOWED,
)


class InvalidCondition(ValueError):
    """A condition descriptor names a kind outside the closed vocabulary."""


@dataclasses.dataclass(frozen=True, slots=True)
class RoutineConditionContext:
    """What the caller of ``evaluate_due`` currently knows. Every field defaults to
    "unknown" — a routine created before any real subsystem supplies this context simply
    fails every condition it declares, which is the safe direction (module docstring)."""

    owner_present: bool | None = None
    #: Where ``owner_present`` came from - see app.routines.presence_link. A condition that
    #: passed on a caller's assertion about a room the caller cannot see must be
    #: distinguishable, afterwards, from one that passed on real perception.
    owner_present_source: str = "unknown"
    quiet_hours_active: bool | None = None
    display_state: str | None = None
    #: whether the owner currently has some other active task/goal running.
    active_task_present: bool | None = None
    policy_permissions: dict[str, bool] = dataclasses.field(default_factory=dict)
    #: Whether the Presence Engine's greeting policy currently says to greet, and the
    #: gate it named. None means it was not consulted - which fails closed, because a
    #: greeting sent on an unconsulted policy is exactly the 03:00 case the policy exists
    #: to prevent.
    greeting_allowed: bool | None = None
    greeting_reason: str = "not_evaluated"
    #: The full app.presence.greeting.GreetingDecision behind `greeting_allowed`/
    #: `greeting_reason` above, carried opaquely (ADR-0060) so app.routines.service can
    #: start the greeting cooldown AFTER a briefing actually narrates, via
    #: app.routines.presence_link.record_greeting_delivered, without this module — or any
    #: evaluator below — ever importing app.presence or inspecting the decision's shape.
    #: Typed ``Any`` on purpose: this package does not depend on app.presence's types.
    greeting_decision: Any = None


def validate_condition(raw: dict[str, Any]) -> dict[str, Any]:
    kind = raw.get("kind")
    if kind not in CONDITION_KINDS:
        raise InvalidCondition(
            f"unknown condition kind: {kind!r}; must be one of {CONDITION_KINDS}"
        )
    detail = raw.get("detail") or {}
    if not isinstance(detail, dict):
        raise InvalidCondition(f"condition detail must be an object, got {type(detail).__name__}")
    return {"kind": kind, "detail": dict(detail)}


# ------------------------------------------------------------------ evaluators


def _eval_owner_present(
    detail: dict[str, Any], context: RoutineConditionContext
) -> tuple[bool, str]:
    required = bool(detail.get("required", True))
    source = context.owner_present_source or "unknown"
    if context.owner_present is None:
        # "We do not know" is not "the owner is away". The source says WHY it is unknown -
        # never observed, or observed and aged out - which is the difference between a
        # camera that was never enabled and one that stopped reporting.
        return False, f"owner_presence_unknown ({source})"
    if context.owner_present == required:
        return True, f"owner_present_matched ({source})"
    return False, f"owner_present={context.owner_present}, required={required} ({source})"


def _eval_quiet_hours(detail: dict[str, Any], context: RoutineConditionContext) -> tuple[bool, str]:
    allow_during_quiet_hours = bool(detail.get("allow_during_quiet_hours", False))
    if context.quiet_hours_active is None:
        return False, "quiet_hours_unknown"
    if context.quiet_hours_active and not allow_during_quiet_hours:
        return False, "quiet_hours_active"
    return True, "quiet_hours_clear"


def _eval_display_state(
    detail: dict[str, Any], context: RoutineConditionContext
) -> tuple[bool, str]:
    required_state = detail.get("required_state")
    if not required_state:
        raise InvalidCondition("display_state condition requires detail.required_state")
    if context.display_state is None:
        return False, "display_state_unknown"
    if context.display_state == required_state:
        return True, "display_state_matched"
    return False, f"display_state={context.display_state}, required={required_state}"


def _eval_active_task(detail: dict[str, Any], context: RoutineConditionContext) -> tuple[bool, str]:
    #: default False: by default a routine should only fire when the owner is NOT already
    #: in the middle of something, so it does not interrupt.
    must_be_active = bool(detail.get("must_be_active", False))
    if context.active_task_present is None:
        return False, "active_task_unknown"
    if context.active_task_present == must_be_active:
        return True, "active_task_matched"
    return False, f"active_task_present={context.active_task_present}, required={must_be_active}"


def _eval_policy_permission(
    detail: dict[str, Any], context: RoutineConditionContext
) -> tuple[bool, str]:
    policy = detail.get("policy")
    if not policy:
        raise InvalidCondition("policy_permission condition requires detail.policy")
    if context.policy_permissions is None or policy not in context.policy_permissions:
        return False, f"policy_permission_unknown:{policy}"
    if context.policy_permissions[policy]:
        return True, f"policy_permission_granted:{policy}"
    return False, f"policy_permission_denied:{policy}"


def _eval_greeting_allowed(
    detail: dict[str, Any], context: RoutineConditionContext
) -> tuple[bool, str]:
    """Defer to app.presence.greeting via the context, and carry its reason through.

    The reason is the point: "why not now?" is answerable straight off the firing record
    (cooldown_active, implausible_time, wake_not_sustained, ...) without re-deriving
    anything, and the owner can be told the same word.
    """
    required = bool(detail.get("required", True))
    if context.greeting_allowed is None:
        return False, f"greeting_not_evaluated ({context.greeting_reason})"
    if context.greeting_allowed == required:
        return True, f"greeting_allowed={context.greeting_allowed} ({context.greeting_reason})"
    return False, (
        f"greeting_allowed={context.greeting_allowed}, required={required} "
        f"({context.greeting_reason})"
    )


_EVALUATORS = {
    CONDITION_KIND_OWNER_PRESENT: _eval_owner_present,
    CONDITION_KIND_QUIET_HOURS: _eval_quiet_hours,
    CONDITION_KIND_DISPLAY_STATE: _eval_display_state,
    CONDITION_KIND_ACTIVE_TASK: _eval_active_task,
    CONDITION_KIND_POLICY_PERMISSION: _eval_policy_permission,
    CONDITION_KIND_GREETING_ALLOWED: _eval_greeting_allowed,
}


def evaluate_condition(
    kind: str, detail: dict[str, Any], context: RoutineConditionContext
) -> tuple[bool, str]:
    evaluator = _EVALUATORS.get(kind)
    if evaluator is None:
        raise InvalidCondition(f"unknown condition kind: {kind!r}")
    return evaluator(detail, context)


def evaluate_conditions(
    conditions: list[dict[str, Any]], context: RoutineConditionContext
) -> tuple[bool, list[dict[str, Any]]]:
    """Evaluates every condition (never short-circuits) so a skip reason always names
    EVERY unmet condition, not just the first. A routine with no conditions always passes —
    "no conditions" is a legitimate, explicit choice, not a vacuous failure."""
    results: list[dict[str, Any]] = []
    for raw in conditions:
        kind = raw.get("kind")
        detail = raw.get("detail") or {}
        passed, reason = evaluate_condition(kind, detail, context)
        results.append({"kind": kind, "passed": passed, "reason": reason})
    all_passed = all(r["passed"] for r in results)
    return all_passed, results


__all__ = [
    "CONDITION_KINDS",
    "CONDITION_KIND_ACTIVE_TASK",
    "CONDITION_KIND_DISPLAY_STATE",
    "CONDITION_KIND_OWNER_PRESENT",
    "CONDITION_KIND_GREETING_ALLOWED",
    "CONDITION_KIND_POLICY_PERMISSION",
    "CONDITION_KIND_QUIET_HOURS",
    "InvalidCondition",
    "RoutineConditionContext",
    "evaluate_condition",
    "evaluate_conditions",
    "validate_condition",
]
