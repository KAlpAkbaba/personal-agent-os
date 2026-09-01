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

    def _select_batch(self, session: Session) -> list[tuple[uuid.UUID, uuid.UUID | None, str]]:
        """Read a batch of announceable tasks WITHOUT stamping them.

        Stamping happens only after a delivery attempt (see `sweep_once`), so a
        crash between selection and delivery leaves the row unstamped for the
        next pass — which is what migration 0010 promises.
        """
        stmt = (
            select(Task)
            .where(Task.status == TASK_STATUS_READY, Task.announced_at.is_(None))
            .order_by(Task.ready_at)
            .limit(self._batch)
        )
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            # Two API processes must not pick up the same task in one pass.
            stmt = stmt.with_for_update(skip_locked=True)
        batch: list[tuple[uuid.UUID, uuid.UUID | None, str]] = []
        for task in session.execute(stmt).scalars().all():
            artifact = session.execute(
                select(Artifact).where(Artifact.task_id == task.id)
            ).scalars().first()
            if artifact is None:
                batch.append((task.id, None, ""))
            else:
                batch.append((task.id, artifact.id, artifact.title))
        return batch

    def _stamp(self, task_ids: list[uuid.UUID]) -> None:
        if not task_ids:
            return
        now = _utcnow()
        with self._session_factory() as session:
            for task_id in task_ids:
                task = session.get(Task, task_id)
                if task is not None and task.announced_at is None:
                    task.announced_at = now
            session.commit()

    def sweep_once(self) -> int:
        """One pass. Returns how many tasks were announced. Safe to call directly.

        Ordering matters: deliver first, stamp second. Stamping first would make
        a crash between the two silently drop that task's notification forever,
        which is exactly what the durability claim rules out. The cost of this
        order is an at-least-once push if the process dies after delivering but
        before stamping — the provider's collapse key already makes a duplicate
        artifact-ready notice idempotent for the owner, so a rare duplicate is
        strictly better than a silent loss.
        """
        with self._session_factory() as session:
            batch = self._select_batch(session)
        announced = 0
        settled: list[uuid.UUID] = []
        for task_id, artifact_id, title in batch:
            if artifact_id is None:
                # Nothing to announce; stamp so it is not re-examined forever.
                settled.append(task_id)
                continue
            try:
                self._notifier(artifact_id, title, task_id)
                announced += 1
                settled.append(task_id)
            except Exception:
                # One dead provider must not stall the batch — and must not
                # consume the task either: leaving it unstamped means the next
                # sweep retries it once the provider recovers.
                logger.exception("artifact_ready_announce_failed", task_id=str(task_id))
        self._stamp(settled)
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
