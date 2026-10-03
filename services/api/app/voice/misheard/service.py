"""The misheard notebook's store: one writer, an in-process hold, and the ways a row leaves.

``record`` is the ONE writer. It refuses quietly (``None``, nothing written) in "sadece dinle",
for an empty sentence and for a reason or mode the CONTRACT does not name; it cuts to the
table's widths; it is idempotent on ``(session_id, heard_at)``; and it never raises into the
voice turn that called it - the database work runs in a savepoint, so a fault here leaves the
caller's transaction usable (PostgreSQL aborts a whole transaction on one failed statement).

Nothing in this module logs a sentence or a meaning, and nothing logs an exception's text: a
database error's message carries the statement's parameters, which here ARE the sentence. A
log line names the event, the reason, the mode and the error's type - no more.

``hold`` / ``held`` keep the latest sentence of a session in THIS process for 120 seconds,
because an objection ("hayır, dur") and a failed tool arrive after the sentence they are
about, and the audit row and the route telemetry are wordless on purpose. The hold is never
written to disk, the database, the session's ``context_json`` or a log.

Three things delete an expired row, none of them a session: ``record`` (every write), the
owner's GET, and the application's own ``PurgeLoop`` (a pass when it starts, then one every
24 h; it answers for itself in ``/v1/system/health``). ``list_items`` hides an expired row
even when none of them has run yet.
"""

from __future__ import annotations

import asyncio
import math
import threading
import uuid
from collections.abc import Callable, Sized
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.loops import LoopHeartbeat
from app.voice.misheard.models import (
    ENGINE_WIDTH,
    INTENT_WIDTH,
    MEANT_WIDTH,
    SENTENCE_WIDTH,
    TOOL_WIDTH,
    MisheardUtterance,
)

logger = get_logger("app.voice.misheard")

RETENTION_DAYS = 30
#: How long the latest sentence of a session stays in this process's memory.
HOLD_TTL_SECONDS = 120
PURGE_INTERVAL_SECONDS = 24 * 60 * 60
#: How long the lifespan's purge pass waits for a row another transaction holds (PostgreSQL's
#: ``lock_timeout``, for the pass's own transaction only). Past it the pass gives up and
#: counts a failure; the row lives until the next pass and is never listed meanwhile.
PURGE_LOCK_TIMEOUT_S = 3

REASON_NO_INTENT = "no_intent"
REASON_ASKED_QUESTION = "asked_question"
REASON_OBJECTED = "objected"
REASON_TOOL_FAILED = "tool_failed"
REASONS = frozenset({REASON_NO_INTENT, REASON_ASKED_QUESTION, REASON_OBJECTED, REASON_TOOL_FAILED})
MODES = frozenset({"paid", "local"})
BANDS = frozenset({"high", "medium", "low"})


def _utc(value: datetime | None) -> datetime | None:
    """One clock for every comparison: aware UTC. A naive value is UTC already (SQLite hands a
    timestamptz back naive, and stores an aware one without converting it)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _cut(value: object, width: int) -> str | None:
    """A name cut to its column: None for nothing, never one character too many."""
    if not isinstance(value, str):
        return None
    text = value.replace("\x00", "").strip()
    return text[:width] or None


def _band(value: object) -> str | None:
    return value if isinstance(value, str) and value in BANDS else None


def _confidence(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


# ------------------------------------------------------------------- is this a request?


def is_request(candidates: int | Sized, machine_named: bool) -> bool:
    """Whether a sentence with NO intent belongs in the notebook at all.

    The narrowing of the proposal's first condition (sahip incelemesi bekliyor): only when the
    understanding policy saw at least one candidate reading, or the sentence named a machine.
    Plain conversation is never stored. ``candidates`` is the count of candidate readings, or
    the readings themselves.
    """
    count = candidates if isinstance(candidates, int) else len(candidates)
    return count > 0 or bool(machine_named)


# ------------------------------------------------------------------- the one writer


def record(
    db: Session,
    *,
    sentence: str,
    mode: str,
    reason: str,
    session_id: uuid.UUID | None,
    heard_at: datetime | None = None,
    now: datetime | None = None,
    engine: str | None = None,
    device_id: uuid.UUID | None = None,
    band: str | None = None,
    confidence: float | None = None,
    resolved_intent: str | None = None,
    tool: str | None = None,
    listen_only: bool = False,
) -> MisheardUtterance | None:
    """Write one row, or nothing. Never raises; never commits (the caller's transaction)."""
    if listen_only:
        return None
    # A caller's type error is a refusal like the others: never an exception in its turn.
    if not isinstance(reason, str) or not isinstance(mode, str):
        return None
    if not all(value is None or isinstance(value, datetime) for value in (heard_at, now)):
        return None
    text = _cut(sentence, SENTENCE_WIDTH)
    if text is None or reason not in REASONS or mode not in MODES or session_id is None:
        return None
    moment = _utc(now) or datetime.now(UTC)
    heard = _utc(heard_at) or moment
    try:
        with db.begin_nested():
            row = db.execute(
                select(MisheardUtterance).where(
                    MisheardUtterance.session_id == session_id,
                    MisheardUtterance.heard_at == heard,
                )
            ).scalar_one_or_none()
            if row is None:
                row = MisheardUtterance(
                    heard_at=heard,
                    sentence=text,
                    mode=mode,
                    engine=_cut(engine, ENGINE_WIDTH),
                    device_id=device_id,
                    band=_band(band),
                    confidence=_confidence(confidence),
                    reason=reason,
                    resolved_intent=_cut(resolved_intent, INTENT_WIDTH),
                    tool=_cut(tool, TOOL_WIDTH) if reason == REASON_TOOL_FAILED else None,
                    session_id=session_id,
                    expires_at=heard + timedelta(days=RETENTION_DAYS),
                )
                db.add(row)
                db.flush()
    except Exception as exc:  # noqa: BLE001 - the notebook never costs the owner a turn
        logger.warning("misheard_record_failed", error=type(exc).__name__, reason=reason, mode=mode)
        return None
    try:
        with db.begin_nested():
            purge(db, moment)
    except Exception as exc:  # noqa: BLE001 - the row is written; the sweep comes again
        logger.warning("misheard_purge_failed", error=type(exc).__name__, trigger="record")
    return row


# ------------------------------------------------------------------- the hold


@dataclass(frozen=True)
class HeldSentence:
    """The latest sentence of a session with what was known when it was heard.

    ``sentence`` is left out of the repr: an entry that ends up in a log line or a traceback
    shows its mode and band, never its words."""

    sentence: str = field(repr=False)
    mode: str
    engine: str | None
    device_id: uuid.UUID | None
    band: str | None
    confidence: float | None
    resolved_intent: str | None
    heard_at: datetime


_hold: dict[str, tuple[HeldSentence, datetime]] = {}
_hold_lock = threading.Lock()


def _drop_expired_holds(moment: datetime) -> None:
    """Called with the lock held: a sentence past its 120 s leaves memory for every session,
    not only the one that asks."""
    limit = timedelta(seconds=HOLD_TTL_SECONDS)
    for key in [k for k, (_, at) in _hold.items() if moment - at >= limit]:
        del _hold[key]


def hold(session_id: uuid.UUID | str, entry: HeldSentence, now: datetime) -> None:
    """Keep ``entry`` as the session's latest sentence; the one before it is gone."""
    moment = _utc(now) or datetime.now(UTC)
    with _hold_lock:
        _drop_expired_holds(moment)
        _hold[str(session_id)] = (entry, moment)


def held(session_id: uuid.UUID | str, now: datetime) -> HeldSentence | None:
    """The session's latest sentence while it is younger than ``HOLD_TTL_SECONDS``."""
    moment = _utc(now) or datetime.now(UTC)
    with _hold_lock:
        _drop_expired_holds(moment)
        kept = _hold.get(str(session_id))
        return kept[0] if kept is not None else None


def reset_hold() -> None:
    with _hold_lock:
        _hold.clear()


# ------------------------------------------------------------------- how a row leaves


def purge(db: Session, now: datetime) -> int:
    """Delete every row whose ``expires_at`` has passed; the count. Flushes, never commits."""
    moment = _utc(now) or datetime.now(UTC)
    result = db.execute(delete(MisheardUtterance).where(MisheardUtterance.expires_at <= moment))
    return int(result.rowcount or 0)


def forget_all(db: Session) -> int:
    """'Defteri unut': every row, answered or not."""
    result = db.execute(delete(MisheardUtterance))
    return int(result.rowcount or 0)


def forget_one(db: Session, item_id: uuid.UUID) -> bool:
    result = db.execute(delete(MisheardUtterance).where(MisheardUtterance.id == item_id))
    return bool(result.rowcount)


def list_items(db: Session, now: datetime) -> list[MisheardUtterance]:
    """Newest first; an expired row is never returned, purged or not."""
    moment = _utc(now) or datetime.now(UTC)
    return list(
        db.execute(
            select(MisheardUtterance)
            .where(MisheardUtterance.expires_at > moment)
            .order_by(MisheardUtterance.heard_at.desc(), MisheardUtterance.id)
        ).scalars()
    )


def answer(db: Session, item_id: uuid.UUID, meant: str, now: datetime) -> MisheardUtterance | None:
    """The owner's answer on one row; None for a row that is not there (or has expired) and
    for an empty answer. The sentence and its expiry stay as they were."""
    moment = _utc(now) or datetime.now(UTC)
    text = _cut(meant, MEANT_WIDTH)
    if text is None:
        return None
    row = db.get(MisheardUtterance, item_id)
    if row is None or _utc(row.expires_at) <= moment:
        return None
    row.meant = text
    row.answered_at = moment
    db.flush()
    return row


# ------------------------------------------------------------------- the lifespan's purge

SessionScope = Callable[[], AbstractContextManager[Session]]


def _bound_the_pass(db: Session) -> None:
    """On PostgreSQL the pass waits at most ``PURGE_LOCK_TIMEOUT_S`` for a locked row. SET
    LOCAL: the setting ends with the pass's transaction and never reaches the pool."""
    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    if getattr(dialect, "name", None) == "postgresql":
        db.execute(sql_text(f"SET LOCAL lock_timeout = '{int(PURGE_LOCK_TIMEOUT_S)}s'"))


class PurgeLoop:
    """The purge that needs nobody: once when the application starts, then every 24 h.

    ``session_scope`` is asked for a session at each pass (never held between passes), the
    pass commits its own delete, and a pass that fails is logged by its error's type and
    tried again at the next interval - it never raises into the application.

    ``start`` only creates the task, like every other loop of the lifespan: the first pass is
    the task's first iteration, so a row another transaction holds can never hold the
    application's start (misheard-purge-start-bounded). The pass itself is bounded by
    ``PURGE_LOCK_TIMEOUT_S``. One clock - the one passed in - decides both what has expired
    and whether the loop is behind; the loop reads it on the event loop, before the pass
    goes to its thread.
    """

    def __init__(
        self,
        session_scope: SessionScope,
        *,
        interval_s: float = PURGE_INTERVAL_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_scope = session_scope
        self._interval_s = interval_s
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._beat = LoopHeartbeat(name="misheard_purge", interval_s=interval_s)
        self.last_removed: int | None = None

    @property
    def passes(self) -> int:
        """Every pass that was tried, whether or not it could reach the table."""
        return self._beat.passes + self._beat.failures

    def purge_once(self, moment: datetime | None = None) -> int | None:
        """One pass as of ``moment`` (the loop's clock when None); the count, or None when it
        could not run."""
        removed: int | None = None
        failure: str | None = None
        if moment is None:
            moment = self._clock()
        try:
            with self._session_scope() as db:
                _bound_the_pass(db)
                removed = purge(db, moment)
                db.commit()
        except Exception as exc:  # noqa: BLE001 - housekeeping never takes the process down
            removed = None
            failure = type(exc).__name__
            logger.warning("misheard_purge_failed", error=failure, trigger="lifespan")
        with _hold_lock:
            _drop_expired_holds(_utc(moment) or datetime.now(UTC))
        self.last_removed = removed
        if failure is None:
            self._beat.record_pass(now=_utc(moment))
        else:
            # Not LoopHeartbeat.record_failure: it keeps the exception's TEXT, and health is
            # the one endpoint that answers without an owner session.
            self._beat.failures += 1
            self._beat.last_error = failure
            self._beat.last_pass_at = _utc(moment)
        if removed:
            logger.info("misheard_purged", removed=removed, trigger="lifespan")
        return removed

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self.purge_once, self._clock())
            await asyncio.sleep(self._interval_s)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        self._beat.bind(self._task, now=_utc(self._clock()))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
            self._beat.bind(None)

    def health_check(self) -> dict[str, Any]:
        """Advisory, like every loop: "fail" says the loop is behind (or has died), and it
        never turns the application's health red. Wordless - counts, times, an error's type."""
        health = self._beat.health_check(now=_utc(self._clock()))
        health["last_removed"] = self.last_removed
        health["retention_days"] = RETENTION_DAYS
        return health

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()
