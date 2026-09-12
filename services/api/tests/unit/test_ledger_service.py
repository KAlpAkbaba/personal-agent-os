"""Unit tests: app.ledger.service (M16 track A, M16_ACTIVITY_LEDGER_SPEC.md §1).

record idempotency, query filters, latest, count_by_status, vocabulary
validation, and backfill from seeded canonical rows built with the REAL
shapes those tables carry in production (research report stats incl.
rejected_by_reason, audit_events, releases, incidents) — SQLite only,
mirrors tests/unit/test_research_routes.py's ALL_TABLES pattern.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Task
from app.broker.models import AuditEvent
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.ledger.vocabulary import InvalidVocabulary
from app.research.models import (
    STAGE_FAILED,
    STAGE_RANKING,
    STAGE_READY,
    ResearchReportRow,
    ResearchRunRow,
)
from app.selfhealing.models import Incident, Release

ALL_TABLES = [
    Task.__table__,
    ResearchRunRow.__table__,
    ResearchReportRow.__table__,
    AuditEvent.__table__,
    Release.__table__,
    Incident.__table__,
    ActivityEventRow.__table__,
    PendingBriefingRow.__table__,
]

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

#: the real 2026-09-04 owner run's rejection breakdown (task instructions).
REJECTED_BY_REASON = {
    "off_topic": 9,
    "interstitial": 11,
    "date_uncertain": 3,
    "duplicate_event": 1,
    "outside_recency_window": 4,
}


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


def _event(**overrides) -> ledger_service.ActivityEvent:
    base: dict = dict(
        event_type="research.completed",
        subsystem="research",
        action="research_completed",
        factual_summary="Araştırma tamamlandı: 3 bulgu, 3 kaynak.",
        source="live",
        source_ref=f"test:{uuid.uuid4()}",
    )
    base.update(overrides)
    return ledger_service.ActivityEvent(**base)


def _seed_research_completed(session, task_id: uuid.UUID, *, updated_at: datetime) -> None:
    """A research run + report shaped exactly like the real 2026-09-04 owner
    run: five findings, five sources, and a quality-gate rejection breakdown
    (task instructions)."""
    session.add(Task(id=task_id, intent="AI haberleri", status="ready", created_at=updated_at))
    session.add(
        ResearchRunRow(
            task_id=task_id,
            stage=STAGE_READY,
            created_at=updated_at,
            updated_at=updated_at,
            events_json=[
                {
                    "stage": STAGE_RANKING,
                    "at": (updated_at - timedelta(minutes=1)).isoformat(),
                    "detail": "5 evidence ranked, 28 rejected",
                    "rejected": dict(REJECTED_BY_REASON),
                    "rejected_examples": [{"url": "https://example.com/x", "reason": "off_topic"}],
                }
            ],
        )
    )
    findings = [{"id": f"f{i}", "title": f"Bulgu {i}", "summary": "…"} for i in range(5)]
    sources = [{"id": f"s{i}", "url": f"https://example.com/{i}"} for i in range(5)]
    report_json = {
        "task_id": str(task_id),
        "topic": "AI haberleri",
        "findings": findings,
        "sources": sources,
        "stats": {
            "queries": 3,
            "discovered": 240,
            "fetched": 33,
            "fetch_failed": 2,
            "deduplicated": 0,
            "evidence": 5,
            "rejected": 28,
            "rejected_by_reason": dict(REJECTED_BY_REASON),
        },
    }
    session.add(
        ResearchReportRow(
            task_id=task_id,
            report_json=report_json,
            synthesis_provider="deterministic",
            created_at=updated_at,
            updated_at=updated_at,
        )
    )
    session.commit()


# ------------------------------------------------------------------- record


def test_record_is_idempotent_on_source_and_source_ref(session):
    event = _event(source_ref="research_runs:abc:ready")
    row1 = ledger_service.record(session, event)
    row2 = ledger_service.record(session, event)
    assert row1.event_id == row2.event_id
    assert session.query(ActivityEventRow).count() == 1


def test_record_returns_new_row_for_a_different_source_ref(session):
    row1 = ledger_service.record(session, _event(source_ref="a"))
    row2 = ledger_service.record(session, _event(source_ref="b"))
    assert row1.event_id != row2.event_id
    assert session.query(ActivityEventRow).count() == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_type", "not.a.real.type"),
        ("subsystem", "not_a_subsystem"),
        ("status", "not_a_status"),
        ("severity", "not_a_severity"),
        ("production_state", "not_a_state"),
    ],
)
def test_record_rejects_unknown_vocabulary(session, field, value):
    with pytest.raises(InvalidVocabulary):
        ledger_service.record(session, _event(**{field: value}))


def test_record_accepts_a_dynamic_deployment_event_type(session):
    row = ledger_service.record(
        session,
        _event(
            event_type="deployment.windows_agent.released",
            subsystem="deployment",
            action="release_promoted",
            source_ref="releases:1:released",
        ),
    )
    assert row.event_type == "deployment.windows_agent.released"


# -------------------------------------------------------------- query/latest


def test_query_filters_by_subsystem_and_orders_newest_first(session):
    old = ledger_service.record(
        session, _event(source_ref="a", occurred_at=NOW - timedelta(hours=2))
    )
    new = ledger_service.record(session, _event(source_ref="b", occurred_at=NOW))
    ledger_service.record(
        session,
        _event(
            source_ref="c",
            occurred_at=NOW,
            subsystem="voice",
            event_type="voice.session.created",
            action="voice_session_created",
            factual_summary="Sesli oturum oluşturuldu.",
        ),
    )

    rows = ledger_service.query(session, subsystems=["research"])
    assert [r.event_id for r in rows] == [new.event_id, old.event_id]


def test_query_filters_by_since_and_event_type(session):
    ledger_service.record(session, _event(source_ref="a", occurred_at=NOW - timedelta(hours=2)))
    recent = ledger_service.record(session, _event(source_ref="b", occurred_at=NOW))

    rows = ledger_service.query(session, since=NOW - timedelta(hours=1))
    assert [r.event_id for r in rows] == [recent.event_id]

    rows = ledger_service.query(session, event_types=["research.completed"])
    assert len(rows) == 2


def test_query_filters_by_research_job_id(session):
    tid = uuid.uuid4()
    mine = ledger_service.record(
        session, _event(source_ref="mine", occurred_at=NOW, research_job_id=tid)
    )
    ledger_service.record(
        session, _event(source_ref="other", occurred_at=NOW, research_job_id=uuid.uuid4())
    )
    rows = ledger_service.query(session, research_job_id=tid)
    assert [r.event_id for r in rows] == [mine.event_id]


def test_query_limit_is_bounded(session):
    for i in range(5):
        ledger_service.record(
            session, _event(source_ref=f"cap-{i}", occurred_at=NOW - timedelta(minutes=i))
        )
    assert len(ledger_service.query(session, limit=2)) == 2
    # a limit above the ceiling is clamped, never an error.
    assert len(ledger_service.query(session, limit=10_000)) == 5


def test_latest_defaults_to_completed_and_failed(session):
    ledger_service.record(session, _event(source_ref="l1", occurred_at=NOW - timedelta(minutes=5)))
    failed = ledger_service.record(
        session,
        _event(
            source_ref="l2",
            status="failed",
            event_type="research.failed",
            action="research_failed",
            factual_summary="Araştırma başarısız oldu: timeout.",
            occurred_at=NOW,
        ),
    )
    row = ledger_service.latest(session)
    assert row.event_id == failed.event_id


def test_latest_filters_by_subsystem(session):
    ledger_service.record(session, _event(source_ref="r1", occurred_at=NOW))
    voice = ledger_service.record(
        session,
        _event(
            source_ref="v1",
            occurred_at=NOW + timedelta(seconds=1),
            subsystem="voice",
            event_type="voice.session.closed",
            action="voice_session_closed",
            factual_summary="Sesli oturum kapandı.",
        ),
    )
    row = ledger_service.latest(session, subsystems=["voice"])
    assert row.event_id == voice.event_id


def test_count_by_status(session):
    ledger_service.record(session, _event(source_ref="s1", occurred_at=NOW))
    ledger_service.record(
        session,
        _event(
            source_ref="s2",
            status="failed",
            event_type="research.failed",
            action="research_failed",
            factual_summary="Araştırma başarısız oldu: timeout.",
            occurred_at=NOW,
        ),
    )
    assert ledger_service.count_by_status(session, NOW - timedelta(hours=1)) == {
        "completed": 1,
        "failed": 1,
    }


# ---------------------------------------------------------------- backfill


def test_backfill_creates_research_completed_with_real_report_shape(session):
    task_id = uuid.uuid4()
    updated_at = NOW - timedelta(minutes=10)
    _seed_research_completed(session, task_id, updated_at=updated_at)

    report = ledger_service.backfill(session, now=NOW)
    assert report.created.get("research_completed") == 1
    assert report.created.get("research_quality_gate") == 1

    row = session.execute(
        select(ActivityEventRow).where(
            ActivityEventRow.event_type == "research.completed",
            ActivityEventRow.research_job_id == task_id,
        )
    ).scalar_one()
    assert row.source == "backfill:research_runs"
    assert row.source_ref == f"research_runs:{task_id}:ready"
    assert row.detail_json["findings"] == 5
    assert row.detail_json["sources"] == 5
    assert row.detail_json["rejected_by_reason"] == REJECTED_BY_REASON
    assert row.factual_summary == "Araştırma tamamlandı: 5 bulgu, 5 kaynak; 28 sayfa elendi."
    assert {"kind": "research_report", "ref": str(task_id)} in row.evidence_refs

    gate_row = session.execute(
        select(ActivityEventRow).where(ActivityEventRow.event_type == "research.quality_gate")
    ).scalar_one()
    assert gate_row.detail_json["rejected"] == REJECTED_BY_REASON
    assert gate_row.factual_summary == "Kalite kapısı 28 sayfayı eledi."


def test_backfill_rerun_records_nothing_new(session):
    task_id = uuid.uuid4()
    _seed_research_completed(session, task_id, updated_at=NOW - timedelta(minutes=10))

    first = ledger_service.backfill(session, now=NOW)
    assert first.total_created > 0

    second = ledger_service.backfill(session, now=NOW + timedelta(minutes=1))
    assert second.created == {}
    assert second.skipped.get("research_completed") == 1
    assert second.skipped.get("research_quality_gate") == 1

    # exactly one research.completed row exists, never two.
    count = (
        session.execute(
            select(ActivityEventRow).where(ActivityEventRow.event_type == "research.completed")
        )
        .scalars()
        .all()
    )
    assert len(count) == 1


def test_backfill_creates_research_failed_with_error_class(session):
    task_id = uuid.uuid4()
    updated_at = NOW - timedelta(minutes=5)
    session.add(
        Task(
            id=task_id,
            intent="konu",
            status="failed",
            error_class="dependency_unavailable",
            error_message="boom",
            created_at=updated_at,
        )
    )
    session.add(
        ResearchRunRow(
            task_id=task_id,
            stage=STAGE_FAILED,
            error="boom",
            created_at=updated_at,
            updated_at=updated_at,
        )
    )
    session.commit()

    report = ledger_service.backfill(session, now=NOW)
    assert report.created.get("research_failed") == 1
    row = session.execute(
        select(ActivityEventRow).where(ActivityEventRow.event_type == "research.failed")
    ).scalar_one()
    assert row.detail_json["error_class"] == "dependency_unavailable"
    assert row.factual_summary == "Araştırma başarısız oldu: dependency_unavailable."
    assert row.status == "failed"


def test_backfill_skips_research_completed_already_recorded_live(session):
    """A run the LIVE writer already covered (spec §1.2) must not become a
    second, backfill-sourced duplicate of the same fact."""
    task_id = uuid.uuid4()
    updated_at = NOW - timedelta(minutes=10)
    _seed_research_completed(session, task_id, updated_at=updated_at)
    ledger_service.record(
        session,
        ledger_service.build_research_completed_event(
            task_id=task_id,
            occurred_at=updated_at,
            report_json=session.execute(
                select(ResearchReportRow).where(ResearchReportRow.task_id == task_id)
            )
            .scalar_one()
            .report_json,
            source="live",
            source_ref=f"research_runs:{task_id}:ready",
        ),
    )

    report = ledger_service.backfill(session, now=NOW)
    assert report.created.get("research_completed") is None
    assert report.skipped.get("research_completed") == 1
    rows = (
        session.execute(
            select(ActivityEventRow).where(ActivityEventRow.event_type == "research.completed")
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].source == "live"


def test_backfill_creates_voice_session_events_from_audit_events(session):
    session.add(
        AuditEvent(
            category="voice_realtime",
            action="voice_session_created",
            subject_ref="sess-1",
            created_at=NOW - timedelta(minutes=3),
        )
    )
    session.commit()

    report = ledger_service.backfill(session, now=NOW)
    assert report.created.get("voice_session") == 1
    row = session.execute(
        select(ActivityEventRow).where(ActivityEventRow.event_type == "voice.session.created")
    ).scalar_one()
    assert row.factual_summary == "Sesli oturum oluşturuldu."
    assert row.source == "backfill:audit_events"


def test_backfill_skips_a_voice_session_already_recorded_live(session):
    """B06 req 70. The live writer and the backfill describe one session's creation under
    two different ``source`` values, so uniqueness on (source, source_ref) cannot see they
    are the same fact — research has been guarded against this since M16 and voice was not,
    which put TWO rows in the ledger for every session created since the live writer
    shipped."""
    session_id = uuid.uuid4()
    ledger_service.record(
        session,
        ledger_service.ActivityEvent(
            event_type="voice.session.created",
            subsystem="voice",
            action="voice_session_created",
            factual_summary="Sesli oturum oluşturuldu.",
            occurred_at=NOW - timedelta(minutes=3),
            source="live",
            source_ref=ledger_service.voice_session_source_ref(session_id, "created"),
        ),
    )
    session.add(
        AuditEvent(
            category="voice_realtime",
            action="voice_session_created",
            subject_ref=str(session_id),
            created_at=NOW - timedelta(minutes=3),
        )
    )
    session.commit()

    report = ledger_service.backfill(session, now=NOW)

    assert report.created.get("voice_session") is None
    assert report.skipped.get("voice_session") == 1
    rows = (
        session.execute(
            select(ActivityEventRow).where(ActivityEventRow.event_type == "voice.session.created")
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].source == "live"


def test_backfill_writes_one_row_for_a_session_attached_three_times(session):
    """The live writer collapses every attach of a session into the one row its key names,
    so the backfill has to mean the same thing by its own row — otherwise the two halves
    disagree about how many times a session was attached."""
    session_id = uuid.uuid4()
    for minutes in (30, 20, 10):
        session.add(
            AuditEvent(
                category="voice_realtime",
                action="voice_session_attached",
                subject_ref=str(session_id),
                created_at=NOW - timedelta(minutes=minutes),
            )
        )
    session.commit()

    report = ledger_service.backfill(session, now=NOW)

    assert report.created.get("voice_session") == 1
    assert report.skipped.get("voice_session") == 2
    row = session.execute(
        select(ActivityEventRow).where(ActivityEventRow.event_type == "voice.session.attached")
    ).scalar_one()
    # the OLDEST of the three, so a re-run keeps choosing the same one
    assert row.occurred_at.replace(tzinfo=UTC) == NOW - timedelta(minutes=30)

    ledger_service.backfill(session, now=NOW + timedelta(minutes=1))
    assert (
        len(
            session.execute(
                select(ActivityEventRow).where(
                    ActivityEventRow.event_type == "voice.session.attached"
                )
            )
            .scalars()
            .all()
        )
        == 1
    )


def test_backfill_still_covers_a_session_no_live_writer_ever_saw(session):
    """The guard must not swallow the case the backfill exists for: a session from before
    the live writer shipped has audit rows and no ledger row, and still gets one."""
    session_id = uuid.uuid4()
    session.add(
        AuditEvent(
            category="voice_realtime",
            action="voice_session_closed",
            subject_ref=str(session_id),
            created_at=NOW - timedelta(days=30),
        )
    )
    session.commit()

    report = ledger_service.backfill(session, now=NOW)

    assert report.created.get("voice_session") == 1
    row = session.execute(
        select(ActivityEventRow).where(ActivityEventRow.event_type == "voice.session.closed")
    ).scalar_one()
    assert row.source == "backfill:audit_events"


def test_one_sessions_states_are_separate_facts(session):
    """Dedup is per (session, state): created and closed are two things that happened."""
    session_id = uuid.uuid4()
    for action, minutes in (("voice_session_created", 40), ("voice_session_closed", 5)):
        session.add(
            AuditEvent(
                category="voice_realtime",
                action=action,
                subject_ref=str(session_id),
                created_at=NOW - timedelta(minutes=minutes),
            )
        )
    session.commit()

    report = ledger_service.backfill(session, now=NOW)

    assert report.created.get("voice_session") == 2


def test_backfill_creates_deployment_events_from_releases(session):
    rel_id = uuid.uuid4()
    session.add(
        Release(
            id=rel_id,
            component="cloud_core",
            version="1.2.3",
            manifest_digest="sha256:abc",
            status="active",
            promoted_at=NOW - timedelta(hours=1),
        )
    )
    session.commit()

    report = ledger_service.backfill(session, now=NOW)
    assert report.created.get("deployment") == 1
    row = session.execute(
        select(ActivityEventRow).where(
            ActivityEventRow.event_type == "deployment.cloud_core.released"
        )
    ).scalar_one()
    assert row.version == "1.2.3"
    assert row.production_state == "deployed"
    assert {"kind": "release", "ref": str(rel_id)} in row.evidence_refs


def test_backfill_spells_any_release_component_and_keeps_its_name(session):
    """Found on the real dev database: a release component with hyphens and a hash
    (``browser-agent-demo-4326f3af``) made the whole backfill raise. The component is
    slugged into the event type, kept verbatim on ``module``, and the run continues."""
    session.add(
        Release(
            id=uuid.uuid4(),
            component="browser-agent-demo-4326f3af",
            version="0.1.0",
            manifest_digest="sha256:def",
            status="rolled_back",
            promoted_at=NOW - timedelta(hours=3),
            rolled_back_at=NOW - timedelta(hours=2),
        )
    )
    session.commit()

    report = ledger_service.backfill(session, now=NOW)
    assert report.created.get("deployment") == 2
    types = {
        r.event_type
        for r in session.execute(select(ActivityEventRow)).scalars()
        if r.subsystem == "deployment"
    }
    assert types == {
        "deployment.browser_agent_demo_4326f3af.released",
        "deployment.browser_agent_demo_4326f3af.rolled_back",
    }
    row = session.execute(
        select(ActivityEventRow).where(
            ActivityEventRow.event_type == "deployment.browser_agent_demo_4326f3af.rolled_back"
        )
    ).scalar_one()
    assert row.module == "browser-agent-demo-4326f3af"
    assert ledger_service.component_slug("Cloud Core / api") == "cloud_core_api"


def test_backfill_creates_incident_opened(session):
    inc_id = uuid.uuid4()
    session.add(
        Incident(
            id=inc_id,
            component="research",
            severity="critical",
            fingerprint="fp-1",
            first_seen_at=NOW - timedelta(hours=2),
        )
    )
    session.commit()

    report = ledger_service.backfill(session, now=NOW)
    assert report.created.get("incident") == 1
    row = session.execute(
        select(ActivityEventRow).where(ActivityEventRow.event_type == "incident.opened")
    ).scalar_one()
    assert row.severity == "critical"
    assert {"kind": "incident", "ref": str(inc_id)} in row.evidence_refs


def test_backfill_writes_its_own_ledger_backfill_event_every_run(session):
    """Unlike the source-derived categories, the backfill-ran-fact itself is
    always new (spec §1.2 writer table) — this is NOT what "zero new" means
    for a re-run; that guarantee is about the source tables, asserted above."""
    ledger_service.backfill(session, now=NOW)
    ledger_service.backfill(session, now=NOW + timedelta(seconds=1))
    rows = (
        session.execute(
            select(ActivityEventRow).where(ActivityEventRow.event_type == "ledger.backfill")
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
