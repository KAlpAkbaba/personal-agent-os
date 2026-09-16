"""The inbox on the routine clock (B45 req 360).

M21 read mail only when the owner asked ("no polling in M21"). The clock now asks too, at
its own throttle, so the index and the morning briefing know what arrived without a
sentence being spoken first. Polling an unconfigured account is a quiet no-op - an absent
account is not a failure to report every few minutes - and the poll never sends, marks,
moves or deletes anything: it lists and indexes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.logging import get_logger

logger = get_logger(__name__)

#: The shortest cadence the poller accepts, whatever a setting says.
MIN_POLL_INTERVAL_S = 60.0


class MailPoller:
    """Throttles ``MailService.poll`` to ``interval_s`` on a clock that ticks far faster."""

    def __init__(self, service: Any, *, enabled: bool, interval_s: float) -> None:
        self._service = service
        self._enabled = enabled
        self._interval_s = max(MIN_POLL_INTERVAL_S, float(interval_s))
        self._last_polled_at: datetime | None = None
        self._last_result: dict[str, Any] | None = None

    @property
    def interval_s(self) -> float:
        return self._interval_s

    def due(self, now: datetime) -> bool:
        if not self._enabled:
            return False
        if self._last_polled_at is None:
            return True
        return (now - self._last_polled_at).total_seconds() >= self._interval_s

    def tick(self, session: Session, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        if not self.due(now):
            return {"status": "disabled" if not self._enabled else "not_due"}
        # Stamped BEFORE the call: a provider that fails is retried at the next interval,
        # never hammered on every ten-second clock tick.
        self._last_polled_at = now
        try:
            result = self._service.poll(session, now=now)
        except Exception as exc:  # noqa: BLE001 - one failed poll is logged, never fatal to the clock
            logger.warning("mail_poll_failed", error_class=type(exc).__name__)
            result = {"status": "failed", "error_class": type(exc).__name__}
        self._last_result = result
        return result

    def health_check(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "interval_s": self._interval_s,
            "last_polled_at": self._last_polled_at.isoformat() if self._last_polled_at else None,
            "last_result": self._last_result,
        }


__all__ = ["MIN_POLL_INTERVAL_S", "MailPoller"]
