"""Native builds a stopped process left mid-flight (Phase 8, 2026-09-11).

A build moves planned -> generating -> building -> testing -> packaging -> validating ->
a verdict inside ONE tool call. If the process running that call stops - a crash, or a
colour drained during a release - the row stays in whichever in-flight state it had
reached, for ever: ``native.check`` then reports a build still "building" that nothing is
building, and ``native.fix``/``native.rebuild`` reason about a row that will never move.

``fail_interrupted_builds`` closes rows still in flight long after anything could still be
working on them. ``planned`` is deliberately NOT in flight: a planned row is waiting for the
owner's "build it", which may come days later.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.nativefactory.models import (
    STATE_BUILDING,
    STATE_FAILED,
    STATE_GENERATING,
    STATE_PACKAGING,
    STATE_TESTING,
    STATE_VALIDATING,
    NativeBuildRow,
)

IN_FLIGHT_STATES: Final[tuple[str, ...]] = (
    STATE_GENERATING,
    STATE_BUILDING,
    STATE_TESTING,
    STATE_PACKAGING,
    STATE_VALIDATING,
)

#: Every step is bounded by its own cap (the device's per-step limits; the lab runner's),
#: and the whole chain ends far inside this. A row still in flight after it was abandoned.
INTERRUPTED_BUILD_AFTER: Final = timedelta(hours=2)

SPEECH_BUILD_INTERRUPTED: Final = (
    "Bu derleme yarıda kaldı efendim; onu yürüten süreç durdu. Yeniden derleyebilirim."
)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def fail_interrupted_builds(
    db: Session, *, now: datetime | None = None, older_than: timedelta = INTERRUPTED_BUILD_AFTER
) -> int:
    now = now or datetime.now(UTC)
    cutoff = now - older_than
    rows = db.execute(
        select(NativeBuildRow).where(NativeBuildRow.state.in_(IN_FLIGHT_STATES))
    ).scalars().all()
    closed = 0
    for row in rows:
        if _aware(row.updated_at) >= cutoff:
            continue
        row.error_class = "interrupted"
        row.error_message = f"{SPEECH_BUILD_INTERRUPTED} (stopped while {row.state})"[:1000]
        row.state = STATE_FAILED
        row.updated_at = now
        closed += 1
    if closed:
        db.commit()
    return closed
