"""Unit tests: app.goals.service (overnight plan Phase 4).

Covers: legal/illegal status transitions, cycle refusal, success criteria
satisfied only from evidence (never by assertion), the owner-approval gate,
blocker recording, and ledger events written alongside lifecycle changes —
SQLite only, mirrors tests/unit/test_ledger_service.py's fixture pattern.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import TASK_STATUS_READY, Task
from app.goals import service as goals_service
from app.goals.models import (
    GOAL_STATUS_ABANDONED,
    GOAL_STATUS_ACHIEVED,
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_DRAFT,
    GOAL_STATUS_WAITING_OWNER,
    Goal,
    GoalTask,
)
from app.goals.state import IllegalGoalTransition
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_GOAL_ACHIEVED,
    EVENT_TYPE_GOAL_CREATED,
    EVENT_TYPE_GOAL_STATUS_CHANGED,
)

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


def _create(session, **overrides) -> Goal:
    base: dict = dict(title="Ev ağını yükselt", intent="Wi-Fi 6E ile değiştir.")
    base.update(overrides)
    return goals_service.create_goal(session, **base)


# ------------------------------------------------------------------- creation


def test_create_goal_defaults_to_draft(session) -> None:
    goal = _create(session)
    assert goal.status == GOAL_STATUS_DRAFT
    assert goal.horizon == "someday"
    assert goal.success_criteria == []


def test_create_goal_is_idempotent_on_source_ref(session) -> None:
    first = _create(session, source="owner", source_ref="fixed-ref")
    second = _create(session, source="owner", source_ref="fixed-ref", title="different title")
    assert first.goal_id == second.goal_id
    assert second.title == "Ev ağını yükselt"  # unchanged: the second call was a no-op


def test_create_goal_records_a_ledger_event(session) -> None:
    goal = _create(session)
    rows = session.query(ActivityEventRow).filter_by(event_type=EVENT_TYPE_GOAL_CREATED).all()
    assert len(rows) == 1
    assert rows[0].related_goal_id == goal.goal_id


def test_create_goal_rejects_unknown_check_kind(session) -> None:
    with pytest.raises(goals_service.InvalidCheckKind):
        _create(session, success_criteria=[{"statement": "x", "check_kind": "not_a_real_kind"}])


def test_create_goal_normalizes_criteria_ids_and_unsatisfied(session) -> None:
    # "satisfied": True is ignored on write - only evaluate() may ever set it.
    goal = _create(session, success_criteria=[{"statement": "kanıt var", "satisfied": True}])
    assert len(goal.success_criteria) == 1
    criterion = goal.success_criteria[0]
    assert criterion["id"]
    assert criterion["satisfied"] is False  # never trusted from the caller


def test_create_subgoal_links_parent(session) -> None:
    parent = _create(session)
    child = _create(session, parent_goal_id=parent.goal_id, title="Alt hedef")
    subgoals = goals_service.list_subgoals(session, parent.goal_id)
    assert [g.goal_id for g in subgoals] == [child.goal_id]


# --------------------------------------------------------------- transitions


def test_legal_transition_draft_to_active(session) -> None:
    goal = _create(session)
    updated = goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    assert updated.status == GOAL_STATUS_ACTIVE


def test_illegal_transition_raises(session) -> None:
    goal = _create(session)
    with pytest.raises(IllegalGoalTransition):
        goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACHIEVED)


def test_transition_from_terminal_status_raises(session) -> None:
    goal = _create(session)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ABANDONED)
    with pytest.raises(IllegalGoalTransition):
        goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)


def test_idempotent_reassert_of_same_status_is_allowed(session) -> None:
    goal = _create(session)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    again = goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    assert again.status == GOAL_STATUS_ACTIVE


def test_status_change_records_a_status_changed_event(session) -> None:
    goal = _create(session)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    rows = (
        session.query(ActivityEventRow).filter_by(event_type=EVENT_TYPE_GOAL_STATUS_CHANGED).all()
    )
    assert len(rows) == 1
    assert rows[0].detail_json["new"] == GOAL_STATUS_ACTIVE


def test_reaching_achieved_records_a_goal_achieved_event(session) -> None:
    goal = _create(session)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACHIEVED)
    rows = session.query(ActivityEventRow).filter_by(event_type=EVENT_TYPE_GOAL_ACHIEVED).all()
    assert len(rows) == 1


# --------------------------------------------------------------- owner gate


def test_owner_approval_required_to_leave_waiting_owner(session) -> None:
    goal = _create(session, requires_owner_approval=True)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_WAITING_OWNER)
    with pytest.raises(goals_service.OwnerApprovalRequiredError):
        goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)


def test_approve_clears_the_owner_gate(session) -> None:
    goal = _create(session, requires_owner_approval=True)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_WAITING_OWNER)
    approved = goals_service.approve(session, goal.goal_id)
    assert approved.status == GOAL_STATUS_ACTIVE
    assert approved.approved_at is not None


def test_no_owner_approval_required_leaves_waiting_owner_freely(session) -> None:
    goal = _create(session, requires_owner_approval=False)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_WAITING_OWNER)
    updated = goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    assert updated.status == GOAL_STATUS_ACTIVE


# ------------------------------------------------------------------ blockers


def test_record_blocker_appends_without_changing_status(session) -> None:
    goal = _create(session)
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    updated = goals_service.record_blocker(session, goal.goal_id, reason="no_capacity")
    assert updated.status == GOAL_STATUS_ACTIVE
    assert len(updated.blockers) == 1
    assert updated.blockers[0]["reason"] == "no_capacity"


# -------------------------------------------------------------- dependencies


def test_add_dependency_records_edge(session) -> None:
    a = _create(session, title="A")
    b = _create(session, title="B")
    updated = goals_service.add_dependency(session, a.goal_id, b.goal_id)
    assert str(b.goal_id) in updated.depends_on


def test_add_dependency_refuses_self_dependency(session) -> None:
    a = _create(session, title="A")
    with pytest.raises(goals_service.GoalCycleError):
        goals_service.add_dependency(session, a.goal_id, a.goal_id)


def test_add_dependency_refuses_direct_cycle(session) -> None:
    a = _create(session, title="A")
    b = _create(session, title="B")
    goals_service.add_dependency(session, a.goal_id, b.goal_id)  # A depends on B
    with pytest.raises(goals_service.GoalCycleError):
        goals_service.add_dependency(session, b.goal_id, a.goal_id)  # B depends on A: cycle


def test_add_dependency_refuses_transitive_cycle(session) -> None:
    a = _create(session, title="A")
    b = _create(session, title="B")
    c = _create(session, title="C")
    goals_service.add_dependency(session, a.goal_id, b.goal_id)  # A -> B
    goals_service.add_dependency(session, b.goal_id, c.goal_id)  # B -> C
    with pytest.raises(goals_service.GoalCycleError):
        goals_service.add_dependency(session, c.goal_id, a.goal_id)  # C -> A would close the loop


# -------------------------------------------------------- evaluate (evidence)


def test_manual_evidence_criterion_unsatisfied_until_evidence_recorded(session) -> None:
    goal = _create(
        session,
        success_criteria=[{"statement": "kanıtlandı", "check_kind": "manual_evidence"}],
    )
    criterion_id = goal.success_criteria[0]["id"]

    result = goals_service.evaluate(session, goal.goal_id)
    assert result.all_satisfied is False

    goals_service.record_evidence(
        session, goal.goal_id, criterion_id, {"kind": "artifact", "ref": "abc"}
    )
    result = goals_service.evaluate(session, goal.goal_id)
    assert result.all_satisfied is True
    assert result.criteria[0].evidence_refs == [{"kind": "artifact", "ref": "abc"}]


def test_task_status_criterion_satisfied_only_when_linked_task_reaches_target(session) -> None:
    task = Task(intent="rapor", status="RUNNING")
    session.add(task)
    session.commit()

    goal = _create(
        session,
        success_criteria=[
            {
                "statement": "görev hazır",
                "check_kind": "task_status",
                "detail": {"task_role": "primary"},
            }
        ],
    )
    goals_service.link_task(session, goal.goal_id, task.id, role="primary")

    assert goals_service.evaluate(session, goal.goal_id).all_satisfied is False

    task.status = TASK_STATUS_READY
    session.commit()

    assert goals_service.evaluate(session, goal.goal_id).all_satisfied is True


def test_ledger_event_criterion_satisfied_when_matching_event_exists(session) -> None:
    goal = _create(
        session,
        success_criteria=[
            {
                "statement": "dağıtım yapıldı",
                "check_kind": "ledger_event",
                "detail": {"event_type": "deployment.agent.installed"},
            }
        ],
    )
    assert goals_service.evaluate(session, goal.goal_id).all_satisfied is False

    from app.ledger import service as ledger_service

    ledger_service.record(
        session,
        ledger_service.ActivityEvent(
            event_type="deployment.agent.installed",
            subsystem="deployment",
            action="agent_installed",
            factual_summary="Ajan kuruldu.",
            source="live",
            source_ref="agent:host-1:installed",
        ),
    )
    assert goals_service.evaluate(session, goal.goal_id).all_satisfied is True


def test_evaluate_and_maybe_complete_transitions_active_goal_to_achieved(session) -> None:
    goal = _create(
        session,
        success_criteria=[{"statement": "kanıtlandı", "check_kind": "manual_evidence"}],
    )
    criterion_id = goal.success_criteria[0]["id"]
    goals_service.transition_status(session, goal.goal_id, GOAL_STATUS_ACTIVE)
    goals_service.record_evidence(session, goal.goal_id, criterion_id, {"kind": "x", "ref": "1"})

    result = goals_service.evaluate_and_maybe_complete(session, goal.goal_id)
    assert result.all_satisfied is True
    reloaded = goals_service.get_goal(session, goal.goal_id)
    assert reloaded.status == GOAL_STATUS_ACHIEVED


def test_evaluate_never_marks_satisfied_without_a_registered_evaluator(session) -> None:
    """A criterion is normalized to a known check_kind at creation time (so this
    path cannot normally be reached), but evaluate() must still never assert
    success for anything it does not know how to check — defense in depth."""
    goal = _create(session)
    goal.success_criteria = [
        {
            "id": "x",
            "statement": "?",
            "check_kind": "made_up",
            "satisfied": False,
            "evidence_refs": [],
        }
    ]
    session.commit()
    result = goals_service.evaluate(session, goal.goal_id)
    assert result.criteria[0].satisfied is False


def test_unknown_goal_raises_not_found(session) -> None:
    with pytest.raises(goals_service.GoalNotFoundError):
        goals_service.transition_status(session, uuid.uuid4(), GOAL_STATUS_ACTIVE)
