"""When JARVIS phones the owner (the owner, 2026-10-05: "önemli bir şey olunca beni arasın").

A call is the loudest thing this system can do, and on the Twilio trial it spends minutes the
owner has about 75 of a month. So the rule is small and closed:

* only the kinds below ever ring; everything else is a notification and nothing more;
* quiet hours are the notification ladder's own (``app.notifications.service``): a CRITICAL
  call passes through them, and so does a call the owner asked for himself ('beni ara' on an
  alarm, the settings page's test button) - an alarm he set for 06:00 is inside 23:00-07:30
  and ringing then is the whole point;
* at most ``max_per_hour`` calls an hour for EVERY reason, critical included - the cap is what
  stops one flapping event from ringing the phone all night.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, time
from typing import Final, Literal
from zoneinfo import ZoneInfo

from app.notifications import events as notification_events
from app.notifications.service import QUIET_FROM, QUIET_UNTIL, QUIET_ZONE, in_quiet_hours

KIND_SECURITY_CRITICAL: Final[str] = "security.critical"
KIND_RELEASE_FAILED: Final[str] = "release.failed"
#: An alarm the owner set as 'beni ara' records a notification of this kind when it fires.
KIND_ALARM_CALL_ME: Final[str] = "alarm.call_me"
#: A spend question he did not answer (the money row of the roadmap) records this kind.
KIND_SPEND_UNANSWERED: Final[str] = "spend.unanswered"
#: Aktivra's 'önemli' channel, when it exists.
KIND_AKTIVRA_IMPORTANT: Final[str] = "aktivra.important"
#: The settings page's 'test araması yap'.
KIND_TEST_CALL: Final[str] = "telephony.test"
#: The notice a call the owner never answered leaves behind. Never a call reason itself.
UNANSWERED_NOTIFICATION_KIND: Final[str] = "telephony.unanswered"

DEFAULT_MAX_PER_HOUR: Final[int] = 3

Urgency = Literal["critical", "owner_asked", "important"]


@dataclasses.dataclass(frozen=True, slots=True)
class CallRule:
    kind: str
    urgency: Urgency
    #: The Turkish sentence a call opens with, before the event's own text.
    opening: str


RULES: Final[dict[str, CallRule]] = {
    KIND_SECURITY_CRITICAL: CallRule(
        KIND_SECURITY_CRITICAL, "critical", "Güvenlikle ilgili acil bir durum var."
    ),
    KIND_RELEASE_FAILED: CallRule(
        KIND_RELEASE_FAILED, "important", "Sistemde bir sürüm sorunu var."
    ),
    KIND_ALARM_CALL_ME: CallRule(
        KIND_ALARM_CALL_ME, "owner_asked", "Kurduğun alarm için arıyorum."
    ),
    KIND_SPEND_UNANSWERED: CallRule(
        KIND_SPEND_UNANSWERED, "important", "Bir harcama sorum cevapsız kaldı."
    ),
    KIND_AKTIVRA_IMPORTANT: CallRule(
        KIND_AKTIVRA_IMPORTANT, "important", "Aktivra'dan önemli bir haber var."
    ),
    KIND_TEST_CALL: CallRule(KIND_TEST_CALL, "owner_asked", ""),
}

#: The notifications the system already raises that mean a call. The recovery supervisor's
#: alert and an announced rollback are both "a release failed"; the future kinds above are
#: recorded as notifications under their own names, so they map onto themselves.
_NOTIFICATION_KINDS: Final[dict[str, str]] = {
    notification_events.RECOVERY_ALERT: KIND_RELEASE_FAILED,
    notification_events.ROLLBACK_HAPPENED: KIND_RELEASE_FAILED,
    KIND_SECURITY_CRITICAL: KIND_SECURITY_CRITICAL,
    KIND_ALARM_CALL_ME: KIND_ALARM_CALL_ME,
    KIND_SPEND_UNANSWERED: KIND_SPEND_UNANSWERED,
    KIND_AKTIVRA_IMPORTANT: KIND_AKTIVRA_IMPORTANT,
}


@dataclasses.dataclass(frozen=True, slots=True)
class CallDecision:
    call: bool
    #: critical | owner_asked | important | not_important | quiet_hours | hourly_cap
    reason: str


def call_kind_for_notification(notification_kind: str) -> str | None:
    return _NOTIFICATION_KINDS.get(notification_kind)


def is_critical(kind: str) -> bool:
    rule = RULES.get(kind)
    return rule is not None and rule.urgency == "critical"


def decide(
    kind: str,
    *,
    now: datetime,
    calls_in_last_hour: int,
    max_per_hour: int = DEFAULT_MAX_PER_HOUR,
    quiet_start: time = QUIET_FROM,
    quiet_end: time = QUIET_UNTIL,
    zone: ZoneInfo = QUIET_ZONE,
) -> CallDecision:
    rule = RULES.get(kind)
    if rule is None:
        return CallDecision(False, "not_important")
    if calls_in_last_hour >= max_per_hour:
        return CallDecision(False, "hourly_cap")
    if rule.urgency == "important" and in_quiet_hours(
        now, start=quiet_start, end=quiet_end, zone=zone
    ):
        return CallDecision(False, "quiet_hours")
    return CallDecision(True, rule.urgency)


__all__ = [
    "DEFAULT_MAX_PER_HOUR",
    "KIND_AKTIVRA_IMPORTANT",
    "KIND_ALARM_CALL_ME",
    "KIND_RELEASE_FAILED",
    "KIND_SECURITY_CRITICAL",
    "KIND_SPEND_UNANSWERED",
    "KIND_TEST_CALL",
    "RULES",
    "UNANSWERED_NOTIFICATION_KIND",
    "CallDecision",
    "CallRule",
    "call_kind_for_notification",
    "decide",
    "is_critical",
]
