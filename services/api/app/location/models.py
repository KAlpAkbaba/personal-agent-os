"""``location_context`` (docs/DECISIONS.md ADR-0090): one row per location OBSERVATION —
an explicit device capture (``windows_location``/``mobile_gps``) or the owner's durable
default for a capability (``owner_default``). ``app.location.service.LocationContext`` is
the portable, in-memory shape a resolver hands to a consumer; this row is what makes it
durable and lets the owner ask "how do you know where I am?" and get the truth back from
the record, not from whatever the model happens to remember saying.

Home/default and current-observed are DIFFERENT concepts and never collapse into one
(task brief): a default row's ``source`` is ALWAYS ``owner_default`` and it never carries
evidence of where the owner currently is — it is read only at resolution tier 3, after a
fresh device capture has already failed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import Boolean, DateTime, Float, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

# --------------------------------------------------------------- source vocabulary

#: A place named IN THE REQUEST ("Ankara'da hava nasıl?") — resolution tier 1, always
#: wins over every stored observation (task brief: "uses Ankara whatever the device
#: says"). Never persisted as a durable capture; constructed fresh per resolution.
SOURCE_EXPLICIT_OWNER_REQUEST = "explicit_owner_request"
#: The owner's durable, explicitly-set default for a capability — tier 3. Never inferred,
#: never invented (task brief): ``LocationService.set_default`` is the only writer.
SOURCE_OWNER_DEFAULT = "owner_default"
#: A live pull/push from the Windows device agent — tier 2 when fresh, tier 4 (relabeled
#: ``recent_trusted_location``) when aged past ``FRESHNESS_S`` but still within its
#: recent window. MEASURED unavailable today (task brief): the deployed agent advertises
#: no location capability (see ``app.location.providers``), so this source exists as a
#: seam with no live writer yet.
SOURCE_WINDOWS_LOCATION = "windows_location"
#: A future mobile device's GPS fix — same tier structure as ``windows_location``. Not
#: implemented (no mobile device exists yet); the column and the freshness table are
#: ready so a mobile client can start writing rows without a resolver change.
SOURCE_MOBILE_GPS = "mobile_gps"
#: A ``windows_location``/``mobile_gps`` row the resolver is using PAST its fresh window
#: but still within its recent one (tier 4) — the row's own ``source`` column keeps the
#: original capture's identity; this label is applied to the in-memory
#: ``LocationContext`` the resolver hands back, so "how do you know" answers truthfully
#: that the location is aging, not fresh.
SOURCE_RECENT_TRUSTED_LOCATION = "recent_trusted_location"
#: A coarse IP-geolocation lookup — tier 5, LOW confidence only, never persisted as a
#: durable row (an IP lookup is re-derived every time, never trusted as history; task
#: brief: "do not build a coordinate history by default").
SOURCE_IP_COARSE = "ip_coarse"

LOCATION_SOURCES: Final[tuple[str, ...]] = (
    SOURCE_EXPLICIT_OWNER_REQUEST,
    SOURCE_OWNER_DEFAULT,
    SOURCE_WINDOWS_LOCATION,
    SOURCE_MOBILE_GPS,
    SOURCE_RECENT_TRUSTED_LOCATION,
    SOURCE_IP_COARSE,
)

# ------------------------------------------------------------ confidence vocabulary

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCES: Final[tuple[str, ...]] = (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW)

# ---------------------------------------------------------- permission scope vocabulary

#: Location authority is capability-scoped (task brief: "keep location authority
#: capability-scoped so unrelated capabilities do not gain it") — a row's
#: ``permission_scope`` names WHICH capability may read it as a default/observation.
#: Weather is the only consumer today; a future capability gets its own scope value and
#: its own default row rather than inheriting weather's.
PERMISSION_SCOPE_WEATHER = "weather"
PERMISSION_SCOPES: Final[tuple[str, ...]] = (PERMISSION_SCOPE_WEATHER,)

# --------------------------------------------------------------------- freshness table

#: (fresh_s, recent_s) per DEVICE-SUPPLIED source (owner_default/explicit/ip_coarse are
#: not looked up by freshness — see ``app.location.service``). Below fresh_s a captured
#: row is tier 2 ("fresh trusted device location"); between fresh_s and recent_s it is
#: still usable at tier 4 ("recent_trusted_location") but downgraded to MEDIUM
#: confidence; past recent_s it is not used at all and the resolver falls through to IP
#: or unresolved.
#:
#: windows_location: fresh=15 min — protects against answering with where a laptop was
#: when it was last suspended/moved rooms; a closed lid can sit for hours without a new
#: fix. recent=3 h — long enough to still beat a bare owner default after, say, a
#: mid-morning meeting elsewhere, short enough that an overnight-stale fix is never
#: treated as anything but IP-grade guessing.
#: mobile_gps: fresh=10 min — a phone moves faster than a desk; a car ride can cross
#: cities in that window, so the bar is tighter than a stationary Windows machine's.
#: recent=2 h — same reasoning as windows_location, scaled down with the tighter fresh
#: bound.
FRESHNESS_S: Final[dict[str, tuple[float, float]]] = {
    SOURCE_WINDOWS_LOCATION: (15 * 60.0, 3 * 3600.0),
    SOURCE_MOBILE_GPS: (10 * 60.0, 2 * 3600.0),
}


class LocationContextRow(Base):
    """One location observation or default (see module docstring). ``location_id`` is the
    literal column name the task brief specifies."""

    __tablename__ = "location_context"
    __table_args__ = (
        Index("ix_location_context_source_captured_at", "source", "captured_at"),
        Index("ix_location_context_default_scope", "is_default", "permission_scope"),
    )

    location_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    region: Mapped[str | None] = mapped_column(String(200), nullable=True)
    country: Mapped[str | None] = mapped_column(String(200), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The fresh-tier boundary (``captured_at + FRESHNESS_S[source][0]``) for a
    #: device-supplied row; ``NULL`` for explicit/owner_default rows, which are not
    #: looked up by freshness at all (module docstring).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    #: True for AT MOST ONE row per ``permission_scope`` at a time (``LocationService.
    #: set_default`` demotes the previous one in the same transaction it inserts a new
    #: one) — never true for any row whose ``source`` is not ``owner_default``.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    permission_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PERMISSION_SCOPE_WEATHER
    )


__all__ = [
    "CONFIDENCES",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "FRESHNESS_S",
    "LOCATION_SOURCES",
    "PERMISSION_SCOPES",
    "PERMISSION_SCOPE_WEATHER",
    "SOURCE_EXPLICIT_OWNER_REQUEST",
    "SOURCE_IP_COARSE",
    "SOURCE_MOBILE_GPS",
    "SOURCE_OWNER_DEFAULT",
    "SOURCE_RECENT_TRUSTED_LOCATION",
    "SOURCE_WINDOWS_LOCATION",
    "LocationContextRow",
]
