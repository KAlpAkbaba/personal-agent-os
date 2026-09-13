"""B18 req 71: the Experience Engine, on a clock, and working through its own backlog.

`app.experience.engine.ingest` has been complete since it was written and the only thing
that ever called it was `POST /v1/experience/ingest`. The feature matrix measured the
result exactly: **1441 activity events, 0 memories**. A system that keeps a durable record
of everything it does and never reads it back is keeping a diary it cannot remember.

**Two directions, because one is not enough.** `ledger.query` is newest-first and capped at
200 rows, so a scheduler that only ever asks for "everything since my last pass" keeps up
with today and never reaches the 1441 events already there; and one that only ever asks for
the newest 200 re-reads the same page for ever. So a pass does both: catch up on what is
new, then, if there is room left in the page budget, walk one page further back into the
history. The backlog finishes when a backward page comes back short, and after that every
pass is just the forward half.

**The cursor is a ledger event, not private state.** Each pass writes
``experience.ingested`` carrying the window it scanned, and the next pass reads the latest
one to know where it got to. Three reasons rather than a table: the ledger is already this
system's record of what it did, so "when did the engine last run and how far back has it
read" becomes answerable through `activity.explain` like everything else; there is no
second source of truth to disagree with the memories actually written; and a cursor derived
from the memories themselves would STALL, because an event whose summary the write policy
ignores produces no row, and a whole page of those would leave the cursor where it was.
The engine excludes this event type from its own ingest — a system that learned from the
record of its own learning would corroborate itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.experience.engine import (
    DEFAULT_INGEST_LIMIT,
    EXCLUDED_EVENT_TYPES,
    ingest,
)
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_EXPERIENCE_INGESTED,
    SEVERITY_INFO,
    SUBSYSTEM_MEMORY,
)
from app.logging import get_logger
from app.memory.graph import sync_from_events

logger = get_logger("app.experience.scheduler")

#: How often a pass may run. Fifteen minutes: the ledger is written by things that take
#: minutes (a research run, a release, a morning briefing), and a derivation pass that runs
#: faster than its input changes is just re-reading.
DEFAULT_INTERVAL_S = 900.0

#: The floor, so a misconfiguration cannot turn this into a busy loop over the ledger.
MIN_INTERVAL_S = 60.0

#: How far back the FIRST pass looks when there is no cursor at all. The forward half is
#: for keeping up; the backlog half is what reaches the history, one page per pass.
FIRST_PASS_WINDOW = timedelta(hours=24)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands naive datetimes back; the ledger stores UTC by construction."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(slots=True)
class Cursor:
    """Where the last pass got to, read back out of the ledger."""

    newest_scanned: datetime | None = None
    oldest_scanned: datetime | None = None
    backlog_done: bool = False

    @property
    def started(self) -> bool:
        return self.newest_scanned is not None or self.oldest_scanned is not None


@dataclass(slots=True)
class PassResult:
    """What one pass did. Every count is from the engine's own report."""

    ran: bool = False
    #: B18 req 47/48: nodes and edges asserted from the same events this pass read.
    entities: int = 0
    edges: int = 0
    #: Rows this pass READ. On a pass that also walks the backlog, the boundary row is read
    #: twice on purpose - see `run_once` - so this is rows read and not distinct events.
    #: `episodic_created` is the exact number and is what the owner-facing receipt leads
    #: with.
    events_scanned: int = 0
    episodic_created: int = 0
    backlog_done: bool = False
    reason: str = ""
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "events_scanned": self.events_scanned,
            "episodic_created": self.episodic_created,
            "entities": self.entities,
            "edges": self.edges,
            "backlog_done": self.backlog_done,
            "reason": self.reason,
            "errors": len(self.errors),
        }


def read_cursor(session: Session) -> Cursor:
    """The latest `experience.ingested`, or an empty cursor when there has never been one."""
    rows = ledger_service.query(
        session, event_types=(EVENT_TYPE_EXPERIENCE_INGESTED,), limit=1
    )
    if not rows:
        return Cursor()
    detail = rows[0].detail_json or {}

    def _at(key: str) -> datetime | None:
        raw = detail.get(key)
        if not raw:
            return None
        try:
            return _aware(datetime.fromisoformat(str(raw)))
        except ValueError:  # pragma: no cover - a hand-edited row
            return None

    return Cursor(
        newest_scanned=_at("newest_scanned"),
        oldest_scanned=_at("oldest_scanned"),
        backlog_done=bool(detail.get("backlog_done")),
    )


def _write_cursor(
    session: Session,
    cursor: Cursor,
    result: PassResult,
    *,
    now: datetime,
) -> None:
    """Record the pass AND its cursor. Best-effort: a pass that ingested memories and then
    could not write its receipt has still done the work, and the next pass re-reads an
    overlapping window, which the engine is idempotent about by construction."""
    try:
        ledger_service.record(
            session,
            ledger_service.ActivityEvent(
                event_type=EVENT_TYPE_EXPERIENCE_INGESTED,
                subsystem=SUBSYSTEM_MEMORY,
                action="experience.ingest",
                severity=SEVERITY_INFO,
                factual_summary=(
                    f"Deneyim motoru {result.episodic_created} yeni bellek yazdı "
                    f"({result.events_scanned} etkinlik okundu)."
                ),
                source="live",
                source_ref=f"experience_ingest:{now.isoformat()}",
                occurred_at=now,
                detail_json={
                    "newest_scanned": (
                        cursor.newest_scanned.isoformat() if cursor.newest_scanned else None
                    ),
                    "oldest_scanned": (
                        cursor.oldest_scanned.isoformat() if cursor.oldest_scanned else None
                    ),
                    "backlog_done": cursor.backlog_done,
                    **result.as_dict(),
                },
            ),
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("experience_cursor_write_failed", error=f"{type(exc).__name__}: {exc}")
        try:
            session.rollback()
        except Exception:  # noqa: BLE001 - nothing further to do about it
            pass


class ExperienceScheduler:
    """Throttles itself and rides the routine clock, exactly as the Evolution Supervisor
    does — one cadence in this process, not a timer per subsystem."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        interval_s: float = DEFAULT_INTERVAL_S,
        limit: int = DEFAULT_INGEST_LIMIT,
        embedder: Any = None,
    ) -> None:
        self.enabled = enabled
        self.interval_s = max(MIN_INTERVAL_S, float(interval_s))
        self.limit = limit
        self._embedder = embedder
        self.last_run_at: datetime | None = None
        self.runs = 0
        self.events_scanned_total = 0
        self.memories_created_total = 0
        self.backlog_done = False
        self.last_error: str | None = None

    def bind_embedder(self, embedder: Any) -> None:
        """`create_app` builds the MemoryRuntime after this; the embedder arrives then, and
        it must be the SAME one the index was built with (ADR-0078's lesson, again)."""
        self._embedder = embedder

    def due(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        if self.last_run_at is None:
            return True
        return (now - self.last_run_at).total_seconds() >= self.interval_s

    def tick(self, session: Session, *, now: datetime | None = None) -> PassResult:
        """One pass, or a no-op when it is not due yet. Never raises into the clock."""
        now = now or utcnow()
        if not self.due(now):
            return PassResult(reason="not_due")
        self.last_run_at = now
        try:
            result = self.run_once(session, now=now)
        except Exception as exc:  # noqa: BLE001 - the clock drives alarms; never sink it
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            logger.exception("experience_ingest_failed")
            return PassResult(reason=f"error:{type(exc).__name__}")
        self.runs += 1
        self.events_scanned_total += result.events_scanned
        self.memories_created_total += result.episodic_created
        self.backlog_done = result.backlog_done
        return result

    def run_once(self, session: Session, *, now: datetime | None = None) -> PassResult:
        """Catch up on what is new, then walk one page further into the backlog."""
        now = now or utcnow()
        cursor = read_cursor(session)
        result = PassResult(ran=True, backlog_done=cursor.backlog_done)

        # ---- forward: everything at or after where the last pass stopped reading.
        since = cursor.newest_scanned or (now - FIRST_PASS_WINDOW)
        forward = ingest(session, embedder=self._embedder, since=since, now=now, limit=self.limit)
        result.events_scanned += forward.events_scanned
        result.episodic_created += forward.episodic_created
        result.errors.extend(forward.errors)
        # Everything up to NOW has been offered to the forward pass, so that is where the
        # next one starts. Not `forward.oldest_scanned` and not the newest row returned: a
        # window that produced no rows still covered its window, and a cursor that only
        # moves when something was written would re-read a quiet stretch for ever.
        newest = _aware(now)

        # ---- backward: one page older than the oldest this engine has ever read. Skipped
        # once a backward page comes back short, because there is nothing further back.
        # Seeded from what the FORWARD pass actually read, never from `since`. The first
        # draft used `since` - which on a first run is "24 hours ago" - so the backward
        # window asked for events older than that, found none in a fixture whose history
        # was hours old, and declared the backlog finished having read four of twelve
        # events. A cursor seeded from a horizon rather than from data will always be
        # wrong in whichever direction the data is not.
        oldest = cursor.oldest_scanned or _aware(forward.oldest_scanned) or since
        if not cursor.backlog_done and result.events_scanned < self.limit:
            room = self.limit - result.events_scanned
            # `until` is INCLUSIVE, so the boundary event is read a second time. Left that
            # way on purpose: the engine is idempotent per event (a deterministic
            # `Memory.key` from the event id), and one re-read row is cheaper than an
            # off-by-one that skips an event for ever.
            backward = ingest(
                session, embedder=self._embedder, now=now, limit=room, until=oldest
            )
            result.events_scanned += backward.events_scanned
            result.episodic_created += backward.episodic_created
            result.errors.extend(backward.errors)
            if backward.events_scanned == 0 or backward.events_scanned < room:
                result.backlog_done = True
            oldest = backward.oldest_scanned or oldest

        # B18 req 47/48: the same events, as a graph. Here rather than inside `ingest`
        # because `app.experience.engine`'s own rule is that it writes memories and only
        # memories; the scheduler is the composition point, which is what a scheduler is
        # for. Never raises - a graph that could not be written costs a graph.
        try:
            graph = sync_from_events(session, self._last_rows(session, since=since, now=now))
            result.entities = graph.entities
            result.edges = graph.edges
        except Exception as exc:  # noqa: BLE001 - the clock drives alarms
            logger.warning("entity_graph_failed", error=f"{type(exc).__name__}: {exc}")

        _write_cursor(
            session,
            Cursor(
                newest_scanned=newest,
                oldest_scanned=oldest,
                backlog_done=result.backlog_done,
            ),
            result,
            now=now,
        )
        return result

    def _last_rows(self, session: Session, *, since: datetime, now: datetime) -> list[Any]:
        """The events this pass covered, re-read for the graph.

        A second read rather than threading the rows out of `ingest`: the engine's
        signature is "ledger in, memories out" and widening it to hand back its input so a
        different subsystem can use it would make it the graph's dependency too. The read
        is the same indexed query the pass just made and both writers are idempotent.

        It applies the ENGINE's own exclusion list, and that is not a detail. The first
        draft did not, so the graph read this pass's own `experience.ingested` receipt and
        grew a `system: memory` node and a `capability: experience.ingest` node - the
        system building a graph of itself building a graph. Caught by the "a second pass
        adds no duplicates" test, which is the shape that finds this: the count grew by
        exactly one node per pass, for ever.
        """
        rows = ledger_service.query(session, since=since, until=now, limit=self.limit)
        return [row for row in rows if row.event_type not in EXCLUDED_EVENT_TYPES]

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.enabled else "disabled",
            # No I/O here - the pass runs on the clock, not on a health probe - but the
            # endpoint's shape is uniform across every check and a probe that skipped the
            # field would be the one nobody could chart.
            "latency_ms": 0.0,
            "enabled": self.enabled,
            "interval_s": self.interval_s,
            "runs": self.runs,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "events_scanned_total": self.events_scanned_total,
            "memories_created_total": self.memories_created_total,
            "backlog_done": self.backlog_done,
            "last_error": self.last_error,
        }


__all__ = [
    "DEFAULT_INTERVAL_S",
    "FIRST_PASS_WINDOW",
    "MIN_INTERVAL_S",
    "Cursor",
    "ExperienceScheduler",
    "PassResult",
    "read_cursor",
]
