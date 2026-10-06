"""Bank mails into the ledger: a balance kept with its time, a spend confirmed or booked once.

Input is ``mail_index`` - what the mail poll already listed from the owner's own inbox (sender,
subject, the first lines of the body). Each bank mail is read ONCE: its reference
(``<account_key>:<Message-ID>``) is written to ``money_bank_notices`` whatever it said, so the
next poll skips it. Nothing here contacts a bank.

**Never counted twice.** A card spend in a bank mail first looks for a spend JARVIS already
holds that it is the same as: not cancelled, not cash, not already confirmed by a bank mail,
the same amount to the kuruş, and heard between ``MATCH_BEFORE`` before and ``MATCH_AFTER``
after the mail's time (the card is swiped at the till, the mail follows within minutes; a
conversation's time is the time the price was agreed). The closest in time wins and the mail's
reference is written ONTO it - confirmed, not added. Only when none is found is the mail a
new, confirmed row of its own.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.mail.models import MailIndexRow
from app.money import banks, categories, service
from app.money.models import MoneyBalance, MoneyBankNotice, MoneyEntry

logger = get_logger("app.money.ledger")

#: A conversation spend up to this long BEFORE the bank mail is the same spend...
MATCH_BEFORE: Final = timedelta(hours=12)
#: ...and this long after it (a clock skew, a price agreed just after the swipe).
MATCH_AFTER: Final = timedelta(minutes=30)
#: How far back the inbox is read for bank mails on each pass.
LOOKBACK: Final = timedelta(days=40)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def mail_ref(row: MailIndexRow) -> str:
    return f"{row.account_key or ''}:{row.provider_message_id}"[:600]


def find_match(db: Session, kurus: int, at: datetime) -> MoneyEntry | None:
    at = _aware(at)
    candidates = db.scalars(
        select(MoneyEntry).where(
            MoneyEntry.direction == "out",
            MoneyEntry.amount_kurus == kurus,
            MoneyEntry.status != service.STATUS_CANCELLED,
            MoneyEntry.bank_ref.is_(None),
            MoneyEntry.source != service.SOURCE_BANK,
            (MoneyEntry.method.is_(None)) | (MoneyEntry.method != service.METHOD_CASH),
        )
    ).all()
    near = [
        row
        for row in candidates
        if at - MATCH_BEFORE <= _aware(row.occurred_at) <= at + MATCH_AFTER
    ]
    if not near:
        return None
    return min(near, key=lambda row: abs((_aware(row.occurred_at) - at).total_seconds()))


def _keep_balance(db: Session, notice: banks.BankNotice, ref: str, *, now: datetime) -> bool:
    if notice.balance_kurus is None:
        return False
    row = db.scalars(select(MoneyBalance).where(MoneyBalance.bank == notice.bank.key)).first()
    if row is not None and _aware(row.as_of) >= _aware(notice.at):
        return False  # an older mail never overwrites a newer balance
    if row is None:
        row = MoneyBalance(id=uuid.uuid4(), bank=notice.bank.key)
        db.add(row)
    row.balance_kurus = notice.balance_kurus
    row.as_of = notice.at
    row.source_ref = ref
    row.updated_at = now
    return True


def apply_notice(
    db: Session, notice: banks.BankNotice, ref: str, *, now: datetime
) -> tuple[str, MoneyEntry | None]:
    """One parsed bank mail into the ledger: (outcome, the entry it touched)."""
    entry: MoneyEntry | None = None
    outcome = "none"
    if notice.kind == banks.KIND_SPEND and notice.amount_kurus:
        category = categories.categorize(notice.merchant)
        entry = find_match(db, notice.amount_kurus, notice.at)
        if entry is not None:
            entry.status = service.STATUS_CONFIRMED
            entry.bank_ref = ref
            entry.bank = notice.bank.key
            entry.confirmed_at = now
            entry.updated_at = now
            entry.category = entry.category or category
            if notice.merchant and not entry.description.startswith(notice.merchant):
                entry.description = f"{notice.merchant} - {entry.description}"[:120]
            outcome = "matched"
        else:
            entry = MoneyEntry(
                id=uuid.uuid4(),
                direction="out",
                amount_kurus=notice.amount_kurus,
                status=service.STATUS_CONFIRMED,
                source=service.SOURCE_BANK,
                method=service.METHOD_CARD,
                category=category,
                description=(notice.merchant or notice.bank.name)[:120],
                occurred_at=notice.at,
                created_at=now,
                updated_at=now,
                confirmed_at=now,
                bank_ref=ref,
                bank=notice.bank.key,
            )
            db.add(entry)
            outcome = "booked"
    elif notice.kind == banks.KIND_INCOME and notice.amount_kurus:
        entry = MoneyEntry(
            id=uuid.uuid4(),
            direction="in",
            amount_kurus=notice.amount_kurus,
            status=service.STATUS_CONFIRMED,
            source=service.SOURCE_BANK,
            method=None,
            description=f"{notice.bank.name} - gelen para",
            occurred_at=notice.at,
            created_at=now,
            updated_at=now,
            confirmed_at=now,
            bank_ref=ref,
            bank=notice.bank.key,
        )
        db.add(entry)
        outcome = "booked"
    return outcome, entry


def ingest_mail(db: Session, *, now: datetime) -> dict[str, int]:
    """Every bank mail in the index not read yet, read once. Commits."""
    counts = {"read": 0, "matched": 0, "booked": 0, "balances": 0, "unparsed": 0}
    seen = set(db.scalars(select(MoneyBankNotice.ref)).all())
    since = _aware(now) - LOOKBACK
    rows = db.scalars(
        select(MailIndexRow).where(MailIndexRow.date.is_not(None), MailIndexRow.date >= since)
    ).all()
    for row in rows:
        bank = banks.bank_for(row.from_email)
        if bank is None:
            continue
        ref = mail_ref(row)
        if ref in seen:
            continue
        seen.add(ref)
        at = _aware(row.date) if row.date else _aware(now)
        notice = banks.parse_notice(row.from_email, row.subject, row.snippet, at=at)
        counts["read"] += 1
        record = MoneyBankNotice(
            id=uuid.uuid4(), ref=ref, bank=bank.key, at=at, created_at=now, outcome="none"
        )
        if notice is None:
            record.kind = "unparsed"
            counts["unparsed"] += 1
        else:
            record.kind = notice.kind
            record.amount_kurus = notice.amount_kurus
            record.balance_kurus = notice.balance_kurus
            record.merchant = notice.merchant
            outcome, entry = apply_notice(db, notice, ref, now=now)
            db.flush()
            record.outcome = outcome
            record.entry_id = entry.id if entry is not None else None
            if outcome in ("matched", "booked"):
                counts[outcome] += 1
            if _keep_balance(db, notice, ref, now=now):
                counts["balances"] += 1
        db.add(record)
        db.commit()
    if counts["read"]:
        logger.info("money_bank_mail_read", **counts)
    return counts


def after_mail_poll(db: Session, now: datetime) -> Any:
    """The mail poller's hook (``app.mail.poller.MailPoller(after_poll=...)``)."""
    return ingest_mail(db, now=now)


__all__ = [
    "LOOKBACK",
    "MATCH_AFTER",
    "MATCH_BEFORE",
    "after_mail_poll",
    "find_match",
    "ingest_mail",
]
