"""``WeatherService`` (docs/DECISIONS.md ADR-0091): resolve -> real provider -> evidence
stored -> truthful answer, against ``FakeWeatherProvider`` (never real network in a unit
test — ``test_weather_providers.py`` proves ``OpenMeteoProvider`` itself)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.receipt import TERMINAL_FAILED, TERMINAL_VERIFIED
from app.ledger.models import ActivityEventRow
from app.location.models import LocationContextRow
from app.location.service import LocationService
from app.weather.models import WeatherQueryEvidenceRow
from app.weather.providers import FakeWeatherProvider
from app.weather.service import WeatherService

NOW = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        LocationContextRow.__table__,
        WeatherQueryEvidenceRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def test_current_answers_from_the_owner_default_when_nothing_else_resolves(db) -> None:
    location = LocationService()
    location.set_default(db, city="İstanbul", now=NOW)
    service = WeatherService(location_service=location, provider=FakeWeatherProvider())

    result = service.current(db, session_id="s1", now=NOW)

    assert result["terminal_status"] == TERMINAL_VERIFIED
    assert "İstanbul" in result["speech"]
    assert result["observed_after"]["server"]["location"]["source"] == "owner_default"


def test_current_answers_the_explicit_place_over_the_default(db) -> None:
    location = LocationService()
    location.set_default(db, city="İstanbul", now=NOW)
    service = WeatherService(location_service=location, provider=FakeWeatherProvider())

    result = service.current(db, requested_place="Ankara", session_id="s1", now=NOW)

    assert "Ankara" in result["speech"]
    assert result["observed_after"]["server"]["location"]["city"] == "Ankara"


def test_current_is_honest_when_the_location_is_unresolved(db) -> None:
    location = LocationService()  # no default, no device fix, no IP provider
    service = WeatherService(location_service=location, provider=FakeWeatherProvider())

    result = service.current(db, session_id="s1", now=NOW)

    assert result["terminal_status"] == TERMINAL_FAILED
    assert result["error_class"] == "location_unresolved"
    assert "bilmiyorum" in result["speech"]


def test_current_is_honest_when_no_provider_is_configured(db) -> None:
    location = LocationService()
    location.set_default(db, city="İstanbul", now=NOW)
    service = WeatherService(location_service=location, provider=None)

    result = service.current(db, session_id="s1", now=NOW)

    assert result["terminal_status"] == TERMINAL_FAILED
    assert result["error_class"] == "dependency_unavailable"
    assert "ulaşamıyorum" in result["speech"]


def test_current_writes_evidence_only_on_a_real_answer(db) -> None:
    location = LocationService()
    location.set_default(db, city="İstanbul", now=NOW)
    service = WeatherService(location_service=location, provider=FakeWeatherProvider())

    service.current(db, session_id="s1", now=NOW)

    rows = db.query(WeatherQueryEvidenceRow).all()
    assert len(rows) == 1
    assert rows[0].location_source == "owner_default"
    assert rows[0].provider == "fake"


def test_last_evidence_answers_which_location_the_weather_was_for(db) -> None:
    location = LocationService()
    service = WeatherService(location_service=location, provider=FakeWeatherProvider())

    service.current(db, requested_place="Ankara", session_id="s1", now=NOW)
    result = service.last_evidence(db)

    assert result["terminal_status"] == TERMINAL_VERIFIED
    assert "Ankara" in result["speech"]
    assert result["observed_after"]["server"]["location"]["city"] == "Ankara"


def test_last_evidence_with_no_prior_query_is_honest(db) -> None:
    location = LocationService()
    service = WeatherService(location_service=location, provider=FakeWeatherProvider())

    result = service.last_evidence(db)

    assert result["terminal_status"] == TERMINAL_FAILED
    assert result["error_class"] == "no_prior_query"
    assert "önce" in result["speech"]


def test_last_evidence_returns_the_most_recent_of_several_queries(db) -> None:
    location = LocationService()
    service = WeatherService(location_service=location, provider=FakeWeatherProvider())

    from datetime import timedelta

    service.current(db, requested_place="Ankara", session_id="s1", now=NOW)
    service.current(db, requested_place="İstanbul", session_id="s1", now=NOW + timedelta(seconds=1))
    result = service.last_evidence(db)

    assert "İstanbul" in result["speech"]
