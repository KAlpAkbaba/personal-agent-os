"""The house's stock: levels, the shopping list, the rhythm, and the sentences said about them.

* A level is var / azaldı / bitti. Running low or out puts the item on the list; bought
  ("aldım") sets it full and takes it off.
* A DEPLETION is a move from var (or from nothing) to azaldı/bitti: "azaldı" then "bitti" is
  one depletion, not two. A RESTOCK is "aldım". Both are dated rows in ``household_events``.
* The rhythm (``cycle_days``) is the mean of the last gaps between depletions, from the events
  only - never from the previous rhythm - so computing it twice changes nothing. Two gaps (three
  depletions) are needed; one gap is a coincidence.
* An item whose rhythm says it runs out within a few days is "soon": reminded once per cycle
  (``reminders``), and named in the list read without being put on the list.

The clock is always given (``now``): the tests drive weeks in milliseconds.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.household import parse
from app.household.models import (
    EVENT_DEPLETED,
    EVENT_RESTOCKED,
    NAME_WIDTH,
    QUANTITY_WIDTH,
    HouseholdEvent,
    HouseholdItem,
)

MAX_ITEMS: int = 300
#: Gaps shorter than this are one depletion said twice, not a rhythm.
MIN_GAP_DAYS: Final = 1.0
MIN_GAPS: Final = 2
RHYTHM_GAPS: Final = 5
#: A prediction this many cycles late is stale: the owner stopped saying, nobody is nagged.
STALE_CYCLES: Final = 1.0

SPEECH_LIST_EMPTY: Final = "Alışveriş listesi boş."
SPEECH_WHICH: Final = "Hangi ürün efendim?"

_LEVEL_ALIASES: Final[dict[str, str]] = {
    "var": parse.LEVEL_FULL,
    "aldım": parse.LEVEL_FULL,
    "aldim": parse.LEVEL_FULL,
    "dolu": parse.LEVEL_FULL,
    "azaldı": parse.LEVEL_LOW,
    "azaldi": parse.LEVEL_LOW,
    "az": parse.LEVEL_LOW,
    "bitti": parse.LEVEL_OUT,
    "yok": parse.LEVEL_OUT,
}
_SEVERITY: Final[dict[str | None, int]] = {parse.LEVEL_OUT: 0, parse.LEVEL_LOW: 1}


class HouseholdRefused(ValueError):
    """A request the store cannot keep; ``message`` is the Turkish sentence to say."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ItemChange:
    item: HouseholdItem
    speech: str
    already: bool = False


@dataclass(frozen=True)
class ShoppingList:
    items: list[HouseholdItem] = field(default_factory=list)
    soon: list[HouseholdItem] = field(default_factory=list)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- names


def clean_name(raw: object) -> str:
    """The item's name as it may be kept, or HouseholdRefused."""
    if not isinstance(raw, str):
        raise HouseholdRefused("Ürünün adını yazı olarak söyle.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise HouseholdRefused("Ürün adında okunamayan bir karakter var.")
    name = " ".join(raw.split())
    if not name:
        raise HouseholdRefused("Ürünün adı boş olamaz.")
    if len(name) > NAME_WIDTH:
        raise HouseholdRefused(f"Ürün adı en fazla {NAME_WIDTH} harf olabilir.")
    if not re.search(r"[^\W\d_]", name):
        raise HouseholdRefused("Ürün adında en az bir harf olmalı.")
    return name


def clean_quantity(raw: object) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or any(ord(ch) < 32 for ch in raw):
        raise HouseholdRefused("Miktarı yazı olarak söyle (örneğin 'iki paket').")
    quantity = " ".join(raw.split())
    if len(quantity) > QUANTITY_WIDTH:
        raise HouseholdRefused(f"Miktar en fazla {QUANTITY_WIDTH} harf olabilir.")
    return quantity or None


def _split(raw: object) -> tuple[str, str | None]:
    """The name and the amount said in it: "iki şişe süt" is süt, "iki şişe"."""
    name, amount = parse.split_amount(clean_name(raw))
    if amount is not None:
        problem = parse.quantity_problem(amount)
        if problem is not None:
            raise HouseholdRefused(problem)
    return name, clean_quantity(amount)


def clean_level(raw: object) -> str:
    level = _LEVEL_ALIASES.get(parse.turkish_lower(raw).strip()) if isinstance(raw, str) else None
    if level is None:
        raise HouseholdRefused("Seviye 'var', 'azaldı' ya da 'bitti' olmalı.")
    return level


def _key(name: str) -> str:
    key = parse.item_key(name)
    if not key:
        raise HouseholdRefused("Ürün adında en az bir harf olmalı.")
    return key


def _display(name: str) -> str:
    words = [w for w in parse.turkish_lower(name).split() if w]
    shown = parse.display_name([re.split(r"['’`]", w, maxsplit=1)[0] for w in words])
    return shown[:NAME_WIDTH] or name


# --------------------------------------------------------------------------- reads


def find_item(db: Session, name: str) -> HouseholdItem | None:
    """By key; else the one item whose head word is this name's ("deterjan" finds
    "bulaşık deterjanı" when it is the only deterjan)."""
    key = parse.item_key(name)
    if not key:
        return None
    row = db.scalars(select(HouseholdItem).where(HouseholdItem.key == key)).first()
    if row is not None:
        return row
    if " " in key:
        return None
    rows = [r for r in db.scalars(select(HouseholdItem)) if r.key.split()[-1] == key]
    return rows[0] if len(rows) == 1 else None


def list_items(db: Session) -> list[HouseholdItem]:
    return list(
        db.scalars(select(HouseholdItem).order_by(HouseholdItem.created_at, HouseholdItem.name))
    )


def _locked(db: Session, item_id: uuid.UUID) -> HouseholdItem | None:
    """The row, locked until this request commits; None when it is gone - forgotten by another
    device while this one waited (test team round t-r10070152: 8 concurrent 'unut' answered 200
    twice, 'bitti' racing 'unut' answered 500). SQLite has no row locks and needs none."""
    return db.scalars(
        select(HouseholdItem)
        .where(HouseholdItem.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def _get_or_create(db: Session, name: str, now: datetime) -> HouseholdItem:
    clean = clean_name(name)
    key = _key(clean)
    found = find_item(db, clean)
    row = _locked(db, found.id) if found is not None else None
    if row is not None:
        return row
    count = len(db.scalars(select(HouseholdItem.id)).all())
    if count >= MAX_ITEMS:
        raise HouseholdRefused(f"Ev listesinde en fazla {MAX_ITEMS} ürün tutabilirim.")
    row = HouseholdItem(
        id=uuid.uuid4(),
        name=_display(clean),
        key=key,
        level=None,
        on_list=False,
        created_at=now,
        updated_at=now,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        # Another device said the same new item between the read and this insert and its row
        # holds the key: only the savepoint is undone, and this request lands on that row.
        existing = db.scalars(select(HouseholdItem.id).where(HouseholdItem.key == key)).first()
        won = _locked(db, existing) if existing is not None else None
        if won is None:
            raise
        return won
    return row


# --------------------------------------------------------------------------- writes


def set_level(db: Session, name: str, level: str, *, now: datetime) -> ItemChange:
    level = clean_level(level)
    said, _ = _split(name)
    row = _get_or_create(db, said, now)
    previous = row.level
    if level == parse.LEVEL_FULL:
        was_listed = row.on_list
        row.level = level
        row.on_list = False
        row.list_quantity = None
        row.restocked_at = now
        db.add(HouseholdEvent(id=uuid.uuid4(), item_id=row.id, kind=EVENT_RESTOCKED, at=now))
        speech = (
            f"Tamam, {row.name} alındı; listeden çıkardım."
            if was_listed
            else f"Tamam, {row.name} alındı diye not ettim."
        )
    else:
        if previous in (None, parse.LEVEL_FULL):
            row.depleted_at = now
            db.add(HouseholdEvent(id=uuid.uuid4(), item_id=row.id, kind=EVENT_DEPLETED, at=now))
        row.level = level
        row.on_list = True
        word = "azaldı" if level == parse.LEVEL_LOW else "bitti"
        speech = f"Tamam, {row.name} {word}; listeye ekledim."
    row.updated_at = now
    db.flush()
    recompute_cycle(db, row)
    db.commit()
    return ItemChange(item=row, speech=speech)


def add_to_list(db: Session, name: str, *, quantity: object, now: datetime) -> ItemChange:
    said, spoken = _split(name)
    amount = clean_quantity(quantity) or spoken
    row = _get_or_create(db, said, now)
    if row.on_list and (amount is None or amount == row.list_quantity):
        db.commit()
        return ItemChange(item=row, speech=f"{_cap(row.name)} zaten listede.", already=True)
    row.on_list = True
    if amount is not None:
        row.list_quantity = amount
    row.updated_at = now
    db.commit()
    return ItemChange(item=row, speech=f"Listeye ekledim: {_entry(row)}.")


def remove_from_list(db: Session, name: str, *, now: datetime) -> ItemChange | None:
    row = find_item(db, name)
    if row is None or not row.on_list:
        return None
    row.on_list = False
    row.list_quantity = None
    row.updated_at = now
    db.commit()
    return ItemChange(item=row, speech=f"Listeden çıkardım: {row.name}.")


def remove_by_id(db: Session, item_id: uuid.UUID, *, now: datetime) -> HouseholdItem | None:
    row = _locked(db, item_id)  # a forget racing this one deletes it first, or waits
    if row is None:
        return None
    row.on_list = False
    row.list_quantity = None
    row.updated_at = now
    db.commit()
    return row


def forget_item(db: Session, item_id: uuid.UUID) -> int:
    row = _locked(db, item_id)
    if row is None:
        return 0
    db.execute(delete(HouseholdEvent).where(HouseholdEvent.item_id == item_id))
    db.delete(row)
    db.commit()
    return 1


# --------------------------------------------------------------------------- the rhythm


def recompute_cycle(db: Session, row: HouseholdItem) -> float | None:
    """The mean of the last gaps between depletions - from the events alone."""
    times = [
        _aware(at)
        for at in db.scalars(
            select(HouseholdEvent.at)
            .where(HouseholdEvent.item_id == row.id, HouseholdEvent.kind == EVENT_DEPLETED)
            .order_by(HouseholdEvent.at)
        )
    ]
    gaps: list[float] = []
    for earlier, later in zip(times, times[1:], strict=False):
        assert earlier is not None and later is not None
        days = (later - earlier).total_seconds() / 86400.0
        if days >= MIN_GAP_DAYS:
            gaps.append(days)
    gaps = gaps[-RHYTHM_GAPS:]
    row.cycle_days = round(sum(gaps) / len(gaps), 2) if len(gaps) >= MIN_GAPS else None
    return row.cycle_days


def lead_days(cycle_days: float) -> int:
    """How many days before the predicted run-out to say it: a few, by the rhythm's length."""
    return max(1, min(3, round(cycle_days / 7)))


def cycle_start(row: HouseholdItem) -> datetime | None:
    """Where the current cycle is counted from: the last depletion - or the last purchase when
    it came after the reminder window had already opened. Bought on day 66 against a run-out
    predicted for day 63, the next run-out is about a cycle after the purchase, not "now"."""
    depleted = _aware(row.depleted_at)
    if row.cycle_days is None or depleted is None:
        return None
    restocked = _aware(row.restocked_at)
    window_opens = depleted + timedelta(days=row.cycle_days - lead_days(row.cycle_days))
    if restocked is not None and restocked >= window_opens:
        return restocked
    return depleted


def predicted_out_at(row: HouseholdItem) -> datetime | None:
    start = cycle_start(row)
    if start is None or row.cycle_days is None:
        return None
    return start + timedelta(days=row.cycle_days)


def _is_soon(row: HouseholdItem, now: datetime) -> bool:
    if row.level != parse.LEVEL_FULL or row.on_list or row.cycle_days is None:
        return False
    predicted = predicted_out_at(row)
    if predicted is None:
        return False
    window_opens = predicted - timedelta(days=lead_days(row.cycle_days))
    stale = predicted + timedelta(days=row.cycle_days * STALE_CYCLES)
    return window_opens <= _aware(now) < stale  # type: ignore[operator]


def due_reminders(db: Session, *, now: datetime) -> list[HouseholdItem]:
    """Items to remind about now: soon, and not yet reminded in this cycle."""
    due: list[HouseholdItem] = []
    for row in db.scalars(select(HouseholdItem).where(HouseholdItem.cycle_days.is_not(None))):
        if not _is_soon(row, now):
            continue
        reminded = _aware(row.reminded_at)
        start = cycle_start(row)
        if reminded is not None and start is not None and reminded >= start:
            continue
        due.append(row)
    return sorted(due, key=lambda r: r.name)


def reminder_line(row: HouseholdItem) -> str:
    days = round(row.cycle_days or 0)
    return (
        f"{_cap(row.name)} genelde {days} günde bir bitiyor; birkaç gün içinde bitebilir. "
        "Listeye eklemek için 'listeye ekle' demen yeter."
    )


# --------------------------------------------------------------------------- the list


def shopping_list(db: Session, *, now: datetime) -> ShoppingList:
    rows = list_items(db)
    listed = [r for r in rows if r.on_list]
    listed.sort(key=lambda r: (_SEVERITY.get(r.level, 2), _aware(r.updated_at)))
    soon = sorted((r for r in rows if _is_soon(r, now)), key=lambda r: r.name)
    return ShoppingList(items=listed, soon=soon)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _entry(row: HouseholdItem) -> str:
    name = f"{row.list_quantity} {row.name}" if row.list_quantity else row.name
    if row.level in (parse.LEVEL_LOW, parse.LEVEL_OUT):
        name = f"{name} ({row.level})"
    return name


def _joined(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " ve " + parts[-1]


def list_speech(listed: ShoppingList) -> str:
    entries = [_entry(r) for r in listed.items]
    head = f"Listede {len(entries)} şey var: {_joined(entries)}." if entries else SPEECH_LIST_EMPTY
    if listed.soon:
        head += f" Yakında bitebilir: {_joined([r.name for r in listed.soon])}."
    return head


__all__ = [
    "MAX_ITEMS",
    "SPEECH_LIST_EMPTY",
    "SPEECH_WHICH",
    "HouseholdRefused",
    "ItemChange",
    "ShoppingList",
    "add_to_list",
    "clean_level",
    "clean_name",
    "clean_quantity",
    "cycle_start",
    "due_reminders",
    "find_item",
    "forget_item",
    "lead_days",
    "list_items",
    "list_speech",
    "predicted_out_at",
    "recompute_cycle",
    "remove_by_id",
    "remove_from_list",
    "reminder_line",
    "set_level",
    "shopping_list",
]
