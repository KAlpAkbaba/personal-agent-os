"""SQLAlchemy ORM models (M0: singleton owner only).

The canonical schema is managed by Alembic migrations under alembic/versions.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        dict[str, Any]: JSON().with_variant(JSONB(), "postgresql"),
    }


class Owner(Base):
    """Singleton owner row (exactly one active row by product invariant)."""

    __tablename__ = "owner"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    locale: Mapped[str] = mapped_column(String(35), nullable=False, server_default="tr-TR")
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Europe/Istanbul"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    settings_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, server_default="{}"
    )
