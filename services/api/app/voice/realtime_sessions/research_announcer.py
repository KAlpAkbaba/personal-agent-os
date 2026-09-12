"""Completes a pending ``research.start`` tool call once its research run's durable
row reaches a terminal stage (M18.2 DEFECT 2, ADR-0067).

Same cross-process shape as ``app.mobile.announcer.ArtifactReadyAnnouncer``, and for
the same reason (see that module's own docstring): the Temporal worker holds no
sideband/device-WebSocket registrations — it is a different process from the API's
``BrokerRuntime`` — so a push attempted there would succeed at delivering nothing.
This sweeper runs HERE, in the API process, where the live device connections
actually are; the worker's only job is to make the run's durable row (``ResearchRunRow``
/ ``ResearchReportRow``) terminal. The two communicate through that row, never
directly.

Wiring the actual trigger — a ``research.start`` tool call whose handler starts the
real Temporal ``BrowserResearchWorkflow`` and records the run's ``task_id`` on its own
``result_json`` — is a separate, larger change (see ADR-0067's "not yet wired" note);
this sweeper is the completion half of that path and is fully exercised by its own
tests against a fabricated linkage, independent of whether anything sets it yet.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.loops import LoopHeartbeat
from app.notifications import delivery
from app.research import runs_service
from app.research.models import STAGE_FAILED, TERMINAL_STAGES
from app.research.result import build_insufficient_terminal_payload, build_tool_terminal_payload
from app.voice.realtime_sessions.models import TOOL_STATUS_RUNNING, RealtimeToolCall
from app.voice.realtime_sessions.service import complete_tool_call_system
from app.voice.realtime_sessions.sideband import SidebandPusher

logger = get_logger("app.voice.realtime_sessions.research_announcer")

SessionFactory = Callable[[], AbstractContextManager[Session]]

DEFAULT_INTERVAL_S = 5.0
DEFAULT_BATCH = 20

#: The tool name a pending call must carry to be a candidate (docs/M18_ACTION_CONTRACT.md
#: research.start; app.voice.realtime_sessions.tools.default_registry).
RESEARCH_START_TOOL = "research.start"


class ResearchToolCallAnnouncer:
    """Drains RUNNING ``research.start`` tool calls whose research run has finished."""

    def __init__(
        self,
        session_factory: SessionFactory,
        sideband: SidebandPusher,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        batch: int = DEFAULT_BATCH,
    ) -> None:
        self._session_factory = session_factory
        self._sideband = sideband
        self._interval_s = interval_s
        self._batch = batch
        self._task: asyncio.Task[None] | None = None
        #: B07 req 18: this loop's own health. Four of the nine background loops
        #: could not be seen on the health surface at all, and they were the four
        #: that carry a notification to the owner - so the failure they can have is
        #: the one nobody would notice.
        self.heartbeat = LoopHeartbeat(
            name="research_tool_call_announcer", interval_s=self._interval_s
        )

    # ------------------------------------------------------------------ sweep

    def _claim_batch(self, session: Session, *, now: datetime) -> list[RealtimeToolCall]:
        # B07 req 17: a call whose completion keeps throwing used to be retried on every
        # pass with nothing counting the attempts.
        stmt = (
            select(RealtimeToolCall)
            .where(
                RealtimeToolCall.name == RESEARCH_START_TOOL,
                RealtimeToolCall.status == TOOL_STATUS_RUNNING,
                RealtimeToolCall.announce_quarantined_at.is_(None),
            )
            .where(
                (RealtimeToolCall.announce_next_at.is_(None))
                | (RealtimeToolCall.announce_next_at <= now)
            )
            .order_by(RealtimeToolCall.created_at)
            .limit(self._batch)
        )
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        return list(session.execute(stmt).scalars().all())

    def sweep_once(self, now: datetime | None = None) -> int:
        """One pass. Returns how many tool calls were completed. Safe to call directly.

        ``now`` exists so ONE clock decides a pass: the batch it claims and the backoff it
        writes. Production passes nothing and gets the real clock.

        A call whose run has not reached a terminal stage yet (or whose result never
        carried a ``task_id`` at all — every call today, until ``research.start`` is
        wired to the real pipeline) is left RUNNING for the next pass; nothing here
        ever guesses at a linkage it was not given.
        """
        completed = 0
        moment = now or datetime.now(UTC)
        with self._session_factory() as session:
            for call in self._claim_batch(session, now=moment):
                task_id = str((call.result_json or {}).get("task_id") or "")
                if not task_id:
                    continue
                try:
                    tid = uuid.UUID(task_id)
                except ValueError:
                    continue
                run = runs_service.get_run(session, tid)
                if run is None or run.stage not in TERMINAL_STAGES:
                    continue
                result, error = self._terminal_payload(session, tid, run.stage, run.error)
                session_row = self._session_row(session, call)
                if session_row is None:
                    continue
                try:
                    outcome = complete_tool_call_system(
                        session,
                        session_row,
                        call_id=call.call_id,
                        result=result,
                        error=error,
                        sideband=self._sideband,
                        trace_id=None,
                    )
                except Exception:  # noqa: BLE001 - one bad call must not stall the batch
                    logger.exception(
                        "research_tool_call_announce_failed", task_id=task_id, call_id=call.call_id
                    )
                    self._record_failure(call, moment, reason="completion_raised")
                    session.commit()
                    continue
                if outcome is not None:
                    completed += 1
        if completed:
            logger.info("research_tool_call_announced", count=completed)
        return completed

    def _record_failure(self, call: RealtimeToolCall, now: datetime, *, reason: str) -> None:
        """One more failed attempt on this call; quarantine it once the bound is reached.

        The call is left RUNNING rather than failed: quarantine says "we stopped trying to
        announce it", which is a different fact from "the research failed", and conflating
        the two would tell the owner their research broke when the postman did."""
        attempts, next_at, quarantined = delivery.record_failure(
            attempts=call.announce_attempts or 0, now=now, key=call.id
        )
        call.announce_attempts = attempts
        call.announce_next_at = next_at
        call.announce_quarantined_at = quarantined
        if quarantined is not None:
            logger.warning(
                "research_tool_call_announce_quarantined",
                call_id=call.call_id,
                **delivery.quarantine_summary(attempts, reason=reason),
            )

    @staticmethod
    def _session_row(session: Session, call: RealtimeToolCall) -> Any:
        from app.voice.realtime_sessions.models import RealtimeSessionRow

        return session.get(RealtimeSessionRow, call.session_id)

    @staticmethod
    def _terminal_payload(
        session: Session, task_id: uuid.UUID, stage: str, error: str | None
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if stage == STAGE_FAILED:
            return None, {"error_class": "research_failed", "message": (error or "")[:2000]}
        report_row = runs_service.get_report(session, task_id)
        report_json = dict(report_row.report_json) if report_row is not None else None
        if not report_json:
            return build_insufficient_terminal_payload(), None
        return build_tool_terminal_payload(report_json), None

    # ------------------------------------------------------------- background

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.sweep_once)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.heartbeat.record_failure(exc)  # noqa: BLE001 - the sweep loop must never die silently
                logger.exception("research_tool_call_sweep_failed")
            else:
                self.heartbeat.record_pass()
            await asyncio.sleep(self._interval_s)

    async def start(self) -> None:
        # Mirrors app.voice.realtime_sessions.routes._bind_loop: BrokerSideband.push
        # is scheduled onto whichever loop it was bound to, and this sweeper runs its
        # own background loop rather than inside a per-request handler that would
        # otherwise bind it.
        bind = getattr(self._sideband, "bind_loop", None)
        if bind is not None:
            bind(asyncio.get_running_loop())
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            self.heartbeat.bind(self._task)

    def health_check(self) -> dict[str, Any]:
        return self.heartbeat.health_check()

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


def pending_research_tool_call_ids(session: Session, *, limit: int = 100) -> Iterable[str]:
    """Diagnostic: which RUNNING research.start calls are waiting to be announced."""
    rows = (
        session.execute(
            select(RealtimeToolCall.call_id)
            .where(
                RealtimeToolCall.name == RESEARCH_START_TOOL,
                RealtimeToolCall.status == TOOL_STATUS_RUNNING,
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(rows)


__all__ = [
    "DEFAULT_BATCH",
    "DEFAULT_INTERVAL_S",
    "RESEARCH_START_TOOL",
    "ResearchToolCallAnnouncer",
    "pending_research_tool_call_ids",
]
