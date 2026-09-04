"""The Cognitive Core loop (overnight plan Phase 4): explicit roles with
narrow interfaces, not one giant prompt.

    understand goal -> inspect world state -> identify missing information
    -> plan -> execute -> observe -> evaluate -> replan (bounded)
    -> complete or escalate -> learn

Each role is a ``Protocol`` so a future model-backed implementation (an LLM
planner, say) can be substituted without touching ``Orchestrator`` — the seam
CLAUDE.md's self-development rule requires ("Gap/incident -> issue/spec ->
isolated branch -> code -> tests -> review -> ... -> promote or rollback")
starts here: today every implementation in this module is a deterministic,
fully-tested reference; NO network call and NO LLM call happens anywhere in
this package.

``Orchestrator`` is the only thing that touches ``app.goals.service`` (the
durable state), ``app.ledger.service`` (the durable narrative) and
``app.uistate`` (the ephemeral presentation) — the three kinds of "what
happened" this repo distinguishes.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.goals import service as goals_service
from app.goals.models import GOAL_STATUS_ACTIVE, GOAL_STATUS_WAITING_OWNER, Goal
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_GOAL_ERROR,
    EVENT_TYPE_GOAL_ESCALATED,
    EVENT_TYPE_GOAL_STEP_EXECUTED,
    SUBSYSTEM_GOAL,
)
from app.logging import get_logger
from app.uistate import UiState, UiStatePublisher, get_publisher

logger = get_logger("app.goals.cognitive")

#: bounded replanning (task instructions): a step that keeps failing escalates
#: to the owner instead of looping forever. Named constant, not a magic number.
MAX_REPLANS = 3


def utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------- types


class Verdict(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    RETRY = "retry"


@dataclasses.dataclass(frozen=True, slots=True)
class PlanStep:
    step_id: str
    description: str
    capability: str
    arguments: dict[str, Any] = dataclasses.field(default_factory=dict)
    success_criterion_id: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Plan:
    goal_id: str
    steps: tuple[PlanStep, ...]
    rationale: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class Observation:
    step_id: str
    ok: bool
    result: dict[str, Any] = dataclasses.field(default_factory=dict)
    error: str | None = None
    evidence_refs: list[dict[str, Any]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True, slots=True)
class Capability:
    name: str
    #: takes the step's arguments, returns a result dict. May include
    #: ``"ok": False`` / ``"error": ...`` / ``"evidence_refs": [...]`` — a
    #: RAISED exception means something unexpected broke (a bug, a dependency
    #: down), not a routine "this attempt didn't work" (module docstring).
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    description: str = ""


@dataclasses.dataclass(slots=True)
class LoopResult:
    goal_id: uuid.UUID
    outcome: str  # "completed" | "escalated" | "failed"
    iterations: int
    observations: list[Observation]


# ---------------------------------------------------------------- protocols


class Planner(Protocol):
    def plan(self, goal: Goal, world_state: Mapping[str, Any]) -> Plan: ...


class Actor(Protocol):
    def act(self, step: PlanStep) -> Observation: ...


class Critic(Protocol):
    def evaluate(self, step: PlanStep, observation: Observation) -> Verdict: ...


class MemoryManager(Protocol):
    def recall(self, goal: Goal) -> dict[str, Any]: ...

    def record(self, goal: Goal, observation: Observation, verdict: Verdict) -> None: ...


class CapabilityRouter(Protocol):
    def route(self, name: str) -> Capability | None: ...

    def names(self) -> tuple[str, ...]: ...


# ------------------------------------------------------ deterministic reference impls


class AllowListCapabilityRouter:
    """Only explicitly registered capabilities are routable — an unknown name
    returns ``None`` rather than raising, so the Actor reports
    ``capability_not_allowed`` as a normal (unsatisfied) observation instead
    of crashing the loop. This is the allow-list: nothing reaches
    ``Capability.handler`` that was not registered here."""

    def __init__(self, capabilities: Iterable[Capability] = ()) -> None:
        self._capabilities: dict[str, Capability] = {c.name: c for c in capabilities}

    def register(self, capability: Capability) -> None:
        if capability.name in self._capabilities:
            raise ValueError(f"capability already registered: {capability.name}")
        self._capabilities[capability.name] = capability

    def route(self, name: str) -> Capability | None:
        return self._capabilities.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._capabilities))


class RuleBasedPlanner:
    """Decomposes a goal into one step per UNSATISFIED success criterion — no
    model call, just a direct read of ``goal.success_criteria`` (task
    instructions: "decomposes a goal into steps from its success criteria").
    A criterion's ``check_kind`` names the capability that can move it
    forward (``criterion.<check_kind>``); a real deployment registers a
    capability under that name per check_kind it wants the loop to act on."""

    def plan(self, goal: Goal, world_state: Mapping[str, Any]) -> Plan:
        steps: list[PlanStep] = []
        for raw in goal.success_criteria or []:
            criterion = dict(raw)
            if criterion.get("satisfied"):
                continue
            check_kind = criterion.get("check_kind", "manual_evidence")
            criterion_id = str(criterion.get("id"))
            steps.append(
                PlanStep(
                    step_id=criterion_id,
                    description=str(criterion.get("statement", "")),
                    capability=f"criterion.{check_kind}",
                    arguments={
                        "goal_id": str(goal.goal_id),
                        "criterion_id": criterion_id,
                        **dict(criterion.get("detail") or {}),
                    },
                    success_criterion_id=criterion_id,
                )
            )
        return Plan(
            goal_id=str(goal.goal_id),
            steps=tuple(steps),
            rationale="decomposed from unmet success criteria",
        )


class RegistryActor:
    """Executes one step by routing its capability through a
    ``CapabilityRouter``. A capability result dict becomes an ``Observation``;
    a capability that RAISES propagates — the Orchestrator treats that as an
    unexpected error, distinct from a routine unsatisfied result."""

    def __init__(self, router: CapabilityRouter) -> None:
        self.router = router

    def act(self, step: PlanStep) -> Observation:
        capability = self.router.route(step.capability)
        if capability is None:
            return Observation(step_id=step.step_id, ok=False, error="capability_not_allowed")
        raw = capability.handler(dict(step.arguments))
        if not isinstance(raw, dict):
            raise TypeError(f"capability {step.capability!r} must return a dict, got {type(raw)}")
        ok = bool(raw.get("ok", True))
        return Observation(
            step_id=step.step_id,
            ok=ok,
            result=raw,
            error=None if ok else str(raw.get("error") or "unsatisfied"),
            evidence_refs=list(raw.get("evidence_refs") or []),
        )


class CriteriaCritic:
    """Verdict from the observation alone: success is success; a refused
    capability is a hard boundary (not worth retrying — the allow-list is a
    policy decision, not a transient fault); anything else is worth one of
    the bounded replans."""

    def evaluate(self, step: PlanStep, observation: Observation) -> Verdict:
        if observation.ok:
            return Verdict.SATISFIED
        if observation.error == "capability_not_allowed":
            return Verdict.UNSATISFIED
        return Verdict.RETRY


class NullMemoryManager:
    """Reference MemoryManager: recalls nothing, records nothing. This
    package must not import ``app.memory`` (owned by another track); a real
    memory-backed implementation plugs in behind this same Protocol."""

    def recall(self, goal: Goal) -> dict[str, Any]:
        return {}

    def record(self, goal: Goal, observation: Observation, verdict: Verdict) -> None:
        return None


class InMemoryMemoryManager:
    """Reference MemoryManager that actually remembers, in-process: the last
    ``limit`` (observation, verdict) pairs per goal_id. Useful for tests and
    for a planner that wants "what did we just try" without a DB round trip."""

    def __init__(self, *, limit: int = 20) -> None:
        self._limit = limit
        self._by_goal: dict[str, list[tuple[Observation, Verdict]]] = {}

    def recall(self, goal: Goal) -> dict[str, Any]:
        history = self._by_goal.get(str(goal.goal_id), [])
        return {"recent": [(obs.step_id, obs.ok, verdict.value) for obs, verdict in history]}

    def record(self, goal: Goal, observation: Observation, verdict: Verdict) -> None:
        bucket = self._by_goal.setdefault(str(goal.goal_id), [])
        bucket.append((observation, verdict))
        if len(bucket) > self._limit:
            del bucket[: len(bucket) - self._limit]


# ----------------------------------------------------------------- orchestrator


class Orchestrator:
    """Runs the bounded plan/act/observe/evaluate/replan loop for one goal.

    Publishes ``app.uistate`` transitions at every phase and writes
    ``app.ledger`` events for the loop-specific facts (per-step execution,
    escalation, hard error); ``app.goals.service`` already records the
    goal-lifecycle facts (created, status changed, achieved) on its own.
    """

    def __init__(
        self,
        *,
        session: Session,
        planner: Planner,
        actor: Actor,
        critic: Critic,
        memory: MemoryManager,
        router: CapabilityRouter,
        ui_publisher: UiStatePublisher | None = None,
        max_replans: int = MAX_REPLANS,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.planner = planner
        self.actor = actor
        self.critic = critic
        self.memory = memory
        self.router = router
        self.ui_publisher = ui_publisher or get_publisher()
        self.max_replans = max_replans
        self._now = now or utcnow

    def _publish(self, goal: Goal, state: UiState, *, detail: dict[str, Any] | None = None) -> None:
        self.ui_publisher.publish(
            "goal", str(goal.goal_id), state, detail=detail or {}, at=self._now()
        )

    def _ledger(
        self,
        goal: Goal,
        *,
        event_type: str,
        action: str,
        factual_summary: str,
        source_ref: str,
        detail: dict[str, Any] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
    ) -> None:
        try:
            ledger_service.record(
                self.session,
                ledger_service.ActivityEvent(
                    event_type=event_type,
                    subsystem=SUBSYSTEM_GOAL,
                    action=action,
                    factual_summary=factual_summary,
                    source="live",
                    source_ref=source_ref,
                    occurred_at=self._now(),
                    related_goal_id=goal.goal_id,
                    evidence_refs=evidence_refs or [],
                    detail_json=detail or {},
                ),
            )
        except Exception:  # noqa: BLE001 - the ledger is evidence, never a hard dependency
            logger.warning("cognitive_ledger_note_failed", event_type=event_type)

    def run(self, goal_id: uuid.UUID) -> LoopResult:
        goal = goals_service.get_goal(self.session, goal_id)
        if goal is None:
            raise goals_service.GoalNotFoundError(f"unknown goal: {goal_id}")

        observations: list[Observation] = []
        attempt = 0
        try:
            while attempt <= self.max_replans:
                self._publish(goal, UiState.THINKING, detail={"attempt": attempt})
                recalled = self.memory.recall(goal)
                world_state = {"recalled": recalled}
                plan = self.planner.plan(goal, world_state)

                if not plan.steps:
                    break  # nothing left unmet — fall through to the evaluate/complete check

                all_steps_satisfied = True
                for step in plan.steps:
                    self._publish(
                        goal,
                        UiState.TOOL_RUNNING,
                        detail={"step_id": step.step_id, "capability": step.capability},
                    )
                    observation = self.actor.act(step)
                    observations.append(observation)
                    verdict = self.critic.evaluate(step, observation)
                    self.memory.record(goal, observation, verdict)
                    self._ledger(
                        goal,
                        event_type=EVENT_TYPE_GOAL_STEP_EXECUTED,
                        action="goal_step_executed",
                        factual_summary=(
                            f"Adım çalıştırıldı ({step.capability}): "
                            f"{'başarılı' if observation.ok else 'başarısız'}."
                        ),
                        source_ref=(
                            f"goals:{goal.goal_id}:step:{step.step_id}:"
                            f"{attempt}:{self._now().isoformat()}"
                        ),
                        detail={
                            "capability": step.capability,
                            "ok": observation.ok,
                            "verdict": verdict.value,
                            "error": observation.error,
                        },
                        evidence_refs=observation.evidence_refs,
                    )
                    if verdict == Verdict.SATISFIED:
                        for ref in observation.evidence_refs:
                            if step.success_criterion_id is not None:
                                goals_service.record_evidence(
                                    self.session, goal.goal_id, step.success_criterion_id, ref
                                )
                    else:
                        all_steps_satisfied = False

                evaluation = goals_service.evaluate(self.session, goal.goal_id)
                if evaluation.all_satisfied:
                    break
                if all_steps_satisfied:
                    # every step this round reported success, but criteria
                    # outside this plan remain unmet (e.g. a criterion this
                    # planner does not yet know how to act on) — replanning
                    # again cannot help; escalate rather than spin.
                    break
                attempt += 1
        except Exception as exc:  # noqa: BLE001 - an UNEXPECTED failure, not a routine one
            self.session.rollback()  # defensive: an arbitrary capability may have left a
            # partial write pending; every service call above already commits its own
            # work, so this is normally a no-op.
            self._publish(goal, UiState.ERROR, detail={"error": str(exc)})
            self._ledger(
                goal,
                event_type=EVENT_TYPE_GOAL_ERROR,
                action="goal_error",
                factual_summary=(
                    f"Hedef döngüsü beklenmeyen bir hatayla durdu: {type(exc).__name__}."
                ),
                source_ref=f"goals:{goal.goal_id}:error:{self._now().isoformat()}",
                detail={"error_class": type(exc).__name__, "error": str(exc)[:2000]},
            )
            return LoopResult(
                goal_id=goal.goal_id,
                outcome="failed",
                iterations=attempt,
                observations=observations,
            )

        evaluation = goals_service.evaluate_and_maybe_complete(self.session, goal.goal_id)
        goal = goals_service.get_goal(self.session, goal.goal_id) or goal
        if evaluation.all_satisfied:
            self._publish(goal, UiState.GOAL_COMPLETED)
            outcome = "completed"
        else:
            goals_service.record_blocker(
                self.session,
                goal.goal_id,
                reason="replan_budget_exhausted",
                detail={"max_replans": self.max_replans, "attempts": attempt},
            )
            if goal.status == GOAL_STATUS_ACTIVE:
                goals_service.transition_status(
                    self.session, goal.goal_id, GOAL_STATUS_WAITING_OWNER
                )
            self._publish(goal, UiState.WAITING_OWNER)
            self._ledger(
                goal,
                event_type=EVENT_TYPE_GOAL_ESCALATED,
                action="goal_escalated",
                factual_summary=(
                    f"Hedef sahibe iletildi: {self.max_replans} yeniden planlama denendi, "
                    "ölçüt karşılanamadı."
                ),
                source_ref=f"goals:{goal.goal_id}:escalated:{self._now().isoformat()}",
                detail={"max_replans": self.max_replans, "attempts": attempt},
            )
            outcome = "escalated"

        return LoopResult(
            goal_id=goal.goal_id, outcome=outcome, iterations=attempt, observations=observations
        )


__all__ = [
    "MAX_REPLANS",
    "Actor",
    "AllowListCapabilityRouter",
    "Capability",
    "CapabilityRouter",
    "Critic",
    "CriteriaCritic",
    "InMemoryMemoryManager",
    "LoopResult",
    "MemoryManager",
    "NullMemoryManager",
    "Observation",
    "Orchestrator",
    "Plan",
    "PlanStep",
    "Planner",
    "RegistryActor",
    "RuleBasedPlanner",
    "Verdict",
]
