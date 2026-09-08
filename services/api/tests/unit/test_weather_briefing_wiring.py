"""Wiring test: Owner Location Context / Live Weather / Morning Briefing's runtime,
through the REAL application object (docs/DECISIONS.md ADR-0090) — the same pattern
``test_mail_calendar_wiring.py`` establishes: what a voice tool reads through
``ToolContext.live`` is what ``create_app`` actually built, and the default provider is
the real, keyless Open-Meteo one (never a fake in production — module docstring of
``app.weather.providers``)."""

from __future__ import annotations

from app.briefing.service import BriefingService
from app.config import Settings
from app.location.service import LocationService
from app.main import create_app
from app.weather.providers import OpenMeteoProvider
from app.weather.service import WeatherService

VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"


def test_create_app_wires_location_weather_and_briefing_services() -> None:
    app = create_app(Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY))
    live = app.state.voice_realtime.live_sources()

    assert isinstance(live["location_service"], LocationService)
    assert live["location_service"] is app.state.location_service
    assert isinstance(live["weather_service"], WeatherService)
    assert live["weather_service"] is app.state.weather_service
    assert isinstance(live["briefing_service"], BriefingService)
    assert live["briefing_service"] is app.state.briefing_service


def test_weather_defaults_to_the_real_keyless_open_meteo_provider() -> None:
    """No signup, no key required (module docstring of ``app.weather.providers``) — so,
    unlike mail/calendar's honest ``account_missing`` default, weather defaults to a
    REAL provider without any owner action."""
    app = create_app(Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY))
    weather = app.state.weather_service
    assert isinstance(weather._provider, OpenMeteoProvider)  # type: ignore[attr-defined]


def test_weather_provider_none_setting_is_the_honest_dependency_unavailable_path() -> None:
    app = create_app(
        Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY, weather_provider="none")
    )
    weather = app.state.weather_service
    assert weather._provider is None  # type: ignore[attr-defined]


def test_no_default_location_until_the_owner_sets_one() -> None:
    """task brief §1: "Do NOT invent one" — a fresh app has no durable default."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.location.models import LocationContextRow

    app = create_app(Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY))
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    LocationContextRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        assert app.state.location_service.get_default(session) is None


def test_a_runtime_registers_the_three_services_additively() -> None:
    from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime

    runtime = RealtimeVoiceRuntime(Settings(_env_file=None), providers={})
    assert "weather_service" not in runtime.live_sources()
    assert "location_service" not in runtime.live_sources()
    assert "briefing_service" not in runtime.live_sources()
    markers = {
        "weather_service": object(),
        "location_service": object(),
        "briefing_service": object(),
    }
    runtime.register_live(**markers)
    live = runtime.live_sources()
    for key, marker in markers.items():
        assert live[key] is marker
