"""Deterministic collector: ledger rows for a period (and a device) -> :class:`NarrativeFacts`.

Reads only through ``app.ledger.service.query`` (read-only). The ledger has no device column;
a writer that knows the machine puts it in ``detail_json["device"]`` and a row that names none
is a cloud row (``bulut``). A device word that matches nothing selects NOTHING - an empty
answer the narrator says plainly - never everything.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.devices.aliases import extract_alias
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import STATUS_COMPLETED, STATUS_FAILED
from app.narrative.facts import (
    DEVICE_CLOUD,
    NO_CAPABLE_DEVICE,
    FactEvent,
    NarrativeFacts,
    Period,
)

_PAGE = 200  # ledger_service.query clamps its limit to 200


def fold(text: str) -> str:
    return text.strip().replace("İ", "i").casefold()


def resolve_period(word: str, now: datetime) -> Period:
    """'bu hafta' = the last 7 days ending now; 'bugün' = UTC midnight..now; 'dün' = the UTC
    day before. Anything else is refused - a guessed window would narrate the wrong rows."""
    w = fold(word)
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if w in ("bu hafta", "son 7 gün", "son bir hafta"):
        return Period(now - timedelta(days=7), now, "bu hafta")
    if w in ("bugün", "bugun"):
        return Period(midnight, now, "bugün")
    if w in ("dün", "dun"):
        return Period(midnight - timedelta(days=1), midnight, "dün")
    raise ValueError(f"unknown period: {word!r}")


def canonical_device(word: str) -> str:
    """'ofiste' -> 'ofis', 'bulut' stays, a device name is its folded self."""
    alias = extract_alias(word)
    if alias is not None:
        return alias
    folded = fold(word)
    return DEVICE_CLOUD if folded in ("bulut", "cloud") else folded


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


def _row_device(row: ActivityEventRow) -> str:
    named = (row.detail_json or {}).get("device")
    return canonical_device(named) if isinstance(named, str) and named.strip() else DEVICE_CLOUD


def _is_no_capable(row: ActivityEventRow) -> bool:
    return (row.detail_json or {}).get("error_class") == NO_CAPABLE_DEVICE or (
        row.result == NO_CAPABLE_DEVICE
    )


def _fetch(db: Session, period: Period) -> list[ActivityEventRow]:
    """Every failed/completed row of the window. The ledger query is capped at one page, so
    pages walk back in time; ties on the boundary timestamp are re-read and de-duplicated."""
    seen: dict[object, ActivityEventRow] = {}
    cursor = period.end
    while True:
        page = ledger_service.query(
            db,
            since=period.start,
            until=cursor,
            statuses=(STATUS_FAILED, STATUS_COMPLETED),
            limit=_PAGE,
        )
        fresh = [r for r in page if r.event_id not in seen]
        for r in fresh:
            seen[r.event_id] = r
        if len(page) < _PAGE or not fresh:
            break
        cursor = min(_aware(r.occurred_at) for r in page)
    return [r for r in seen.values() if _aware(r.occurred_at) < period.end]


def _fact(row: ActivityEventRow) -> FactEvent:
    return FactEvent(
        event_id=str(row.event_id),
        occurred_at=_aware(row.occurred_at),
        subsystem=row.subsystem,
        event_type=row.event_type,
        summary=row.factual_summary,
        reason=row.result,
        device=_row_device(row),
    )


def _newest_first(events: list[FactEvent]) -> tuple[FactEvent, ...]:
    return tuple(sorted(events, key=lambda e: (-e.occurred_at.timestamp(), e.event_id)))


def collect(
    db: Session,
    period: Period | str,
    device: str | None = None,
    *,
    now: datetime | None = None,
) -> NarrativeFacts:
    now = now or ledger_service.utcnow()
    window = resolve_period(period, now) if isinstance(period, str) else period
    wanted = canonical_device(device) if device and device.strip() else None

    failed: list[FactEvent] = []
    completed: dict[str, list[FactEvent]] = defaultdict(list)
    no_capable: list[FactEvent] = []
    by_subsystem: dict[str, int] = defaultdict(int)
    by_device: dict[str, int] = defaultdict(int)
    for row in _fetch(db, window):
        fact = _fact(row)
        if wanted is not None and fact.device != wanted:
            continue
        by_subsystem[fact.subsystem] += 1
        by_device[fact.device] += 1
        if row.status == STATUS_FAILED:
            failed.append(fact)
            if _is_no_capable(row):
                no_capable.append(fact)
        else:
            completed[fact.subsystem].append(fact)

    return NarrativeFacts(
        covered=Period(window.start, min(window.end, now), window.label),
        device=wanted,
        failed=_newest_first(failed),
        completed=tuple((sub, _newest_first(evs)) for sub, evs in sorted(completed.items())),
        counts_by_subsystem=tuple(sorted(by_subsystem.items())),
        counts_by_device=tuple(sorted(by_device.items())),
        no_capable_device=_newest_first(no_capable),
        total=sum(by_subsystem.values()),
    )


__all__ = ["Period", "canonical_device", "collect", "fold", "resolve_period"]
