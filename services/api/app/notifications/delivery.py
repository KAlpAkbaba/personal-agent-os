"""B07 req 14/15/16/17/376: how many times, how far apart, and when to stop.

Three announcers, three copies of the same missing idea. None of them counted attempts, so:

* the push announcer left a failing task unstamped and re-attempted it on every sweep -
  at a five-second interval that is **17,280 attempts a day** against a provider that is
  never going to answer;
* the briefing announcer picked ``urgent[0]``, and when speaking it failed it returned
  without touching anything. The same row is ``urgent[0]`` on the next pass, and the one
  after, for ever. One row the speaker cannot handle blocks every notification behind it -
  permanently, silently, and that is where the owner's missing notices went;
* the research announcer retried a throwing tool call on every pass, unbounded.

The shape they were all missing is the same three facts per queued item - **how many times
have we tried, when may we try again, and have we given up** - so it is one module rather
than three near-copies that drift.

**Giving up is not losing the item.** An exhausted item is *quarantined*: it stops being
attempted, it is recorded as undeliverable with the reason, and - the whole point - the queue
behind it moves. A queue that cannot give up on one item has not got a retry policy, it has
got a lock.

**Backoff is capped and jittered.** Capped because an hour between attempts is already long
enough that nothing is gained by making it a day; jittered because three announcers waking
on the same schedule after the same outage is a thundering herd against a provider that has
only just come back.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Final

#: After this many failed attempts an item is quarantined rather than retried again. Six
#: attempts spread over the backoff below is a little under two hours of trying, which is
#: long enough to ride out a provider restart and short enough that a genuinely broken item
#: stops consuming the queue the same morning.
MAX_ATTEMPTS: Final[int] = 6

#: First wait after a failure, doubling each time: 30s, 1m, 2m, 4m, 8m, 16m.
BASE_BACKOFF: Final[timedelta] = timedelta(seconds=30)

#: The ceiling. Nothing is gained by waiting longer than this between attempts, and a lot is
#: lost: an item that becomes deliverable again should be delivered soon after, not tomorrow.
MAX_BACKOFF: Final[timedelta] = timedelta(hours=1)

#: Up to this much is added to each wait, derived from the item's own id. Deterministic, so a
#: test can predict it, and different per item, so a batch that failed together does not
#: retry together.
MAX_JITTER: Final[timedelta] = timedelta(seconds=15)


def _jitter(key: object) -> timedelta:
    """A stable per-item offset in [0, MAX_JITTER).

    Derived from the id rather than from ``random``: the same item always waits the same
    extra moment, so a failure is reproducible and a test does not have to seed anything.
    """
    digest = hashlib.sha256(str(key).encode("utf-8")).digest()
    fraction = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return timedelta(seconds=MAX_JITTER.total_seconds() * fraction)


def backoff_for(attempts: int, *, key: object = "") -> timedelta:
    """How long to wait after ``attempts`` failures. ``attempts`` counts failures so far."""
    if attempts <= 0:
        return timedelta(0)
    doubled = BASE_BACKOFF * (2 ** (attempts - 1))
    return min(doubled, MAX_BACKOFF) + _jitter(key)


def next_attempt_at(attempts: int, *, now: datetime, key: object = "") -> datetime:
    return now + backoff_for(attempts, key=key)


def is_exhausted(attempts: int) -> bool:
    return attempts >= MAX_ATTEMPTS


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def may_attempt(
    *,
    attempts: int,
    next_attempt_at: datetime | None,
    quarantined_at: datetime | None,
    now: datetime,
) -> bool:
    """Whether this item may be attempted at ``now``.

    A quarantined item never may. An item waiting out its backoff may not yet. Everything
    else may - including an item that has never been attempted, whose ``next_attempt_at`` is
    None and which must not be delayed by a policy meant for failures.
    """
    if quarantined_at is not None:
        return False
    if is_exhausted(attempts):
        return False
    scheduled = _aware(next_attempt_at)
    return scheduled is None or scheduled <= now


def record_failure(
    *, attempts: int, now: datetime, key: object = ""
) -> tuple[int, datetime | None, datetime | None]:
    """The three fields after one more failure: (attempts, next_attempt_at, quarantined_at).

    Returns the quarantine stamp instead of a next attempt once the bound is reached, so a
    caller cannot accidentally keep an exhausted item alive by writing both.
    """
    attempts = attempts + 1
    if is_exhausted(attempts):
        return attempts, None, now
    return attempts, next_attempt_at(attempts, now=now, key=key), None


def quarantine_summary(attempts: int, *, reason: str) -> dict[str, Any]:
    """What to log when an item is given up on. Named so all three announcers say it the
    same way and one query finds every quarantined item, whatever queue it was in."""
    return {"attempts": attempts, "max_attempts": MAX_ATTEMPTS, "reason": reason}


__all__ = [
    "BASE_BACKOFF",
    "MAX_ATTEMPTS",
    "MAX_BACKOFF",
    "MAX_JITTER",
    "backoff_for",
    "is_exhausted",
    "may_attempt",
    "next_attempt_at",
    "quarantine_summary",
    "record_failure",
]
