"""``BriefingService`` (docs/DECISIONS.md ADR-0091): the morning briefing assembled from
real sources only, its durable preferences, and the two narrower single-topic tools
(``system_status``/``overnight_work``) that must never speak the whole briefing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.receipt import TERMINAL_FAILED, TERMINAL_VERIFIED
from app.briefing.models import BriefingPreferencesRow
from app.briefing.service import (
    BriefingService,
    date_time_sentence,
    get_preferences_row,
    greeting_for,
    update_preferences,
)
from app.config import Settings
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_EVOLUTION_BUILD_COMPLETED,
    EVENT_TYPE_EVOLUTION_TESTS_FAILED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    SUBSYSTEM_EVOLUTION,
)
from app.location.models import LocationContextRow
from app.location.service import LocationService
from app.weather.models import WeatherQueryEvidenceRow
from app.weather.providers import FakeWeatherProvider
from app.weather.service import WeatherService

NOW = datetime(2026, 9, 9, 8, 30, tzinfo=UTC)  # 11:30 Europe/Istanbul


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        BriefingPreferencesRow.__table__,
        LocationContextRow.__table__,
        WeatherQueryEvidenceRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _weather_live() -> dict:
    location = LocationService()
    weather = WeatherService(location_service=location, provider=FakeWeatherProvider())
    return {"weather_service": weather}


def test_greeting_is_time_of_day_correct() -> None:
    tz_aware = lambda h: datetime(2026, 9, 9, h, 0)  # noqa: E731 - tiny test helper
    assert greeting_for(tz_aware(8)) == "Günaydın efendim."
    assert greeting_for(tz_aware(14)) == "İyi günler efendim."
    assert greeting_for(tz_aware(19)) == "İyi akşamlar efendim."
    assert greeting_for(tz_aware(23)) == "İyi geceler efendim."


def test_date_time_sentence_is_turkish() -> None:
    sentence = date_time_sentence(datetime(2026, 9, 9, 11, 30))
    assert "9 Eylül 2026" in sentence
    assert "Çarşamba" in sentence
    assert "11:30" in sentence


def test_preferences_default_to_the_spec_shape(db) -> None:
    row = get_preferences_row(db)
    assert row.morning_briefing_enabled is True
    assert row.include_weather is True
    assert row.include_system_status is True
    assert row.include_calendar is True
    assert row.include_overnight_work is True
    assert row.include_news_summary is True
    assert row.auto_open_news_video is False  # task brief: never on by default


def test_update_preferences_rejects_unknown_fields(db) -> None:
    with pytest.raises(ValueError):
        update_preferences(db, {"not_a_real_field": True})


def test_build_assembles_greeting_date_and_weather(db) -> None:
    service = BriefingService()
    result = service.build(db, settings=Settings(), live=_weather_live(), now=NOW)
    assert result["terminal_status"] == TERMINAL_VERIFIED
    speech = result["speech"]
    assert "efendim" in speech  # greeting
    assert "2026" in speech  # date
    assert "sürüm" in speech  # system status
    assert "gece" in speech.lower() or "Gece" in speech  # overnight summary


def test_build_is_honest_about_the_absent_news_resolver(db) -> None:
    service = BriefingService()
    result = service.build(db, settings=Settings(), live={}, now=NOW)
    assert "Haber özeti şu an bağlı değil" in result["speech"]


def test_build_omits_a_section_the_owner_turned_off(db) -> None:
    update_preferences(db, {"include_weather": False})
    service = BriefingService()
    result = service.build(db, settings=Settings(), live=_weather_live(), now=NOW)
    assert "weather" not in result["observed_after"]["server"]["sections"]


def test_build_refuses_honestly_when_the_briefing_is_disabled(db) -> None:
    update_preferences(db, {"morning_briefing_enabled": False})
    service = BriefingService()
    result = service.build(db, settings=Settings(), live={}, now=NOW)
    assert result["terminal_status"] == TERMINAL_FAILED
    assert result["error_class"] == "briefing_disabled"
    assert "kapalı" in result["speech"]


def test_system_status_and_overnight_work_are_distinct_from_the_full_briefing(db) -> None:
    """task brief §5: "weather vs system status vs combined briefing... deterministic
    and distinct" — a narrow question must not speak the whole briefing."""
    service = BriefingService()
    status = service.system_status(db, settings=Settings(), live={}, now=NOW)
    assert "sürüm" in status["speech"]
    assert "Günaydın" not in status["speech"]
    assert "Haber" not in status["speech"]

    overnight = service.overnight_work(db, now=NOW)
    assert "geliştirme" in overnight["speech"] or "olmadı" in overnight["speech"]
    assert "sürüm" not in overnight["speech"]


def test_overnight_work_reflects_real_ledger_evidence_not_a_fabrication(db) -> None:
    ledger_service.record(
        db,
        ledger_service.ActivityEvent(
            event_type=EVENT_TYPE_EVOLUTION_BUILD_COMPLETED,
            subsystem=SUBSYSTEM_EVOLUTION,
            action="evolution.build",
            status=STATUS_COMPLETED,
            factual_summary="test candidate built",
            occurred_at=NOW - timedelta(hours=2),
            source="live",
            source_ref="test:overnight:1",
        ),
    )
    ledger_service.record(
        db,
        ledger_service.ActivityEvent(
            event_type=EVENT_TYPE_EVOLUTION_TESTS_FAILED,
            subsystem=SUBSYSTEM_EVOLUTION,
            action="evolution.build",
            status=STATUS_FAILED,
            factual_summary="test candidate failed",
            occurred_at=NOW - timedelta(hours=1),
            source="live",
            source_ref="test:overnight:2",
        ),
    )
    service = BriefingService()
    result = service.overnight_work(db, now=NOW)
    assert "2 otonom geliştirme etkinliği" in result["speech"]
    assert "1 tamamlandı" in result["speech"]
    assert "1 başarısız" in result["speech"]


def test_overnight_work_with_no_events_says_so_plainly(db) -> None:
    service = BriefingService()
    result = service.overnight_work(db, now=NOW)
    assert "olmadı" in result["speech"]
