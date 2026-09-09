"""Unit tests: app.routines.service.

Covers: creation is idempotent and arms immediately with two ledger events, cancellation
(including its own idempotency and the illegal-transition-on-a-resolved-routine case), a
condition failing with a recorded reason, a recurring schedule routine firing more than
once across days while staying armed, a presence trigger firing from the app.uistate tail,
idempotent firing (never twice for the same occurrence, even across two evaluate_due
calls), and that a media action's owner-chosen url/title survives a full
create -> evaluate -> read round trip unchanged.

SQLite only, mirrors tests/unit/test_goals_service.py's fixture pattern.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_ROUTINE_ARMED,
    EVENT_TYPE_ROUTINE_CANCELLED,
    EVENT_TYPE_ROUTINE_CREATED,
    EVENT_TYPE_ROUTINE_EXECUTED,
    EVENT_TYPE_ROUTINE_SKIPPED,
    EVENT_TYPE_ROUTINE_TRIGGERED,
)
from app.routines import service as routines_service
from app.routines.conditions import RoutineConditionContext
from app.routines.models import (
    FIRING_STATUS_SKIPPED,
    FIRING_STATUS_TRIGGERED,
    ROUTINE_STATUS_ARMED,
    ROUTINE_STATUS_CANCELLED,
    ROUTINE_STATUS_COMPLETED,
    Routine,
    RoutineFiring,
)
from app.routines.state import IllegalRoutineTransition
from app.uistate.contract import UiState, UiStateEvent
from app.uistate.publisher import UiStatePublisher, set_publisher

ALL_TABLES = [Routine.__table__, RoutineFiring.__table__, ActivityEventRow.__table__]


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


@pytest.fixture(autouse=True)
def _fresh_uistate_publisher():
    """app.uistate.publisher is a process-wide singleton (tests/unit/test_uistate.py's own
    pattern) — swap in a fresh one so this file's presence-trigger tests never see events
    left over from another test."""
    set_publisher(UiStatePublisher())
    yield
    set_publisher(UiStatePublisher())


def _ledger_event_types(session, routine: Routine) -> list[str]:
    rows = session.execute(
        select(ActivityEventRow).where(
            ActivityEventRow.related_module_id == f"routine:{routine.routine_id}"
        )
    ).scalars().all()
    return [r.event_type for r in rows]


# ------------------------------------------------------------------------------ creation


def test_create_routine_arms_immediately_with_created_and_armed_events(session) -> None:
    routine = routines_service.create_routine(
        session,
        name="Sabah rutini",
        trigger_kind="at",
        trigger={"at": "2026-09-05T07:30:00Z"},
    )
    assert routine.status == ROUTINE_STATUS_ARMED
    assert routine.armed_at is not None
    events = _ledger_event_types(session, routine)
    assert EVENT_TYPE_ROUTINE_CREATED in events
    assert EVENT_TYPE_ROUTINE_ARMED in events


def test_create_routine_is_idempotent_on_source_ref(session) -> None:
    first = routines_service.create_routine(
        session,
        name="Sabah rutini",
        trigger_kind="at",
        trigger={"at": "2026-09-05T07:30:00Z"},
        source="owner",
        source_ref="fixed-ref",
    )
    second = routines_service.create_routine(
        session,
        name="different name",
        trigger_kind="at",
        trigger={"at": "2026-09-05T07:30:00Z"},
        source="owner",
        source_ref="fixed-ref",
    )
    assert first.routine_id == second.routine_id
    assert second.name == "Sabah rutini"  # unchanged: the second call was a no-op


# --------------------------------------------------------------------------- cancellation


def test_cancel_routine_records_ledger_event(session) -> None:
    routine = routines_service.create_routine(
        session, name="X", trigger_kind="at", trigger={"at": "2026-09-05T07:30:00Z"}
    )
    cancelled = routines_service.cancel_routine(
        session, routine.routine_id, reason="artık gerekmiyor"
    )
    assert cancelled.status == ROUTINE_STATUS_CANCELLED
    assert cancelled.cancel_reason == "artık gerekmiyor"
    assert EVENT_TYPE_ROUTINE_CANCELLED in _ledger_event_types(session, routine)


def test_cancel_routine_twice_is_idempotent(session) -> None:
    routine = routines_service.create_routine(
        session, name="X", trigger_kind="at", trigger={"at": "2026-09-05T07:30:00Z"}
    )
    routines_service.cancel_routine(session, routine.routine_id)
    again = routines_service.cancel_routine(session, routine.routine_id)
    assert again.status == ROUTINE_STATUS_CANCELLED


def test_cancel_unknown_routine_raises(session) -> None:
    with pytest.raises(routines_service.RoutineNotFoundError):
        routines_service.cancel_routine(session, uuid.uuid4())


def test_cancel_a_completed_routine_is_illegal(session) -> None:
    routine = routines_service.create_routine(
        session, name="X", trigger_kind="at", trigger={"at": "2026-09-05T07:30:00Z"}
    )
    routines_service.evaluate_due(session, now=datetime(2026, 9, 5, 8, 0, tzinfo=UTC))
    refreshed = routines_service.get_routine(session, routine.routine_id)
    assert refreshed.status == ROUTINE_STATUS_COMPLETED
    with pytest.raises(IllegalRoutineTransition):
        routines_service.cancel_routine(session, routine.routine_id)


# ------------------------------------------------------------------------- at-trigger


def test_evaluate_due_at_trigger_fires_once_and_never_twice(session) -> None:
    """Idempotent firing (task brief): a second evaluate_due call for the SAME occurrence
    must not create a second RoutineFiring row or a second routine.triggered event."""
    routine = routines_service.create_routine(
        session, name="X", trigger_kind="at", trigger={"at": "2026-09-05T07:30:00Z"}
    )
    now = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)

    first = routines_service.evaluate_due(session, now=now)
    assert first.outcomes[0].status == FIRING_STATUS_TRIGGERED

    second = routines_service.evaluate_due(session, now=now)
    assert second.checked == 0  # the routine is now completed, no longer armed
    assert second.outcomes == ()

    firings = routines_service.list_firings(session, routine.routine_id)
    assert len(firings) == 1
    triggered_events = [
        e for e in _ledger_event_types(session, routine) if e == EVENT_TYPE_ROUTINE_TRIGGERED
    ]
    assert len(triggered_events) == 1


def test_evaluate_due_at_trigger_not_due_yet_is_not_checked_as_fired(session) -> None:
    routines_service.create_routine(
        session, name="X", trigger_kind="at", trigger={"at": "2026-09-05T07:30:00Z"}
    )
    result = routines_service.evaluate_due(session, now=datetime(2026, 9, 5, 7, 0, tzinfo=UTC))
    assert result.outcomes == ()


# ---------------------------------------------------------------------- conditions/skip


def test_evaluate_due_skips_when_condition_fails_and_records_reason(session) -> None:
    routine = routines_service.create_routine(
        session,
        name="X",
        trigger_kind="at",
        trigger={"at": "2026-09-05T07:30:00Z"},
        conditions=[{"kind": "owner_present", "detail": {}}],
    )
    result = routines_service.evaluate_due(
        session,
        now=datetime(2026, 9, 5, 8, 0, tzinfo=UTC),
        context=RoutineConditionContext(owner_present=False),
    )
    assert result.outcomes[0].status == FIRING_STATUS_SKIPPED
    assert result.outcomes[0].reason  # never silently dropped

    firings = routines_service.list_firings(session, routine.routine_id)
    assert firings[0].status == FIRING_STATUS_SKIPPED
    assert firings[0].skip_reason
    assert firings[0].conditions_result[0]["passed"] is False

    events = _ledger_event_types(session, routine)
    assert EVENT_TYPE_ROUTINE_SKIPPED in events
    assert EVENT_TYPE_ROUTINE_TRIGGERED not in events
    assert EVENT_TYPE_ROUTINE_EXECUTED not in events

    # a one-shot 'at' routine resolves (skip counts as resolution) — it will not be
    # reconsidered forever just because it never actually fired.
    refreshed = routines_service.get_routine(session, routine.routine_id)
    assert refreshed.status == ROUTINE_STATUS_COMPLETED


# ------------------------------------------------------------------- recurring schedule


def test_recurring_schedule_stays_armed_and_can_fire_on_multiple_days(session) -> None:
    routine = routines_service.create_routine(
        session,
        name="Günlük",
        trigger_kind="schedule",
        trigger={"weekdays": list(range(7)), "time": "07:30", "timezone": "Europe/Istanbul"},
    )
    from zoneinfo import ZoneInfo

    istanbul = ZoneInfo("Europe/Istanbul")
    day1 = datetime(2026, 9, 5, 7, 32, tzinfo=istanbul).astimezone(UTC)
    day2 = datetime(2026, 9, 6, 7, 32, tzinfo=istanbul).astimezone(UTC)

    result1 = routines_service.evaluate_due(session, now=day1)
    assert result1.outcomes[0].status == FIRING_STATUS_TRIGGERED
    refreshed = routines_service.get_routine(session, routine.routine_id)
    assert refreshed.status == ROUTINE_STATUS_ARMED  # recurring: never completes on its own

    # same day, called again — idempotent, no second firing.
    result1b = routines_service.evaluate_due(session, now=day1)
    assert result1b.outcomes == ()

    # a new day — fires again, a SECOND firing row with a different occurrence_key.
    result2 = routines_service.evaluate_due(session, now=day2)
    assert result2.outcomes[0].status == FIRING_STATUS_TRIGGERED

    firings = routines_service.list_firings(session, routine.routine_id)
    assert len(firings) == 2
    assert {f.occurrence_key for f in firings} == {"2026-09-05", "2026-09-06"}


# --------------------------------------------------------------------- presence trigger


def test_presence_trigger_fires_from_uistate_tail(session) -> None:
    from app.uistate.publisher import get_publisher

    routine = routines_service.create_routine(
        session, name="Eve döndü", trigger_kind="presence", trigger={"event": "owner.returned"}
    )
    # No routine in this suite imports app.presence — the seam is purely the event name
    # string published through app.uistate, exactly as the task brief specifies.
    get_publisher().publish(UiStateEvent(state=UiState.IDLE, subsystem="system"))  # unrelated noise
    published = get_publisher().publish(
        UiStateEvent(state="owner.returned", subsystem="system")  # type: ignore[arg-type]
    )
    assert published is not None

    result = routines_service.evaluate_due(session, now=routines_service.utcnow())
    assert result.outcomes[0].status == FIRING_STATUS_TRIGGERED
    assert result.outcomes[0].occurrence_key == published.event_id

    # a second evaluate_due call must not re-fire on the same presence event.
    again = routines_service.evaluate_due(session, now=routines_service.utcnow())
    assert again.outcomes == ()
    refreshed = routines_service.get_routine(session, routine.routine_id)
    assert refreshed.status == ROUTINE_STATUS_ARMED  # presence routines never auto-complete


# -------------------------------------------------------------------------- dispatch


def test_media_action_preserves_the_owners_requested_item_end_to_end(session) -> None:
    media_detail = {"url": "https://example.com/sabah-podcasti", "title": "Sabah Podcasti — 42"}
    routine = routines_service.create_routine(
        session,
        name="Uyanınca müzik",
        trigger_kind="at",
        trigger={"at": "2026-09-05T07:30:00Z"},
        actions=[{"kind": "media_playback", "detail": media_detail}],
    )
    routines_service.evaluate_due(session, now=datetime(2026, 9, 5, 8, 0, tzinfo=UTC))
    firings = routines_service.list_firings(session, routine.routine_id)
    assert firings[0].actions_snapshot[0]["detail"] == media_detail


def test_dispatcher_failure_does_not_break_execution_and_is_recorded(session) -> None:
    class ExplodingDispatcher:
        def dispatch(
            self,
            *,
            routine_id: uuid.UUID,
            firing_id: uuid.UUID,
            action: dict[str, Any],
            now: Any = None,
        ):
            raise RuntimeError("device offline")

    routine = routines_service.create_routine(
        session,
        name="X",
        trigger_kind="at",
        trigger={"at": "2026-09-05T07:30:00Z"},
        actions=[{"kind": "voice_briefing", "detail": {"text": "Günaydın"}}],
    )
    result = routines_service.evaluate_due(
        session, now=datetime(2026, 9, 5, 8, 0, tzinfo=UTC), dispatcher=ExplodingDispatcher()
    )
    assert result.outcomes[0].status == FIRING_STATUS_TRIGGERED

    executed = session.execute(
        select(ActivityEventRow).where(
            ActivityEventRow.related_module_id == f"routine:{routine.routine_id}",
            ActivityEventRow.event_type == EVENT_TYPE_ROUTINE_EXECUTED,
        )
    ).scalar_one()
    assert executed.detail_json["dispatch_results"][0]["ok"] is False
    assert "device offline" in executed.detail_json["dispatch_results"][0]["detail"]["error"]


def test_alarm_action_publishes_alarm_triggered_uistate(session) -> None:
    from app.uistate.publisher import get_publisher

    routines_service.create_routine(
        session,
        name="Alarm",
        trigger_kind="at",
        trigger={"at": "2026-09-05T07:30:00Z"},
        actions=[{"kind": "alarm", "detail": {}}],
    )
    routines_service.evaluate_due(session, now=datetime(2026, 9, 5, 8, 0, tzinfo=UTC))
    current = get_publisher().current()
    assert current is not None
    assert current.state == UiState.ALARM_TRIGGERED
