"""The house's two tables.

Canonical schema: ``alembic/versions/20261006_0070_household_stock.py``.
``household_items`` is one row per
thing the house keeps (its level, whether it is on the shopping list, the rhythm learnt for
it); ``household_events`` is one dated row per depletion and per restock - the only input the
rhythm is computed from, so recomputing it never reads its own output.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

NAME_WIDTH = 60
KEY_WIDTH = 80
LEVEL_WIDTH = 8
QUANTITY_WIDTH = 40
KIND_WIDTH = 16

EVENT_DEPLETED = "depleted"
EVENT_RESTOCKED = "restocked"


class HouseholdItem(Base):
    __tablename__ = "household_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(NAME_WIDTH), nullable=False)
    key: Mapped[str] = mapped_column(String(KEY_WIDTH), nullable=False, unique=True)
    #: var | azaldı | bitti, or None when an item was only ever put on the list.
    level: Mapped[str | None] = mapped_column(String(LEVEL_WIDTH), nullable=True)
    on_list: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    list_quantity: Mapped[str | None] = mapped_column(String(QUANTITY_WIDTH), nullable=True)
    usual_quantity: Mapped[str | None] = mapped_column(String(QUANTITY_WIDTH), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    depleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The mean of the last gaps between depletions, in days; None until two gaps are known.
    cycle_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: When the "bitmeden" reminder was sent; one per cycle (compared with depleted_at).
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HouseholdEvent(Base):
    __tablename__ = "household_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("household_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(KIND_WIDTH), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
