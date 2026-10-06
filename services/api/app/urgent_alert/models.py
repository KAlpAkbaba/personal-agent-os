"""The alarm's receipts, kept: one row per alarm Pushover accepted.

In memory a receipt died with the process, and an alarm whose receipt is gone can never be
reported seen or unseen - the phone rings for three hours and the ledger says nothing. The row
is written in the same transaction as the ``alert.sent`` ledger line; the loop closes it with
``seen`` (the phone's "acknowledge", or the owner read it in the inbox - ``source``),
``unseen`` (it rang out) or ``cancelled`` (read elsewhere first, the ringing stopped).

Migration: ``alembic/versions/urgent_alert_receipts.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

OUTCOME_SEEN: Final[str] = "seen"
OUTCOME_UNSEEN: Final[str] = "unseen"
OUTCOME_CANCELLED: Final[str] = "cancelled"
SOURCE_PUSHOVER: Final[str] = "pushover"
SOURCE_INBOX: Final[str] = "inbox"


class UrgentAlertReceiptRow(Base):
    __tablename__ = "urgent_alert_receipts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    notification_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("notifications.id"), nullable=False
    )
    receipt_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: seen | unseen | cancelled; NULL while open.
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Where "seen" came from: pushover (the phone) | inbox (read on the web or a toast).
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=SOURCE_PUSHOVER)


Index(
    "ix_urgent_alert_receipts_open",
    UrgentAlertReceiptRow.closed_at,
    postgresql_where=text("closed_at IS NULL"),
    sqlite_where=text("closed_at IS NULL"),
)


__all__ = [
    "OUTCOME_CANCELLED",
    "OUTCOME_SEEN",
    "OUTCOME_UNSEEN",
    "SOURCE_INBOX",
    "SOURCE_PUSHOVER",
    "UrgentAlertReceiptRow",
]
