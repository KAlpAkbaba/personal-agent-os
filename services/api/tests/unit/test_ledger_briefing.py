"""Unit tests: app.ledger.briefing (M16 track A, M16_ACTIVITY_LEDGER_SPEC.md §4).

Policy classification table, deterministic Turkish speech, and the
pending_briefings queue/deliver/expiry lifecycle — SQLite only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger import briefing as briefing_service
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow, PendingBriefingRow

ALL_TABLES = [ActivityEventRow.__table__, PendingBriefingRow.__table__]

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


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
        event_type="voice.session.created",
        subsystem="voice",
        action="voice_session_created",
        factual_summary="Sesli oturum oluşturuldu.",
        source="live",
        source_ref=f"test:{uuid.uuid4()}",
    )
    base.update(overrides)
    return ledger_service.ActivityEvent(**base)


# ------------------------------------------------------------- classify_policy


def test_classify_policy_critical_severity_is_immediate():
    event = _event(severity="critical", factual_summary="Sunucu erişilemez durumda.")
    assert briefing_service.classify_policy(event) == briefing_service.POLICY_IMMEDIATE


def test_classify_policy_critical_beats_everything_else():
    """severity is checked first (spec §4 table row order): even a
    research.completed event becomes immediate if it is severity=critical."""
    event = _event(
        event_type="research.completed", severity="critical", action="research_completed"
    )
    assert briefing_service.classify_policy(event) == briefing_service.POLICY_IMMEDIATE


def test_classify_policy_research_completed_is_completion():
    event = _event(event_type="research.completed", action="research_completed")
    assert briefing_service.classify_policy(event) == briefing_service.POLICY_COMPLETION


def test_classify_policy_research_failed_is_completion():
    event = _event(event_type="research.failed", action="research_failed", status="failed")
    assert briefing_service.classify_policy(event) == briefing_service.POLICY_COMPLETION


def test_classify_policy_shadow_ready_is_once():
    event = _event(
        event_type="evolution.shadow_ready",
        subsystem="evolution",
        action="shadow_ready",
        production_state="shadow_ready",
    )
    assert briefing_service.classify_policy(event) == briefing_service.POLICY_ONCE


def test_classify_policy_evolution_activity_is_digest():
    event = _event(
        event_type="evolution.build_completed", subsystem="evolution", action="build_completed"
    )
    assert briefing_service.classify_policy(event) == briefing_service.POLICY_DIGEST


def test_classify_policy_deployment_activity_is_digest():
    event = _event(
        event_type="deployment.cloud_core.released",
        subsystem="deployment",
        action="release_promoted",
    )
    assert briefing_service.classify_policy(event) == briefing_service.POLICY_DIGEST


def test_classify_policy_low_value_activity_is_ledger_only():
    event = _event()  # voice.session.created, the fixture default
    assert briefing_service.classify_policy(event) == briefing_service.POLICY_LEDGER_ONLY


# ------------------------------------------------------------------ speech_for


def test_speech_for_research_completed_spells_the_finding_count_in_turkish():
    event = _event(
        event_type="research.completed",
        action="research_completed",
        detail_json={"findings": 5, "sources": 5},
    )
    speech = briefing_service.speech_for(event)
    assert "Beş önemli sonuç çıkardım" in speech
    assert "5" not in speech  # spoken, not written — digits never appear.


def test_speech_for_research_failed_is_deterministic():
    event = _event(event_type="research.failed", action="research_failed", status="failed")
    assert briefing_service.speech_for(event) == (
        "Efendim, bilginize; araştırma başarısız oldu. İsterseniz ayrıntısını anlatabilirim."
    )


def test_speech_for_immediate_includes_the_factual_summary():
    event = _event(severity="critical", factual_summary="Sunucu erişilemez durumda.")
    speech = briefing_service.speech_for(event)
    assert speech == "Efendim, önemli bir durum var. Sunucu erişilemez durumda."


def test_speech_for_once_includes_the_factual_summary():
    event = _event(
        event_type="evolution.shadow_ready",
        subsystem="evolution",
        action="shadow_ready",
        production_state="shadow_ready",
        factual_summary="Yeni modül gölge ortamda hazır.",
    )
    assert briefing_service.speech_for(event) == (
        "Efendim, bilginize; Yeni modül gölge ortamda hazır."
    )


# --------------------------------------------------------- queue/pending/deliver


def test_queue_briefing_is_a_noop_for_ledger_only_events(session):
    row = ledger_service.record(session, _event())  # voice.session.created -> ledger_only
    result = briefing_service.queue_briefing(session, row, NOW)
    assert result is None
    assert session.query(PendingBriefingRow).count() == 0


def test_queue_briefing_queues_a_completion_briefing(session):
    row = ledger_service.record(
        session,
        _event(
            event_type="research.completed",
            action="research_completed",
            detail_json={"findings": 3, "sources": 4},
        ),
    )
    briefing = briefing_service.queue_briefing(session, row, NOW)
    assert briefing is not None
    assert briefing.policy == briefing_service.POLICY_COMPLETION
    assert briefing.priority == 10
    assert briefing.event_ids == [str(row.event_id)]
    assert briefing.delivered_at is None
    assert briefing.expires_at == NOW + timedelta(hours=24)


def test_pending_excludes_delivered_and_expired(session):
    completion_row = ledger_service.record(
        session, _event(event_type="research.completed", action="research_completed")
    )
    immediate_row = ledger_service.record(
        session,
        _event(source_ref="crit-1", severity="critical", factual_summary="Kritik bir durum var."),
    )

    completion = briefing_service.queue_briefing(session, completion_row, NOW)
    immediate = briefing_service.queue_briefing(session, immediate_row, NOW)

    # a third, already-expired briefing (e.g. an old digest) must not appear.
    expired = PendingBriefingRow(
        created_at=NOW - timedelta(days=2),
        policy=briefing_service.POLICY_DIGEST,
        priority=30,
        speech="eski özet",
        event_ids=[],
        expires_at=NOW - timedelta(hours=1),
    )
    session.add(expired)
    session.commit()

    pending = briefing_service.pending(session, NOW)
    ids = [b.briefing_id for b in pending]
    assert expired.briefing_id not in ids
    # immediate (priority 0) sorts before completion (priority 10).
    assert ids == [immediate.briefing_id, completion.briefing_id]

    briefing_service.mark_delivered(session, completion.briefing_id, "say", NOW)
    pending_after = briefing_service.pending(session, NOW)
    assert [b.briefing_id for b in pending_after] == [immediate.briefing_id]


def test_mark_delivered_is_idempotent(session):
    row = ledger_service.record(
        session, _event(event_type="research.completed", action="research_completed")
    )
    briefing = briefing_service.queue_briefing(session, row, NOW)

    first = briefing_service.mark_delivered(session, briefing.briefing_id, "say", NOW)
    assert first.delivered_at == NOW
    assert first.delivered_via == "say"

    later = NOW + timedelta(minutes=5)
    second = briefing_service.mark_delivered(session, briefing.briefing_id, "push", later)
    assert second.delivered_at == NOW  # unchanged — already delivered.
    assert second.delivered_via == "say"


def test_mark_delivered_unknown_briefing_returns_none(session):
    assert briefing_service.mark_delivered(session, uuid.uuid4(), "say", NOW) is None
