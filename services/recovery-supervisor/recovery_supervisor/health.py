"""Release health policy state machine (RECOVERY_AND_SELF_HEALING.md §4).

A release is unhealthy only after ``failure_threshold`` CONSECUTIVE failed
check cycles that all fall within ``window_s`` seconds. A single transient
failure (or failures interleaved with successes) never triggers rollback.
Pure state machine — no I/O — so it is exhaustively unit-testable.
"""

from __future__ import annotations

import time


class HealthPolicy:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"

    def __init__(self, failure_threshold: int = 3, window_s: float = 30.0) -> None:
        if failure_threshold < 2:
            # threshold 1 would violate "never roll back on one transient failure"
            raise ValueError("failure_threshold must be >= 2")
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        self.failure_threshold = failure_threshold
        self.window_s = window_s
        self._failure_times: list[float] = []

    def record(self, ok: bool, now: float | None = None) -> str:
        """Record one check cycle result; returns the resulting state."""
        now = time.monotonic() if now is None else now
        if ok:
            # Any success breaks the consecutive-failure run entirely.
            self._failure_times.clear()
            return self.HEALTHY
        self._failure_times.append(now)
        # Only consecutive failures inside the sliding window count.
        cutoff = now - self.window_s
        self._failure_times = [t for t in self._failure_times if t >= cutoff]
        if len(self._failure_times) >= self.failure_threshold:
            return self.UNHEALTHY
        return self.DEGRADED

    @property
    def consecutive_failures(self) -> int:
        return len(self._failure_times)

    def reset(self) -> None:
        self._failure_times.clear()
