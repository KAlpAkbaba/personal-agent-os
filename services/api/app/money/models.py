"""The money ledger's tables (money-ledger).

Canonical schema: ``alembic/versions/20261006_0071_money_ledger.py``.

* ``money_entries`` - one row per spend or income JARVIS knows of: the amount in kuruş, the
  status (tentative / confirmed / cancelled), where it came from (a conversation, a bank mail,
  his answer to a question, his own words), how it was paid (card / cash), the category and
  when it happened. A bank mail that confirms a tentative row writes its reference ONTO that
  row (``bank_ref``, unique) - it never adds a second one, so one spend is counted once.
* ``money_balances`` - the last balance each bank's mail stated, with the mail's time.
* ``money_bank_notices`` - one row per bank mail READ (unique reference), parsed or not, and
  the entry it booked or confirmed: a mail is read once however many polls see it.
* ``money_questions`` - "X liralık bir harcama yaptınız mı?": the spend he is (to be) asked
  about, when, and his answer. Unique per (conversation, segment): asked once.
* ``money_scans`` - how far each conversation has been read for spends.

No column holds a bank password, a card number or an account number.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

REF_WIDTH = 600
BANK_WIDTH = 40
DESCRIPTION_WIDTH = 120
CATEGORY_WIDTH = 24


class MoneyEntry(Base):
    __tablename__ = "money_entries"
    __table_args__ = (
        CheckConstraint("amount_kurus > 0", name="ck_money_entries_amount_positive"),
        CheckConstraint(
            "status IN ('tentative', 'confirmed', 'cancelled')", name="ck_money_entries_status"
        ),
        CheckConstraint("direction IN ('out', 'in')", name="ck_money_entries_direction"),
        UniqueConstraint("bank_ref", name="uq_money_entries_bank_ref"),
        UniqueConstraint(
            "conversation_id", "segment_seq", name="uq_money_entries_conversation_segment"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    direction: Mapped[str] = mapped_column(String(4), nullable=False, default="out")
    amount_kurus: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    #: conversation | bank | question | voice | web
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    #: card | cash | None (not known: a bank mail of money in)
    method: Mapped[str | None] = mapped_column(String(8), nullable=True)
    category: Mapped[str | None] = mapped_column(String(CATEGORY_WIDTH), nullable=True)
    description: Mapped[str] = mapped_column(String(DESCRIPTION_WIDTH), nullable=False, default="")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Where a conversation spend was heard (both None for every other source).
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    segment_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The bank mail that booked or confirmed this row: "<account_key>:<Message-ID>".
    bank_ref: Mapped[str | None] = mapped_column(String(REF_WIDTH), nullable=True)
    bank: Mapped[str | None] = mapped_column(String(BANK_WIDTH), nullable=True)


class MoneyBalance(Base):
    __tablename__ = "money_balances"
    __table_args__ = (UniqueConstraint("bank", name="uq_money_balances_bank"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bank: Mapped[str] = mapped_column(String(BANK_WIDTH), nullable=False)
    balance_kurus: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The time of the MAIL that stated it - the "as of" he is told.
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(REF_WIDTH), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MoneyBankNotice(Base):
    __tablename__ = "money_bank_notices"
    __table_args__ = (UniqueConstraint("ref", name="uq_money_bank_notices_ref"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref: Mapped[str] = mapped_column(String(REF_WIDTH), nullable=False)
    bank: Mapped[str] = mapped_column(String(BANK_WIDTH), nullable=False)
    #: spend | income | balance | unparsed
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    amount_kurus: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    balance_kurus: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    merchant: Mapped[str | None] = mapped_column(String(DESCRIPTION_WIDTH), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: matched (a tentative row confirmed) | booked (a new row) | none
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("money_entries.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MoneyQuestion(Base):
    __tablename__ = "money_questions"
    __table_args__ = (
        UniqueConstraint("conversation_id", "anchor_seq", name="uq_money_questions_anchor"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    anchor_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_kurus: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    method: Mapped[str] = mapped_column(String(8), nullable=False, default="card")
    category: Mapped[str | None] = mapped_column(String(CATEGORY_WIDTH), nullable=True)
    #: no_acceptance | two_prices | unknown_speaker
    reason: Mapped[str] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: yes | no | None (not answered)
    answer: Mapped[str | None] = mapped_column(String(8), nullable=True)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("money_entries.id", ondelete="SET NULL"), nullable=True
    )


class MoneyScan(Base):
    __tablename__ = "money_scans"

    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Set once the ended conversation's questions were written: it is never read again.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


MONEY_TABLES = (
    MoneyEntry.__table__,
    MoneyBalance.__table__,
    MoneyBankNotice.__table__,
    MoneyQuestion.__table__,
    MoneyScan.__table__,
)

__all__ = [
    "MONEY_TABLES",
    "MoneyBalance",
    "MoneyBankNotice",
    "MoneyEntry",
    "MoneyQuestion",
    "MoneyScan",
]
