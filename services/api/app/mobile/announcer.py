"""Artifact-ready announcement sweeper (M9).

Closes the last wire in the notification path. The split is deliberate:

- the **Temporal worker** knows when a research task reaches READY, and already
  records it by transitioning the task row. It holds no push registrations and
  no vendor tokens, so a delivery attempted there would succeed at delivering
  nothing.
- the **API process** holds the live registrations and the provider state, so it
  must be the one that delivers.

They communicate through the durable record they both already see, exactly the
way the M6 recovery supervisor's incident outbox does — no long-lived service
credential is minted for the worker, and nothing is lost if the API is down when
a task completes.

`tasks.announced_at` is the marker: the sweeper claims READY-and-unannounced
tasks, delivers, and stamps the column. Claiming happens in the same
transaction as the read (`SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL), so
two API processes never announce the same task twice, and a crash between claim
and delivery leaves the row unstamped for the next pass to retry.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.models import TASK_STATUS_READY, Artifact, Task
from app.logging import get_logger

logger = get_logger("app.mobile.announcer")

SessionFactory = Callable[[], AbstractContextManager[Session]]

DEFAULT_INTERVAL_S = 5.0
DEFAULT_BATCH = 20


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ArtifactReadyAnnouncer:
    """Drains READY-but-unannounced tasks and pushes an artifact-ready notice."""

    def __init__(
        self,
        session_factory: SessionFactory,
        notifier: Callable[[uuid.UUID, str, uuid.UUID], object],
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        batch: int = DEFAULT_BATCH,
    ) -> None:
        self._session_factory = session_factory
        # (artifact_id, title, task_id) -> delivery; injected so tests drive a
        # fake provider and the API wires MobileService.notify_artifact_ready.
        self._notifier = notifier
        self._interval_s = interval_s
        self._batch = batch
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ sweep

    def _claim(self, session: Session) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
        """Claim a batch of announceable tasks. Returns (task_id, artifact_id, title)."""
        stmt = (
            select(Task)
            .where(Task.status == TASK_STATUS_READY, Task.announced_at.is_(None))
            .order_by(Task.ready_at)
            .limit(self._batch)
        )
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            # Two API processes must not announce the same task.
            stmt = stmt.with_for_update(skip_locked=True)
        claimed: list[tuple[uuid.UUID, uuid.UUID, str]] = []
        now = _utcnow()
        for task in session.execute(stmt).scalars().all():
            artifact = session.execute(
                select(Artifact).where(Artifact.task_id == task.id)
            ).scalars().first()
            # Stamp regardless: a READY task with no artifact has nothing to
            # announce and must not be re-examined on every sweep.
            task.announced_at = now
            if artifact is not None:
                claimed.append((task.id, artifact.id, artifact.title))
        session.commit()
        return claimed

    def sweep_once(self) -> int:
        """One pass. Returns how many tasks were announced. Safe to call directly."""
        with self._session_factory() as session:
            claimed = self._claim(session)
        announced = 0
        for task_id, artifact_id, title in claimed:
            try:
                self._notifier(artifact_id, title, task_id)
                announced += 1
            except Exception:
                # Delivery is best-effort per task: one dead provider must not
                # stall the rest of the batch. The task stays stamped — the
                # owner still sees the artifact in the inbox; only the push
                # for it was lost, which is what a push is allowed to be.
                logger.exception("artifact_ready_announce_failed", task_id=str(task_id))
        if announced:
            logger.info("artifact_ready_announced", count=announced)
        return announced

    # ------------------------------------------------------------- background

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.sweep_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("artifact_ready_sweep_failed")
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


def pending_task_ids(session: Session, *, limit: int = 100) -> Iterable[uuid.UUID]:
    """Diagnostic: which tasks are waiting to be announced."""
    rows = session.execute(
        select(Task.id)
        .where(Task.status == TASK_STATUS_READY, Task.announced_at.is_(None))
        .order_by(Task.ready_at)
        .limit(limit)
    ).scalars().all()
    return rows


__all__ = ["ArtifactReadyAnnouncer", "pending_task_ids", "DEFAULT_INTERVAL_S"]
