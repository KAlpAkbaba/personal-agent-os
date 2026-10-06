"""The receipt loop: did the owner see the alarm? (urgent-alert-rung ADR, 6 and 7.)

Every ``interval_s`` (60 s), over the open rows of ``urgent_alert_receipts``:

1. **Seen another way.** A receipt whose notification is already read (``read_at``: the web
   inbox, a toast) is cancelled at Pushover so the phone does not ring on for three hours;
   closed ``cancelled`` with source ``inbox``, ledger ``alert.seen`` (``source: inbox``).
2. **Asked.** The rest go through ``poll_open_receipts``: acknowledged -> ``alert.seen``,
   rang out -> ``alert.unseen``, a week unanswerable -> ``alert.unseen``.

No open receipt, no request - the provider is never asked about nothing. A process without
the two keys still runs the loop (a pass that does nothing is a pass), so health can tell
"not configured" from "dead".
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.logging import get_logger
from app.loops import LoopHeartbeat
from app.urgent_alert.models import OUTCOME_CANCELLED, SOURCE_INBOX
from app.urgent_alert.receipts import poll_open_receipts
from app.urgent_alert.rung import AlarmRung
from app.urgent_alert.store_sql import SqlReceiptStore

logger = get_logger("app.urgent_alert.loop")

HEALTH_NAME = "urgent_alert_receipts"


class UrgentAlertLoop:
    def __init__(
        self,
        store: SqlReceiptStore,
        *,
        rung: AlarmRung | None,
        interval_s: float = 60.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._rung = rung
        self._interval_s = interval_s
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self.heartbeat = LoopHeartbeat(name=HEALTH_NAME, interval_s=interval_s)

    @property
    def configured(self) -> bool:
        return self._rung is not None and self._rung.available()

    @property
    def rung(self) -> AlarmRung | None:
        return self._rung

    @property
    def store(self) -> SqlReceiptStore:
        return self._store

    @property
    def session_factory(self) -> Any:
        return self._store.session_factory

    def now(self) -> datetime:
        return self._clock()

    def _pass(self, moment: datetime) -> dict[str, int]:
        counts = {"seen": 0, "unseen": 0, "cancelled": 0}
        provider = self._rung.provider if self._rung is not None else None
        if provider is None or not self._store.count_open():
            return counts
        # Read elsewhere first: no poll for a receipt that is about to be cancelled.
        for read in self._store.read_elsewhere():
            receipt_id = read.item.receipt.receipt_id
            if not provider.cancel(receipt_id):
                # Pushover did not take the cancel; it is closed anyway (he has seen it), and
                # the ringing ends by itself at expiry.
                logger.info("alarm_cancel_not_accepted", receipt=receipt_id[:6])
            self._store.close(receipt_id, OUTCOME_CANCELLED, read.read_at, source=SOURCE_INBOX)
            counts["cancelled"] += 1
        # The store's close writes alert.seen / alert.unseen beside each closed row.
        for event in poll_open_receipts(self._store, provider, moment):
            counts[event.kind] += 1
        return counts

    def run_once(self, now: datetime | None = None) -> dict[str, int]:
        moment = now or self._clock()
        try:
            counts = self._pass(moment)
        except Exception as exc:  # noqa: BLE001 - one bad pass never ends the loop
            logger.warning("urgent_alert_loop_pass_failed", error=type(exc).__name__)
            self.heartbeat.record_failure(exc, now=moment)
            return {"seen": 0, "unseen": 0, "cancelled": 0}
        self.heartbeat.record_pass(now=moment)
        return counts

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self.run_once, self._clock())
            await asyncio.sleep(self._interval_s)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        self.heartbeat.bind(self._task, now=self._clock())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None
        self.heartbeat.bind(None)

    def health_check(self) -> dict[str, Any]:
        return self.heartbeat.health_check(now=self._clock())


__all__ = ["HEALTH_NAME", "UrgentAlertLoop"]
