"""``WeatherProvider`` behind a Protocol (module discipline shared with
``app.calendar.providers``/``app.mail.providers``: ``app.weather.service`` never imports
an HTTP client directly). ``OpenMeteoProvider`` is the real, default implementation —
Open-Meteo's forecast and geocoding APIs need no signup and no API key for non-commercial
use (verified against the vendor's own docs, 2026-09-08: "Only required to commercial use
to access reserved API resources"), so CLAUDE.md's "never invent a credential" rule is not
in tension with shipping a genuinely live default — unlike a vendor that gates access
behind registration, where the honest answer stays ``dependency_unavailable`` until an
owner action supplies one (see ``app.location.providers`` for exactly that case with IP
geolocation).

``FakeWeatherProvider`` is the deterministic fixture for tests and the voice corpus —
never imported by production, the same discipline ``app.calendar.providers.
FakeCalendarProvider`` documents for itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

import httpx

from app.logging import get_logger
from app.weather.models import WeatherObservation

logger = get_logger("app.weather.providers")

PROVIDER_OPEN_METEO: Final = "open_meteo"
PROVIDER_NONE: Final = "none"
WEATHER_PROVIDERS: Final[tuple[str, ...]] = (PROVIDER_OPEN_METEO, PROVIDER_NONE)


class WeatherError(Exception):
    """Typed provider failure. ``reason`` is one of the class attributes below — the
    service maps each to its own honest Turkish sentence, never a generic apology."""

    REASON_DEPENDENCY_UNAVAILABLE: Final = "dependency_unavailable"  # no provider configured
    REASON_LOCATION_NOT_FOUND: Final = "location_not_found"  # geocoding found nothing
    REASON_PROVIDER_ERROR: Final = "provider_error"  # the provider answered, but badly
    REASON_TIMEOUT: Final = "timeout"

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


class WeatherProvider(Protocol):
    name: str

    def current(
        self, *, latitude: float | None, longitude: float | None, city: str | None
    ) -> WeatherObservation: ...


#: WMO weather codes (https://open-meteo.com/en/docs — "WMO Weather interpretation
#: codes"), mapped to a concise Turkish condition phrase. Every code the API documents is
#: covered; an unlisted code is reported as "bilinmeyen hava durumu kodu N" rather than
#: silently mapped to the nearest guess (task brief's "honest failure over invented
#: answer" applied to a single field, not just to the whole call).
_WMO_CONDITIONS: Final[dict[int, str]] = {
    0: "açık",
    1: "genelde açık",
    2: "parçalı bulutlu",
    3: "çok bulutlu",
    45: "sisli",
    48: "kırağı sisi",
    51: "hafif çiseleme",
    53: "orta şiddette çiseleme",
    55: "yoğun çiseleme",
    56: "hafif donan çiseleme",
    57: "yoğun donan çiseleme",
    61: "hafif yağmurlu",
    63: "orta şiddette yağmurlu",
    65: "kuvvetli yağmurlu",
    66: "hafif donan yağmur",
    67: "kuvvetli donan yağmur",
    71: "hafif kar yağışlı",
    73: "orta şiddette kar yağışlı",
    75: "kuvvetli kar yağışlı",
    77: "kar taneleri",
    80: "hafif sağanak yağmur",
    81: "orta şiddette sağanak yağmur",
    82: "şiddetli sağanak yağmur",
    85: "hafif kar sağanağı",
    86: "kuvvetli kar sağanağı",
    95: "gök gürültülü fırtına",
    96: "hafif dolulu gök gürültülü fırtına",
    99: "kuvvetli dolulu gök gürültülü fırtına",
}


def condition_for(code: int | None) -> str:
    if code is None:
        return "bilinmiyor"
    if code in _WMO_CONDITIONS:
        return _WMO_CONDITIONS[code]
    return f"bilinmeyen hava durumu kodu {code}"


def _round1(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


#: Every string this provider hands back is bounded HERE, where the vendor's JSON
#: enters the process — not at the database, which is where an unbounded one is found
#: the expensive way. A geocoded place name and its region are free text from a third
#: party; they reach a spoken sentence AND `weather_query_evidence`'s fixed-width
#: columns (`condition` String(64), `summary` String(500)). On SQLite an over-long value
#: is silently stored; on Postgres — production — it raises, and the M26 review proved
#: live that a raised insert leaves the caller's session needing a rollback, which then
#: fails the WHOLE tool-call turn, not just the weather answer. Bounding at the seam is
#: what makes that unreachable rather than merely unlikely.
MAX_PLACE_NAME_LEN: Final = 120
MAX_SUMMARY_LEN: Final = 480  # < the column's 500, leaving room for nothing to be lost


def _bounded(value: str | None, limit: int) -> str | None:
    """A third party's string, cut to what this system promised to store. Returns
    ``None`` unchanged so a missing field stays missing rather than becoming ""."""
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if len(text) > limit else text


class OpenMeteoProvider:
    """Two calls: geocode a city name when no coordinates were given
    (``geocoding-api.open-meteo.com``), then the forecast
    (``api.open-meteo.com/v1/forecast``). Coordinates given directly (a future device
    GPS fix) skip geocoding entirely."""

    name = PROVIDER_OPEN_METEO

    def __init__(
        self,
        *,
        forecast_base_url: str = "https://api.open-meteo.com/v1/forecast",
        geocoding_base_url: str = "https://geocoding-api.open-meteo.com/v1/search",
        timeout_s: float = 10.0,
    ) -> None:
        self._forecast_base_url = forecast_base_url
        self._geocoding_base_url = geocoding_base_url
        self._timeout_s = timeout_s

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout_s)

    def _geocode(self, city: str) -> tuple[float, float, str, str | None, str | None]:
        """-> (latitude, longitude, resolved_name, region, timezone)."""
        with self._client() as client:
            try:
                response = client.get(
                    self._geocoding_base_url, params={"name": city, "count": 1, "language": "tr"}
                )
                response.raise_for_status()
                data = response.json()
            except httpx.TimeoutException as exc:
                raise WeatherError(WeatherError.REASON_TIMEOUT, str(exc)) from exc
            except httpx.HTTPError as exc:
                raise WeatherError(WeatherError.REASON_PROVIDER_ERROR, str(exc)) from exc
            except ValueError as exc:
                # A 200 whose body is not JSON: a captive portal, a CDN error page, a
                # misrouted endpoint. `json.JSONDecodeError` is a `ValueError`, and it is
                # NOT an `httpx.HTTPError`, so before this it escaped every typed handler
                # and the owner heard an internal-bug failure instead of the honest
                # "the weather service did not answer properly" sentence.
                raise WeatherError(
                    WeatherError.REASON_PROVIDER_ERROR, f"geocoding response was not JSON: {exc}"
                ) from exc
        results = data.get("results") or []
        if not results:
            raise WeatherError(WeatherError.REASON_LOCATION_NOT_FOUND, city)
        first = results[0]
        try:
            latitude = float(first["latitude"])
            longitude = float(first["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherError(
                WeatherError.REASON_PROVIDER_ERROR, "malformed geocoding result"
            ) from exc
        return (
            latitude,
            longitude,
            _bounded(str(first.get("name") or city), MAX_PLACE_NAME_LEN) or city,
            _bounded(first.get("admin1"), MAX_PLACE_NAME_LEN),
            first.get("timezone"),
        )

    def current(
        self, *, latitude: float | None, longitude: float | None, city: str | None
    ) -> WeatherObservation:
        label = city or ""
        region: str | None = None
        if latitude is None or longitude is None:
            if not city:
                raise WeatherError(
                    WeatherError.REASON_LOCATION_NOT_FOUND, "no city and no coordinates"
                )
            latitude, longitude, label, region, _tz = self._geocode(city)

        with self._client() as client:
            try:
                response = client.get(
                    self._forecast_base_url,
                    params={
                        "latitude": latitude,
                        "longitude": longitude,
                        "current": "temperature_2m,weather_code",
                        "daily": (
                            "temperature_2m_max,temperature_2m_min,"
                            "precipitation_probability_max,weather_code"
                        ),
                        "timezone": "auto",
                        "forecast_days": 1,
                    },
                )
                response.raise_for_status()
                data = response.json()
            except httpx.TimeoutException as exc:
                raise WeatherError(WeatherError.REASON_TIMEOUT, str(exc)) from exc
            except httpx.HTTPError as exc:
                raise WeatherError(WeatherError.REASON_PROVIDER_ERROR, str(exc)) from exc
            except ValueError as exc:  # see _geocode: a 200 that is not JSON
                raise WeatherError(
                    WeatherError.REASON_PROVIDER_ERROR, f"forecast response was not JSON: {exc}"
                ) from exc

        current = data.get("current") or {}
        daily = data.get("daily") or {}
        try:
            temperature_c = _round1(current.get("temperature_2m"))
            code = current.get("weather_code")
            code = int(code) if code is not None else None
            highs = daily.get("temperature_2m_max") or []
            lows = daily.get("temperature_2m_min") or []
            probs = daily.get("precipitation_probability_max") or []
            daily_high_c = _round1(highs[0]) if highs else None
            daily_low_c = _round1(lows[0]) if lows else None
            precipitation_probability = (
                _round1(probs[0]) if probs and probs[0] is not None else None
            )
        except (TypeError, ValueError, IndexError) as exc:
            raise WeatherError(
                WeatherError.REASON_PROVIDER_ERROR, "malformed forecast result"
            ) from exc

        condition = condition_for(code)
        observed_at_raw = current.get("time")
        try:
            observed_at = (
                datetime.fromisoformat(observed_at_raw).replace(tzinfo=UTC)
                if observed_at_raw
                else datetime.now(UTC)
            )
        except ValueError:
            observed_at = datetime.now(UTC)

        place = f"{label}, {region}" if region and region != label else label
        temp_part = f"{temperature_c:.0f}°C" if temperature_c is not None else "sıcaklık bilinmiyor"
        summary = f"{place}: {condition}, {temp_part}."
        if daily_high_c is not None and daily_low_c is not None:
            summary += f" Bugün en yüksek {daily_high_c:.0f}°C, en düşük {daily_low_c:.0f}°C."
        if precipitation_probability is not None:
            summary += f" Yağış olasılığı yüzde {precipitation_probability:.0f}."
        # The last word on length: whatever the pieces did, what leaves here fits the
        # column that stores it and the sentence the owner hears.
        summary = summary[:MAX_SUMMARY_LEN]

        return WeatherObservation(
            location_label=place,
            observed_at=observed_at,
            temperature_c=temperature_c,
            condition=condition,
            precipitation_probability=precipitation_probability,
            daily_high_c=daily_high_c,
            daily_low_c=daily_low_c,
            summary=summary,
            provider=self.name,
        )


@dataclass(frozen=True, slots=True)
class _FakeFixture:
    temperature_c: float
    condition_code: int
    daily_high_c: float
    daily_low_c: float
    precipitation_probability: float | None


class FakeWeatherProvider:
    """Deterministic fixture keyed by city name (case-insensitive), for unit tests and
    the voice corpus — never imported by production (module docstring)."""

    name = "fake"

    def __init__(self, fixtures: dict[str, _FakeFixture] | None = None) -> None:
        self._fixtures = fixtures or {
            "istanbul": _FakeFixture(21.4, 2, 24.0, 17.0, 20.0),
            "ankara": _FakeFixture(18.2, 61, 22.0, 11.0, 70.0),
            "antalya": _FakeFixture(28.6, 0, 31.0, 22.0, 0.0),
        }

    def current(
        self, *, latitude: float | None, longitude: float | None, city: str | None
    ) -> WeatherObservation:
        key = (city or "").strip().lower()
        # Turkish-casefold the common diacritics so "İstanbul"/"istanbul" both match.
        key = key.replace("i̇", "i")
        fixture = self._fixtures.get(key)
        if fixture is None:
            if latitude is None or longitude is None:
                raise WeatherError(WeatherError.REASON_LOCATION_NOT_FOUND, city or "")
            fixture = _FakeFixture(19.0, 1, 23.0, 14.0, 10.0)
        label = city or "bilinen konum"
        condition = condition_for(fixture.condition_code)
        summary = (
            f"{label}: {condition}, {fixture.temperature_c:.0f}°C. "
            f"Bugün en yüksek {fixture.daily_high_c:.0f}°C, en düşük {fixture.daily_low_c:.0f}°C."
        )
        return WeatherObservation(
            location_label=label,
            observed_at=datetime.now(UTC),
            temperature_c=fixture.temperature_c,
            condition=condition,
            precipitation_probability=fixture.precipitation_probability,
            daily_high_c=fixture.daily_high_c,
            daily_low_c=fixture.daily_low_c,
            summary=summary,
            provider=self.name,
        )


def build_weather_provider(settings: Any) -> WeatherProvider | None:
    """``OpenMeteoProvider`` by default (module docstring: genuinely keyless), ``None``
    (dependency_unavailable) only when the owner explicitly sets
    ``PAGENTOS_WEATHER_PROVIDER=none``."""
    configured = getattr(settings, "weather_provider", PROVIDER_OPEN_METEO)
    provider = str(configured or PROVIDER_OPEN_METEO)
    if provider == PROVIDER_NONE:
        return None
    return OpenMeteoProvider(
        forecast_base_url=getattr(
            settings, "weather_open_meteo_forecast_url", "https://api.open-meteo.com/v1/forecast"
        ),
        geocoding_base_url=getattr(
            settings,
            "weather_open_meteo_geocoding_url",
            "https://geocoding-api.open-meteo.com/v1/search",
        ),
        timeout_s=float(getattr(settings, "weather_request_timeout_s", 10.0) or 10.0),
    )


__all__ = [
    "PROVIDER_NONE",
    "PROVIDER_OPEN_METEO",
    "WEATHER_PROVIDERS",
    "FakeWeatherProvider",
    "OpenMeteoProvider",
    "WeatherError",
    "WeatherProvider",
    "build_weather_provider",
    "condition_for",
]
