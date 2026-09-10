"""Keep the map of the system in step with the system (ADR-0111).

The index is only worth consulting if it describes the code that is running. It
did not.

``build_index`` had exactly one caller in the product -- ``POST
/v1/selfmodel/index``, which runs when a human asks -- so production's index was
written once, on 2026-09-05 at 16:35 UTC, and never again. Five days and six
releases later it held 222 of the checkout's 423 modules, and the 201 it was
missing included ``app.voice.realtime_sessions.tools_operator`` and
``app.operator.plans``: both files of the typing defect the owner reported on
2026-09-09. A map that cannot contain the bug cannot be used to find the bug,
and nothing in the system said so, because a stale row and a fresh row look
exactly alike.

Two triggers, and no third:

* **Once at startup.** The image is immutable, so in production the source can
  only change when a release replaces the process -- which is exactly when this
  runs. This is the trigger that matters.
* **Then on an interval**, because a developer checkout changes underneath a
  running process. An unchanged pass costs one ``stat`` per file and writes
  nothing (measured on this repository: 0.3 s, 0 writes, against 2.7 s and
  14 773 writes for the cold run), so the interval is cheap enough to be honest
  rather than clever about it.

It runs in a thread and its failures are logged, never raised: the self-model is
an aid to diagnosis, and an aid to diagnosis must not be able to take Cloud Core
down. This is the same rule the ledger backfill in ``app/main.py`` follows.

This is NOT self-modification. Nothing here writes source, proposes a change or
restarts anything; it reads the checkout and writes four tables that describe
it. The constitution's self-development path is untouched and still required for
any change to code.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Final

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.selfmodel.indexer import IndexReport, build_index, default_repo_root

logger = get_logger("app.selfmodel.refresh")

#: Long enough that the steady state is a no-op nobody notices, short enough
#: that a developer's edit is in the map before they go looking for it.
DEFAULT_INTERVAL_S: Final[float] = 900.0
#: The startup pass waits this long first, so a release's readiness probe and
#: the first owner request are never behind a repository walk.
DEFAULT_INITIAL_DELAY_S: Final[float] = 5.0

SessionFactory = Callable[[], AbstractContextManager[Session]]


class SelfModelRefresher:
    """Re-index the checkout at startup and then periodically."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        initial_delay_s: float = DEFAULT_INITIAL_DELAY_S,
    ) -> None:
        self._session_factory = session_factory
        self._interval_s = interval_s
        self._initial_delay_s = initial_delay_s
        self._task: asyncio.Task[None] | None = None
        self._last: IndexReport | None = None

    # ----------------------------------------------------------------- one pass

    def refresh_once(self) -> IndexReport | None:
        """One index pass. Returns the report, or ``None`` when it failed.

        The root is ``default_repo_root()`` and nothing may pass another one --
        the same rule ``app/selfmodel/routes.py`` states for the HTTP surface. A
        repository walker whose root is an argument is a file reader with extra
        steps.
        """
        try:
            with self._session_factory() as session:
                report = build_index(session, repo_root=default_repo_root())
                session.commit()
        except Exception as exc:  # noqa: BLE001 - diagnosis must not break the product
            logger.warning("selfmodel_refresh_failed", error=f"{type(exc).__name__}: {exc}")
            return None
        self._last = report
        if report.modules_reparsed or report.modules_removed:
            logger.info(
                "selfmodel_refreshed",
                modules=report.modules_discovered,
                reparsed=report.modules_reparsed,
                removed=report.modules_removed,
                duration_ms=report.duration_ms,
            )
        return report

    # --------------------------------------------------------------- background

    async def _loop(self) -> None:
        await asyncio.sleep(self._initial_delay_s)
        while True:
            try:
                await asyncio.to_thread(self.refresh_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("selfmodel_refresh_pass_failed")
            await asyncio.sleep(self._interval_s)

    async def start(self) -> None:
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

    @property
    def last_report(self) -> dict[str, Any] | None:
        return self._last.to_dict() if self._last is not None else None


__all__ = [
    "DEFAULT_INITIAL_DELAY_S",
    "DEFAULT_INTERVAL_S",
    "SelfModelRefresher",
]
