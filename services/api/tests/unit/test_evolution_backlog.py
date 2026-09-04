"""Unit tests for the evolution backlog: lifecycle law and evidence-only creation.

The two rules that matter most are asserted from several directions:

* ``OWNER_APPROVED`` is reachable only by an owner action — refused by the
  transition table for a system/lab actor, refused by ``advance()`` for every
  actor (it is not a status change), and reachable through ``approve()`` only
  with an owner-session capability;
* ``LIVE`` is reachable only when an owner approval is recorded AND a release
  evidence provider confirms an owner-approved release exists. The default
  provider confirms nothing, so out of the box LIVE cannot be entered.
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
    Grant,
    LabAuthority,
    ProductionAuthority,
    mint_owner_capability,
)
from app.evolution.backlog import (
    LEGAL_TRANSITIONS,
    ActorKind,
    OpportunityBacklog,
    OpportunityStatus,
    StaticReleaseEvidenceProvider,
    assert_transition,
    legal_targets,
    parse_evidence,
    transition_table,
)
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.models import CapabilityGap, EvolutionOpportunity
from app.evolution.service import (
    EVOLUTION_VERSION,
    EvolutionService,
    RecordingUiStatePublisher,
)
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
                factual_summary="Araştırma işi üç kez aynı hatayla düştü.",
                detail_json={},
                source="live",
                source_ref=f"test:{event_id}",
            )
        )
        session.commit()
    return str(event_id)


def insert_incident(session_scope) -> str:
    incident_id = uuid.uuid4()
    with session_scope() as session:
        session.add(
            Incident(
                id=incident_id,
                component="cloud_core",
                severity="warning",
                fingerprint="a" * 64,
                evidence_json={},
                status="open",
                occurrence_count=3,
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
        )
        session.commit()
    return str(incident_id)


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
        "title": "Araştırma işini yeniden dene",
        "statement": "Aynı hata üç kez tekrarladı; yeniden deneme eklenmeli.",
        "evidence_refs": [{"kind": "ledger_event", "ref": event_id}],
        "scores": SCORES,
        "source": "ledger_event",
        "source_ref": f"activity_events:{event_id}",
    }
    body.update(overrides)
    return service.create_from_evidence(**body)


def drive_to(stack, opportunity_id: str, target: OpportunityStatus) -> dict:
    """Walk the lab-side path with the engine's own (lab) authority."""
    _, service, _ = stack
    path = [
        OpportunityStatus.RESEARCHING,
        OpportunityStatus.DESIGN_READY,
        OpportunityStatus.BUILDING,
        OpportunityStatus.TESTING,
        OpportunityStatus.EVALUATING,
        OpportunityStatus.SHADOW_READY,
    ]
    result: dict = {}
    for status in path:
        result = service.advance(opportunity_id, target=status, actor=ActorKind.LAB)
        if status is target:
            return result
    return result


# ---------------------------------------------------- the transition table


def test_every_status_has_a_row_and_the_table_is_serialisable() -> None:
    table = transition_table()
    assert set(table) == {str(s) for s in OpportunityStatus}
    assert table[str(OpportunityStatus.REJECTED)] == []
    assert table[str(OpportunityStatus.SUPERSEDED)] == []
    assert table[str(OpportunityStatus.SHADOW_READY)] == sorted(
        [
            str(OpportunityStatus.OWNER_APPROVED),
            str(OpportunityStatus.REJECTED),
            str(OpportunityStatus.QUARANTINED),
            str(OpportunityStatus.SUPERSEDED),
        ]
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current, targets in LEGAL_TRANSITIONS.items()
        for target in targets
        if target is not OpportunityStatus.OWNER_APPROVED and target is not OpportunityStatus.LIVE
    ],
)
def test_every_legal_transition_is_accepted_for_the_owner(
    current: OpportunityStatus, target: OpportunityStatus
) -> None:
    assert_transition(current, target, ActorKind.OWNER)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current in OpportunityStatus
        for target in OpportunityStatus
        if target not in LEGAL_TRANSITIONS[current] and current is not target
    ],
)
def test_every_illegal_transition_is_refused(
    current: OpportunityStatus, target: OpportunityStatus
) -> None:
    with pytest.raises(EvolutionError) as excinfo:
        assert_transition(current, target, ActorKind.OWNER, approved_by="owner", release_ref="r")
    assert excinfo.value.error_class == EvolutionErrorClass.LIFECYCLE_VIOLATION


def test_a_status_cannot_transition_to_itself() -> None:
    with pytest.raises(EvolutionError):
        assert_transition(OpportunityStatus.BUILDING, OpportunityStatus.BUILDING, ActorKind.OWNER)


def test_the_lab_can_never_jump_the_wall() -> None:
    """No lab-driven path reaches approval, qualification or production."""
    for current in OpportunityStatus:
        for target in (
            OpportunityStatus.OWNER_APPROVED,
            OpportunityStatus.QUALIFYING,
            OpportunityStatus.LIVE,
            OpportunityStatus.ROLLED_BACK,
        ):
            assert target not in legal_targets(current, ActorKind.LAB)


# ------------------------------------------ OWNER_APPROVED needs the owner


@pytest.mark.parametrize("actor", [ActorKind.SYSTEM, ActorKind.LAB])
def test_owner_approved_is_refused_for_a_non_owner_actor(actor: ActorKind) -> None:
    with pytest.raises(EvolutionError) as excinfo:
        assert_transition(OpportunityStatus.SHADOW_READY, OpportunityStatus.OWNER_APPROVED, actor)
    assert excinfo.value.error_class == EvolutionErrorClass.LIFECYCLE_VIOLATION
    assert excinfo.value.details["target"] == str(OpportunityStatus.OWNER_APPROVED)


def test_owner_approved_is_accepted_for_the_owner() -> None:
    assert_transition(
        OpportunityStatus.SHADOW_READY,
        OpportunityStatus.OWNER_APPROVED,
        ActorKind.OWNER,
    )


def test_advance_never_reaches_owner_approved_even_as_the_owner(stack) -> None:
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    drive_to(stack, opportunity["opportunity_id"], OpportunityStatus.SHADOW_READY)
    with pytest.raises(EvolutionError) as excinfo:
        service.advance(
            opportunity["opportunity_id"],
            target=OpportunityStatus.OWNER_APPROVED,
            actor=ActorKind.OWNER,
        )
    assert excinfo.value.error_class == EvolutionErrorClass.PERMISSION_DENIED


def test_approve_requires_an_owner_capability(stack) -> None:
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    drive_to(stack, opportunity["opportunity_id"], OpportunityStatus.SHADOW_READY)
    for bogus in (None, "owner", object(), {"session_id": "x"}):
        with pytest.raises(EvolutionError):
            service.approve(opportunity["opportunity_id"], bogus)  # type: ignore[arg-type]


def test_approve_records_who_approved_and_when(stack) -> None:
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    drive_to(stack, opportunity["opportunity_id"], OpportunityStatus.SHADOW_READY)
    capability = mint_owner_capability(FakeSession())
    approved = service.approve(opportunity["opportunity_id"], capability, note="olur")
    assert approved["status"] == str(OpportunityStatus.OWNER_APPROVED)
    assert approved["approved_by"].startswith("owner_session:")
    assert approved["approved_at"] is not None
    assert approved["detail"]["transitions"][-1]["actor"] == str(ActorKind.OWNER)


# ------------------------------------------------ LIVE needs a real release


def test_live_is_refused_without_an_owner_approval() -> None:
    with pytest.raises(EvolutionError) as excinfo:
        assert_transition(
            OpportunityStatus.QUALIFYING,
            OpportunityStatus.LIVE,
            ActorKind.SYSTEM,
            approved_by=None,
            release_ref="cloud_core:1.2.3",
        )
    assert excinfo.value.error_class == EvolutionErrorClass.LIFECYCLE_VIOLATION


def test_live_is_refused_without_release_evidence() -> None:
    with pytest.raises(EvolutionError):
        assert_transition(
            OpportunityStatus.QUALIFYING,
            OpportunityStatus.LIVE,
            ActorKind.SYSTEM,
            approved_by="owner_session:1",
            release_ref=None,
        )


def test_the_default_release_provider_makes_live_unreachable(stack) -> None:
    """Fail-safe: an unwired release source refuses rather than assumes."""
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    opportunity_id = opportunity["opportunity_id"]
    drive_to(stack, opportunity_id, OpportunityStatus.SHADOW_READY)
    service.approve(opportunity_id, mint_owner_capability(FakeSession()))
    production = ProductionAuthority.for_owner(mint_owner_capability(FakeSession()))
    service.advance(
        opportunity_id,
        target=OpportunityStatus.QUALIFYING,
        actor=ActorKind.SYSTEM,
        production_authority=production,
    )
    with pytest.raises(EvolutionError) as excinfo:
        service.advance(
            opportunity_id,
            target=OpportunityStatus.LIVE,
            actor=ActorKind.SYSTEM,
            production_authority=production,
        )
    assert excinfo.value.error_class == EvolutionErrorClass.LIFECYCLE_VIOLATION


def test_live_is_reachable_once_an_owner_approved_release_exists(stack) -> None:
    session_scope, _, ui = stack
    opportunity = make_opportunity(stack)
    opportunity_id = opportunity["opportunity_id"]
    service = EvolutionService(
        session_scope,
        ui=ui,
        release_evidence=StaticReleaseEvidenceProvider({opportunity_id: "cloud_core:1.4.0"}),
    )
    for status in (
        OpportunityStatus.RESEARCHING,
        OpportunityStatus.DESIGN_READY,
        OpportunityStatus.BUILDING,
        OpportunityStatus.TESTING,
        OpportunityStatus.EVALUATING,
        OpportunityStatus.SHADOW_READY,
    ):
        service.advance(opportunity_id, target=status, actor=ActorKind.LAB)
    service.approve(opportunity_id, mint_owner_capability(FakeSession()))
    production = ProductionAuthority.for_owner(mint_owner_capability(FakeSession()))
    service.advance(
        opportunity_id,
        target=OpportunityStatus.QUALIFYING,
        actor=ActorKind.SYSTEM,
        production_authority=production,
    )
    live = service.advance(
        opportunity_id,
        target=OpportunityStatus.LIVE,
        actor=ActorKind.SYSTEM,
        production_authority=production,
    )
    assert live["status"] == str(OpportunityStatus.LIVE)
    assert live["detail"]["release_ref"] == "cloud_core:1.4.0"


# --------------------------------- production-side transitions need authority


@pytest.mark.parametrize(
    "target",
    [
        OpportunityStatus.QUALIFYING,
        OpportunityStatus.LIVE,
        OpportunityStatus.ROLLED_BACK,
    ],
)
def test_a_production_side_transition_refuses_the_engines_own_authority(
    stack, target: OpportunityStatus
) -> None:
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    opportunity_id = opportunity["opportunity_id"]
    drive_to(stack, opportunity_id, OpportunityStatus.SHADOW_READY)
    service.approve(opportunity_id, mint_owner_capability(FakeSession()))

    # No production authority at all.
    with pytest.raises(EvolutionError) as no_authority:
        service.advance(opportunity_id, target=target, actor=ActorKind.SYSTEM)
    assert no_authority.value.error_class == EvolutionErrorClass.PERMISSION_DENIED

    # The engine's own lab authority is not a substitute.
    with pytest.raises(EvolutionError) as lab_authority:
        service.advance(
            opportunity_id,
            target=target,
            actor=ActorKind.SYSTEM,
            production_authority=LabAuthority.issue("evolution.engine"),
        )
    assert lab_authority.value.error_class == EvolutionErrorClass.PERMISSION_DENIED

    # ...and a lab actor is refused before authority is even considered.
    with pytest.raises(EvolutionError):
        service.advance(opportunity_id, target=target, actor=ActorKind.LAB)


def test_the_engine_service_cannot_be_built_with_production_authority(stack) -> None:
    session_scope, _, _ = stack
    production = ProductionAuthority.for_owner(mint_owner_capability(FakeSession()))
    with pytest.raises(EvolutionError):
        EvolutionService(session_scope, authority=production)


def test_a_lab_authority_missing_a_grant_refuses_that_step(stack) -> None:
    session_scope, _, _ = stack
    narrow = LabAuthority.issue(
        "evolution.engine",
        {Grant.READ_EVIDENCE, Grant.PROPOSE_CANDIDATE},
    )
    service = EvolutionService(session_scope, authority=narrow)
    event_id = insert_ledger_event(session_scope)
    opportunity = service.create_from_evidence(
        title="Küçük iyileştirme",
        statement="Kanıta dayalı bir fırsat.",
        evidence_refs=[{"kind": "ledger_event", "ref": event_id}],
        scores=SCORES,
        source="ledger_event",
        source_ref=f"activity_events:{event_id}",
    )
    service.advance(
        opportunity["opportunity_id"],
        target=OpportunityStatus.RESEARCHING,
        actor=ActorKind.LAB,
    )
    service.advance(
        opportunity["opportunity_id"],
        target=OpportunityStatus.DESIGN_READY,
        actor=ActorKind.LAB,
    )
    with pytest.raises(EvolutionError) as excinfo:
        service.advance(
            opportunity["opportunity_id"],
            target=OpportunityStatus.BUILDING,
            actor=ActorKind.LAB,
        )
    assert excinfo.value.details["required_grant"] == str(Grant.WRITE_LAB_WORKSPACE)


# ---------------------------------------------------- evidence-only creation


def test_an_opportunity_with_no_evidence_is_refused(stack) -> None:
    _, service, _ = stack
    for empty in (None, []):
        with pytest.raises(EvolutionError) as excinfo:
            service.create_from_evidence(
                title="Sezgi",
                statement="İçime doğdu.",
                evidence_refs=empty,
                scores=SCORES,
                source="owner",
                source_ref="hunch:1",
            )
        assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR


def test_evidence_that_does_not_exist_is_refused(stack) -> None:
    _, service, _ = stack
    with pytest.raises(EvolutionError) as excinfo:
        service.create_from_evidence(
            title="Hayali kanıt",
            statement="Var olmayan bir olaya dayanıyor.",
            evidence_refs=[{"kind": "ledger_event", "ref": str(uuid.uuid4())}],
            scores=SCORES,
            source="ledger_event",
            source_ref="activity_events:missing",
        )
    assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR


def test_an_unknown_evidence_kind_is_refused() -> None:
    with pytest.raises(EvolutionError):
        parse_evidence([{"kind": "vibes", "ref": "x"}])


def test_a_real_incident_is_accepted_and_marked_verified(stack) -> None:
    session_scope, service, _ = stack
    incident_id = insert_incident(session_scope)
    opportunity = service.create_from_evidence(
        title="Tekrarlayan olayı kökten çöz",
        statement="Aynı bileşen üç kez düştü.",
        evidence_refs=[{"kind": "incident", "ref": incident_id, "note": "3x"}],
        scores=SCORES,
        source="incident",
        source_ref=f"incidents:{incident_id}",
    )
    verification = opportunity["detail"]["evidence_verification"]
    assert verification == [{"kind": "incident", "ref": incident_id, "verified": True}]
    assert opportunity["origin"][0]["note"] == "3x"
    assert opportunity["status"] == str(OpportunityStatus.IDEA)
    assert opportunity["detail"]["evolution_version"] == EVOLUTION_VERSION


def test_a_lesson_ref_is_recorded_as_unverified_while_experience_is_absent(
    stack,
) -> None:
    """`app.experience` is being built by another agent; an unverifiable ref is
    labelled, not silently trusted and not wrongly rejected."""
    _, service, _ = stack
    event_id = insert_ledger_event(_stack_scope(stack))
    opportunity = service.create_from_evidence(
        title="Derse dayalı fırsat",
        statement="Bir dersten ve bir olaydan çıktı.",
        evidence_refs=[
            {"kind": "ledger_event", "ref": event_id},
            {"kind": "lesson", "ref": "lesson-42"},
        ],
        scores=SCORES,
        source="lesson",
        source_ref="lessons:42",
    )
    verification = {
        item["kind"]: item["verified"] for item in opportunity["detail"]["evidence_verification"]
    }
    assert verification["ledger_event"] is True
    assert verification["lesson"] is False


def _stack_scope(stack):
    return stack[0]


def test_creation_is_idempotent_on_source_and_source_ref(stack) -> None:
    first = make_opportunity(stack)
    second = make_opportunity(stack, source_ref=first["source_ref"])
    assert second["opportunity_id"] == first["opportunity_id"]


def test_the_score_and_its_rationale_are_persisted(stack) -> None:
    opportunity = make_opportunity(stack)
    assert opportunity["scores"]["composite"] > 0
    assert opportunity["detail"]["scoring_rationale"]
    assert opportunity["detail"]["risk_vetoed"] is False


# ------------------------------------------------ auditing and the UI stream


def test_every_transition_is_kept_in_the_opportunity_history(stack) -> None:
    opportunity = make_opportunity(stack)
    final = drive_to(stack, opportunity["opportunity_id"], OpportunityStatus.SHADOW_READY)
    history = final["detail"]["transitions"]
    assert [entry["to"] for entry in history] == [
        "researching",
        "design_ready",
        "building",
        "testing",
        "evaluating",
        "shadow_ready",
    ]
    assert {entry["actor"] for entry in history} == {str(ActorKind.LAB)}


def test_reserved_evolution_event_types_are_written_to_the_ledger(stack) -> None:
    session_scope, service, _ = stack
    opportunity = make_opportunity(stack)
    drive_to(stack, opportunity["opportunity_id"], OpportunityStatus.SHADOW_READY)
    service.approve(opportunity["opportunity_id"], mint_owner_capability(FakeSession()))

    with session_scope() as session:
        rows = (
            session.query(ActivityEventRow).filter(ActivityEventRow.subsystem == "evolution").all()
        )
    written = {row.event_type for row in rows}
    assert written == {
        "evolution.idea_created",
        "evolution.module_designed",
        "evolution.build_started",
        "evolution.build_completed",
        "evolution.tests_passed",
        "evolution.shadow_ready",
        "evolution.owner_approval_required",
    }
    for row in rows:
        assert row.factual_summary
        assert row.related_module_id == opportunity["opportunity_id"]
        assert row.evidence_refs  # never fabricated: the origin travels with it


def test_shadow_ready_also_asks_the_owner(stack) -> None:
    session_scope, _, _ = stack
    opportunity = make_opportunity(stack)
    drive_to(stack, opportunity["opportunity_id"], OpportunityStatus.SHADOW_READY)
    with session_scope() as session:
        pending = (
            session.query(ActivityEventRow)
            .filter(
                ActivityEventRow.event_type == "evolution.owner_approval_required",
                ActivityEventRow.status == "pending",
            )
            .all()
        )
    assert len(pending) == 1
    assert pending[0].production_state == "approval_required"


def test_the_ui_stream_follows_the_lab_work(stack) -> None:
    _, _, ui = stack
    opportunity = make_opportunity(stack)
    drive_to(stack, opportunity["opportunity_id"], OpportunityStatus.SHADOW_READY)
    assert [key for key, _ in ui.published] == [
        "evolution.researching",
        "evolution.designing",
        "evolution.building",
        "evolution.testing",
        "evolution.testing",
        "evolution.shadow_ready",
    ]
    assert ui.published[-1][1]["opportunity_id"] == opportunity["opportunity_id"]


def test_pending_owner_actions_lists_what_is_waiting(stack) -> None:
    _, service, _ = stack
    opportunity = make_opportunity(stack)
    assert service.pending_owner_actions()["count"] == 0
    drive_to(stack, opportunity["opportunity_id"], OpportunityStatus.SHADOW_READY)
    pending = service.pending_owner_actions()
    assert pending["count"] == 1
    assert pending["awaiting_approval"][0]["opportunity_id"] == (opportunity["opportunity_id"])
    assert "approve" in pending["action"]


def test_the_backlog_is_ordered_by_composite(stack) -> None:
    session_scope, service, _ = stack
    for index, relevance in enumerate((0.2, 0.9, 0.5)):
        event_id = insert_ledger_event(session_scope)
        service.create_from_evidence(
            title=f"Fırsat {index}",
            statement="Kanıta dayalı.",
            evidence_refs=[{"kind": "ledger_event", "ref": event_id}],
            scores={**SCORES, "owner_relevance": relevance},
            source="ledger_event",
            source_ref=f"activity_events:{event_id}",
        )
    composites = [row["scores"]["composite"] for row in service.list_opportunities()]
    assert composites == sorted(composites, reverse=True)


def test_an_unknown_opportunity_is_not_found(stack) -> None:
    session_scope, service, _ = stack
    backlog = OpportunityBacklog(session_scope)
    with pytest.raises(EvolutionError) as excinfo:
        backlog.get(uuid.uuid4())
    assert excinfo.value.error_class == EvolutionErrorClass.NOT_FOUND
    with pytest.raises(EvolutionError):
        service.get("not-a-uuid")
