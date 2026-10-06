"""The loop that drives ``OwnerCaller``: the five-minute retry and the important events.

Every ``interval_s`` (30 s): ask Twilio what became of the calls placed five minutes ago, and
ring once for each recent important notification nobody has called about. A process with no
Twilio credentials still runs it - a pass that does nothing is a pass - so health can tell
"not configured" from "dead".
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.logging import get_logger
from app.loops import LoopHeartbeat
from app.telephony.service import OwnerCaller

logger = get_logger("app.telephony.loop")

HEALTH_NAME = "telephony_calls"


class TelephonyLoop:
    def __init__(
        self,
        caller: OwnerCaller,
        *,
        interval_s: float = 30.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._caller = caller
        self._interval_s = interval_s
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self.heartbeat = LoopHeartbeat(name=HEALTH_NAME, interval_s=interval_s)

    def run_once(self, now: datetime | None = None) -> dict[str, int]:
        moment = now or self._clock()
        counts: dict[str, int] = {}
        try:
            if self._caller.configured:
                counts = dict(self._caller.sweep(moment))
                counts["event_calls"] = self._caller.call_for_notifications(moment)
        except Exception as exc:  # noqa: BLE001 - one bad pass never ends the loop
            logger.warning("telephony_loop_pass_failed", error=type(exc).__name__)
            self.heartbeat.record_failure(exc, now=moment)
            return counts
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


__all__ = ["HEALTH_NAME", "TelephonyLoop"]
