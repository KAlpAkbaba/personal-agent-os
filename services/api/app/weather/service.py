"""``WeatherService``: resolve the location (``app.location.service.LocationService``,
the ONE resolution order), call the real provider, store the evidence, answer truthfully
— never weather from model memory, never a guessed location (task brief §1, §2).

READ-tier only (the same class ``app.calendar.service.CalendarService.agenda`` belongs
to): nothing here writes anything the owner would recognise as a "mutation" except the
evidence row itself, which is bookkeeping, not an external effect — no confirmation gate
needed, the same reasoning ``CalendarService.agenda``'s own module docstring gives.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_FAILED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.ledger import service as ledger_service
from app.ledger.vocabulary import EVENT_TYPE_WEATHER_QUERIED, SUBSYSTEM_WEATHER
from app.location.models import PERMISSION_SCOPE_WEATHER, SOURCE_IP_COARSE
from app.location.service import LocationService
from app.logging import get_logger
from app.weather.models import WeatherObservation, WeatherQueryEvidenceRow
from app.weather.providers import WeatherError, WeatherProvider

logger = get_logger("app.weather.service")

CAPABILITY_WEATHER_CURRENT = "weather.current"
CAPABILITY_WEATHER_LAST_EVIDENCE = "weather.last_evidence"

SPEECH_DEPENDENCY_UNAVAILABLE = "Şu an hava durumu sağlayıcısına ulaşamıyorum efendim."
_ERROR_SPEECH: dict[str, str] = {
    WeatherError.REASON_DEPENDENCY_UNAVAILABLE: SPEECH_DEPENDENCY_UNAVAILABLE,
    WeatherError.REASON_LOCATION_NOT_FOUND: "{place} adında bir yer bulamadım efendim.",
    WeatherError.REASON_PROVIDER_ERROR: (
        "Hava durumu servisinden sağlıklı bir yanıt alamadım efendim."
    ),
    WeatherError.REASON_TIMEOUT: "Hava durumu servisi zamanında yanıt vermedi efendim.",
}
SPEECH_IP_COARSE_CAVEAT = " Bu tahmini yalnızca IP adresinizden çıkardım; kesin değildir."
SPEECH_NO_PRIOR_QUERY = "Daha önce sizin için hava durumu sormadım efendim."


def _now() -> datetime:
    return datetime.now(UTC)


def _observation_dict(observation: WeatherObservation) -> dict[str, Any]:
    """A JSON-safe view of ``observation`` for a receipt's ``observed_after.server``
    (which ``app.actions.receipt.record_receipt`` writes into the ledger's ``JSON``
    ``detail_json`` column). A bare ``dataclasses.asdict(observation)`` was tried first
    and found by this module's own test suite to leave ``observed_at`` as a raw
    ``datetime`` — Python's ``json`` module cannot serialise that, so the ledger write
    raised INSIDE ``record_receipt``'s best-effort ``try`` and, because that write had
    already started a failed flush, left the CALLER's session unable to commit anything
    afterward (see the regression fix in ``app.actions.receipt.record_receipt`` too:
    a best-effort write must never poison the session that made it). Every other
    receipt-writing service in this repository (``CalendarService``, ``MailService``)
    avoids this by hand-formatting each datetime with ``.isoformat()`` before it ever
    reaches a receipt; this helper is that same discipline, named once."""
    data = asdict(observation)
    data["observed_at"] = observation.observed_at.isoformat()
    return data


class WeatherService:
    def __init__(
        self, *, location_service: LocationService, provider: WeatherProvider | None
    ) -> None:
        self._location_service = location_service
        self._provider = provider

    # --------------------------------------------------------------------- plumbing

    def _answer(
        self,
        *,
        capability: str,
        execution: str,
        terminal: str,
        speech: str,
        db: Session,
        session_id: str | None,
        server: dict[str, Any],
        error_class: str | None = None,
        extra: dict[str, Any] | None = None,
        now: datetime,
    ) -> dict[str, Any]:
        receipt = ActionReceipt(
            action_id=str(uuid.uuid4()),
            capability=capability,
            requested_state="read",
            execution_status=execution,
            terminal_status=terminal,
            observed_after={"server": server, "local": {}},
            error_class=error_class,
            speech=speech,
            started_at=now,
            completed_at=now,
            session_id=session_id,
            observed_at=now,
        )
        record_receipt(db, receipt, SUBSYSTEM_WEATHER)
        out = receipt.as_dict()
        if extra:
            out.update(extra)
        return out

    # -------------------------------------------------------------------------- read

    def current(
        self,
        session: Session,
        *,
        requested_place: str | None = None,
        device_id: uuid.UUID | None = None,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or _now()
        resolution = self._location_service.resolve(
            session,
            requested_place=requested_place,
            device_id=device_id,
            capability=PERMISSION_SCOPE_WEATHER,
            now=now,
        )
        if not resolution.resolved:
            return self._answer(
                capability=CAPABILITY_WEATHER_CURRENT,
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                speech=resolution.explanation,
                db=session,
                session_id=session_id,
                server={"reason": "location_unresolved"},
                error_class="location_unresolved",
                now=now,
            )
        ctx = resolution.context
        assert ctx is not None  # resolved=True always carries a context

        if self._provider is None:
            return self._answer(
                capability=CAPABILITY_WEATHER_CURRENT,
                execution=EXECUTION_FAILED,
                terminal=TERMINAL_FAILED,
                speech=SPEECH_DEPENDENCY_UNAVAILABLE,
                db=session,
                session_id=session_id,
                server={
                    "reason": WeatherError.REASON_DEPENDENCY_UNAVAILABLE,
                    "location_source": ctx.source,
                },
                error_class=WeatherError.REASON_DEPENDENCY_UNAVAILABLE,
                now=now,
            )

        try:
            observation = self._provider.current(
                latitude=ctx.latitude, longitude=ctx.longitude, city=ctx.city
            )
        except WeatherError as exc:
            speech = _ERROR_SPEECH.get(exc.reason, "Hava durumunu öğrenemedim efendim.").format(
                place=ctx.label
            )
            return self._answer(
                capability=CAPABILITY_WEATHER_CURRENT,
                execution=EXECUTION_FAILED,
                terminal=TERMINAL_FAILED,
                speech=speech,
                db=session,
                session_id=session_id,
                server={"reason": exc.reason, "location_source": ctx.source},
                error_class=exc.reason,
                now=now,
            )

        speech = observation.summary
        if resolution.reason == "ip_coarse" or ctx.source == SOURCE_IP_COARSE:
            speech += SPEECH_IP_COARSE_CAVEAT

        location_snapshot = {
            "source": ctx.source,
            "resolution_reason": resolution.reason,
            "city": ctx.city,
            "region": ctx.region,
            "country": ctx.country,
            "latitude": ctx.latitude,
            "longitude": ctx.longitude,
            "confidence": ctx.confidence,
        }
        evidence = WeatherQueryEvidenceRow(
            id=uuid.uuid4(),
            queried_at=now,
            location_json=location_snapshot,
            location_source=ctx.source,
            confidence=ctx.confidence,
            provider=observation.provider,
            observed_at=observation.observed_at,
            temperature_c=observation.temperature_c,
            condition=observation.condition,
            precipitation_probability=observation.precipitation_probability,
            daily_high_c=observation.daily_high_c,
            daily_low_c=observation.daily_low_c,
            summary=speech,
            session_id=session_id,
        )
        # The evidence row is EVIDENCE, never a dependency of the answer - and never a
        # way to break the turn it belongs to. The M26-era review proved live that an
        # unguarded commit here poisons the caller's session: SQLAlchemy leaves a session
        # that failed a flush needing an explicit rollback, and the realtime tool
        # dispatcher's own unconditional commit at the end of the turn then raises
        # PendingRollbackError - so a too-long place name from a third-party geocoder
        # would fail the WHOLE tool-call round trip, not merely lose one weather receipt.
        # The same rollback discipline `app.actions.receipt.record_receipt` already
        # applies for exactly this reason. The provider bounds its own strings now
        # (app.weather.providers.MAX_PLACE_NAME_LEN); this is the second wall.
        evidence_written = True
        try:
            session.add(evidence)
            session.commit()
        except Exception:  # noqa: BLE001 - a lost receipt must never cost the answer
            evidence_written = False
            session.rollback()
            logger.warning(
                "weather_evidence_write_failed", extra={"provider": observation.provider}
            )

        try:
            ledger_service.record(
                session,
                ledger_service.ActivityEvent(
                    event_type=EVENT_TYPE_WEATHER_QUERIED,
                    subsystem=SUBSYSTEM_WEATHER,
                    action=CAPABILITY_WEATHER_CURRENT,
                    factual_summary=(
                        f"weather.current -> {observation.location_label} ({ctx.source})"
                    ),
                    occurred_at=now,
                    detail_json={"location": location_snapshot, "provider": observation.provider},
                    source="live",
                    # Only point at a row that EXISTS. A dangling reference to an
                    # evidence row the write above lost would be a receipt claiming a
                    # record nobody can read back - the failure this whole family is
                    # built to make impossible.
                    source_ref=(
                        f"weather_query_evidence:{evidence.id}"
                        if evidence_written
                        else f"weather_query_evidence_lost:{evidence.id}"
                    ),
                ),
            )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the answer
            logger.warning("weather_ledger_failed")

        return self._answer(
            capability=CAPABILITY_WEATHER_CURRENT,
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            speech=speech,
            db=session,
            session_id=session_id,
            server={"observation": _observation_dict(observation), "location": location_snapshot},
            now=now,
            extra={"evidence_id": str(evidence.id) if evidence_written else None},
        )

    def last_evidence(self, session: Session, *, session_id: str | None = None) -> dict[str, Any]:
        """ "Hangi konumun havasını söyledin?" (task brief §2) — read the record, never
        the model's memory of what it said."""
        now = _now()
        row = (
            session.execute(
                select(WeatherQueryEvidenceRow)
                .order_by(WeatherQueryEvidenceRow.queried_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if row is None:
            return self._answer(
                capability=CAPABILITY_WEATHER_LAST_EVIDENCE,
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                speech=SPEECH_NO_PRIOR_QUERY,
                db=session,
                session_id=session_id,
                server={"reason": "no_prior_query"},
                error_class="no_prior_query",
                now=now,
            )
        location = dict(row.location_json or {})
        place = (
            location.get("city")
            or location.get("region")
            or location.get("country")
            or "bilinmeyen bir yer"
        )
        speech = (
            f"Son olarak {place} için, {row.location_source} kaynaklı konum bilgisiyle "
            f"({row.confidence} güven), {row.provider} sağlayıcısından hava durumunu "
            f"söylemiştim: {row.summary}"
        )
        return self._answer(
            capability=CAPABILITY_WEATHER_LAST_EVIDENCE,
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            speech=speech,
            db=session,
            session_id=session_id,
            server={
                "location": location,
                "provider": row.provider,
                "queried_at": row.queried_at.isoformat(),
            },
            now=now,
        )


__all__ = ["CAPABILITY_WEATHER_CURRENT", "CAPABILITY_WEATHER_LAST_EVIDENCE", "WeatherService"]
