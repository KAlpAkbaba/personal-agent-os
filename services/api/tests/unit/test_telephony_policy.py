"""jarvis-calls-owner: WHEN JARVIS phones the owner (app.telephony.policy).

A phone call is the loudest thing this system can do, and on the Twilio trial it also spends
minutes the owner has 75 of a month. So the rule is small and every edge of it is pinned:

* a critical event calls, a routine one never does;
* quiet hours (the notification ladder's own 23:00-07:30, Europe/Istanbul) stop every call
  except a critical one and one the owner asked for himself ('beni ara');
* at most N calls an hour, for every reason - the cap is what stops a flapping event from
  ringing the phone all night.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.notifications import events as notification_events
from app.telephony import policy

#: 14:00 in Istanbul - outside quiet hours.
DAY = datetime(2026, 10, 5, 11, 0, tzinfo=UTC)
#: 02:00 in Istanbul - inside quiet hours.
NIGHT = datetime(2026, 10, 4, 23, 0, tzinfo=UTC)


def test_a_critical_security_event_calls() -> None:
    decision = policy.decide(policy.KIND_SECURITY_CRITICAL, now=DAY, calls_in_last_hour=0)

    assert decision.call is True
    assert decision.reason == "critical"


@pytest.mark.parametrize(
    "kind",
    [notification_events.TASK_COMPLETED, notification_events.RESEARCH_FINISHED, "anything.else"],
)
def test_a_routine_event_never_calls(kind: str) -> None:
    decision = policy.decide(kind, now=DAY, calls_in_last_hour=0)

    assert decision.call is False
    assert decision.reason == "not_important"


@pytest.mark.parametrize(
    "kind",
    [
        policy.KIND_RELEASE_FAILED,
        policy.KIND_ALARM_CALL_ME,
        policy.KIND_SPEND_UNANSWERED,
        policy.KIND_AKTIVRA_IMPORTANT,
    ],
)
def test_the_important_kinds_call_in_the_day(kind: str) -> None:
    assert policy.decide(kind, now=DAY, calls_in_last_hour=0).call is True


def test_quiet_hours_stop_an_important_but_not_critical_call() -> None:
    decision = policy.decide(policy.KIND_RELEASE_FAILED, now=NIGHT, calls_in_last_hour=0)

    assert decision.call is False
    assert decision.reason == "quiet_hours"


def test_quiet_hours_do_not_stop_a_critical_call() -> None:
    assert policy.decide(policy.KIND_SECURITY_CRITICAL, now=NIGHT, calls_in_last_hour=0).call


def test_quiet_hours_do_not_stop_a_call_the_owner_asked_for() -> None:
    """An alarm he set as 'beni ara' at 06:00 is inside 23:00-07:30 - and it is the point."""
    assert policy.decide(policy.KIND_ALARM_CALL_ME, now=NIGHT, calls_in_last_hour=0).call
    assert policy.decide(policy.KIND_TEST_CALL, now=NIGHT, calls_in_last_hour=0).call


@pytest.mark.parametrize("kind", [policy.KIND_SECURITY_CRITICAL, policy.KIND_RELEASE_FAILED])
def test_the_hourly_cap_stops_every_call(kind: str) -> None:
    allowed = policy.decide(kind, now=DAY, calls_in_last_hour=2, max_per_hour=3)
    capped = policy.decide(kind, now=DAY, calls_in_last_hour=3, max_per_hour=3)

    assert allowed.call is True
    assert capped.call is False
    assert capped.reason == "hourly_cap"


def test_the_notification_kinds_that_mean_a_call() -> None:
    """The events the system already raises map onto call reasons; the rest map to nothing."""
    assert policy.call_kind_for_notification(notification_events.RECOVERY_ALERT) == (
        policy.KIND_RELEASE_FAILED
    )
    assert policy.call_kind_for_notification(notification_events.ROLLBACK_HAPPENED) == (
        policy.KIND_RELEASE_FAILED
    )
    assert policy.call_kind_for_notification(policy.KIND_ALARM_CALL_ME) == policy.KIND_ALARM_CALL_ME
    assert policy.call_kind_for_notification(notification_events.TASK_COMPLETED) is None
    # The notice a failed call leaves must never itself ring the phone.
    assert policy.call_kind_for_notification(policy.UNANSWERED_NOTIFICATION_KIND) is None
