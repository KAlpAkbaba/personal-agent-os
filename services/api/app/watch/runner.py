"""The watch's two loops: the runner that reads what is due, and the 30-day purge.

``apply_observation`` is the one place a reading becomes state. Unchanged hash -> a ``same``
row and nothing else (no parse, no model call, no notification). Otherwise the value is taken
from the selector's text by our tr-TR parser and only then, for a numeric condition, from the
model (``extract``); ``compare.judge`` decides; the baseline moves only on a reading that was
understood. The owner hears through ``app.notifications.service.record`` (its ladder owns
quiet hours) - one line, ``group_key`` ``watch:<id>`` - and the ledger gets a row only for
``watch.changed`` / ``watch.condition_met`` / ``watch.read_failed``: every reading lives in
``watch_readings``, not in the ledger.

``WatchRunner`` reads one watch at a time (a lock: a second pass while one is in flight does
nothing) and counts the next due time from now, so a Core that was down ten hours reads once
on return. It is off by default (``watch_runner_enabled``) until browser-redirect-guard is
released. ``PurgeLoop`` keeps the 30 days, like the misheard notebook's.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.assistant_chat import ChatProvider
from app.ledger import service as ledger
from app.ledger import vocabulary as ledger_vocab
from app.logging import get_logger
from app.loops import LoopHeartbeat
from app.notifications import service as notifications
from app.watch import compare, extract
from app.watch.models import Watch, WatchReading
from app.watch.reader import Observation, Reader
from app.watch.service import WatchChange, change_of

logger = get_logger("app.watch.runner")

RETENTION_DAYS: Final = 30
PURGE_INTERVAL_SECONDS: Final = 24 * 60 * 60
PURGE_LOCK_TIMEOUT_S: Final = 3
RUN_INTERVAL_SECONDS: Final = 60.0
#: At most this many readings in one pass; the rest are due at the next.
PASS_LIMIT: Final = 20
REASON_READ_ERROR: Final = "Okuma sırasında bir hata oldu."

_EVENT_TYPES: Final = {
    compare.OUTCOME_CHANGED: ledger_vocab.EVENT_TYPE_WATCH_CHANGED,
    compare.OUTCOME_CONDITION_MET: ledger_vocab.EVENT_TYPE_WATCH_CONDITION_MET,
    compare.OUTCOME_UNREADABLE: ledger_vocab.EVENT_TYPE_WATCH_READ_FAILED,
}

SessionScope = Callable[[], AbstractContextManager[Session]]


class Announcer(Protocol):
    def announce(self, db: Session, change: WatchChange) -> bool: ...


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ------------------------------------------------------------------ one reading


def _tell(
    db: Session,
    watch: Watch,
    reading: WatchReading,
    *,
    now: datetime,
    announcer: Announcer | None,
) -> WatchChange:
    reading.notified = True
    db.flush()
    change = change_of(db, watch, reading)
    event_type = _EVENT_TYPES[reading.outcome]
    notifications.record(
        db,
        kind=event_type,
        title=watch.label,
        body=change.line_tr,
        group_key=f"watch:{watch.id}",
        data={"watch_id": str(watch.id), "outcome": reading.outcome},
        now=now,
    )
    failed = reading.outcome == compare.OUTCOME_UNREADABLE
    ledger.record(
        db,
        ledger.ActivityEvent(
            event_type=event_type,
            subsystem=ledger_vocab.SUBSYSTEM_WATCH,
            action=event_type.replace(".", "_"),
            factual_summary=change.line_tr,
            source="watch",
            source_ref=f"watch:{watch.id}:{reading.id}",
            status=ledger_vocab.STATUS_FAILED if failed else ledger_vocab.STATUS_COMPLETED,
            severity=ledger_vocab.SEVERITY_WARNING if failed else ledger_vocab.SEVERITY_NOTICE,
            occurred_at=now,
            detail_json={
                "watch_id": str(watch.id),
                "outcome": reading.outcome,
                "value": reading.value,
            },
        ),
    )
    if announcer is not None:
        try:
            announcer.announce(db, change)
        except Exception as exc:  # noqa: BLE001 - speech is a nicety; the row is written
            logger.warning("watch_announce_failed", error=type(exc).__name__)
    return change


def _failure(
    db: Session,
    watch: Watch,
    prior: compare.Prior,
    *,
    now: datetime,
    reason: str,
    sha: str | None,
    announcer: Announcer | None,
) -> WatchChange | None:
    """A reading that was not understood: counted, never moving the baseline; told when it
    is the first reading or the third failure in a row."""
    notify = compare.failure_notifies(prior)
    watch.consecutive_failures = prior.consecutive_failures + 1
    watch.last_outcome = compare.OUTCOME_UNREADABLE
    reading = WatchReading(
        id=uuid.uuid4(),
        watch_id=watch.id,
        read_at=now,
        text_sha256=sha,
        value=None,
        outcome=compare.OUTCOME_UNREADABLE,
        reason=reason[:120],
        notified=False,
    )
    db.add(reading)
    db.flush()
    if not notify:
        db.commit()
        return None
    return _tell(db, watch, reading, now=now, announcer=announcer)


def apply_observation(
    db: Session,
    watch: Watch,
    observation: Observation,
    *,
    now: datetime,
    provider: ChatProvider | None = None,
    announcer: Announcer | None = None,
) -> WatchChange | None:
    """Turn one reading into state; the change the owner was told of, or None. Commits."""
    seen = db.execute(
        select(WatchReading.id).where(
            WatchReading.watch_id == watch.id, WatchReading.read_at == now
        )
    ).first()
    if seen is not None:
        return None  # the same reading processed twice: nothing the second time
    condition = compare.parse_condition(watch.condition)
    prior = compare.Prior(watch.baseline_sha256, watch.condition_met, watch.consecutive_failures)
    watch.last_read_at = now
    watch.next_due_at = compare.next_due(now, watch.id, watch.every_hours)

    if not observation.ok or not observation.text_sha256:
        return _failure(
            db,
            watch,
            prior,
            now=now,
            reason=observation.reason_tr or "Sayfa okunamadı.",
            sha=None,
            announcer=announcer,
        )
    sha = observation.text_sha256
    if prior.baseline_sha256 is not None and sha == prior.baseline_sha256:
        # The hash short-circuit: unchanged means nothing - no parse, no model, no notice.
        watch.consecutive_failures = 0
        watch.last_outcome = compare.OUTCOME_SAME
        db.add(
            WatchReading(
                watch_id=watch.id,
                read_at=now,
                text_sha256=sha,
                value=watch.last_value,
                outcome=compare.OUTCOME_SAME,
                notified=False,
            )
        )
        db.commit()
        return None

    value: float | None = None
    if condition.numeric:
        value = compare.parse_tr_number(observation.text)
        if value is None:
            value = extract.extract_number(
                provider,
                observation.text,
                label=watch.label,
                condition=watch.condition,
                now_tr=now.isoformat(),
            )
    verdict = compare.judge(condition, prior, sha=sha, text=observation.text, value=value)
    if verdict.outcome == compare.OUTCOME_UNREADABLE:
        return _failure(
            db,
            watch,
            prior,
            now=now,
            reason=verdict.reason_tr or compare.REASON_NO_VALUE,
            sha=sha,
            announcer=announcer,
        )

    watch.consecutive_failures = 0
    watch.last_outcome = verdict.outcome
    watch.last_value = value
    if verdict.move_baseline:
        watch.baseline_sha256 = sha
    if verdict.condition_met is not None:
        watch.condition_met = verdict.condition_met
    reading = WatchReading(
        id=uuid.uuid4(),
        watch_id=watch.id,
        read_at=now,
        text_sha256=sha,
        value=value,
        outcome=verdict.outcome,
        notified=False,
    )
    db.add(reading)
    db.flush()
    if not verdict.notify:
        db.commit()
        return None
    return _tell(db, watch, reading, now=now, announcer=announcer)


# ------------------------------------------------------------------ the runner


class WatchRunner:
    """Reads the due watches, one at a time, every ``interval_s`` while enabled."""

    def __init__(
        self,
        session_scope: SessionScope,
        reader: Reader,
        *,
        provider_factory: Callable[[], ChatProvider | None] | None = None,
        announcer: Announcer | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        enabled: bool = True,
        interval_s: float = RUN_INTERVAL_SECONDS,
    ) -> None:
        self._session_scope = session_scope
        self.reader = reader
        self.announcer = announcer
        self.enabled = enabled
        self._provider_factory = provider_factory
        self._provider: ChatProvider | None = None
        self._clock = clock
        self._interval_s = interval_s
        self._in_flight = threading.Lock()
        self._task: asyncio.Task[None] | None = None
        self._beat = LoopHeartbeat(name="watch_runner", interval_s=interval_s)

    def _model(self) -> ChatProvider | None:
        if self._provider is None and self._provider_factory is not None:
            self._provider = self._provider_factory()
        return self._provider

    def _due(self, moment: datetime) -> list[uuid.UUID]:
        with self._session_scope() as db:
            return list(
                db.execute(
                    select(Watch.id)
                    .where(Watch.next_due_at <= moment)
                    .order_by(Watch.next_due_at, Watch.id)
                    .limit(PASS_LIMIT)
                ).scalars()
            )

    def _read_one(self, watch_id: uuid.UUID, moment: datetime) -> bool:
        with self._session_scope() as db:
            watch = db.get(Watch, watch_id)
            if watch is None or _aware(watch.next_due_at) > moment:
                return False  # removed, or read by someone else meanwhile
            try:
                observation = self.reader.read(
                    db, url=watch.url, selector=watch.selector, watch_id=watch.id
                )
            except Exception as exc:  # noqa: BLE001 - a broken reading is a failed reading
                logger.warning(
                    "watch_reader_raised", watch_id=str(watch_id), error=type(exc).__name__
                )
                observation = Observation(
                    ok=False, reason_tr=REASON_READ_ERROR, error_class=type(exc).__name__
                )
            apply_observation(
                db, watch, observation, now=moment, provider=self._model(), announcer=self.announcer
            )
            return True

    def run_due(self, now: datetime | None = None) -> int:
        """One pass: every due watch read once, one at a time. The number read; 0 when a
        pass is already in flight."""
        if not self._in_flight.acquire(blocking=False):
            return 0
        try:
            moment = _aware(now or self._clock())
            done = 0
            for watch_id in self._due(moment):
                try:
                    done += int(self._read_one(watch_id, moment))
                except Exception as exc:  # noqa: BLE001 - one watch never stops the others
                    logger.warning(
                        "watch_reading_failed", watch_id=str(watch_id), error=type(exc).__name__
                    )
            return done
        finally:
            self._in_flight.release()

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.run_due, self._clock())
                self._beat.record_pass(now=_aware(self._clock()))
            except Exception as exc:  # noqa: BLE001 - the loop never dies of a pass
                self._beat.failures += 1
                self._beat.last_error = type(exc).__name__
            await asyncio.sleep(self._interval_s)

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        self._beat.bind(self._task, now=_aware(self._clock()))

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
        """Advisory and wordless: counts, times, an error's type. "skipped" while off."""
        health = self._beat.health_check(now=_aware(self._clock()))
        health["enabled"] = self.enabled
        return health


# ------------------------------------------------------------------ the 30-day purge


def purge(db: Session, moment: datetime) -> int:
    cutoff = moment - timedelta(days=RETENTION_DAYS)
    result = db.execute(delete(WatchReading).where(WatchReading.read_at < cutoff))
    return int(result.rowcount or 0)


def _bound_the_pass(db: Session) -> None:
    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    if getattr(dialect, "name", None) == "postgresql":
        db.execute(sql_text(f"SET LOCAL lock_timeout = '{int(PURGE_LOCK_TIMEOUT_S)}s'"))


class PurgeLoop:
    """The readings' 30 days, kept by the process: a pass when it starts, then every 24 h.
    The misheard notebook's ``PurgeLoop`` is the precedent; a failed pass is counted by its
    error's type and tried again at the next interval."""

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
        self._beat = LoopHeartbeat(name="watch_purge", interval_s=interval_s)
        self.last_removed: int | None = None

    def purge_once(self, moment: datetime | None = None) -> int | None:
        removed: int | None = None
        failure: str | None = None
        moment = _aware(moment or self._clock())
        try:
            with self._session_scope() as db:
                _bound_the_pass(db)
                removed = purge(db, moment)
                db.commit()
        except Exception as exc:  # noqa: BLE001 - housekeeping never takes the process down
            failure = type(exc).__name__
            logger.warning("watch_purge_failed", error=failure)
        self.last_removed = removed
        if failure is None:
            self._beat.record_pass(now=moment)
        else:
            self._beat.failures += 1
            self._beat.last_error = failure
            self._beat.last_pass_at = moment
        return removed

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self.purge_once, self._clock())
            await asyncio.sleep(self._interval_s)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        self._beat.bind(self._task, now=_aware(self._clock()))

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
        health = self._beat.health_check(now=_aware(self._clock()))
        health["last_removed"] = self.last_removed
        health["retention_days"] = RETENTION_DAYS
        return health

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()
