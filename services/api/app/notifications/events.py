"""B12 req 381-388: the things the owner has to be told, named in one place.

Eight events, each raised where it actually happens. They are declared here rather than
spelled out at each call site for two reasons:

* **one vocabulary.** A notification's priority decides whether it wakes the owner at night.
  Scattering that judgement across eight modules is how "backup failed" ends up quieter than
  "research finished";
* **a caller can be checked for.** ``test_every_declared_event_has_a_caller`` reads the
  source for each of these, because this repository's most repeated defect is a mechanism
  that exists and nothing calls - three retention sweeps, a speaker verdict, a reconcile
  timer, all written and all unreached.

The sentences are Turkish and say what happened rather than which subsystem said it: the
owner is told "yedek alınamadı", not "backup-cloud-core.sh exited 95".
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, time
from typing import Any, Final

from sqlalchemy.orm import Session

from app.notifications import service as notifications
from app.notifications.models import (
    PRIORITY_LOW,
    PRIORITY_NORMAL,
    PRIORITY_URGENT,
    NotificationRow,
)

TASK_COMPLETED: Final[str] = "task.completed"
TASK_FAILED: Final[str] = "task.failed"
APPROVAL_REQUIRED: Final[str] = "owner.approval_required"
ROLLBACK_HAPPENED: Final[str] = "release.rolled_back"
BACKUP_FAILED: Final[str] = "backup.failed"
ALARM_FAILED: Final[str] = "alarm.failed"
RESEARCH_FINISHED: Final[str] = "research.finished"
CANDIDATE_READY: Final[str] = "selfdev.candidate_ready"
CALENDAR_REMINDER: Final[str] = "calendar.reminder"
#: A quiet window that never contains a moment (start == end): see ``calendar_reminder``.
_NO_QUIET_HOURS: Final[time] = time(0, 0)


@dataclasses.dataclass(frozen=True, slots=True)
class EventSpec:
    """What one owner-facing event is, and how loudly it says it."""

    kind: str
    priority: str
    #: Why this priority and not another. Recorded because the judgement is the whole point
    #: of declaring these together, and because "urgent" is a decision to wake somebody.
    why: str


EVENTS: Final[dict[str, EventSpec]] = {
    TASK_COMPLETED: EventSpec(
        kind=TASK_COMPLETED,
        priority=PRIORITY_NORMAL,
        why="finished work is worth knowing about and is never worth a night's sleep",
    ),
    TASK_FAILED: EventSpec(
        kind=TASK_FAILED,
        priority=PRIORITY_NORMAL,
        why=(
            "the owner will want to look, but nothing they can do at 03:00 is better than "
            "what they can do at breakfast"
        ),
    ),
    APPROVAL_REQUIRED: EventSpec(
        kind=APPROVAL_REQUIRED,
        priority=PRIORITY_NORMAL,
        why=(
            "something is WAITING on the owner, so it must not sit unnoticed - but a queue "
            "that waits until morning is still a queue, and waking them does not shorten it"
        ),
    ),
    ROLLBACK_HAPPENED: EventSpec(
        kind=ROLLBACK_HAPPENED,
        priority=PRIORITY_URGENT,
        why=(
            "production changed underneath the owner without them asking. Sixteen rollbacks "
            "happened in seven days and none of them was ever announced; the owner learning "
            "afterwards that their system rewound itself is exactly the surprise urgency is "
            "for"
        ),
    ),
    BACKUP_FAILED: EventSpec(
        kind=BACKUP_FAILED,
        priority=PRIORITY_URGENT,
        why=(
            "every hour the safety net is down is an hour of unprotected work, and the "
            "failure is silent by nature - nothing else in the day will remind them"
        ),
    ),
    ALARM_FAILED: EventSpec(
        kind=ALARM_FAILED,
        priority=PRIORITY_URGENT,
        why=(
            "an alarm that did not fire is discovered by oversleeping. Telling them at once "
            "is the only moment the information is still useful"
        ),
    ),
    RESEARCH_FINISHED: EventSpec(
        kind=RESEARCH_FINISHED,
        priority=PRIORITY_NORMAL,
        why="the owner asked for it; a completed answer is news, not an emergency",
    ),
    CANDIDATE_READY: EventSpec(
        kind=CANDIDATE_READY,
        priority=PRIORITY_LOW,
        why=(
            "the Evolution Engine proposing something is the least time-critical thing this "
            "system produces, and it is the one most likely to arrive in batches"
        ),
    ),
    CALENDAR_REMINDER: EventSpec(
        kind=CALENDAR_REMINDER,
        priority=PRIORITY_NORMAL,
        why=(
            "the owner set this reminder on the event themselves; it is normal priority "
            "because it is expected, and it ignores quiet hours because its moment is the "
            "whole point"
        ),
    ),
}


def _emit(
    db: Session,
    event: str,
    *,
    title: str,
    body: str,
    group_key: str = "",
    data: dict[str, Any] | None = None,
    now: datetime | None = None,
    honour_quiet_hours: bool = True,
) -> NotificationRow:
    spec = EVENTS[event]
    quiet: dict[str, time] = (
        {} if honour_quiet_hours else {"quiet_start": _NO_QUIET_HOURS, "quiet_end": _NO_QUIET_HOURS}
    )
    return notifications.record(
        db,
        kind=spec.kind,
        title=title,
        body=body,
        priority=spec.priority,
        group_key=group_key,
        data=data,
        now=now,
        **quiet,
    )


# --------------------------------------------------------------------- the events


def task_completed(
    db: Session, *, task_id: Any, what: str, now: datetime | None = None
) -> NotificationRow:
    """req 381. Raised where the task reaches READY, not where it was queued."""
    return _emit(
        db,
        TASK_COMPLETED,
        title="İş tamamlandı",
        body=f"{what} hazır efendim.",
        group_key=f"task:{task_id}",
        data={"task_id": str(task_id)},
        now=now,
    )


def task_failed(
    db: Session,
    *,
    task_id: Any,
    what: str,
    error_class: str = "",
    now: datetime | None = None,
) -> NotificationRow:
    """req 382. The error CLASS, never the raw exception: the owner is told what kind of
    thing went wrong, and the trace stays where traces belong."""
    reason = f" ({error_class})" if error_class else ""
    return _emit(
        db,
        TASK_FAILED,
        title="İş başarısız oldu",
        body=f"{what} tamamlanamadı efendim{reason}.",
        group_key=f"task:{task_id}",
        data={"task_id": str(task_id), "error_class": error_class},
        now=now,
    )


def approval_required(
    db: Session, *, subject: str, what: str, now: datetime | None = None
) -> NotificationRow:
    """req 383. Something is waiting on a decision only the owner can make."""
    return _emit(
        db,
        APPROVAL_REQUIRED,
        title="Onayınız gerekiyor",
        body=f"{what} sizin onayınızı bekliyor efendim.",
        group_key=f"approval:{subject}",
        data={"subject": subject},
        now=now,
    )


def rollback_happened(
    db: Session,
    *,
    component: str,
    from_release: str,
    to_release: str,
    reason: str = "",
    now: datetime | None = None,
) -> NotificationRow:
    """req 384. Sixteen rollbacks in seven days, none of them announced."""
    tail = f" Sebep: {reason}." if reason else ""
    return _emit(
        db,
        ROLLBACK_HAPPENED,
        title="Sürüm geri alındı",
        body=(
            f"{component} {from_release[:7]} sürümünden {to_release[:7]} sürümüne geri "
            f"alındı efendim.{tail}"
        ),
        group_key=f"rollback:{component}",
        data={"component": component, "from": from_release, "to": to_release},
        now=now,
    )


def backup_failed(
    db: Session, *, unit: str, detail: str = "", now: datetime | None = None
) -> NotificationRow:
    """req 385. The safety net going down is silent by nature."""
    tail = f" {detail}" if detail else ""
    return _emit(
        db,
        BACKUP_FAILED,
        title="Yedek alınamadı",
        body=f"Yedekleme başarısız oldu efendim ({unit}).{tail}",
        group_key=f"backup:{unit}",
        data={"unit": unit},
        now=now,
    )


def alarm_failed(
    db: Session, *, alarm_id: Any, label: str = "", reason: str = "", now: datetime | None = None
) -> NotificationRow:
    """req 386. An alarm that did not fire is otherwise discovered by oversleeping."""
    which = f" ({label})" if label else ""
    tail = f" {reason}" if reason else ""
    return _emit(
        db,
        ALARM_FAILED,
        title="Alarm çalmadı",
        body=f"Alarm çalışmadı efendim{which}.{tail}",
        group_key=f"alarm:{alarm_id}",
        data={"alarm_id": str(alarm_id), "label": label},
        now=now,
    )


def research_finished(
    db: Session, *, task_id: Any, question: str, now: datetime | None = None
) -> NotificationRow:
    """req 387."""
    return _emit(
        db,
        RESEARCH_FINISHED,
        title="Araştırma bitti",
        body=f"“{question}” araştırması hazır efendim.",
        group_key=f"research:{task_id}",
        data={"task_id": str(task_id)},
        now=now,
    )


def candidate_ready(
    db: Session, *, opportunity_id: Any, title_text: str, now: datetime | None = None
) -> NotificationRow:
    """req 388."""
    return _emit(
        db,
        CANDIDATE_READY,
        title="Aday hazır",
        body=f"{title_text} incelemenize hazır efendim.",
        group_key=f"candidate:{opportunity_id}",
        data={"opportunity_id": str(opportunity_id)},
        now=now,
    )


def calendar_reminder(
    db: Session,
    *,
    event_uid: str,
    summary: str,
    starts_at: datetime,
    now: datetime | None = None,
) -> NotificationRow:
    """B46 req 358. A reminder the owner put on the event themselves (its VALARM) is due at
    the moment they chose, so quiet hours do not hold it back: they are for what the system
    decides to say, and a 07:00 meeting's reminder delivered at 07:30 is not a reminder."""
    return _emit(
        db,
        CALENDAR_REMINDER,
        title="Takvim hatırlatması",
        body=f"{summary} başlıyor efendim: saat {starts_at.strftime('%H:%M')}.",
        group_key=f"calendar:{event_uid}:{starts_at.isoformat()}",
        data={"event_uid": event_uid, "starts_at": starts_at.isoformat()},
        now=now,
        honour_quiet_hours=False,
    )


#: event -> the function that raises it. Read by the guard that checks each one is actually
#: called from somewhere, so a new event cannot be declared and left unwired.
EMITTERS: Final[dict[str, str]] = {
    TASK_COMPLETED: "task_completed",
    TASK_FAILED: "task_failed",
    APPROVAL_REQUIRED: "approval_required",
    ROLLBACK_HAPPENED: "rollback_happened",
    BACKUP_FAILED: "backup_failed",
    ALARM_FAILED: "alarm_failed",
    RESEARCH_FINISHED: "research_finished",
    CANDIDATE_READY: "candidate_ready",
    CALENDAR_REMINDER: "calendar_reminder",
}


__all__ = [
    "ALARM_FAILED",
    "APPROVAL_REQUIRED",
    "BACKUP_FAILED",
    "CALENDAR_REMINDER",
    "CANDIDATE_READY",
    "EMITTERS",
    "EVENTS",
    "RESEARCH_FINISHED",
    "ROLLBACK_HAPPENED",
    "TASK_COMPLETED",
    "TASK_FAILED",
    "EventSpec",
    "alarm_failed",
    "approval_required",
    "backup_failed",
    "calendar_reminder",
    "candidate_ready",
    "research_finished",
    "rollback_happened",
    "sweep_backup_failures",
    "task_completed",
    "task_failed",
]


def sweep_backup_failures(
    db: Session,
    *,
    backup_root: str,
    now: datetime | None = None,
) -> list[str]:
    """B12 req 385: turn B08's failure markers into a notification, once each.

    B08 made a failed scheduled unit VISIBLE - the marker file, and the health check that
    reads it. Visible is not the same as told: the owner has to go and look. This closes the
    gap, and it is a sweep rather than a hook because the failing unit is a systemd service
    that cannot call into this process (that was the whole reason it writes a file).

    Once each, by group key: `record` supersedes an unread sibling in the same group, so a
    unit that fails every night for a week leaves one unread notification saying so rather
    than seven.
    """
    from app.backup_health import backup_health

    health = backup_health(backup_root, now=now)
    failed = list(health.get("failed_units") or [])
    for unit in failed:
        backup_failed(db, unit=unit, detail="", now=now)
    return failed
