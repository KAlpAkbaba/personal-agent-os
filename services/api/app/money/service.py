"""The ledger's rules: book, confirm, cancel, answer, and say what is known.

JARVIS's OWN record of the owner's money - never the bank. Nothing here moves money, and
nothing here can: there is no transport, no bank credential, no payment call. Every write is
a row in this database the owner can see on /money and take back.

Statuses: a spend heard in a conversation is ``tentative`` until a bank mail with the same
amount in the time window confirms it (``app.money.ledger``); his "evet" to a question, a
cash spend he states and a bank mail of its own are ``confirmed``; "geri al" makes a row
``cancelled`` (kept, never counted, never matched).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.money import categories
from app.money.amounts import format_liralik, format_tl
from app.money.models import MoneyBalance, MoneyEntry, MoneyQuestion

STATUS_TENTATIVE: Final = "tentative"
STATUS_CONFIRMED: Final = "confirmed"
STATUS_CANCELLED: Final = "cancelled"

SOURCE_CONVERSATION: Final = "conversation"
SOURCE_BANK: Final = "bank"
SOURCE_QUESTION: Final = "question"
SOURCE_VOICE: Final = "voice"
SOURCE_WEB: Final = "web"

METHOD_CARD: Final = "card"
METHOD_CASH: Final = "cash"

#: The largest amount the ledger takes from a sentence or a form (10 million TL): a mis-heard
#: "on milyon" is refused, not booked.
MAX_KURUS: Final = 10_000_000 * 100
#: "Geri al" reaches a booking this recent; "evet" a question asked this recently.
UNDO_WINDOW: Final = timedelta(minutes=30)
ANSWER_WINDOW: Final = timedelta(hours=12)

ZONE: Final = ZoneInfo("Europe/Istanbul")

SPEECH_NO_BALANCE: Final = (
    "Bakiyenizi bilmiyorum efendim: bankadan bakiye yazan bir bildirim postası henüz gelmedi."
)
SPEECH_HOW_MUCH: Final = "Ne kadar efendim?"


class MoneyRefused(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def check_amount(kurus: object) -> int:
    if isinstance(kurus, bool) or not isinstance(kurus, int) or kurus <= 0:
        raise MoneyRefused("Tutarı anlayamadım efendim.")
    if kurus > MAX_KURUS:
        raise MoneyRefused("Bu tutar defter için fazla büyük; bir daha söyler misiniz?")
    return kurus


def book_spend(
    db: Session,
    kurus: int,
    *,
    status: str,
    source: str,
    method: str | None,
    occurred_at: datetime,
    now: datetime,
    description: str = "",
    category: str | None = None,
    conversation_id: uuid.UUID | None = None,
    segment_seq: int | None = None,
    bank_ref: str | None = None,
    bank: str | None = None,
    direction: str = "out",
) -> MoneyEntry:
    row = MoneyEntry(
        id=uuid.uuid4(),
        direction=direction,
        amount_kurus=check_amount(kurus),
        status=status,
        source=source,
        method=method,
        category=categories.normalize(category) if category else None,
        description=(description or "")[:120],
        occurred_at=occurred_at,
        created_at=now,
        updated_at=now,
        confirmed_at=now if status == STATUS_CONFIRMED else None,
        conversation_id=conversation_id,
        segment_seq=segment_seq,
        bank_ref=bank_ref,
        bank=bank,
    )
    db.add(row)
    db.commit()
    return row


def cancel(db: Session, entry_id: uuid.UUID, *, now: datetime) -> MoneyEntry | None:
    row = db.get(MoneyEntry, entry_id)
    if row is None:
        return None
    if row.status != STATUS_CANCELLED:
        row.status = STATUS_CANCELLED
        row.cancelled_at = now
        row.updated_at = now
        db.commit()
    return row


def last_undoable(db: Session, *, now: datetime) -> MoneyEntry | None:
    """The newest spend he could mean by "geri al": booked by JARVIS (not a bank mail) in the
    last ``UNDO_WINDOW``, not already cancelled."""
    rows = db.scalars(
        select(MoneyEntry)
        .where(
            MoneyEntry.status != STATUS_CANCELLED,
            MoneyEntry.source != SOURCE_BANK,
        )
        .order_by(MoneyEntry.created_at.desc())
        .limit(5)
    ).all()
    for row in rows:
        if timedelta(0) <= _aware(now) - _aware(row.created_at) <= UNDO_WINDOW:
            return row
    return None


def booked_line(row: MoneyEntry) -> str:
    """The one line said when a conversation spend is booked."""
    paid = "nakit " if row.method == METHOD_CASH else ""
    return (
        f"{format_tl(row.amount_kurus)} {paid}harcamayı deftere geçici olarak yazdım. "
        "Yanlışsa 'geri al' deyin."
    )


def question_line(question: MoneyQuestion) -> str:
    if question.amount_kurus:
        return (
            f"{format_liralik(question.amount_kurus)} bir harcama yaptınız mı? "
            "'Evet', 'hayır' ya da 'evet ama 750' gibi söyleyebilirsiniz."
        )
    return "Az önceki konuşmada bir harcama yaptınız mı? Tutarını söyleyebilirsiniz."


def open_question(db: Session, *, now: datetime) -> MoneyQuestion | None:
    """The newest question asked and not answered, asked in the last ``ANSWER_WINDOW``."""
    row = db.scalars(
        select(MoneyQuestion)
        .where(MoneyQuestion.asked_at.is_not(None), MoneyQuestion.answered_at.is_(None))
        .order_by(MoneyQuestion.asked_at.desc())
        .limit(1)
    ).first()
    if row is None or row.asked_at is None:
        return None
    if _aware(now) - _aware(row.asked_at) > ANSWER_WINDOW:
        return None
    return row


def answer_yes(
    db: Session, question: MoneyQuestion, *, kurus: int | None, now: datetime
) -> MoneyEntry:
    amount = check_amount(kurus if kurus is not None else question.amount_kurus)
    row = book_spend(
        db,
        amount,
        status=STATUS_CONFIRMED,
        source=SOURCE_QUESTION,
        method=question.method,
        occurred_at=question.occurred_at,
        now=now,
        description="Konuşmadan (sizin onayınızla)",
        category=question.category,
    )
    question.answer = "yes"
    question.answered_at = now
    question.entry_id = row.id
    db.commit()
    return row


def answer_no(db: Session, question: MoneyQuestion, *, now: datetime) -> None:
    question.answer = "no"
    question.answered_at = now
    db.commit()


# ------------------------------------------------------------------ what is known


def _local(moment: datetime) -> datetime:
    return _aware(moment).astimezone(ZONE)


def when_said(moment: datetime, *, now: datetime) -> str:
    """ "bugün 14:05", "dün 09:30", "3 Ekim 18:00" - the time a balance is as of."""
    local, today = _local(moment), _local(now).date()
    clock = local.strftime("%H:%M")
    if local.date() == today:
        return f"bugün {clock}"
    if local.date() == today - timedelta(days=1):
        return f"dün {clock}"
    months = (
        "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
        "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
    )  # fmt: skip
    return f"{local.day} {months[local.month - 1]} {clock}"


def balances(db: Session) -> list[MoneyBalance]:
    return list(db.scalars(select(MoneyBalance).order_by(MoneyBalance.as_of.desc())).all())


def bank_name(key: str) -> str:
    from app.money.banks import BANKS

    return next((b.name for b in BANKS if b.key == key), key)


def balance_speech(db: Session, *, now: datetime) -> str:
    rows = balances(db)
    if not rows:
        return SPEECH_NO_BALANCE
    parts = [
        f"{bank_name(r.bank)} hesabınızda {format_tl(r.balance_kurus)} "
        f"({when_said(r.as_of, now=now)} postasına göre)"
        for r in rows
    ]
    return "Son bilinen bakiye: " + "; ".join(parts) + " efendim."


@dataclass(frozen=True)
class Spent:
    category: str | None
    since: datetime
    total_kurus: int
    count: int
    tentative: int
    cash_kurus: int


def month_start(now: datetime) -> datetime:
    local = _local(now)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def spent(db: Session, *, category: str | None, now: datetime) -> Spent:
    """This calendar month's spends (the owner's wall clock), cancelled ones excluded."""
    since = month_start(now)
    query = select(MoneyEntry).where(
        MoneyEntry.direction == "out",
        MoneyEntry.status != STATUS_CANCELLED,
        MoneyEntry.occurred_at >= since,
    )
    if category:
        query = query.where(MoneyEntry.category == category)
    rows = db.scalars(query).all()
    return Spent(
        category=category,
        since=since,
        total_kurus=sum(r.amount_kurus for r in rows),
        count=len(rows),
        tentative=sum(1 for r in rows if r.status == STATUS_TENTATIVE),
        cash_kurus=sum(r.amount_kurus for r in rows if r.method == METHOD_CASH),
    )


def spent_speech(summary: Spent) -> str:
    where = (
        f"{categories.SPOKEN.get(summary.category, summary.category)} " if summary.category else ""
    )
    if not summary.count:
        return f"Bu ay {where}bir harcama kaydım yok efendim."
    said = f"Bu ay {where}{format_tl(summary.total_kurus)} harcadınız ({summary.count} harcama)"
    if summary.cash_kurus:
        said += f", {format_tl(summary.cash_kurus)} nakit"
    if summary.tentative:
        said += f"; {summary.tentative} tanesi henüz banka postasıyla doğrulanmadı"
    return said + " efendim."


def entries(db: Session, *, limit: int = 100) -> list[MoneyEntry]:
    return list(
        db.scalars(select(MoneyEntry).order_by(MoneyEntry.occurred_at.desc()).limit(limit)).all()
    )


def questions(db: Session, *, limit: int = 20) -> list[MoneyQuestion]:
    return list(
        db.scalars(
            select(MoneyQuestion)
            .where(MoneyQuestion.asked_at.is_not(None))
            .order_by(MoneyQuestion.asked_at.desc())
            .limit(limit)
        ).all()
    )


__all__ = [
    "METHOD_CARD",
    "METHOD_CASH",
    "SOURCE_BANK",
    "SOURCE_CONVERSATION",
    "SOURCE_QUESTION",
    "SOURCE_VOICE",
    "SOURCE_WEB",
    "STATUS_CANCELLED",
    "STATUS_CONFIRMED",
    "STATUS_TENTATIVE",
    "MoneyRefused",
    "Spent",
    "answer_no",
    "answer_yes",
    "balance_speech",
    "book_spend",
    "booked_line",
    "cancel",
    "entries",
    "last_undoable",
    "open_question",
    "question_line",
    "spent",
    "spent_speech",
]
