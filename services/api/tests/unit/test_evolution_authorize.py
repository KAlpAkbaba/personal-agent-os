"""Unit tests: the explicit owner-authorisation chain and its risk-tier gate
(M18 spec §5, ADR-0055 §5).

    SHADOW_READY -> OWNER_APPROVAL_REQUIRED -> OWNER_AUTHORIZED

``record_release_footprint`` derives and stores the candidate's risk tier;
``authorize`` is the owner action that consumes it. Tier 3+ needs a SECOND,
explicit confirmation before OWNER_AUTHORIZED is reachable at all.

Also proves the boundary this M18 work found and closed: once an opportunity
is on the PRODUCTION side of the wall (e.g. mid ``ROLLING_BACK``), a lab actor
must not be able to divert it anywhere — including to a target like
``quarantined`` that is, by itself, an ordinary lab-reachable status earlier
in the lifecycle.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.evolution.authority import (
    AuthorityError,
    LabAuthority,
    ProductionAuthority,
    mint_owner_capability,
)
from app.evolution.backlog import (
    ActorKind,
    OpportunityStatus,
    StaticReleaseEvidenceProvider,
)
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.models import CapabilityGap, EvolutionOpportunity
from app.evolution.risk import RiskTier
from app.evolution.service import EvolutionService, RecordingUiStatePublisher
from app.ledger.models import ActivityEventRow
from app.selfhealing.models import Incident
from tests.unit.test_evolution_authority import FakeSession

TABLES = [
    EvolutionOpportunity.__table__,
    CapabilityGap.__table__,
    ActivityEventRow.__table__,
    Incident.__table__,
]

SCORES = {
    "owner_relevance": 0.9,
    "expected_utility": 0.8,
    "recurrence": 0.6,
    "confidence": 0.7,
    "engineering_cost": 0.3,
    "operational_risk": 0.1,
}


def make_session_factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return session_scope


def insert_ledger_event(session_scope) -> str:
    event_id = uuid.uuid4()
    with session_scope() as session:
        session.add(
            ActivityEventRow(
                event_id=event_id,
                occurred_at=datetime.now(UTC),
                recorded_at=datetime.now(UTC),
                event_type="incident.opened",
                subsystem="cloud_core",
                status="completed",
                severity="warning",
                action="incident.opened",
                production_state="n/a",
                evidence_refs=[],
                factual_summary="test evidence",
                detail_json={},
                source="live",
                source_ref=f"test:{event_id}",
            )
        )
        session.commit()
    return str(event_id)


@pytest.fixture()
def stack():
    session_scope = make_session_factory()
    ui = RecordingUiStatePublisher()
    service = EvolutionService(session_scope, ui=ui)
    return session_scope, service, ui


def make_opportunity(stack, **overrides) -> dict:
    session_scope, service, _ = stack
    event_id = insert_ledger_event(session_scope)
    body = {
        "title": "Test candidate",
        "statement": "Bir kanıta dayalı fırsat.",
        "evidence_refs": [{"kind": "ledger_event", "ref": event_id}],
        "scores": SCORES,
        "source": "ledger_event",
        "source_ref": f"activity_events:{event_id}",
    }
    body.update(overrides)
    return service.create_from_evidence(**body)


def drive_to_owner_approval_required(stack, opportunity_id: str) -> dict:
    _, service, _ = stack
    for status in (
        OpportunityStatus.RESEARCHING,
        OpportunityStatus.DESIGN_READY,
        OpportunityStatus.BUILDING,
        OpportunityStatus.TESTING,
        OpportunityStatus.EVALUATING,
        OpportunityStatus.SHADOW_READY,
    ):
        service.advance(opportunity_id, target=status, actor=ActorKind.LAB)
    return service.advance(
        opportunity_id, target=OpportunityStatus.OWNER_APPROVAL_REQUIRED, actor=ActorKind.LAB
    )


# --------------------------------------------------------- record_release_footprint


def test_footprint_derives_and_stores_the_tier_never_accepts_one(stack) -> None:
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    oid = opportunity["opportunity_id"]
    updated = service.record_release_footprint(oid, changed_paths=["docs/x.md"])
    assert updated["detail"]["risk_tier"] == int(RiskTier.UI_ADDITIVE)
    assert updated["detail"]["changed_paths"] == ["docs/x.md"]
    assert "risk_assessment" in updated["detail"]

    # A tier-5 footprint on the SAME opportunity replaces the tier — always
    # derived fresh from the paths given, never accumulated or hand-edited.
    updated = service.record_release_footprint(
        oid, changed_paths=["services/api/app/evolution/authority.py"]
    )
    assert updated["detail"]["risk_tier"] == int(RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY)


def test_footprint_refuses_once_the_candidate_has_left_the_lab(stack) -> None:
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    oid = opportunity["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)
    service.record_release_footprint(oid, changed_paths=["docs/x.md"])  # still allowed here

    capability = mint_owner_capability(FakeSession())
    service.authorize(oid, capability)  # tier 1: no second confirmation needed

    with pytest.raises(EvolutionError) as excinfo:
        service.record_release_footprint(oid, changed_paths=["apps/web/y.tsx"])
    assert excinfo.value.error_class == EvolutionErrorClass.LIFECYCLE_VIOLATION


def test_footprint_requires_a_lab_grant(stack) -> None:
    session_scope, _, _ = stack
    narrow = LabAuthority.issue("evolution.engine", set())
    service = EvolutionService(session_scope, authority=narrow)
    with pytest.raises(EvolutionError):
        service.record_release_footprint(str(uuid.uuid4()), changed_paths=["docs/x.md"])


# -------------------------------------------------------------------- authorize


def test_authorize_requires_the_footprint_to_exist_first(stack) -> None:
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    oid = opportunity["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)
    with pytest.raises(EvolutionError) as excinfo:
        service.authorize(oid, mint_owner_capability(FakeSession()))
    assert excinfo.value.error_class == EvolutionErrorClass.LIFECYCLE_VIOLATION
    assert "never derived" in excinfo.value.message


def test_authorize_requires_a_real_owner_capability(stack) -> None:
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    oid = opportunity["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)
    service.record_release_footprint(oid, changed_paths=["docs/x.md"])
    for bogus in (None, "owner", object(), {"session_id": "x"}):
        with pytest.raises(EvolutionError):
            service.authorize(oid, bogus)  # type: ignore[arg-type]


@pytest.mark.parametrize("actor", [ActorKind.LAB, ActorKind.SYSTEM])
def test_advance_can_never_reach_owner_authorized_either(stack, actor) -> None:
    """Both owner gates (approve/authorize) are separate methods, not statuses
    reachable through advance() by any actor."""
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    oid = opportunity["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)
    with pytest.raises(EvolutionError) as excinfo:
        service.advance(oid, target=OpportunityStatus.OWNER_AUTHORIZED, actor=actor)
    assert excinfo.value.error_class == EvolutionErrorClass.PERMISSION_DENIED


def test_tier_1_and_2_proceed_on_the_first_call(stack) -> None:
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    oid = opportunity["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)
    service.record_release_footprint(oid, changed_paths=["docs/readme.md"])
    updated = service.authorize(oid, mint_owner_capability(FakeSession()))
    assert updated["status"] == str(OpportunityStatus.OWNER_AUTHORIZED)


def test_tier_3_plus_refuses_without_the_second_confirmation(stack) -> None:
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    oid = opportunity["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)
    service.record_release_footprint(oid, changed_paths=["services/api/app/selfhealing/service.py"])

    with pytest.raises(AuthorityError) as excinfo:
        service.authorize(oid, mint_owner_capability(FakeSession()))
    assert excinfo.value.details["reason"] == "second_confirmation_required"
    assert excinfo.value.details["risk_tier"] == int(RiskTier.PRODUCTION_BEHAVIOR)
    # Still OWNER_APPROVAL_REQUIRED: the refused call must not have moved it.
    assert service.get(oid)["status"] == str(OpportunityStatus.OWNER_APPROVAL_REQUIRED)

    updated = service.authorize(oid, mint_owner_capability(FakeSession()), confirm_high_risk=True)
    assert updated["status"] == str(OpportunityStatus.OWNER_AUTHORIZED)
    assert updated["detail"]["second_confirmation_given"] is True


def test_the_tier_consulted_is_the_one_already_recorded_not_a_caller_argument(stack) -> None:
    """authorize() has no `risk_tier` parameter at all - there is nothing to pass."""
    import inspect

    from app.evolution.service import EvolutionService as ES

    sig = inspect.signature(ES.authorize)
    assert "risk_tier" not in sig.parameters
    assert "tier" not in sig.parameters


def test_authorize_writes_a_ledger_event_and_ui_state(stack) -> None:
    session_scope, service, ui = stack
    opportunity = make_opportunity(stack)
    oid = opportunity["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)
    service.record_release_footprint(oid, changed_paths=["docs/x.md"])
    service.authorize(oid, mint_owner_capability(FakeSession()))

    with session_scope() as session:
        written = {
            row.event_type
            for row in session.query(ActivityEventRow).filter(
                ActivityEventRow.event_type == "evolution.owner_authorized"
            )
        }
    assert written == {"evolution.owner_authorized"}
    published = dict(ui.published)
    assert published["release.owner_authorized"]["risk_tier"] == int(RiskTier.UI_ADDITIVE)


# ------------------------------------------------- the quarantine boundary (found+fixed)


def test_a_lab_actor_cannot_divert_a_live_rollback_to_quarantined(stack) -> None:
    """QUARANTINED is an ordinary lab-reachable status from BUILDING/TESTING,
    but it is ALSO legal from ROLLING_BACK — a production-side status. Before
    this fix, `advance()` only checked the TARGET's classification, so a lab
    actor could quarantine an opportunity that was mid rollback using nothing
    but its own default `propose_candidate` grant. Confirmed as a real gap
    against the actual service (not just the transition table) while building
    the M18 executor, then closed in `EvolutionService.advance()`.
    """
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    oid = opportunity["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)
    service.record_release_footprint(oid, changed_paths=["docs/x.md"])
    service.authorize(oid, mint_owner_capability(FakeSession()))

    service.release_evidence = StaticReleaseEvidenceProvider({oid: "cloud_core:9.9.9"})
    production = ProductionAuthority.for_owner(mint_owner_capability(FakeSession()))
    for target in (
        OpportunityStatus.QUALIFYING,
        OpportunityStatus.DEPLOYING,
        OpportunityStatus.FAILED,
        OpportunityStatus.ROLLING_BACK,
    ):
        service.advance(oid, target=target, actor=ActorKind.SYSTEM, production_authority=production)
    assert service.get(oid)["status"] == str(OpportunityStatus.ROLLING_BACK)

    with pytest.raises(EvolutionError) as excinfo:
        service.advance(oid, target=OpportunityStatus.QUARANTINED, actor=ActorKind.LAB)
    assert excinfo.value.error_class == EvolutionErrorClass.PERMISSION_DENIED
    # ...and it genuinely did not move.
    assert service.get(oid)["status"] == str(OpportunityStatus.ROLLING_BACK)

    # A SYSTEM actor WITH production authority may still do it (an honest
    # "abandon this rollback for manual recovery" path stays reachable).
    quarantined = service.advance(
        oid,
        target=OpportunityStatus.QUARANTINED,
        actor=ActorKind.SYSTEM,
        production_authority=production,
    )
    assert quarantined["status"] == str(OpportunityStatus.QUARANTINED)


def test_a_lab_actor_still_can_quarantine_its_own_pre_wall_candidate(stack) -> None:
    """The fix must not break the ORDINARY lab-side use of quarantine."""
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    oid = opportunity["opportunity_id"]
    for status in (
        OpportunityStatus.RESEARCHING,
        OpportunityStatus.DESIGN_READY,
        OpportunityStatus.BUILDING,
    ):
        service.advance(oid, target=status, actor=ActorKind.LAB)
    quarantined = service.advance(oid, target=OpportunityStatus.QUARANTINED, actor=ActorKind.LAB)
    assert quarantined["status"] == str(OpportunityStatus.QUARANTINED)


def test_a_lab_actor_cannot_reject_an_opportunity_mid_qualifying_either(stack) -> None:
    """The same gap shape applies to REJECTED, reachable from QUALIFYING."""
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    oid = opportunity["opportunity_id"]
    drive_to_owner_approval_required(stack, oid)
    service.record_release_footprint(oid, changed_paths=["docs/x.md"])
    service.authorize(oid, mint_owner_capability(FakeSession()))
    service.release_evidence = StaticReleaseEvidenceProvider({oid: "cloud_core:1.0.0"})
    production = ProductionAuthority.for_owner(mint_owner_capability(FakeSession()))
    service.advance(
        oid,
        target=OpportunityStatus.QUALIFYING,
        actor=ActorKind.SYSTEM,
        production_authority=production,
    )
    with pytest.raises(EvolutionError) as excinfo:
        service.advance(oid, target=OpportunityStatus.REJECTED, actor=ActorKind.LAB)
    assert excinfo.value.error_class == EvolutionErrorClass.PERMISSION_DENIED
