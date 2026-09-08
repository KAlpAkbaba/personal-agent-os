"""The per-process clock that never ties: `app.monotonic_clock`."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from app import monotonic_clock
from app.monotonic_clock import MonotonicClock


def test_a_frozen_wall_clock_still_yields_strictly_increasing_instants(monkeypatch) -> None:
    frozen = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102 - the clock stands still
            return frozen if tz is None else frozen.astimezone(tz)

    monkeypatch.setattr(monotonic_clock, "datetime", _Frozen)
    clock = MonotonicClock("test")
    first, second, third = clock.next(), clock.next(), clock.next()
    assert first == frozen
    assert first < second < third
    assert third - first == timedelta(microseconds=2)


def test_a_callers_own_moment_stands_when_it_is_later_and_is_nudged_when_it_ties() -> None:
    clock = MonotonicClock("test")
    moment = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    assert clock.next(moment) == moment
    assert clock.next(moment) == moment + timedelta(microseconds=1)
    later = moment + timedelta(seconds=5)
    assert clock.next(later) == later
    earlier = moment - timedelta(seconds=5)
    assert clock.next(earlier) == later + timedelta(microseconds=1)


def test_a_naive_moment_is_read_as_utc() -> None:
    clock = MonotonicClock("test")
    naive = datetime(2026, 9, 8, 12, 0, 0)
    assert clock.next(naive) == naive.replace(tzinfo=UTC)


def test_concurrent_readers_never_share_an_instant() -> None:
    clock = MonotonicClock("test")
    seen: list[datetime] = []
    lock = threading.Lock()

    def take() -> None:
        for _ in range(200):
            instant = clock.next()
            with lock:
                seen.append(instant)

    threads = [threading.Thread(target=take) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == 1600
    assert len(set(seen)) == 1600
