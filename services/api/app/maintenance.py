"""The retention sweeps every expiry rule in the product promised, run on a clock.

Three sweeps existed and nothing ran them (found 2026-09-11, the takeover audit's Phase 8):

- ``memory``            session/short-retention memories are hard-deleted after their TTL
                        (``app.memory.lifecycle.sweep_expired``). Without the sweep a
                        memory the owner was told would lapse simply stayed - a privacy
                        promise kept only on paper.
- ``identity_sessions`` owner sessions past expiry flip to ``expired`` with their audit row
                        (``IdentityService.sweep_expired``). Authentication already refuses
                        an expired session at use; the sweep is what makes the row say so.
- ``security_assets``   authorised assets past ``valid_until`` flip to expired
                        (``AuthorizedAssetRegistry.sweep_expired``); the scope guard already
                        refuses them at use - the sweep keeps the registry truthful.

One sweep failing never stops the others, and nothing here raises into the application:
each result is its count, or the error it met, logged and kept for ``last_results``. The
first pass waits ``initial_delay_s`` so a process that is starting (or a test's app
lifespan) is not doing housekeeping while it boots.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.logging import get_logger

logger = get_logger("app.maintenance")

Sweep = Callable[[], Any]


def _count(value: Any) -> int:
    """A sweep answers a count or the list of what it flipped; both are a number here."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return len(value)
    except TypeError:
        return 0


class RetentionSweeper:
    def __init__(
        self,
        sweeps: dict[str, Sweep],
        *,
        interval_s: float,
        initial_delay_s: float,
    ) -> None:
        self._sweeps = dict(sweeps)
        self._interval_s = interval_s
        self._initial_delay_s = initial_delay_s
        self._task: asyncio.Task[None] | None = None
        self.last_run_at: datetime | None = None
        self.last_results: dict[str, int | str] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._sweeps)

    def sweep_once(self) -> dict[str, int | str]:
        results: dict[str, int | str] = {}
        for name, sweep in self._sweeps.items():
            try:
                results[name] = _count(sweep())
            except Exception as exc:  # noqa: BLE001 - one sweep never stops the rest
                results[name] = f"error: {type(exc).__name__}"
                logger.exception("retention_sweep_failed", sweep=name)
        self.last_run_at = datetime.now(UTC)
        self.last_results = results
        if any(isinstance(v, int) and v for v in results.values()):
            logger.info("retention_swept", **{k: v for k, v in results.items()})
        return results

    async def _loop(self) -> None:
        await asyncio.sleep(self._initial_delay_s)
        while True:
            try:
                await asyncio.to_thread(self.sweep_once)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - sweep_once already contains its errors
                logger.exception("retention_sweeper_failed")
            await asyncio.sleep(self._interval_s)

    async def start(self) -> None:
        if self._interval_s <= 0:
            logger.info("retention_sweeper_disabled")
            return
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def health_check(self) -> dict[str, Any]:
        """Advisory: housekeeping that has not run yet is not an outage."""
        return {
            "status": "ok" if self.running or self._interval_s <= 0 else "skipped",
            "required": False,
            "sweeps": list(self._sweeps),
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_results": dict(self.last_results),
        }
