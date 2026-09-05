"""The Approval Center's data: what is genuinely waiting on the owner, and how risky it is.

Two things this pins that the authorize tests do not:

1. ``OWNER_APPROVAL_REQUIRED`` is listed. It was added to the lifecycle in M18 and means
   precisely "the owner must act" — and it was the one status ``pending_owner_actions``
   did not return, so a candidate that had formally asked for the owner was the one thing
   the Approval Center could not see.
2. The risk tier travels with the candidate. It is the single most decision-relevant fact
   for an owner deciding whether to authorise, and it was buried in ``detail``. A candidate
   whose tier was never derived says so, rather than being shown a guessed tier.
"""

from __future__ import annotations

from app.evolution.backlog import ActorKind, OpportunityStatus
from app.evolution.risk import SECOND_CONFIRMATION_FLOOR
from tests.unit import test_evolution_authorize as authorize_tests

# Re-bound rather than imported: `from ... import stack` collides with the fixture
# argument of the same name in every test below (ruff F811), the same way
# test_research_uistate.py re-binds its fixtures.
drive_to_owner_approval_required = authorize_tests.drive_to_owner_approval_required
make_opportunity = authorize_tests.make_opportunity
stack = authorize_tests.stack


def _pending_ids(service) -> dict[str, dict]:
    body = service.pending_owner_actions()
    return {row["opportunity_id"]: row for row in body["awaiting_approval"]}


def test_a_candidate_that_asked_for_the_owner_is_listed(stack) -> None:
    _, service, _ = stack
    oid = make_opportunity(stack)["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)

    pending = _pending_ids(service)
    assert oid in pending
    assert pending[oid]["status"] == str(OpportunityStatus.OWNER_APPROVAL_REQUIRED)


def test_shadow_ready_is_still_listed(stack) -> None:
    _, service, _ = stack
    oid = make_opportunity(stack)["opportunity_id"]
    for status in (
        OpportunityStatus.RESEARCHING,
        OpportunityStatus.DESIGN_READY,
        OpportunityStatus.BUILDING,
        OpportunityStatus.TESTING,
        OpportunityStatus.EVALUATING,
        OpportunityStatus.SHADOW_READY,
    ):
        service.advance(oid, target=status, actor=ActorKind.LAB)

    assert oid in _pending_ids(service)


def test_a_candidate_still_in_the_lab_is_not_listed(stack) -> None:
    _, service, _ = stack
    oid = make_opportunity(stack)["opportunity_id"]
    service.advance(oid, target=OpportunityStatus.RESEARCHING, actor=ActorKind.LAB)

    assert oid not in _pending_ids(service)


def test_the_risk_tier_travels_with_the_candidate(stack) -> None:
    _, service, _ = stack
    oid = make_opportunity(stack)["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)
    # The authority kernel itself: the highest tier there is.
    service.record_release_footprint(oid, changed_paths=["services/api/app/evolution/authority.py"])

    row = _pending_ids(service)[oid]
    assert row["risk_tier"] == 5
    assert row["risk_tier_label"]
    assert row["requires_second_confirmation"] is True
    assert isinstance(row["risk_reasons"], list) and row["risk_reasons"]


def test_a_low_tier_does_not_need_a_second_confirmation(stack) -> None:
    _, service, _ = stack
    oid = make_opportunity(stack)["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)
    service.record_release_footprint(oid, changed_paths=["docs/x.md"])

    row = _pending_ids(service)[oid]
    assert row["risk_tier"] == 1
    assert row["requires_second_confirmation"] is False


def test_an_underived_tier_is_reported_as_none_not_guessed(stack) -> None:
    """The honest answer for a candidate nobody has assessed is "not assessed"."""
    _, service, _ = stack
    oid = make_opportunity(stack)["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)

    row = _pending_ids(service)[oid]
    assert row["risk_tier"] is None
    assert row["risk_tier_label"] is None
    assert row["requires_second_confirmation"] is None
    assert row["risk_reasons"] == []


def test_the_note_states_the_floor_it_enforces(stack) -> None:
    """The published policy is derived from the constant the guard consults, never retyped."""
    _, service, _ = stack
    body = service.pending_owner_actions()
    assert body["second_confirmation_floor"] == int(SECOND_CONFIRMATION_FLOOR)
    assert str(int(SECOND_CONFIRMATION_FLOOR)) in body["note"]
    assert "shadow_ready" in body["note"]
