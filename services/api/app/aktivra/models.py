"""``aktivra_events``: one row per event Aktivra's assistant sent and this system accepted.

The event id is unique - a retry finds its row and gets the same notification back instead of
ringing the phone twice - and the row counts the hourly cap, so a restart resets neither. A
row lives 30 days (``app.aktivra.service.sweep_expired``); the notification it made follows
its own rules. No column holds the token, the title or anything of the company's.

Migration: ``alembic/versions/aktivra_events.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class AktivraEventRow(Base):
    __tablename__ = "aktivra_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: Set in the same request; NULL only between the claim and the notification's commit
    #: (the claim comes first so a concurrent duplicate never makes a second notification).
    notification_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("notifications.id"), nullable=True
    )
    #: important | info
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


__all__ = ["AktivraEventRow"]
