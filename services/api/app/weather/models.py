"""``WeatherObservation`` (the structured provider answer) and ``weather_query_evidence``
(what the owner can ask about afterward — docs/DECISIONS.md ADR-90, task brief §2:
"'Hangi konumun havasını söyledin?' must be answerable truthfully"). A row is written for
every ANSWERED query (a real provider result reached the owner); a refused/unavailable
query is ledger evidence (``EVENT_TYPE_WEATHER_QUERIED``) but writes no evidence row here,
since there is nothing the owner was told to ask about afterward.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Float, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

_JSON = JSON().with_variant(JSONB(), "postgresql")


@dataclass(frozen=True, slots=True)
class WeatherObservation:
    """A real provider's answer for one place (task brief §2's exact field list)."""

    location_label: str
    observed_at: datetime
    temperature_c: float | None
    condition: str  # Turkish, human-facing ("açık", "parçalı bulutlu", ...)
    precipitation_probability: float | None  # 0-100, None when the provider has none
    daily_high_c: float | None
    daily_low_c: float | None
    summary: str  # the concise Turkish sentence read to the owner
    provider: str


class WeatherQueryEvidenceRow(Base):
    """The evidence behind the LAST weather answer (and every one before it) — read by
    ``WeatherService.last_evidence`` to answer "Hangi konumun havasını söyledin?" from the
    record, never from what the model recalls saying."""

    __tablename__ = "weather_query_evidence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    queried_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: What the resolver used: {source, city, region, country, latitude, longitude,
    #: confidence, resolution_reason} — a snapshot, not a foreign key, so evidence
    #: survives even if the originating location_context row is later superseded.
    location_json: Mapped[dict] = mapped_column(_JSON, nullable=False)
    location_source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    condition: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    precipitation_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    daily_high_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    daily_low_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__ = ["WeatherObservation", "WeatherQueryEvidenceRow"]
