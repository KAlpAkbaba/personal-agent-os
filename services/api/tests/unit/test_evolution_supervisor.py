"""Unit tests: the Evolution Supervisor (M18.4 spec §3) against a real EvolutionService,
a real ledger and real incident rows on SQLite. Nothing is mocked except time."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.evolution import supervisor as sup
from app.evolution.authority import Scope
from app.evolution.models import Capability, CapabilityGap, EvolutionOpportunity, SkillVersion
from app.evolution.service import EvolutionService
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_ACTION_RECEIPT,
    EVENT_TYPE_EVOLUTION_PAUSED,
    EVENT_TYPE_EVOLUTION_RESUMED,
    EVENT_TYPE_EVOLUTION_SUPERVISOR_SCANNED,
    EVENT_TYPES,
    SUBSYSTEM_AMBIENT,
)
from app.selfhealing.models import Incident, Release

NOW = datetime(2026, 9, 8, 3, 0, tzinfo=UTC)

TABLES = [
    ActivityEventRow.__table__,
    Capability.__table__,
    SkillVersion.__table__,
    CapabilityGap.__table__,
    EvolutionOpportunity.__table__,
    Release.__table__,
    Incident.__table__,
]


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory


@pytest.fixture()
def service(db):
    return EvolutionService(db)


def _incident(session, *, component="cloud-core", status="recovered", count=1) -> dict:
    row = Incident(
        component=component,
        severity="critical",
        fingerprint=f"fp-{uuid.uuid4().hex[:8]}",
        evidence_json={"error_class": "selftest_failed", "failing_check": "selftest"},
        status=status,
        occurrence_count=count,
        first_seen_at=NOW - timedelta(hours=2),
        last_seen_at=NOW - timedelta(minutes=5),
    )
    session.add(row)
    session.commit()
    return {
        "id": str(row.id),
        "component": component,
        "status": status,
        "evidence": row.evidence_json,
        "occurrence_count": count,
        "first_seen_at": row.first_seen_at.isoformat(),
        "last_seen_at": row.last_seen_at.isoformat(),
        "fixed_release_id": None,
    }


def _failed_receipt(session, *, capability: str, error_class: str, at: datetime) -> None:
    ledger_service.record(
        session,
        ledger_service.ActivityEvent(
            event_type=EVENT_TYPE_ACTION_RECEIPT,
            subsystem=SUBSYSTEM_AMBIENT,
            action=capability,
            status="failed",
            factual_summary=f"{capability} failed: {error_class}",
            occurred_at=at,
            detail_json={
                "capability": capability,
                "execution_status": "failed",
                "error_class": error_class,
                "action_id": uuid.uuid4().hex,
            },
            source="test",
            source_ref=f"receipt:{uuid.uuid4().hex}",
        ),
    )
    session.commit()


def _failed_research(session, *, error_class: str, at: datetime) -> None:
    ledger_service.record(
        session,
        ledger_service.build_research_failed_event(
            task_id=uuid.uuid4(), occurred_at=at, error_class=error_class, error="boom"
        ),
    )
    session.commit()


def test_the_events_are_part_of_the_ledger_vocabulary() -> None:
    assert EVENT_TYPE_EVOLUTION_PAUSED in EVENT_TYPES
    assert EVENT_TYPE_EVOLUTION_RESUMED in EVENT_TYPES
    assert EVENT_TYPE_EVOLUTION_SUPERVISOR_SCANNED in EVENT_TYPES


def test_promotion_class_is_derived_from_the_risk_table_never_chosen() -> None:
    assert sup.promotion_class_for(("docs/x.md",))[0] == sup.PROMOTION_AUTO_SAFE
    assert sup.promotion_class_for(("apps/web/app/x.tsx",))[0] == sup.PROMOTION_AUTO_SAFE
    assert (
        sup.promotion_class_for(("services/api/tests/unit/t.py",))[0] == sup.PROMOTION_AUTO_CANARY
    )
    assert (
        sup.promotion_class_for(("services/api/app/voice/intents.py",))[0]
        == sup.PROMOTION_OWNER_APPROVAL_REQUIRED
    )
    assert (
        sup.promotion_class_for(("services/api/app/evolution/authority.py",))[0]
        == sup.PROMOTION_NEVER_AUTO_PROMOTE
    )
    assert (
        sup.promotion_class_for(("services/recovery-supervisor/recovery_supervisor/runner.py",))[0]
        == sup.PROMOTION_NEVER_AUTO_PROMOTE
    )
    assert sup.component_for_capability("alarm.stop") == "alarms"
    assert sup.component_for_capability("display.off") == "ambient"
    assert sup.component_for_capability("eye.disable") == "presence"


def test_an_incident_becomes_one_p0_opportunity_with_verified_evidence(db, service) -> None:
    with db() as session:
        incident = _incident(session, status="recovered", count=3)
    supervisor = sup.EvolutionSupervisor(incidents=lambda **_: [incident], interval_s=60)
    with db() as session:
        result = supervisor.scan(session, now=NOW, evolution_service=service)
        assert result.status == "scanned"
        assert result.signals == 1 and len(result.opened) == 1
        opened = result.opened[0]
        assert opened["source"] == "incident" and opened["source_ref"] == incident["id"]
        assert opened["detail"]["priority"] == sup.PRIORITY_P0
        assert opened["detail"]["promotion_class"] == sup.PROMOTION_OWNER_APPROVAL_REQUIRED
        assert opened["detail"]["risk_tier"] == 3
        assert opened["detail"]["evidence_verification"][0]["verified"] is True
        assert opened["scores"]["recurrence"] == pytest.approx(0.7)
        # A second scan, the same incident: tracked, not duplicated - and no scan row.
        again = supervisor.scan(session, now=NOW + timedelta(minutes=2), evolution_service=service)
        assert again.status == "scanned" and again.opened == [] and again.already_tracked == 1
        assert len(service.list_opportunities()) == 1
        scans = (
            session.execute(
                select(ActivityEventRow).where(
                    ActivityEventRow.event_type == EVENT_TYPE_EVOLUTION_SUPERVISOR_SCANNED
                )
            )
            .scalars()
            .all()
        )
        assert len(scans) == 1
        assert scans[0].detail_json["opened"] == [opened["opportunity_id"]]


def test_recurring_failed_receipts_are_a_p1_signal_and_a_single_failure_is_not(db, service) -> None:
    with db() as session:
        _failed_receipt(
            session,
            capability="display.off",
            error_class="device_offline",
            at=NOW - timedelta(days=1),
        )
        _failed_receipt(
            session,
            capability="display.off",
            error_class="device_offline",
            at=NOW - timedelta(hours=1),
        )
        _failed_receipt(
            session, capability="alarm.stop", error_class="not_ringing", at=NOW - timedelta(hours=1)
        )
        # An old pair outside the window does not count.
        _failed_receipt(
            session,
            capability="eye.enable",
            error_class="camera_missing",
            at=NOW - timedelta(days=20),
        )
        _failed_receipt(
            session,
            capability="eye.enable",
            error_class="camera_missing",
            at=NOW - timedelta(days=21),
        )
        signals = sup.collect_signals(session, now=NOW)
    assert [(s.kind, s.priority, s.component) for s in signals] == [
        (sup.SIGNAL_ACTION_FAILURE, sup.PRIORITY_P1, "ambient")
    ]
    assert signals[0].source_ref == "action_failed:display.off:device_offline"
    assert signals[0].occurrences == 2
    with db() as session:
        result = sup.EvolutionSupervisor().scan(session, now=NOW, evolution_service=service)
    assert [o["title"] for o in result.opened] == [
        "Tekrarlayan eylem hatası: display.off (device_offline)"
    ]


def test_recurring_research_failures_are_a_p2_signal(db, service) -> None:
    with db() as session:
        _failed_research(
            session, error_class="insufficient_valid_findings", at=NOW - timedelta(days=2)
        )
        _failed_research(
            session, error_class="insufficient_valid_findings", at=NOW - timedelta(days=1)
        )
        _failed_research(session, error_class="budget_exhausted", at=NOW - timedelta(days=1))
        signals = sup.collect_signals(session, now=NOW)
    assert [(s.kind, s.priority, s.source_ref) for s in signals] == [
        (
            sup.SIGNAL_RESEARCH_FAILURE,
            sup.PRIORITY_P2,
            "research_failed:insufficient_valid_findings",
        )
    ]
    assert len(signals[0].evidence_refs) == 2


def test_an_open_generation_gap_is_a_p3_signal_owned_by_the_pipeline() -> None:
    gaps = [
        {
            "id": str(uuid.uuid4()),
            "status": "open",
            "resolution": "generation",
            "requested_capability": "text.slugify",
        },
        {
            "id": str(uuid.uuid4()),
            "status": "open",
            "resolution": "composition",
            "requested_capability": "x",
        },
        {
            "id": str(uuid.uuid4()),
            "status": "resolved",
            "resolution": "generation",
            "requested_capability": "y",
        },
    ]
    signals = sup.signals_from_gaps(gaps)
    assert len(signals) == 1
    assert signals[0].priority == sup.PRIORITY_P3
    assert sup.promotion_class_for(signals[0].paths)[0] == sup.PROMOTION_AUTO_CANARY


def test_the_pause_switch_is_a_ledger_row_and_a_paused_scan_opens_nothing(db, service) -> None:
    with db() as session:
        incident = _incident(session)
        assert sup.is_paused(session) is False
        sup.set_paused(session, paused=True, actor="voice", reason="owner said so", now=NOW)
        assert sup.is_paused(session) is True
    supervisor = sup.EvolutionSupervisor(incidents=lambda **_: [incident], interval_s=60)
    with db() as session:
        result = supervisor.scan(session, now=NOW + timedelta(seconds=1), evolution_service=service)
        assert result.status == "skipped_paused"
        assert service.list_opportunities() == []
        sup.set_paused(session, paused=False, actor="voice", now=NOW + timedelta(seconds=2))
        assert sup.is_paused(session) is False
        resumed = supervisor.scan(
            session, now=NOW + timedelta(seconds=70), evolution_service=service, force=True
        )
        assert resumed.status == "scanned" and len(resumed.opened) == 1


def test_the_scan_respects_its_interval_and_the_disabled_switch(db, service) -> None:
    supervisor = sup.EvolutionSupervisor(interval_s=300)
    with db() as session:
        first = supervisor.scan(session, now=NOW, evolution_service=service)
        assert first.status == "scanned"
        assert (
            supervisor.scan(
                session, now=NOW + timedelta(seconds=10), evolution_service=service
            ).status
            == "skipped_not_due"
        )
        assert (
            supervisor.scan(
                session, now=NOW + timedelta(seconds=300), evolution_service=service
            ).status
            == "scanned"
        )
        assert supervisor.scans == 2
    disabled = sup.EvolutionSupervisor(enabled=False)
    with db() as session:
        assert (
            disabled.scan(session, now=NOW, evolution_service=service).status == "skipped_disabled"
        )
    health = supervisor.health()
    assert health["scans"] == 2 and health["last_status"] == "scanned"


def test_a_failing_source_is_a_fact_not_a_crash(db, service) -> None:
    def broken(**_):
        raise RuntimeError("incident store down")

    supervisor = sup.EvolutionSupervisor(incidents=broken)
    with db() as session:
        result = supervisor.scan(session, now=NOW, evolution_service=service)
    assert result.status == "scanned"
    assert supervisor.last_error is not None and "incident store down" in supervisor.last_error


def test_the_supervisor_acts_with_lab_authority_only(service) -> None:
    """Structural: the service it is handed holds the engine's LAB scope and zero production
    grants; the supervisor calls create_from_evidence only (never advance)."""
    assert service.authority.scope is Scope.LAB
    import inspect

    source = inspect.getsource(sup)
    assert ".advance(" not in source
    assert ".approve(" not in source and ".authorize(" not in source
    assert "ProductionAuthority" not in source


def test_status_reads_the_rows_alone(db, service) -> None:
    with db() as session:
        incident = _incident(session, status="recovered")
        fixed = _incident(session, status="fixed")
        fixed["fixed_release_id"] = str(uuid.uuid4())

    def incidents(status=None, limit=200):
        rows = [incident, fixed]
        return [r for r in rows if status is None or r["status"] == status]

    supervisor = sup.EvolutionSupervisor(incidents=incidents)
    with db() as session:
        supervisor.scan(session, now=NOW, evolution_service=service)
        status = supervisor.status(
            session, now=NOW, evolution_service=service, release={"version": "65459a4"}
        )
    assert status["paused"] is False
    assert status["open_by_priority"] == {"P0": 1, "P1": 0, "P2": 0, "P3": 0}
    assert status["building"] == [] and status["pending_candidates"] == []
    assert status["last_fix"]["incident_id"] == fixed["id"]
    assert status["last_fix"]["fixed_release_id"] == fixed["fixed_release_id"]
    assert status["running"] == {"version": "65459a4"}
    assert status["last_scan"]["status"] == "scanned"
