"""The generic durable object focus (docs/M19_DIGITAL_OPERATOR_SPEC.md §4, ADR-0082).

``app.research.focus`` answers "which research is the owner pointing at?" for one kind of
object; this module answers the same question for every OTHER kind an owner utterance can
point a deictic word at ("bunu kapat", "öndeki pencere", "bu kutuya yaz") - starting with
``window`` (M19) and left open for ``app`` and future kinds (M20-M28) without a new table.

Append-only, most-recent-row-wins, exactly like ``ResearchFocusRow`` (ADR-0076): setting
focus on an object that is already current appends again on purpose, because recency IS
the ordering and an UPDATE would erase the history "the previous one" reads.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.operator.models import FOCUS_KINDS, FOCUS_STACK_LIMIT, ObjectFocusRow

#: How many rows to scan to find the last ``FOCUS_STACK_LIMIT`` DISTINCT objects. The
#: table appends, so the same object may hold several consecutive rows; a run of repeats
#: must not hide older objects behind it (mirrors app.research.focus._SCAN_ROWS).
_SCAN_ROWS = FOCUS_STACK_LIMIT * 6

#: "Most recent" must never fall back to a tiebreak on a random UUID. Two focus writes
#: from the SAME process (two operator steps a few milliseconds apart; two calls in one
#: test) can land on the identical ``datetime.now(UTC)`` reading on a coarse wall clock
#: (Windows' default resolution is far coarser than a microsecond), and ``_stack``'s
#: secondary sort key (row id) is not chronological. A caller that already has a real,
#: meaningful moment (a device receipt's own timestamp) passes ``now=`` explicitly and
#: bypasses this entirely; only the default path is nudged.
_focus_clock_lock = threading.Lock()
_focus_last_selected_at: datetime | None = None


def _next_default_selected_at() -> datetime:
    global _focus_last_selected_at
    with _focus_clock_lock:
        now = datetime.now(UTC)
        if _focus_last_selected_at is not None and now <= _focus_last_selected_at:
            now = _focus_last_selected_at + timedelta(microseconds=1)
        _focus_last_selected_at = now
        return now


class UnknownFocusKind(ValueError):
    """``kind`` is not one of :data:`app.operator.models.FOCUS_KINDS`."""


@dataclass(frozen=True, slots=True)
class FocusEntry:
    kind: str
    object_id: str
    label: str = ""
    source: str = ""
    selected_at: datetime | None = None
    meta: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "object_id": self.object_id,
            "label": self.label,
            "source": self.source,
            "selected_at": _iso(self.selected_at),
            "meta": dict(self.meta or {}),
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_kind(kind: str) -> str:
    if kind not in FOCUS_KINDS:
        raise UnknownFocusKind(f"unknown object focus kind: {kind!r}")
    return kind


def set_focus(
    db: Session,
    kind: str,
    object_id: str,
    *,
    label: str = "",
    source: str = "",
    owner_session_id: uuid.UUID | None = None,
    now: datetime | None = None,
    meta: dict[str, Any] | None = None,
) -> ObjectFocusRow:
    """Append one focus row: ``object_id`` of ``kind`` is now the one the owner points at."""
    _validate_kind(kind)
    row = ObjectFocusRow(
        id=uuid.uuid4(),
        owner_session_id=owner_session_id,
        kind=kind,
        object_id=str(object_id)[:200],
        label=str(label or "")[:200],
        source=str(source or "")[:32],
        selected_at=now or _next_default_selected_at(),
        meta_json=dict(meta or {}),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _stack(db: Session, kind: str) -> list[ObjectFocusRow]:
    _validate_kind(kind)
    stmt = (
        select(ObjectFocusRow)
        .where(ObjectFocusRow.kind == kind)
        .order_by(ObjectFocusRow.selected_at.desc(), ObjectFocusRow.id.desc())
        .limit(_SCAN_ROWS)
    )
    return list(db.execute(stmt).scalars().all())


def _distinct(rows: list[ObjectFocusRow]) -> list[ObjectFocusRow]:
    seen: set[str] = set()
    out: list[ObjectFocusRow] = []
    for row in rows:
        if row.object_id in seen:
            continue
        seen.add(row.object_id)
        out.append(row)
        if len(out) >= FOCUS_STACK_LIMIT:
            break
    return out


def _entry(row: ObjectFocusRow) -> FocusEntry:
    return FocusEntry(
        kind=row.kind,
        object_id=row.object_id,
        label=row.label,
        source=row.source,
        selected_at=row.selected_at,
        meta=dict(row.meta_json or {}),
    )


def current(db: Session, kind: str) -> FocusEntry | None:
    """The most recently focused object of ``kind``, or ``None``."""
    rows = _distinct(_stack(db, kind))
    return _entry(rows[0]) if rows else None


def previous(db: Session, kind: str) -> FocusEntry | None:
    """The most recent object of ``kind`` DIFFERENT from the current one, or ``None``."""
    rows = _distinct(_stack(db, kind))
    return _entry(rows[1]) if len(rows) > 1 else None


def stack(db: Session, kind: str) -> list[FocusEntry]:
    """The distinct objects of ``kind``, most recent first, bounded to
    :data:`app.operator.models.FOCUS_STACK_LIMIT`."""
    return [_entry(r) for r in _distinct(_stack(db, kind))]


__all__ = [
    "FocusEntry",
    "UnknownFocusKind",
    "current",
    "previous",
    "set_focus",
    "stack",
]
