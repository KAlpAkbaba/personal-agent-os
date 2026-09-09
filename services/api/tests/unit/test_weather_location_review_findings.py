"""The location/weather/briefing security review's findings, each with the test it did
not have.

Four, all found by an independent review against the real objects before this branch
merged:

* **HIGH** — `WeatherService.current` committed its evidence row unguarded. A failed
  commit (a too-long geocoded place name against a fixed-width column, which Postgres
  enforces and SQLite does not) escapes the service, is swallowed by the realtime tool
  dispatcher's generic handler WITHOUT a rollback, and the dispatcher's own unconditional
  commit at the end of the turn then raises `PendingRollbackError` — failing the whole
  tool-call round trip, not just the weather answer. Exactly the session-poisoning bug
  this branch already fixed once in `record_receipt`, reintroduced one frame away.
* **MEDIUM** — the router's place extractor is a closed 13-city gazetteer, so for every
  other place name the tool fell through to the MODEL's own argument, unchecked against
  what the owner actually said. `location.set_default` is a durable mutation, and the
  model routinely reads documents, mail and web pages.
* **MEDIUM** — `location_context` was insert-only with no retention, which is the "raw
  location-history archive" the product forbids by default, dormant only because no
  device can write to it yet.
* **LOW** — a 200 whose body is not JSON raised `JSONDecodeError`, which is not an
  `httpx.HTTPError`, so it escaped every typed handler.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.location.models import LocationContextRow
from app.location.service import SOURCE_WINDOWS_LOCATION, LocationService
from app.voice.realtime_sessions.tools_weather import _corroborated_place
from app.weather.providers import (
    MAX_PLACE_NAME_LEN,
    MAX_SUMMARY_LEN,
    OpenMeteoProvider,
    WeatherError,
)


@pytest.fixture()
def db(tmp_path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'location.db'}")
    LocationContextRow.__table__.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


# ------------------------------------------------ HIGH: the unbounded provider string


def _provider_with(handler) -> OpenMeteoProvider:
    provider = OpenMeteoProvider()
    provider._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # noqa: SLF001
    return provider


def test_a_geocoded_place_name_is_bounded_before_it_can_reach_a_column() -> None:
    """The vendor chooses this string; the column's width is our promise, not theirs.

    Unbounded, it reached `weather_query_evidence.summary` (String(500)) and — on the
    canonical production engine, which enforces widths — raised inside a commit whose
    failure poisoned the caller's whole turn. Bounded at the seam, that is unreachable
    rather than merely unlikely."""
    monstrous = "Ş" * 4000

    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "latitude": 41.0,
                            "longitude": 29.0,
                            "name": monstrous,
                            "admin1": monstrous,
                            "timezone": "Europe/Istanbul",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "current": {"temperature_2m": 21.0, "weather_code": 0, "time": "2026-09-09T06:00"},
                "daily": {
                    "temperature_2m_max": [26.0],
                    "temperature_2m_min": [17.0],
                    "precipitation_probability_max": [10],
                },
            },
        )

    observation = _provider_with(handler).current(latitude=None, longitude=None, city="x")

    assert len(observation.location_label) <= 2 * MAX_PLACE_NAME_LEN + 2
    assert len(observation.summary) <= MAX_SUMMARY_LEN
    assert len(observation.condition) <= 64


def test_a_two_hundred_that_is_not_json_is_a_typed_provider_failure() -> None:
    """A captive portal, a CDN error page, a misrouted endpoint. `JSONDecodeError` is a
    `ValueError` and NOT an `httpx.HTTPError`, so it used to escape every typed handler
    and the owner heard an internal-bug failure instead of the honest sentence."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Sign in to the hotel wifi</html>")

    with pytest.raises(WeatherError) as caught:
        _provider_with(handler).current(latitude=41.0, longitude=29.0, city=None)
    assert caught.value.reason == WeatherError.REASON_PROVIDER_ERROR

    with pytest.raises(WeatherError) as caught_geo:
        _provider_with(handler).current(latitude=None, longitude=None, city="İstanbul")
    assert caught_geo.value.reason == WeatherError.REASON_PROVIDER_ERROR


# ---------------------------------------- MEDIUM: the model's argument, uncorroborated


@pytest.mark.parametrize(
    ("said", "argument", "expected"),
    [
        ("Varsayılan hava durumu konumumu Paris yap.", "Paris", "Paris"),
        ("Varsayılan hava durumu konumumu Adıyaman yap.", "Adıyaman", "Adıyaman"),
        ("Paris'te hava nasıl?", "Paris", "Paris"),
        # The owner never said it. The model did.
        ("Hava nasıl?", "Paris", None),
        ("Varsayılan konumu ayarla.", "Moskova", None),
        ("Bugün ne yaptın?", "Berlin", None),
        # No transcript at all is not corroboration either.
        ("", "Paris", None),
    ],
)
def test_a_place_is_used_only_when_the_owner_said_it(
    said: str, argument: str, expected: str | None
) -> None:
    """The closed gazetteer covers thirteen cities; the rule it implements has to cover
    every one. An argument the transcript does not carry is not the owner's word, and
    `location.set_default` writes durable state."""
    assert _corroborated_place({"turn": said}, argument) == expected


# --------------------------------------------- MEDIUM: the archive nobody asked for


def test_a_location_older_than_the_resolver_could_ever_read_is_not_kept(db: Session) -> None:
    """ "No raw location-history archive by default" is a product invariant, and an
    insert-only table becomes one the day a device can write to it. The bound is not a
    number picked for comfort: a row older than its source's RECENT window can never be
    returned by any tier of `resolve`, so keeping it stores a position the system has
    promised never to use."""
    service = LocationService()
    device = uuid.uuid4()
    now = datetime.now(UTC)

    service.record_observation(
        db,
        source=SOURCE_WINDOWS_LOCATION,
        city="Eski",
        device_id=device,
        captured_at=now - timedelta(days=4),
    )
    service.record_observation(
        db,
        source=SOURCE_WINDOWS_LOCATION,
        city="Dun",
        device_id=device,
        captured_at=now - timedelta(days=1),
    )
    # Both of the above are past windows_location's 3-hour recent bound.
    service.record_observation(
        db, source=SOURCE_WINDOWS_LOCATION, city="Simdi", device_id=device, captured_at=now
    )

    kept = db.execute(select(LocationContextRow.city)).scalars().all()
    assert kept == ["Simdi"], kept


def test_pruning_never_touches_the_owners_own_default(db: Session) -> None:
    """A default is the owner's setting, not an observation. It has no freshness and no
    expiry, and a device write must never sweep it away."""
    service = LocationService()
    service.set_default(db, city="İstanbul")
    service.record_observation(
        db,
        source=SOURCE_WINDOWS_LOCATION,
        city="Nerede",
        captured_at=datetime.now(UTC) - timedelta(days=9),
    )
    service.record_observation(db, source=SOURCE_WINDOWS_LOCATION, city="Burada")

    default = service.get_default(db)
    assert default is not None
    assert default.city == "İstanbul"


# ------------------- and the assertion that the TOOL actually applies the rule


def _ctx(said: str, db: Session, service: LocationService):
    from app.voice.realtime_sessions.tools import ToolContext

    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="test",
        context={"last_utterance": {"turn": said, "location_default_city": None}},
        db=db,
        live={"location_service": service},
    )


def test_the_tool_refuses_a_default_the_owner_never_named(db: Session) -> None:
    """The helper above is only worth having if the call site uses it. Driven through
    the real tool: the model proposes a city the transcript does not carry, and the
    owner is ASKED rather than having a durable setting written on their behalf."""
    from app.voice.realtime_sessions.tools_weather import location_set_default

    service = LocationService()
    result = location_set_default(
        _ctx("Varsayılan konumu ayarla.", db, service), {"city": "Moskova"}
    )

    assert result["status"] == "needs_clarification"
    assert service.get_default(db) is None


def test_the_tool_accepts_a_default_the_owner_did_name(db: Session) -> None:
    """The other half: an unlisted city the owner actually said is still honoured, so
    the fix narrows nothing the owner can legitimately ask for."""
    from app.voice.realtime_sessions.tools_weather import location_set_default

    service = LocationService()
    result = location_set_default(
        _ctx("Varsayılan hava durumu konumumu Adıyaman yap.", db, service), {"city": "Adıyaman"}
    )

    assert result["status"] == "ok"
    row = service.get_default(db)
    assert row is not None
    assert row.city == "Adıyaman"
