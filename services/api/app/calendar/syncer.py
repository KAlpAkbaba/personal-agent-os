"""The calendar on the routine clock (B46 req 358, 359, 361).

M21 grew ``calendar_index`` only from what the owner asked to hear. B46 keeps a two-week
horizon of the owner's calendar mirrored into it on the clock - so an event deleted
upstream disappears from the index instead of lingering - and raises the reminders the
events themselves carry (their VALARMs) once each, as owner notifications. An unconfigured
account is a quiet no-op. The sync reads and indexes; it never creates, moves or deletes an
event.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.logging import get_logger

logger = get_logger(__name__)

#: The shortest cadence the syncer accepts, whatever a setting says. Reminders are checked
#: on every due pass, so the cadence is also the reminder's worst-case lateness.
MIN_SYNC_INTERVAL_S = 60.0


class CalendarSyncer:
    """Throttles ``CalendarService.sync`` + ``remind_due`` on a clock that ticks far faster."""

    def __init__(self, service: Any, *, enabled: bool, interval_s: float) -> None:
        self._service = service
        self._enabled = enabled
        self._interval_s = max(MIN_SYNC_INTERVAL_S, float(interval_s))
        self._last_run_at: datetime | None = None
        self._last_result: dict[str, Any] | None = None

    @property
    def interval_s(self) -> float:
        return self._interval_s

    def due(self, now: datetime) -> bool:
        if not self._enabled:
            return False
        if self._last_run_at is None:
            return True
        return (now - self._last_run_at).total_seconds() >= self._interval_s

    def tick(self, session: Session, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        if not self.due(now):
            return {"status": "disabled" if not self._enabled else "not_due"}
        # Stamped BEFORE the calls: a failing provider is retried at the next interval,
        # never hammered on every ten-second clock tick.
        self._last_run_at = now
        result: dict[str, Any] = {}
        try:
            result["sync"] = self._service.sync(session, now=now)
        except Exception as exc:  # noqa: BLE001 - one failed pass is logged, never fatal
            logger.warning("calendar_sync_failed", error_class=type(exc).__name__)
            session.rollback()
            result["sync"] = {"status": "failed", "error_class": type(exc).__name__}
        # Reminders run even when the mirror failed: they read the provider directly, and a
        # stale index must not silence a reminder the owner set.
        try:
            result["reminders"] = self._service.remind_due(session, now=now)
        except Exception as exc:  # noqa: BLE001
            logger.warning("calendar_reminders_failed", error_class=type(exc).__name__)
            session.rollback()
            result["reminders"] = {"status": "failed", "error_class": type(exc).__name__}
        self._last_result = result
        return result

    def health_check(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "interval_s": self._interval_s,
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "last_result": self._last_result,
        }


__all__ = ["MIN_SYNC_INTERVAL_S", "CalendarSyncer"]
