"""B11 req 389: reaching the owner, in order, until one way works.

    toast  ->  sound  ->  push  ->  inbox

Each rung is a way to reach the owner sooner than the one after it. The inbox is the floor:
the notification is already there, so the ladder cannot fail - it can only end with the owner
finding out later than the system would have liked.

**A rung that cannot be tried is skipped, not waited for.** No device online means no toast,
and standing still until one appears is how a notification about something urgent arrives
tomorrow. What is available is measured at the moment of the attempt, never assumed.

**A rung that was tried is never tried again for the same notification.** Otherwise the
ladder is a loop: toast fails, push fails, toast is tried again because it is first.

**Push (B11 req 372).** ``PushRung`` sends Web Push (RFC 8030/8291/8292,
``app.webpush``) to every browser the owner has enabled. Its ``deliver`` returning
``True`` means the push SERVICE accepted the message for delivery — RFC 8030's 201/202
— never that the owner has seen it, or even that the browser has received it yet. That
is a weaker guarantee than ``ToastRung`` gives (the device companion confirms an actual
``shown: true``), and it is the most this system can ever know about a Web Push send
without a second round trip nothing here implements; "a queue is not a delivery"
applies to what ``delivered_via='push'`` can mean, not to whether the ladder may stop
trying — it may, the same way ``sound`` will once it exists, because inbox is always
the floor underneath either way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.notifications import service as notifications
from app.notifications import toast as toast_contract
from app.notifications.models import (
    CHANNEL_INBOX,
    CHANNEL_PUSH,
    CHANNEL_SOUND,
    CHANNEL_TOAST,
    NotificationRow,
)
from app.webpush import service as webpush_service

logger = get_logger("app.notifications.ladder")


class Rung(Protocol):
    """One way of reaching the owner. Returns True only when it actually reached them."""

    def available(self) -> bool: ...

    def deliver(self, row: NotificationRow) -> bool: ...


class ToastRung:
    """The device companion's desktop toast - the only channel that works with the browser
    closed and the screen locked, which is the whole reason this batch exists.

    It speaks ``app.routines.dispatch.DeviceActionPort`` and nothing else. Choosing WHICH
    device gets the toast is that port's job, not this rung's: ``BrokerDeviceAction`` already
    selects by capability over the live device views and answers ``no_capable_device`` when
    none can. A second selection here would be a second answer to the same question, and the
    two would drift the first time one of them learned something.
    """

    name = CHANNEL_TOAST

    #: Long enough for a companion to raise a toast and answer; short enough that a device
    #: that has stopped answering does not hold the rest of the ladder behind it.
    TIMEOUT_S = 15.0

    def __init__(self, *, device_action: Any) -> None:
        self._device_action = device_action

    def available(self) -> bool:
        """Whether this deployment HAS the channel, not whether a device is online now.

        Online-ness is measured by the attempt itself: the port answers `no_capable_device`
        in milliseconds without touching a device, so asking first would be the same query
        run twice with the answer able to change in between.
        """
        return self._device_action is not None

    def deliver(self, row: NotificationRow) -> bool:
        try:
            payload = toast_contract.build(
                notification_id=row.id,
                title=row.title or row.kind,
                body=row.body,
                priority=row.priority,
                group_key=row.group_key,
                actions=(row.data_json or {}).get("actions"),
            )
        except toast_contract.ToastRefused as exc:
            # Our own payload is wrong. Not the device's fault and not a reason to retry it:
            # the ladder steps down and the defect is loud here.
            logger.error("toast_payload_refused", notification_id=str(row.id), error=str(exc))
            return False
        outcome = self._device_action.run(
            capability=toast_contract.CAPABILITY,
            payload=payload,
            idempotency_key=f"notify:{row.id}",
            timeout_s=self.TIMEOUT_S,
        )
        if not getattr(outcome, "ok", False):
            logger.info(
                "toast_not_run",
                notification_id=str(row.id),
                error_class=getattr(outcome, "error_class", "") or "unknown",
            )
            return False
        result = getattr(outcome, "result", None)
        shown = toast_contract.was_shown(result)
        if not shown:
            logger.info(
                "toast_not_shown",
                notification_id=str(row.id),
                reason=toast_contract.refusal_reason(result),
            )
        return shown


class PushRung:
    """B11 req 372: Web Push (``app.webpush``), the third rung. See the module
    docstring's "Push" section for what ``deliver`` returning ``True`` does and does
    not mean.

    ``session_factory`` is a plain ``sqlalchemy.orm.sessionmaker`` (mirrors
    ``app.devices.commands.DeviceCommandClient``'s own constructor) rather than
    ``ArtifactRuntime.session`` directly, so this rung never needs to import the whole
    artifact runtime just to open a session.
    """

    name = CHANNEL_PUSH

    def __init__(
        self,
        *,
        session_factory: Any,
        provider: Any,
        vapid_private_key: Any,
        vapid_subject: str,
        timeout_s: float = 10.0,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._vapid_private_key = vapid_private_key
        self._vapid_subject = vapid_subject
        self._timeout_s = timeout_s

    def available(self) -> bool:
        # B11 task brief: "when no key is configured the push rung is skipped
        # honestly". This object is only ever constructed with a key in the first
        # place (see app.main._build_push_rung) - available() still checks rather
        # than assuming, the same discipline ToastRung.available() follows for
        # device_action, so a caller that constructs a PushRung with vapid_private_key
        # left as None (a test, or a future wiring change) gets the honest skip rather
        # than an AttributeError three calls deep in app.webpush.vapid.
        return self._vapid_private_key is not None

    def deliver(self, row: NotificationRow) -> bool:
        if self._vapid_private_key is None:
            return False
        with self._session_factory() as db:
            outcome = webpush_service.send_to_all(
                db,
                provider=self._provider,
                vapid_private_key=self._vapid_private_key,
                vapid_subject=self._vapid_subject,
                notification_id=row.id,
                title=row.title or row.kind,
                body=row.body,
                group_key=row.group_key,
                priority=row.priority,
                timeout_s=self._timeout_s,
            )
        if outcome.attempted == 0:
            logger.info("push_not_reached", notification_id=str(row.id), reason="no_subscriptions")
            return False
        if outcome.accepted == 0:
            logger.info(
                "push_not_reached", notification_id=str(row.id), reason="all_subscriptions_failed"
            )
            return False
        # RFC 8030: 2xx means the push SERVICE accepted the message, not that the owner
        # (or even the browser) has seen it - the module docstring's "Push" section
        # names this limit explicitly; this log line names it again at the one point
        # the ladder actually acts on it.
        logger.info(
            "push_accepted_by_service",
            notification_id=str(row.id),
            accepted=outcome.accepted,
            attempted=outcome.attempted,
        )
        return True


class InboxRung:
    """The floor. It always succeeds, because recording the notification WAS putting it in
    the inbox - this rung exists so the ladder has a truthful end rather than a silence."""

    name = CHANNEL_INBOX

    def available(self) -> bool:
        return True

    def deliver(self, row: NotificationRow) -> bool:  # noqa: ARG002 - the row is already in it
        return True


def deliver_one(
    db: Session,
    row: NotificationRow,
    *,
    rungs: dict[str, Rung],
    now: datetime | None = None,
) -> str | None:
    """Try rungs in order until one reaches the owner. Returns the channel that did.

    One notification, one pass: a rung that fails is recorded as attempted and the NEXT pass
    of the ladder picks up from there. That keeps a slow channel from holding the whole
    queue, and it means the sequence is visible in the row rather than in a call stack.
    """
    moment = now or datetime.now(UTC)
    available = tuple(name for name, rung in rungs.items() if rung.available())

    while True:
        channel = notifications.next_channel(row, available=available)
        if channel is None:
            return None
        rung = rungs.get(channel)
        if rung is None or not rung.available():
            notifications.mark_attempt_failed(
                db, row, channel=channel, reason="unavailable", now=moment, available=available
            )
            continue
        try:
            reached = rung.deliver(row)
        except Exception as exc:  # noqa: BLE001 - one rung's fault never ends the ladder
            logger.warning(
                "notification_rung_failed",
                notification_id=str(row.id),
                channel=channel,
                error=f"{type(exc).__name__}: {exc}",
            )
            reached = False
        if reached:
            notifications.mark_delivered(db, row, channel=channel, now=moment)
            logger.info(
                "notification_delivered", notification_id=str(row.id), channel=channel
            )
            return channel
        notifications.mark_attempt_failed(
            db, row, channel=channel, reason="not_reached", now=moment, available=available
        )


def sweep(
    db: Session,
    *,
    rungs: dict[str, Rung],
    now: datetime | None = None,
    limit: int = 25,
) -> dict[str, int]:
    """One pass over everything the system may currently interrupt the owner about."""
    moment = now or datetime.now(UTC)
    delivered: dict[str, int] = {}
    for row in notifications.deliverable(db, now=moment, limit=limit):
        channel = deliver_one(db, row, rungs=rungs, now=moment)
        if channel:
            delivered[channel] = delivered.get(channel, 0) + 1
    return delivered


def default_rungs(*, device_action: Any = None, push_rung: Rung | None = None) -> dict[str, Rung]:
    """What this deployment can do. Sound is not wired yet and is absent rather than
    present-and-failing: a rung that is always going to say no is noise in the ladder,
    and `available()` returning False is how the ladder skips it in one step.

    ``push_rung`` is built by the caller (``app.main._build_push_rung``) rather than
    here, because building one needs the VAPID private key loaded from settings and a
    provider instance - construction concerns this function has never had for
    ``device_action`` either (``BrokerDeviceAction`` is also built by the caller).
    """
    rungs: dict[str, Rung] = {}
    if device_action is not None:
        rungs[CHANNEL_TOAST] = ToastRung(device_action=device_action)
    if push_rung is not None:
        rungs[CHANNEL_PUSH] = push_rung
    rungs[CHANNEL_INBOX] = InboxRung()
    return rungs


__all__ = [
    "CHANNEL_INBOX",
    "CHANNEL_PUSH",
    "CHANNEL_SOUND",
    "CHANNEL_TOAST",
    "InboxRung",
    "PushRung",
    "Rung",
    "ToastRung",
    "default_rungs",
    "deliver_one",
    "sweep",
]
