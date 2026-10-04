"""The watch store: what a watch may be, how it is listed, forgotten and told.

The CONTRACT (watch-engine, watch-voice, watch-page): ``create_watch``, ``list_watches``,
``remove_watch``, ``forget_all`` and ``changes_since``, with ``WatchView`` / ``WatchChange``.
A watch is a public http(s) page (``app.research.destination`` decides, as for every fetch),
one of four conditions, 1..168 hours, at most 20. A refusal is ``WatchRefused`` with a
Turkish ``reason_tr``. The functions flush; the caller commits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.research.destination import DestinationPolicyError, validate_fetch_target
from app.watch import compare
from app.watch.models import (
    LABEL_WIDTH,
    SELECTOR_WIDTH,
    URL_WIDTH,
    Watch,
    WatchReading,
)

MAX_WATCHES: Final = 20
MIN_HOURS: Final = 1
MAX_HOURS: Final = 168
LINE_WIDTH: Final = 120


class WatchRefused(ValueError):
    def __init__(self, reason_tr: str, code: str = "watch_refused") -> None:
        super().__init__(reason_tr)
        self.reason_tr = reason_tr
        self.code = code


@dataclass(frozen=True, slots=True)
class WatchView:
    id: str
    label: str
    url: str
    condition: str
    every_hours: int
    selector: str | None
    created_at: datetime
    last_read_at: datetime | None
    last_outcome: str | None
    last_value: float | None
    consecutive_failures: int


@dataclass(frozen=True, slots=True)
class WatchChange:
    watch_id: str
    label: str
    at: datetime
    outcome: str
    value: float | None
    previous_value: float | None
    line_tr: str


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def view_of(watch: Watch) -> WatchView:
    return WatchView(
        id=str(watch.id),
        label=watch.label,
        url=watch.url,
        condition=watch.condition,
        every_hours=watch.every_hours,
        selector=watch.selector,
        created_at=_aware(watch.created_at) or datetime.now(UTC),
        last_read_at=_aware(watch.last_read_at),
        last_outcome=watch.last_outcome,
        last_value=watch.last_value,
        consecutive_failures=watch.consecutive_failures,
    )


# ------------------------------------------------------------------ creation


def _url(raw: object) -> str:
    url = raw.strip() if isinstance(raw, str) else ""
    if not url or len(url) > URL_WIDTH:
        raise WatchRefused("Adres boş ya da çok uzun.")
    try:
        validate_fetch_target(url)
    except DestinationPolicyError as exc:
        raise WatchRefused(
            "Bu adres izlenemez: yalnız herkese açık http(s) sayfalar izlenir."
        ) from exc
    return url


def _label(raw: object) -> str:
    label = " ".join(raw.split()) if isinstance(raw, str) else ""
    if not label:
        raise WatchRefused("Nöbete bir ad ver; ad boş olamaz.")
    if len(label) > LABEL_WIDTH:
        raise WatchRefused(f"Nöbetin adı en çok {LABEL_WIDTH} karakter olabilir.")
    return label


def _hours(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or not MIN_HOURS <= raw <= MAX_HOURS:
        raise WatchRefused(f"Okuma aralığı {MIN_HOURS} ile {MAX_HOURS} saat arasında olmalı.")
    return raw


def _condition(raw: object) -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    try:
        compare.parse_condition(text)
    except ValueError as exc:
        raise WatchRefused(
            "Koşul anlaşılmadı: changed, contains:<metin>, number_below:<sayı> "
            "ya da number_above:<sayı> olmalı."
        ) from exc
    if len(text) > 240:
        raise WatchRefused("Koşul çok uzun.")
    return text


def _selector(raw: object) -> str | None:
    if raw is None:
        return None
    selector = raw.strip() if isinstance(raw, str) else ""
    if not selector or len(selector) > SELECTOR_WIDTH:
        raise WatchRefused(f"Seçici boş olamaz ve en çok {SELECTOR_WIDTH} karakter olabilir.")
    return selector


def create_watch(
    db: Session,
    *,
    url: str,
    condition: str,
    label: str,
    every_hours: int = 6,
    selector: str | None = None,
    now: datetime | None = None,
) -> WatchView:
    moment = now or datetime.now(UTC)
    fields = {
        "label": _label(label),
        "condition": _condition(condition),
        "every_hours": _hours(every_hours),
        "selector": _selector(selector),
        "url": _url(url),
    }
    count = db.execute(select(func.count()).select_from(Watch)).scalar_one()
    if count >= MAX_WATCHES:
        raise WatchRefused(f"En çok {MAX_WATCHES} nöbet tutulabilir; önce birini kaldır.")
    # Due at once: the first reading is the baseline, and it is taken now.
    watch = Watch(
        id=uuid.uuid4(),
        created_at=moment,
        next_due_at=moment,
        consecutive_failures=0,
        **fields,
    )
    db.add(watch)
    db.flush()
    return view_of(watch)


# ------------------------------------------------------------------ listing and forgetting


def list_watches(db: Session) -> list[WatchView]:
    rows = db.execute(select(Watch).order_by(Watch.created_at, Watch.id)).scalars()
    return [view_of(row) for row in rows]


def remove_watch(db: Session, watch_id: uuid.UUID) -> bool:
    db.execute(delete(WatchReading).where(WatchReading.watch_id == watch_id))
    removed = db.execute(delete(Watch).where(Watch.id == watch_id)).rowcount
    db.flush()
    return bool(removed)


def forget_all(db: Session) -> int:
    """Every watch and every reading, at once. The number of watches forgotten."""
    db.execute(delete(WatchReading))
    removed = db.execute(delete(Watch)).rowcount
    db.flush()
    return int(removed or 0)


# ------------------------------------------------------------------ what the owner was told


def format_number(value: float) -> str:
    """19499.0 -> '19.499'; 19499.9 -> '19.499,90' (tr-TR)."""
    whole = int(abs(value))
    fraction = round(abs(value) - whole, 2)
    if fraction >= 1:  # rounding carried into the whole part
        whole, fraction = whole + 1, 0.0
    text = f"{whole:,}".replace(",", ".")
    if fraction:
        text = f"{text},{round(fraction * 100):02d}"
    return f"-{text}" if value < 0 else text


def _cut(text: str, width: int) -> str:
    one = " ".join(text.split())
    return one if len(one) <= width else one[: width - 1].rstrip() + "…"


def line_for(
    *,
    label: str,
    condition: str,
    outcome: str,
    value: float | None,
    baseline: bool,
    reason: str | None = None,
) -> str:
    """One Turkish line of at most 120 characters. Never page text: the label the owner
    gave, the condition the owner set, and at most the one number read."""
    name = _cut(label, 40)
    if outcome == compare.OUTCOME_UNREADABLE:
        return _cut(f"{name}: okunamadı - {reason or 'sebep bilinmiyor'}", LINE_WIDTH)
    if outcome == compare.OUTCOME_CHANGED:
        return _cut(f"{name}: sayfa değişti.", LINE_WIDTH)
    parsed = compare.parse_condition(condition)
    if parsed.kind == compare.KIND_CONTAINS:
        quoted = _cut(parsed.text or "", 40)
        if baseline:
            return _cut(f"{name}: Şu an zaten sayfada '{quoted}' var.", LINE_WIDTH)
        return _cut(f"{name}: sayfada '{quoted}' göründü.", LINE_WIDTH)
    shown = format_number(value) if value is not None else "?"
    limit = format_number(parsed.number) if parsed.number is not None else "?"
    side = "altında" if parsed.kind == compare.KIND_BELOW else "üstünde"
    if baseline:
        return _cut(f"{name}: Şu an zaten {shown} ({limit} {side}).", LINE_WIDTH)
    return _cut(f"{name}: {shown} oldu ({limit} {side}).", LINE_WIDTH)


def _earlier(db: Session, reading: WatchReading) -> list[WatchReading]:
    return list(
        db.execute(
            select(WatchReading)
            .where(WatchReading.watch_id == reading.watch_id)
            .where(WatchReading.read_at < reading.read_at)
            .order_by(WatchReading.read_at.desc())
        ).scalars()
    )


def change_of(db: Session, watch: Watch, reading: WatchReading) -> WatchChange:
    earlier = _earlier(db, reading)
    previous = next((r.value for r in earlier if r.value is not None), None)
    baseline = not any(r.outcome != compare.OUTCOME_UNREADABLE for r in earlier)
    return WatchChange(
        watch_id=str(watch.id),
        label=watch.label,
        at=_aware(reading.read_at) or reading.read_at,
        outcome=reading.outcome,
        value=reading.value,
        previous_value=previous,
        line_tr=line_for(
            label=watch.label,
            condition=watch.condition,
            outcome=reading.outcome,
            value=reading.value,
            baseline=baseline,
            reason=reading.reason,
        ),
    )


def changes_since(db: Session, since: datetime) -> list[WatchChange]:
    """What the owner was told since ``since`` (the readings that notified), oldest first."""
    rows = db.execute(
        select(WatchReading, Watch)
        .join(Watch, Watch.id == WatchReading.watch_id)
        .where(WatchReading.notified.is_(True))
        .where(WatchReading.read_at >= since)
        .order_by(WatchReading.read_at, WatchReading.id)
    ).all()
    return [change_of(db, watch, reading) for reading, watch in rows]
