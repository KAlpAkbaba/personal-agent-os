"""Unit tests: app.goals.cognitive (overnight plan Phase 4).

Covers the full loop on a deterministic fake capability (plan -> act ->
observe -> evaluate -> complete), bounded replanning (a step that keeps
failing escalates instead of looping), the ERROR path for an unexpected
capability exception, UI states published in order, and ledger events
written along the way. No LLM/network call anywhere — every Planner/Actor/
Critic/MemoryManager here is the deterministic reference implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Task
from app.goals import service as goals_service
from app.goals.cognitive import (
    MAX_REPLANS,
    AllowListCapabilityRouter,
    Capability,
    CriteriaCritic,
    InMemoryMemoryManager,
    NullMemoryManager,
    Orchestrator,
    RegistryActor,
    RuleBasedPlanner,
)
from app.goals.models import (
    GOAL_STATUS_ACHIEVED,
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_WAITING_OWNER,
    Goal,
    GoalTask,
)
from app.ledger.models import ActivityEventRow
from app.uistate import UiState, reset_publisher

ALL_TABLES = [Task.__table__, Goal.__table__, GoalTask.__table__, ActivityEventRow.__table__]

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ALL_TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture()
def publisher():
    return reset_publisher()


def _active_goal(session, **overrides) -> Goal:
    base: dict = dict(
        title="Ev ağını yükselt",
        intent="Wi-Fi 6E ile değiştir.",
        success_criteria=[{"statement": "kanıtlandı", "check_kind": "manual_evidence"}],
    )
    base.update(overrides)
    goal = goals_service.create_goal(session, **base)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    return goal


def _orchestrator(session, router, *, memory=None) -> Orchestrator:
    return Orchestrator(
        session=session,
        planner=RuleBasedPlanner(),
        actor=RegistryActor(router),
        critic=CriteriaCritic(),
        memory=memory or NullMemoryManager(),
        router=router,
        now=lambda: NOW,
    )


# ------------------------------------------------------------------- happy path


def test_full_loop_completes_from_a_succeeding_capability(session, publisher) -> None:
    goal = _active_goal(session)
    router = AllowListCapabilityRouter(
        [
            Capability(
                name="criterion.manual_evidence",
                handler=lambda args: {"ok": True, "evidence_refs": [{"kind": "test", "ref": "1"}]},
            )
        ]
    )
    result = _orchestrator(session, router).run(goal.goal_id)

    assert result.outcome == "completed"
    reloaded = goals_service.get_goal(session, goal.goal_id)
    assert reloaded.status == GOAL_STATUS_ACHIEVED
    assert reloaded.success_criteria[0]["satisfied"] is True

    states = [e.state for e in publisher.history("goal", str(goal.goal_id))]
    assert states == [UiState.THINKING, UiState.TOOL_RUNNING, UiState.GOAL_COMPLETED]


def test_goal_with_no_criteria_never_auto_completes(session, publisher) -> None:
    """Empty success criteria means the planner has nothing to do on attempt
    zero, but "nothing to check" must never read as "success" (never assert)."""
    goal = _active_goal(session, success_criteria=[])
    router = AllowListCapabilityRouter([])
    result = _orchestrator(session, router).run(goal.goal_id)
    assert result.outcome == "escalated"
    reloaded = goals_service.get_goal(session, goal.goal_id)
    assert reloaded.status == GOAL_STATUS_WAITING_OWNER


# --------------------------------------------------------------- bounded replan


def test_a_persistently_failing_step_escalates_instead_of_looping_forever(
    session, publisher
) -> None:
    calls = {"n": 0}

    def always_fails(args):
        calls["n"] += 1
        return {"ok": False, "error": "still not ready"}

    goal = _active_goal(session)
    router = AllowListCapabilityRouter(
        [Capability(name="criterion.manual_evidence", handler=always_fails)]
    )
    result = _orchestrator(session, router).run(goal.goal_id)

    assert result.outcome == "escalated"
    # exactly MAX_REPLANS+1 attempts: the initial try plus the bounded replans.
    assert calls["n"] == MAX_REPLANS + 1
    reloaded = goals_service.get_goal(session, goal.goal_id)
    assert reloaded.status == GOAL_STATUS_WAITING_OWNER
    assert any(b["reason"] == "replan_budget_exhausted" for b in reloaded.blockers)

    states = [e.state for e in publisher.history("goal", str(goal.goal_id))]
    assert states == (
        [UiState.THINKING, UiState.TOOL_RUNNING] * (MAX_REPLANS + 1) + [UiState.WAITING_OWNER]
    )


def test_a_disallowed_capability_still_escalates_within_the_bound(session, publisher) -> None:
    """A criterion naming a capability nobody registered is a normal
    unsatisfied observation (capability_not_allowed), not a crash."""
    goal = _active_goal(session)
    router = AllowListCapabilityRouter([])  # nothing registered
    result = _orchestrator(session, router).run(goal.goal_id)
    assert result.outcome == "escalated"
    assert all(obs.error == "capability_not_allowed" for obs in result.observations)


def test_escalation_respects_owner_approval_gate_left_intact(session, publisher) -> None:
    """A gated goal that escalates lands in waiting_owner just like an
    ungated one; the gate only matters when something later tries to leave."""
    goal = _active_goal(session, requires_owner_approval=True)
    router = AllowListCapabilityRouter(
        [Capability(name="criterion.manual_evidence", handler=lambda a: {"ok": False})]
    )
    _orchestrator(session, router).run(goal.goal_id)
    reloaded = goals_service.get_goal(session, goal.goal_id)
    assert reloaded.status == GOAL_STATUS_WAITING_OWNER
    with pytest.raises(goals_service.OwnerApprovalRequiredError):
        goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    approved = goals_service.approve(session, goal.goal_id)
    assert approved.status == GOAL_STATUS_ACTIVE


# ------------------------------------------------------------------- error path


def test_an_unexpected_capability_exception_publishes_error_and_fails_the_loop(
    session, publisher
) -> None:
    def boom(args):
        raise RuntimeError("dependency down")

    goal = _active_goal(session)
    router = AllowListCapabilityRouter([Capability(name="criterion.manual_evidence", handler=boom)])
    result = _orchestrator(session, router).run(goal.goal_id)

    assert result.outcome == "failed"
    reloaded = goals_service.get_goal(session, goal.goal_id)
    assert reloaded.status == GOAL_STATUS_ACTIVE  # untouched: the loop errored before deciding

    states = [e.state for e in publisher.history("goal", str(goal.goal_id))]
    assert states == [UiState.THINKING, UiState.TOOL_RUNNING, UiState.ERROR]


def test_a_capability_returning_a_non_dict_is_a_bug_not_a_soft_failure(session, publisher) -> None:
    goal = _active_goal(session)
    router = AllowListCapabilityRouter(
        [Capability(name="criterion.manual_evidence", handler=lambda a: "not a dict")]
    )
    result = _orchestrator(session, router).run(goal.goal_id)
    assert result.outcome == "failed"


# ------------------------------------------------------------------- ledger


def test_step_execution_is_recorded_in_the_ledger(session, publisher) -> None:
    goal = _active_goal(session)
    router = AllowListCapabilityRouter(
        [
            Capability(
                name="criterion.manual_evidence",
                handler=lambda args: {"ok": True, "evidence_refs": [{"kind": "test", "ref": "1"}]},
            )
        ]
    )
    _orchestrator(session, router).run(goal.goal_id)
    rows = session.query(ActivityEventRow).filter_by(event_type="goal.step_executed").all()
    assert len(rows) == 1
    assert rows[0].related_goal_id == goal.goal_id
    assert rows[0].detail_json["ok"] is True


def test_escalation_is_recorded_in_the_ledger(session, publisher) -> None:
    goal = _active_goal(session)
    router = AllowListCapabilityRouter(
        [Capability(name="criterion.manual_evidence", handler=lambda a: {"ok": False})]
    )
    _orchestrator(session, router).run(goal.goal_id)
    rows = session.query(ActivityEventRow).filter_by(event_type="goal.escalated").all()
    assert len(rows) == 1


def test_error_is_recorded_in_the_ledger(session, publisher) -> None:
    def boom(args):
        raise RuntimeError("nope")

    goal = _active_goal(session)
    router = AllowListCapabilityRouter([Capability(name="criterion.manual_evidence", handler=boom)])
    _orchestrator(session, router).run(goal.goal_id)
    rows = session.query(ActivityEventRow).filter_by(event_type="goal.error").all()
    assert len(rows) == 1
    assert rows[0].detail_json["error_class"] == "RuntimeError"


# ------------------------------------------------------------------ memory


def test_in_memory_memory_manager_recalls_prior_observations(session, publisher) -> None:
    memory = InMemoryMemoryManager()
    goal = _active_goal(session)
    router = AllowListCapabilityRouter(
        [
            Capability(
                name="criterion.manual_evidence",
                handler=lambda args: {"ok": True, "evidence_refs": [{"kind": "t", "ref": "1"}]},
            )
        ]
    )
    _orchestrator(session, router, memory=memory).run(goal.goal_id)
    recalled = memory.recall(goal)
    assert recalled["recent"]
    assert recalled["recent"][0][1] is True  # (step_id, ok, verdict)


# --------------------------------------------------------------- allow-list


def test_capability_router_names_reflect_registration() -> None:
    router = AllowListCapabilityRouter([Capability(name="a", handler=lambda a: {})])
    router.register(Capability(name="b", handler=lambda a: {}))
    assert router.names() == ("a", "b")
    with pytest.raises(ValueError):
        router.register(Capability(name="a", handler=lambda a: {}))
