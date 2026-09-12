"""B07 req 14/15/16/17/18/19/375/390: nothing retries for ever, nothing blocks the queue.

Four defects with one shape between them - a loop with no idea what it had already done:

* the push announcer left a failing task unstamped and re-attempted it on every sweep. At a
  five-second interval that is 17,280 attempts a day against a provider that is never going
  to answer;
* the briefing announcer picked the most urgent pending row and, when speaking it failed,
  returned without touching anything. Same row next pass, and the one after. One row the
  speaker could not handle blocked every notification behind it, permanently - which is
  where the owner's missing notices went;
* the research announcer retried a throwing tool call unbounded;
* the routine clock wrapped five sub-ticks in ONE try/except, so a failure in the routine
  evaluation meant the alarm tick did not run at all that pass.

And one of a different shape: the offline fake reported ``delivered``, which is a product
principle violated by a default argument.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.notifications import delivery

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
API_ROOT = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------- the policy


def test_the_first_failure_waits_seconds_not_the_next_tick() -> None:
    """The defect in one number: without a backoff the retry is the next sweep, five
    seconds away, for ever."""
    wait = delivery.backoff_for(1, key="x")

    assert wait >= delivery.BASE_BACKOFF
    assert wait < delivery.BASE_BACKOFF + delivery.MAX_JITTER


def test_the_wait_doubles_and_then_stops_doubling() -> None:
    """Capped: nothing is gained by waiting a day, and a lot is lost - an item that becomes
    deliverable again should be delivered soon after."""
    waits = [delivery.backoff_for(n, key="x") for n in range(1, 12)]

    assert waits == sorted(waits), "monotonic"
    assert waits[-1] <= delivery.MAX_BACKOFF + delivery.MAX_JITTER


def test_two_items_that_failed_together_do_not_retry_together() -> None:
    """A thundering herd against a provider that has only just come back is how a recovered
    outage becomes a second outage."""
    a = delivery.backoff_for(3, key="item-a")
    b = delivery.backoff_for(3, key="item-b")

    assert a != b


def test_the_jitter_is_the_same_every_time_for_the_same_item() -> None:
    """Derived from the id, not from `random`: a failure has to be reproducible, and a test
    should not have to seed anything."""
    assert delivery.backoff_for(2, key="item-a") == delivery.backoff_for(2, key="item-a")


def test_giving_up_is_a_state_not_a_silence() -> None:
    attempts, next_at, quarantined = delivery.record_failure(
        attempts=delivery.MAX_ATTEMPTS - 1, now=NOW, key="x"
    )

    assert attempts == delivery.MAX_ATTEMPTS
    assert quarantined == NOW
    assert next_at is None, "an exhausted item must not also carry a next attempt"


def test_a_quarantined_item_is_never_attempted_again() -> None:
    assert (
        delivery.may_attempt(attempts=2, next_attempt_at=None, quarantined_at=NOW, now=NOW)
        is False
    )


def test_an_item_that_has_never_been_tried_is_not_delayed() -> None:
    """A policy meant for failures must not hold up a first attempt."""
    assert (
        delivery.may_attempt(attempts=0, next_attempt_at=None, quarantined_at=None, now=NOW)
        is True
    )


def test_an_item_waiting_out_its_backoff_is_skipped_then_taken() -> None:
    later = NOW + timedelta(minutes=1)

    assert delivery.may_attempt(
        attempts=1, next_attempt_at=later, quarantined_at=None, now=NOW
    ) is False
    assert delivery.may_attempt(
        attempts=1, next_attempt_at=later, quarantined_at=None, now=later
    ) is True


# -------------------------------------------------- the briefing queue unblocks


@pytest.fixture()
def briefing_db():
    from app.ledger.models import ActivityEventRow, PendingBriefingRow

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (PendingBriefingRow.__table__, ActivityEventRow.__table__):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _queue(db, *, speech: str, priority: int, created: datetime):
    from app.ledger.models import PendingBriefingRow

    row = PendingBriefingRow(
        briefing_id=uuid.uuid4(),
        created_at=created,
        policy="urgent",
        priority=priority,
        speech=speech,
        event_ids=[],
        expires_at=created + timedelta(days=1),
    )
    db.add(row)
    db.commit()
    return row


def test_one_unspeakable_row_no_longer_blocks_the_ones_behind_it(briefing_db) -> None:
    """The defect exactly. The poison row is the most urgent, so it was chosen on every
    pass, failed on every pass, and the second row was never reached - permanently, and
    silently, because a failure to speak returned 0 and touched nothing."""
    from app.ledger.briefing import pending, record_delivery_failure

    poison = _queue(briefing_db, speech="bozuk", priority=10, created=NOW)
    behind = _queue(briefing_db, speech="normal", priority=20, created=NOW)

    # Every attempt fails. The clock advances past each backoff, because the row STEPS
    # ASIDE while it waits - which is itself the fix, and is asserted separately below.
    moment = NOW
    for _ in range(delivery.MAX_ATTEMPTS):
        assert pending(briefing_db, moment)[0].briefing_id == poison.briefing_id
        record_delivery_failure(briefing_db, poison.briefing_id, reason="say_failed", now=moment)
        moment += delivery.MAX_BACKOFF + delivery.MAX_JITTER

    briefing_db.refresh(poison)
    assert poison.quarantined_at is not None
    assert poison.delivered_at is None, "quarantined is not delivered"

    remaining = pending(briefing_db, moment)
    assert [r.briefing_id for r in remaining] == [behind.briefing_id]


def test_a_row_inside_its_backoff_steps_aside_for_the_next_one(briefing_db) -> None:
    """Even before it is exhausted: a row that just failed waits, and the queue moves."""
    from app.ledger.briefing import pending, record_delivery_failure

    first = _queue(briefing_db, speech="ilk", priority=10, created=NOW)
    second = _queue(briefing_db, speech="ikinci", priority=20, created=NOW)

    record_delivery_failure(briefing_db, first.briefing_id, reason="say_failed", now=NOW)

    assert [r.briefing_id for r in pending(briefing_db, NOW)] == [second.briefing_id]
    # ...and comes back once its wait is over.
    later = NOW + timedelta(minutes=5)
    assert first.briefing_id in {r.briefing_id for r in pending(briefing_db, later)}


def test_the_announcer_records_the_failure_rather_than_returning_quietly() -> None:
    """Reading the source, because this is a one-line omission that reintroduces the whole
    defect: `return 0` without counting is exactly what it used to do."""
    import inspect

    from app.ledger.briefing_announcer import PendingBriefingAnnouncer

    source = inspect.getsource(PendingBriefingAnnouncer.sweep_once)
    assert source.count("record_delivery_failure") == 2, "the urgent path AND the digest path"


# ------------------------------------------------------- the fake cannot lie


def test_the_offline_fake_cannot_say_delivered() -> None:
    """req 390, structurally rather than by rule. The fake has no vendor to acknowledge
    anything, so it has nothing to put in `receipt`, so the word is unavailable to it."""
    from app.mobile import providers as P

    fake = P.FakePushProvider()
    fake.register(registration_key="r1", token="device-token-1")

    delivered = fake.deliver(registration_key="r1", message=_message())

    assert delivered.status == P.STATUS_SIMULATED
    assert delivered.is_delivered is False


def test_claiming_delivered_without_a_receipt_is_refused_at_construction() -> None:
    """Not a lint rule, not a comment: the type will not build. A transport with nothing to
    show did not deliver anything."""
    from app.mobile import providers as P

    with pytest.raises(ValueError, match="receipt"):
        P.PushDelivery(
            registration_key="r1",
            provider="fake",
            message=_message(),
            delivered_at=NOW,
            token_fingerprint="ab",
            status=P.STATUS_DELIVERED,
        )


def test_a_real_transport_carries_the_vendors_own_id() -> None:
    from app.mobile import providers as P

    real = P.PushDelivery(
        registration_key="r1",
        provider="fcm",
        message=_message(),
        delivered_at=NOW,
        token_fingerprint="ab",
        status=P.STATUS_DELIVERED,
        detail="projects/x/messages/123 (12 ms)",
        receipt="projects/x/messages/123 (12 ms)",
    )

    assert real.is_delivered is True
    assert real.to_dict()["receipt"]


def _message():
    from app.mobile.providers import PushMessage

    return PushMessage(title="t", body="b", data={"kind": "artifact_ready"})


# ------------------------------------------------- every loop answers for itself


def _started_loops() -> set[str]:
    """The loops `create_app`'s lifespan starts, read from the source.

    Reading rather than restating: a loop added later shows up here without anybody
    remembering to add it, which is the whole point of the assertion below.
    """
    source = (API_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    lifespan = source.split("async def lifespan", 1)[1]
    return set(re.findall(r"await ([a-z_][\w.]*)\.start\(\)", lifespan))


def test_every_background_loop_the_app_starts_can_be_seen_in_health() -> None:
    """req 18. Nine loops start; five could be seen and four could not - and the four were
    the ones that carry a notification to the owner, so their failure is the one nobody
    would notice. This maps each started loop to the health key that reports it, and fails
    if a loop is started with nothing answering for it."""
    reported = {
        "broker": "broker",
        "routine_clock": "routine_clock",
        "artifacts": "artifacts",
        "mobile.announcer": "artifact_ready_announcer",
        "research_tool_call_announcer": "research_tool_call_announcer",
        "selfmodel_refresher": "selfmodel_refresher",
        "briefing_announcer": "briefing_announcer",
        "embedded_worker": "temporal_worker",
        "retention_sweeper": "retention",
    }
    started = _started_loops()

    assert started, "the lifespan was not found - this guard would pass vacuously"
    unreported = sorted(started - set(reported))
    assert not unreported, (
        f"these background loops are started with nothing reporting their health: "
        f"{unreported}. A loop nothing can see is one that can die quietly."
    )


def test_the_health_map_actually_carries_those_keys() -> None:
    """The other half: the mapping above is only worth anything if the keys are real."""
    from tests.unit.test_health_endpoint import ALL_CHECKS

    for key in (
        "artifact_ready_announcer",
        "briefing_announcer",
        "research_tool_call_announcer",
        "selfmodel_refresher",
    ):
        assert key in ALL_CHECKS


def test_a_loop_that_never_started_is_skipped_not_failing() -> None:
    """Every test process is in that state, and it is not a fault."""
    from app.loops import LoopHeartbeat

    beat = LoopHeartbeat(name="x", interval_s=5)

    assert beat.health_check()["status"] == "skipped"


def test_a_loop_alive_but_not_completing_passes_is_reported_failing() -> None:
    """`task.done()` is False for a loop that wakes and does nothing, so the task object
    alone cannot answer "is it working"."""
    from app.loops import STALL_INTERVALS, LoopHeartbeat

    class _Alive:
        def done(self) -> bool:
            return False

    beat = LoopHeartbeat(name="x", interval_s=10)
    beat.bind(_Alive(), now=NOW)  # type: ignore[arg-type]

    assert beat.health_check(now=NOW)["status"] == "ok"
    late = NOW + timedelta(seconds=10 * STALL_INTERVALS + 1)
    assert beat.health_check(now=late)["status"] == "fail"


# --------------------------------------------------- the clock's sub-ticks


def test_a_failing_sub_tick_does_not_stop_the_ones_after_it() -> None:
    """req 19. One try/except wrapped all five, so a failure in the routine evaluation meant
    the alarm tick did not run - an alarm silently dropped by something unrelated to alarms."""
    import asyncio

    from app.routines.clock import RoutineClock

    ran: list[str] = []

    class _Session:
        def rollback(self) -> None:
            ran.append("rollback")

        def close(self) -> None:
            ran.append("close")

    def _boom(session, now):
        raise RuntimeError("routines are broken")

    clock = RoutineClock(
        session_factory=_Session,
        evaluate_due=_boom,
        alarm_tick=lambda s, n: ran.append("alarm"),
        ambient_tick=lambda s, n: ran.append("ambient"),
        interval_s=60,
    )
    asyncio.run(clock.tick_once(now=NOW))

    assert "alarm" in ran and "ambient" in ran
    assert "rollback" in ran, "the next sub-tick must not inherit a broken transaction"
    health = clock.health_check()
    assert health["failing_sub_ticks"] == ["routines"]
    assert health["sub_ticks"]["alarms"]["status"] == "ok"


# ------------------------------------------------------- the audit trail ends


@pytest.fixture()
def audit_db():
    from app.broker.models import AuditEvent
    from app.identity.models import SessionEvent

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (AuditEvent.__table__, SessionEvent.__table__):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _audit_row(db, *, created_at: datetime):
    from app.broker.models import AuditEvent

    db.add(AuditEvent(category="device", action="x", created_at=created_at))
    db.commit()


def test_the_dry_run_counts_and_removes_nothing(audit_db) -> None:
    """The roadmap's rollback plan for this batch, as a test. A retention sweep is the one
    kind of housekeeping whose bug is unrecoverable."""
    from app.broker.models import AuditEvent
    from app.security import audit_retention

    _audit_row(audit_db, created_at=NOW - timedelta(days=400))

    counted = audit_retention.sweep_audit_retention(audit_db, now=NOW, dry_run=True)

    assert counted["audit_events"] == 1
    assert audit_db.query(AuditEvent).count() == 1, "counted, not deleted"


def test_a_real_run_removes_only_what_is_past_its_retention(audit_db) -> None:
    from app.broker.models import AuditEvent
    from app.security import audit_retention

    _audit_row(audit_db, created_at=NOW - timedelta(days=400))
    _audit_row(audit_db, created_at=NOW - timedelta(days=10))

    audit_retention.sweep_audit_retention(audit_db, now=NOW, dry_run=False)

    assert audit_db.query(AuditEvent).count() == 1


def test_the_authentication_record_is_kept_longer_than_device_traffic() -> None:
    """Low volume, high individual value: the thing you actually want when a question about
    access comes up months later."""
    from app.security import audit_retention

    assert (
        audit_retention.RETENTION["session_events"]
        > audit_retention.RETENTION["audit_events"]
    )


def test_the_activity_ledger_is_never_swept() -> None:
    """It is the evidence base the owner's "what have you been doing" answers come from, and
    EVIDENCE truth never ages by design. A policy that quietly skipped a table would look
    identical to one that forgot it, so the skip is named with its reason."""
    from app.security import audit_retention

    assert "activity_events" in audit_retention.NEVER_SWEPT
    assert "activity_events" not in audit_retention.RETENTION
    assert audit_retention.NEVER_SWEPT["activity_events"].strip()


def test_every_append_only_audit_table_has_been_decided_about() -> None:
    """Neither swept nor exempt is the state this requirement exists to end."""
    from app.security import audit_retention

    decided = set(audit_retention.RETENTION) | set(audit_retention.NEVER_SWEPT)
    assert decided == {"audit_events", "session_events", "activity_events"}


def test_the_policy_ships_as_a_dry_run() -> None:
    from app.config import Settings

    assert Settings(_env_file=None).audit_retention_dry_run is True
