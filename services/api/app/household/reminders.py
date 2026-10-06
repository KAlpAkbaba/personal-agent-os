"""The "bitmeden" reminder: an item whose rhythm says it runs out in a few days is said once.

One short notification per item per cycle, through ``app.notifications.service.record`` (its
ladder owns quiet hours and delivery); nothing is put on the list on the owner's behalf. The
loop runs in the API process every ``interval_s`` (an hour) - the rhythm is in days, an hour
late is on time. A failed pass is counted by its error's type and tried again at the next one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy.orm import Session

from app.household import service
from app.logging import get_logger
from app.loops import LoopHeartbeat
from app.notifications import service as notifications

logger = get_logger("app.household.reminders")

KIND_REMINDER: Final = "household.reminder"
TITLE: Final = "Ev stoğu"
REMIND_INTERVAL_SECONDS: Final = 3600.0

SessionScope = Callable[[], AbstractContextManager[Session]]


def remind_due(db: Session, *, now: datetime) -> int:
    """Write one notification per due item and stamp it; returns how many were written."""
    sent = 0
    for row in service.due_reminders(db, now=now):
        row.reminded_at = now
        db.flush()
        notifications.record(
            db,
            kind=KIND_REMINDER,
            title=TITLE,
            body=service.reminder_line(row),
            group_key=f"household:{row.id}",
            data={"item_id": str(row.id), "cycle_days": row.cycle_days},
            now=now,
        )
        sent += 1
    db.commit()
    return sent


class ReminderLoop:
    def __init__(
        self,
        session_scope: SessionScope,
        *,
        interval_s: float = REMIND_INTERVAL_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_scope = session_scope
        self._interval_s = interval_s
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._beat = LoopHeartbeat(name="household_reminders", interval_s=interval_s)
        self.last_sent: int | None = None

    def remind_once(self, moment: datetime | None = None) -> int | None:
        moment = moment or self._clock()
        sent: int | None = None
        try:
            with self._session_scope() as db:
                sent = remind_due(db, now=moment)
        except Exception as exc:  # noqa: BLE001 - a reminder never takes the process down
            logger.warning("household_reminders_failed", error=type(exc).__name__)
            self._beat.record_failure(exc, now=moment)
            self.last_sent = None
            return None
        self.last_sent = sent
        self._beat.record_pass(now=moment)
        return sent

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self.remind_once, self._clock())
            await asyncio.sleep(self._interval_s)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        self._beat.bind(self._task, now=self._clock())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
            self._beat.bind(None)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def health_check(self) -> dict[str, Any]:
        health = self._beat.health_check(now=self._clock())
        health["last_sent"] = self.last_sent
        return health


__all__ = ["KIND_REMINDER", "ReminderLoop", "remind_due"]
