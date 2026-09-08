"""A per-process clock whose readings never tie or go backwards.

Windows' wall clock is far coarser than a microsecond: two rows written in one burst (three
realtime sessions in a test, a research completion and the announcer speaking it, two
operator steps) land on the identical ``datetime.now(UTC)`` and every "newest first" that
sorts by that instant then depends on a row id that is not chronological. Measured three
times in this repository (session listing 2026-09-03, the operator focus, the research focus
2026-09-08). A :class:`MonotonicClock` hands out strictly increasing instants: the wall clock
when it moved on, the previous reading plus one microsecond when it did not.

A caller with a real, meaningful moment (a device receipt's own timestamp) passes it as
``now`` and still gets it back untouched unless it ties or precedes the previous reading.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta


class MonotonicClock:
    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._last: datetime | None = None

    def next(self, now: datetime | None = None) -> datetime:
        with self._lock:
            candidate = now or datetime.now(UTC)
            if candidate.tzinfo is None:
                candidate = candidate.replace(tzinfo=UTC)
            if self._last is not None and candidate <= self._last:
                candidate = self._last + timedelta(microseconds=1)
            self._last = candidate
            return candidate

    def reset(self) -> None:
        with self._lock:
            self._last = None


#: Realtime voice sessions: ``created_at`` is the listing's order (newest first).
SESSION_CLOCK = MonotonicClock("voice.realtime_sessions")
