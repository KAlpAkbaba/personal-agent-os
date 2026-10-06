"""Input edges the test team found on staging (rounds t-d20261006, card
alarm-household-watch-input-edges), pinned through ``create_app``.

- ``/v1/alarms``: a label with a control character, a date in the past or beyond a year, a
  list limit outside 1..200 are each a Turkish 422; a stop of an alarm that is not ringing
  is a Turkish 409; an unknown alarm is a Turkish 404, a stopped one still reads back.
- ``/v1/watches``: a control character in the label (the watch's name) is a Turkish 422.
- ``/v1/household``: a numeric quantity (``2``) is kept as "2"; taking an item off the list
  twice answers 200 with "zaten listede değil" and ``already: true`` (idempotent), an id
  that never existed is 404 with the same sentence.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.alarms import service as alarms_service
from app.alarms.models import AmbientPolicyRow, WakeAlarm
from app.alarms.tr_time import DEFAULT_TIMEZONE
from app.artifacts.runtime import ArtifactRuntime
from app.broker.models import Device
from app.config import Settings
from app.household.models import HouseholdEvent, HouseholdItem
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.notifications.models import NotificationRow
from app.routines.models import Routine, RoutineFiring
from app.uistate.publisher import UiStatePublisher, set_publisher
from app.watch.models import Watch, WatchReading
from tests.identity_support import authenticate, install_identity

TABLES = (
    WakeAlarm.__table__,
    AmbientPolicyRow.__table__,
    Routine.__table__,
    RoutineFiring.__table__,
    ActivityEventRow.__table__,
    Device.__table__,
    Watch.__table__,
    WatchReading.__table__,
    NotificationRow.__table__,
    HouseholdItem.__table__,
    HouseholdEvent.__table__,
)
URL = "https://www.home-assistant.io/blog/"


@pytest.fixture(autouse=True)
def _fresh_uistate_publisher():
    set_publisher(UiStatePublisher())
    yield
    set_publisher(UiStatePublisher())


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.research.destination.resolve_hostname", lambda host: ["93.184.216.34"])


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def client(engine, factory) -> TestClient:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = factory
    app.state.artifacts = artifacts
    app.state.wake_sequence = None
    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    return test_client


def _message(response) -> str:
    detail = response.json()["detail"]
    return detail["message"]


def _turkish(text: str) -> bool:
    return any(ch in text for ch in "çğıöşüÇĞİÖŞÜ")


def _local_today():
    from zoneinfo import ZoneInfo

    return alarms_service.utcnow().astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()


# ------------------------------------------------------------------------- alarms


@pytest.mark.parametrize("field", ["label", "greeting_text"])
@pytest.mark.parametrize("char", ["\x00", "\x07", "\x7f", "\x85", "\x9f"])
def test_an_alarm_text_with_a_control_character_is_a_turkish_422(
    client: TestClient, field: str, char: str
) -> None:
    """C0, DEL and the C1 block (U+0080-U+009F, e.g. NEL) are all Unicode category Cc."""
    response = client.post(
        "/v1/alarms", json={"when": {"relative_seconds": 600}, field: f"sabah{char}koşusu"}
    )
    assert response.status_code == 422, response.text
    assert "okunamayan" in _message(response)
    assert client.get("/v1/alarms").json()["alarms"] == []


_SONG_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize("field", ["url", "title", "remembered"])
def test_an_alarm_media_field_with_a_nul_is_a_turkish_422(client: TestClient, field: str) -> None:
    media = {"url": _SONG_URL, "title": "Sabah şarkısı", "remembered": "her sabahki"}
    media[field] = media[field][:3] + "\x00" + media[field][3:]
    response = client.post("/v1/alarms", json={"when": {"relative_seconds": 600}, "media": media})
    assert response.status_code == 422, response.text
    assert "okunamayan" in _message(response)
    assert client.get("/v1/alarms").json()["alarms"] == []


def test_a_clean_alarm_media_is_still_kept(client: TestClient) -> None:
    media = {"url": _SONG_URL, "title": "Sabah şarkısı", "remembered": "her sabahki"}
    response = client.post("/v1/alarms", json={"when": {"relative_seconds": 600}, "media": media})
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(("field", "char"), [("title", "\x00"), ("title", "\x85"), ("url", "\x00")])
def test_a_wake_song_with_a_control_character_is_a_turkish_422(
    client: TestClient, field: str, char: str
) -> None:
    body = {"url": _SONG_URL, "title": "Sabah şarkısı"}
    body[field] = body[field][:3] + char + body[field][3:]
    response = client.put("/v1/alarms/wake-song", json=body)
    assert response.status_code == 422, response.text
    assert "okunamayan" in _message(response)
    assert client.get("/v1/alarms/wake-song").json()["wake_song"] is None


def test_a_clean_wake_song_is_still_kept(client: TestClient) -> None:
    response = client.put(
        "/v1/alarms/wake-song", json={"url": _SONG_URL, "title": "Sabah şarkısı"}
    )
    assert response.status_code == 200, response.text
    assert client.get("/v1/alarms/wake-song").json()["wake_song"]["title"] == "Sabah şarkısı"


def test_a_cancel_reason_with_a_nul_is_a_turkish_422_and_the_alarm_stays(
    client: TestClient,
) -> None:
    alarm_id = client.post("/v1/alarms", json={"when": {"relative_seconds": 600}}).json()["id"]
    response = client.post(f"/v1/alarms/{alarm_id}/cancel", json={"reason": "uy\x00andım"})
    assert response.status_code == 422, response.text
    assert "okunamayan" in _message(response)
    assert client.get(f"/v1/alarms/{alarm_id}").json()["state"] == "SCHEDULED"
    clean = client.post(f"/v1/alarms/{alarm_id}/cancel", json={"reason": "uyandım"})
    assert clean.status_code == 200, clean.text


def test_an_ordinary_alarm_label_is_still_kept(client: TestClient) -> None:
    """Only NUL and the other unreadable characters are refused; an ordinary label passes."""
    response = client.post(
        "/v1/alarms", json={"when": {"relative_seconds": 600}, "label": "Sabah koşusu"}
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("timezone", [DEFAULT_TIMEZONE, "America/New_York", "Pacific/Kiritimati"])
def test_an_alarm_on_9999_12_31_is_a_turkish_422_not_a_500(
    client: TestClient, timezone: str
) -> None:
    response = client.post(
        "/v1/alarms",
        json={"when": {"date": "9999-12-31", "time": "23:59"}, "timezone": timezone},
    )
    assert response.status_code == 422, response.text
    assert "bir yıl" in _message(response)


def test_an_alarm_more_than_a_year_ahead_is_refused_but_within_a_year_is_kept(
    client: TestClient,
) -> None:
    far = (_local_today() + timedelta(days=400)).isoformat()
    refused = client.post("/v1/alarms", json={"when": {"date": far, "time": "07:30"}})
    assert refused.status_code == 422, refused.text
    assert "bir yıl" in _message(refused)

    near = (_local_today() + timedelta(days=300)).isoformat()
    kept = client.post("/v1/alarms", json={"when": {"date": near, "time": "07:30"}})
    assert kept.status_code == 201, kept.text


def test_an_alarm_in_the_past_is_a_turkish_422_that_says_past(client: TestClient) -> None:
    past = (_local_today() - timedelta(days=1)).isoformat()
    response = client.post("/v1/alarms", json={"when": {"date": past, "time": "07:30"}})
    assert response.status_code == 422, response.text
    assert "geçmişte" in _message(response)


def test_an_unplaceable_time_keeps_the_general_sentence(client: TestClient) -> None:
    response = client.post("/v1/alarms", json={"when_text": "bir ara"})
    assert response.status_code == 422
    assert "geçmişte" not in _message(response)
    assert "bir yıl" not in _message(response)


def _ring(factory, alarm_id: str, state: str = "FIRING") -> None:
    with factory() as session:
        alarm = session.get(WakeAlarm, uuid.UUID(alarm_id))
        alarm.state = state
        session.commit()


def test_stopping_a_snoozed_alarm_is_a_turkish_409_not_a_500(client, factory) -> None:
    """SNOOZED -> STOPPED is not a legal transition; the route used to let the
    IllegalAlarmTransition escape as a 500."""
    created = client.post("/v1/alarms", json={"when": {"relative_seconds": 600}}).json()
    _ring(factory, created["alarm_id"], state="SNOOZED")
    response = client.post(f"/v1/alarms/{created['alarm_id']}/stop")
    assert response.status_code == 409, response.text
    assert "çalmıyor" in _message(response)
    assert client.get(f"/v1/alarms/{created['alarm_id']}").json()["state"] == "SNOOZED"


def test_a_ringing_alarm_stops_and_a_second_stop_is_idempotent(client, factory) -> None:
    created = client.post("/v1/alarms", json={"when": {"relative_seconds": 600}}).json()
    _ring(factory, created["alarm_id"])
    first = client.post(f"/v1/alarms/{created['alarm_id']}/stop")
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "STOPPED"
    second = client.post(f"/v1/alarms/{created['alarm_id']}/stop")
    assert second.status_code == 200
    assert second.json()["state"] == "STOPPED"
    # GET of a stopped alarm reads it back.
    fetched = client.get(f"/v1/alarms/{created['alarm_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["state"] == "STOPPED"


@pytest.mark.parametrize("ended", ["cancel", "COMPLETED", "FAILED"])
def test_stopping_an_alarm_that_already_ended_is_a_turkish_409(client, factory, ended) -> None:
    """It used to answer 200 with "Alarmı kapattım" over a CANCELLED/COMPLETED row."""
    created = client.post("/v1/alarms", json={"when": {"relative_seconds": 600}}).json()
    if ended == "cancel":
        client.post(f"/v1/alarms/{created['alarm_id']}/cancel")
    else:
        _ring(factory, created["alarm_id"], state=ended)
    response = client.post(f"/v1/alarms/{created['alarm_id']}/stop")
    assert response.status_code == 409, response.text
    assert "çalmıyor" in _message(response)


def test_stopping_a_scheduled_alarm_still_turns_it_off(client: TestClient) -> None:
    """Kept as ``test_alarms_routes.py`` pins it: "alarmı kapat" for an alarm still waiting
    turns it off (ADR draft of this card)."""
    created = client.post("/v1/alarms", json={"when": {"relative_seconds": 600}}).json()
    response = client.post(f"/v1/alarms/{created['alarm_id']}/stop")
    assert response.status_code == 200
    assert response.json()["state"] == "STOPPED"


def test_an_unknown_alarm_is_a_turkish_404(client: TestClient) -> None:
    response = client.get(f"/v1/alarms/{uuid.uuid4()}")
    assert response.status_code == 404
    assert _turkish(_message(response))


@pytest.mark.parametrize("limit", [0, -1, 201, 1000])
def test_a_list_limit_outside_1_to_200_is_a_turkish_422(client: TestClient, limit: int) -> None:
    response = client.get("/v1/alarms", params={"limit": limit})
    assert response.status_code == 422, response.text
    assert "200" in _message(response)


def test_a_list_limit_of_200_is_answered(client: TestClient) -> None:
    assert client.get("/v1/alarms", params={"limit": 200}).status_code == 200


# ------------------------------------------------------------------------- watches


@pytest.mark.parametrize("field", ["label", "condition", "url", "selector"])
def test_a_watch_field_with_a_nul_is_a_turkish_422(client: TestClient, field: str) -> None:
    payload = {"url": URL, "condition": "changed", "label": "HA", "selector": ".price"}
    payload[field] = payload[field][:2] + "\x00" + payload[field][2:]
    response = client.post("/v1/watches", json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "control_character"
    assert "okunamayan" in _message(response)
    assert client.get("/v1/watches").json()["items"] == []


def test_a_clean_watch_is_still_made(client: TestClient) -> None:
    response = client.post("/v1/watches", json={"url": URL, "condition": "changed", "label": "HA"})
    assert response.status_code == 201, response.text


# ----------------------------------------------------------------------- household


@pytest.mark.parametrize(("quantity", "kept"), [(2, "2"), (2.5, "2.5"), ("iki paket", "iki paket")])
def test_a_numeric_household_quantity_is_kept(client: TestClient, quantity, kept: str) -> None:
    response = client.post("/v1/household/list", json={"name": "ekmek", "quantity": quantity})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["list_quantity"] == kept


@pytest.mark.parametrize("quantity", [True, [2], {"n": 2}])
def test_a_non_number_non_text_quantity_is_still_refused(client: TestClient, quantity) -> None:
    response = client.post("/v1/household/list", json={"name": "ekmek", "quantity": quantity})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "household_refused"


def test_a_second_removal_says_it_is_no_longer_on_the_list(client: TestClient) -> None:
    added = client.post("/v1/household/list", json={"name": "ekmek", "quantity": 2}).json()
    item_id = added["item"]["id"]

    first = client.delete(f"/v1/household/list/{item_id}")
    assert first.status_code == 200
    assert first.json()["already"] is False
    assert first.json()["item"]["on_list"] is False

    second = client.delete(f"/v1/household/list/{item_id}")
    assert second.status_code == 200, second.text
    assert second.json()["already"] is True
    assert "zaten listede değil" in second.json()["speech"]
    assert second.json()["item"]["on_list"] is False


def test_removing_an_id_that_never_existed_is_a_404_with_the_same_sentence(
    client: TestClient,
) -> None:
    response = client.delete(f"/v1/household/list/{uuid.uuid4()}")
    assert response.status_code == 404
    assert "listede değil" in _message(response)
