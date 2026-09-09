"""``OpenMeteoProvider`` against ``httpx.MockTransport`` (docs/DECISIONS.md ADR-0091) —
the same mocking discipline ``tests/unit/test_calendar_caldav_provider.py`` already uses
for a real HTTP-backed provider: no real network call from a test, ever.
"""

from __future__ import annotations

import httpx
import pytest

from app.weather.providers import (
    FakeWeatherProvider,
    OpenMeteoProvider,
    WeatherError,
    condition_for,
)


def _mock_httpx(monkeypatch, handler) -> None:
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


_GEOCODE_ANKARA = {
    "results": [
        {
            "name": "Ankara",
            "latitude": 39.92,
            "longitude": 32.85,
            "country": "Türkiye",
            "admin1": "Ankara",
            "timezone": "Europe/Istanbul",
        }
    ]
}
_FORECAST = {
    "current": {"time": "2026-09-09T08:00", "temperature_2m": 18.2, "weather_code": 61},
    "daily": {
        "temperature_2m_max": [22.0],
        "temperature_2m_min": [11.0],
        "precipitation_probability_max": [70.0],
        "weather_code": [61],
    },
}


def test_current_geocodes_a_city_name_then_fetches_the_forecast(monkeypatch) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "geocoding-api" in str(request.url):
            return httpx.Response(200, json=_GEOCODE_ANKARA)
        return httpx.Response(200, json=_FORECAST)

    _mock_httpx(monkeypatch, handler)
    provider = OpenMeteoProvider()
    observation = provider.current(latitude=None, longitude=None, city="Ankara")

    assert any("geocoding-api" in u for u in seen)
    assert any("api.open-meteo.com" in u for u in seen)
    assert observation.provider == "open_meteo"
    assert observation.temperature_c == 18.2
    assert observation.condition == "hafif yağmurlu"  # WMO 61
    assert observation.daily_high_c == 22.0
    assert observation.daily_low_c == 11.0
    assert observation.precipitation_probability == 70.0
    assert "Ankara" in observation.summary


def test_current_skips_geocoding_when_coordinates_are_already_known(monkeypatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert "geocoding-api" not in str(request.url)
        return httpx.Response(200, json=_FORECAST)

    _mock_httpx(monkeypatch, handler)
    provider = OpenMeteoProvider()
    observation = provider.current(latitude=39.92, longitude=32.85, city="Ankara")
    assert len(calls) == 1
    assert observation.temperature_c == 18.2


def test_geocoding_with_no_results_raises_location_not_found(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    _mock_httpx(monkeypatch, handler)
    provider = OpenMeteoProvider()
    with pytest.raises(WeatherError) as excinfo:
        provider.current(latitude=None, longitude=None, city="Nowherestan")
    assert excinfo.value.reason == WeatherError.REASON_LOCATION_NOT_FOUND


def test_no_city_and_no_coordinates_raises_location_not_found() -> None:
    provider = OpenMeteoProvider()
    with pytest.raises(WeatherError) as excinfo:
        provider.current(latitude=None, longitude=None, city=None)
    assert excinfo.value.reason == WeatherError.REASON_LOCATION_NOT_FOUND


def test_http_error_from_the_provider_is_a_typed_provider_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _mock_httpx(monkeypatch, handler)
    provider = OpenMeteoProvider()
    with pytest.raises(WeatherError) as excinfo:
        provider.current(latitude=39.92, longitude=32.85, city="Ankara")
    assert excinfo.value.reason == WeatherError.REASON_PROVIDER_ERROR


def test_timeout_is_a_typed_timeout_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow", request=request)

    _mock_httpx(monkeypatch, handler)
    provider = OpenMeteoProvider()
    with pytest.raises(WeatherError) as excinfo:
        provider.current(latitude=39.92, longitude=32.85, city="Ankara")
    assert excinfo.value.reason == WeatherError.REASON_TIMEOUT


def test_malformed_forecast_response_is_a_typed_provider_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"current": {"weather_code": "not-a-number"}})

    _mock_httpx(monkeypatch, handler)
    provider = OpenMeteoProvider()
    with pytest.raises(WeatherError) as excinfo:
        provider.current(latitude=39.92, longitude=32.85, city="Ankara")
    assert excinfo.value.reason == WeatherError.REASON_PROVIDER_ERROR


def test_every_documented_wmo_code_has_a_turkish_condition() -> None:
    for code in (
        0,
        1,
        2,
        3,
        45,
        48,
        51,
        53,
        55,
        56,
        57,
        61,
        63,
        65,
        66,
        67,
        71,
        73,
        75,
        77,
        80,
        81,
        82,
        85,
        86,
        95,
        96,
        99,
    ):
        condition = condition_for(code)
        assert condition and "bilinmeyen" not in condition


def test_an_undocumented_wmo_code_is_reported_honestly_not_guessed() -> None:
    condition = condition_for(12345)
    assert "bilinmeyen" in condition
    assert "12345" in condition


def test_condition_for_none_code_is_unknown_not_a_crash() -> None:
    assert condition_for(None) == "bilinmiyor"


def test_fake_provider_is_deterministic_for_known_fixture_cities() -> None:
    provider = FakeWeatherProvider()
    observation = provider.current(latitude=None, longitude=None, city="İstanbul")
    assert observation.temperature_c == 21.4
    assert observation.provider == "fake"


def test_fake_provider_refuses_an_unknown_city_with_no_coordinates() -> None:
    provider = FakeWeatherProvider()
    with pytest.raises(WeatherError) as excinfo:
        provider.current(latitude=None, longitude=None, city="Unknownville")
    assert excinfo.value.reason == WeatherError.REASON_LOCATION_NOT_FOUND
