"""Unit tests: /v1/alarms, /v1/ambient and /v1/devices/{id}/status (M18.3 spec §3, §8.2).

Mirrors ``tests/unit/test_routines_routes.py``'s client fixture pattern: fully offline,
real owner authentication (``tests.identity_support``), an injected SQLite ArtifactRuntime.

The one thing this file exists to pin above the CRUD: the greeting-audio route is the ONE
endpoint in this milestone that does not require an owner session, because the fetcher is a
Windows service holding no session. Its authority is a 256-bit single-use token, and these
tests prove it is single-use, that an unknown token is indistinguishable from a spent one,
and that no OTHER alarm endpoint inherited the exemption.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.alarms.audio_store import AudioStore
from app.alarms.models import AmbientPolicyRow, WakeAlarm
from app.alarms.routes import ALARMS_VERSION
from app.artifacts.runtime import ArtifactRuntime
from app.broker.models import Device
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.routines.models import Routine, RoutineFiring
from app.uistate.publisher import UiStatePublisher, set_publisher
from tests.identity_support import authenticate, install_identity

ALL_TABLES = [
    WakeAlarm.__table__,
    AmbientPolicyRow.__table__,
    Routine.__table__,
    RoutineFiring.__table__,
    ActivityEventRow.__table__,
    # ``GET /v1/devices/{id}/status`` distinguishes "no such device" (404) from "this
    # device is not telling me" (200 with a null status), so it reads the device row.
    Device.__table__,
]


@pytest.fixture(autouse=True)
def _fresh_uistate_publisher():
    set_publisher(UiStatePublisher())
    yield
    set_publisher(UiStatePublisher())


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ALL_TABLES:
        table.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def artifacts_runtime(engine) -> ArtifactRuntime:
    settings = Settings(_env_file=None)
    runtime = ArtifactRuntime(settings)
    runtime._engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return runtime


@pytest.fixture()
def app_and_client(artifacts_runtime):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.artifacts = artifacts_runtime
    # No device runtime in a unit process: cancel/stop still work, they simply have no
    # device to tell (``app/alarms/routes.py::_sequence``).
    app.state.wake_sequence = None
    app.state.alarm_audio_store = AudioStore()
    return app, TestClient(app)


@pytest.fixture()
def client(app_and_client) -> TestClient:
    app, test_client = app_and_client
    authenticate(app, test_client, settings=Settings(_env_file=None))
    return test_client


# ------------------------------------------------------------------------- gating


def test_alarm_endpoints_require_an_owner_session(app_and_client) -> None:
    _, test_client = app_and_client
    for path in ("/v1/alarms", "/v1/alarms/policy", "/v1/ambient/policy"):
        assert test_client.get(path).status_code == 401, path
    assert test_client.post("/v1/alarms", json={}).status_code == 401
    assert test_client.post("/v1/ambient/test-display", json={}).status_code == 401


def test_the_audio_route_is_the_only_exemption(app_and_client) -> None:
    """It answers 404 rather than 401 for an unknown token: the token IS the authority, so
    "no session" is not the reason it failed."""
    _, test_client = app_and_client
    assert test_client.get("/v1/alarms/audio/nope").status_code == 404


# ------------------------------------------------------------------------- alarms


def test_alarms_policy_reports_the_vocabulary(client: TestClient) -> None:
    body = client.get("/v1/alarms/policy").json()
    assert body["alarms_version"] == ALARMS_VERSION
    assert "SCHEDULED" in body["states"]
    assert "FAILED" in body["states"]
    assert body["default_timezone"] == "Europe/Istanbul"


def test_create_an_alarm_from_the_owners_words(client: TestClient) -> None:
    response = client.post(
        "/v1/alarms", json={"when_text": "Yarın sabah 07:30'da beni uyandır."}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["local_time"] == "07:30"
    assert body["state"] == "SCHEDULED"
    assert body["speech"] == "Alarmı yarın yedi otuza kurdum efendim."
    assert body["routine_id"]


def test_create_an_alarm_from_the_structured_form(client: TestClient) -> None:
    response = client.post("/v1/alarms", json={"when": {"relative_seconds": 90}, "test": True})
    assert response.status_code == 201
    body = response.json()
    assert body["is_test"] is True
    assert body["max_play_seconds"] == 120


def test_an_unparseable_when_is_a_422_not_a_guess(client: TestClient) -> None:
    response = client.post("/v1/alarms", json={"when_text": "bir ara"})
    assert response.status_code == 422


def test_creating_without_a_when_is_refused(client: TestClient) -> None:
    assert client.post("/v1/alarms", json={}).status_code == 422


def test_list_get_and_cancel(client: TestClient) -> None:
    created = client.post("/v1/alarms", json={"when": {"relative_seconds": 600}}).json()
    alarm_id = created["alarm_id"]

    listed = client.get("/v1/alarms").json()["alarms"]
    assert [a["alarm_id"] for a in listed] == [alarm_id]

    fetched = client.get(f"/v1/alarms/{alarm_id}").json()
    assert fetched["alarm_id"] == alarm_id

    cancelled = client.post(f"/v1/alarms/{alarm_id}/cancel", json={"reason": "owner"})
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "CANCELLED"
    assert cancelled.json()["speech"] == "Alarmı iptal ettim efendim."

    # ...and a cancelled alarm leaves the default listing.
    assert client.get("/v1/alarms").json()["alarms"] == []
    assert len(client.get("/v1/alarms", params={"include_terminal": True}).json()["alarms"]) == 1


def test_an_unknown_alarm_is_a_404(client: TestClient) -> None:
    import uuid

    missing = uuid.uuid4()
    assert client.get(f"/v1/alarms/{missing}").status_code == 404
    assert client.post(f"/v1/alarms/{missing}/cancel").status_code == 404
    assert client.post(f"/v1/alarms/{missing}/stop").status_code == 404


def test_snoozing_an_idle_alarm_is_a_409(client: TestClient) -> None:
    created = client.post("/v1/alarms", json={"when": {"relative_seconds": 600}}).json()
    response = client.post(f"/v1/alarms/{created['alarm_id']}/snooze", json={"minutes": 5})
    assert response.status_code == 409


def test_stopping_an_idle_alarm_is_idempotent(client: TestClient) -> None:
    created = client.post("/v1/alarms", json={"when": {"relative_seconds": 600}}).json()
    first = client.post(f"/v1/alarms/{created['alarm_id']}/stop")
    assert first.status_code == 200
    assert first.json()["state"] == "STOPPED"
    assert client.post(f"/v1/alarms/{created['alarm_id']}/stop").json()["state"] == "STOPPED"


# ------------------------------------------------------------------- the audio route


def test_a_greeting_token_is_redeemable_exactly_once(app_and_client) -> None:
    app, test_client = app_and_client
    handle = app.state.alarm_audio_store.put(b"RIFF....WAVEfake")

    first = test_client.get(handle.path())
    assert first.status_code == 200
    assert first.content == b"RIFF....WAVEfake"
    assert first.headers["content-type"].startswith("audio/wav")
    assert first.headers["cache-control"] == "no-store"

    # Spent. A token captured from a log after the fact is already useless.
    assert test_client.get(handle.path()).status_code == 404


def test_a_spent_token_and_an_unknown_one_are_indistinguishable(app_and_client) -> None:
    """A probe must learn nothing from the difference."""
    app, test_client = app_and_client
    handle = app.state.alarm_audio_store.put(b"wav")
    test_client.get(handle.path())
    spent = test_client.get(handle.path())
    unknown = test_client.get("/v1/alarms/audio/definitely-not-a-token")
    assert spent.status_code == unknown.status_code == 404
    assert spent.json() == unknown.json()


def test_the_audio_route_needs_no_owner_session_even_when_one_exists(client, app_and_client):
    """The companion holds no session; an authenticated caller must not be the only one
    who can fetch."""
    app, _ = app_and_client
    handle = app.state.alarm_audio_store.put(b"wav")
    assert client.get(handle.path()).status_code == 200


# ------------------------------------------------------------------------ ambient


def test_ambient_policy_reports_the_settings_and_the_live_decision(client: TestClient) -> None:
    """"Why are my screens still on?" is answered from the same pure ``decide`` the tick
    uses, so the explanation cannot drift from the behaviour."""
    body = client.get("/v1/ambient/policy").json()
    assert body["policy"]["auto_off_enabled"] is False
    assert body["decision"]["action"] == "none"
    assert body["decision"]["reason"] in ("display_not_on", "policy_disabled")
    assert body["display"] == "unknown"


def test_putting_the_policy_changes_only_what_was_named(client: TestClient) -> None:
    response = client.put("/v1/ambient/policy", json={"auto_off_enabled": True})
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] == {"auto_off_enabled": True}
    assert body["speech"] == "Otomatik ekran kapatmayı açtım efendim."
    assert body["policy"]["off_when_away"] is True  # untouched

    again = client.put("/v1/ambient/policy", json={"away_after_s": 1800})
    assert again.json()["changed"] == {"away_after_s": 1800}
    assert again.json()["policy"]["auto_off_enabled"] is True  # still on


def test_the_policy_bounds_are_enforced(client: TestClient) -> None:
    assert client.put("/v1/ambient/policy", json={"away_after_s": 5}).status_code == 422
    assert (
        client.put("/v1/ambient/policy", json={"asleep_min_confidence": 2.0}).status_code == 422
    )
    assert client.put("/v1/ambient/policy", json={"unknown_field": True}).status_code == 422


def test_test_display_arms_a_moment_and_says_what_will_happen(client: TestClient) -> None:
    response = client.post("/v1/ambient/test-display", json={"delay_seconds": 10})
    assert response.status_code == 200
    body = response.json()
    assert body["delay_seconds"] == 10
    assert "10 saniye sonra ekranlar kapanacak" in body["speech"]
    assert client.get("/v1/ambient/policy").json()["pending_display_test_at"] is not None

    from app.ambient import service as ambient_service

    ambient_service.cancel_display_test()


# ------------------------------------------------------------------- device status


def test_an_unknown_device_status_is_a_404(client: TestClient) -> None:
    import uuid

    assert client.get(f"/v1/devices/{uuid.uuid4()}/status").status_code == 404


def test_devices_status_requires_an_owner_session(app_and_client) -> None:
    import uuid

    _, test_client = app_and_client
    assert test_client.get(f"/v1/devices/{uuid.uuid4()}/status").status_code == 401


# ------------------------------------------------------------------- the wake song


def test_the_wake_song_is_absent_until_the_owner_names_one(client: TestClient) -> None:
    assert client.get("/v1/alarms/wake-song").json() == {"wake_song": None}


def test_the_owner_names_the_wake_song_and_a_remembered_alarm_resolves_to_it(
    client: TestClient,
) -> None:
    song = "https://www.youtube.com/watch?v=RxabLA7UQ9k"
    put = client.put("/v1/alarms/wake-song", json={"url": song, "title": "Time"})
    assert put.status_code == 200
    assert put.json()["wake_song"] == {"url": song, "title": "Time"}
    assert client.get("/v1/alarms/wake-song").json()["wake_song"]["url"] == song

    created = client.post(
        "/v1/alarms",
        json={
            "when": {"relative_seconds": 90},
            "media": {"remembered": "seçtiğim müzik"},
            "test": True,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["media_source"] == {"kind": "remembered", "name": "seçtiğim müzik"}
    assert body["resolved_media_identity"] == {"kind": "youtube", "url": song, "title": "Time"}
    # The lifecycle instants an owner harness reads are on the row.
    assert body["created_at"] is not None
    assert body["armed_at"] is None and body["triggered_at"] is None
    client.post(f"/v1/alarms/{body['alarm_id']}/cancel", json={})


def test_the_wake_song_route_refuses_anything_but_an_http_url(client: TestClient) -> None:
    put = client.put
    assert put("/v1/alarms/wake-song", json={"url": "Hans Zimmer Time"}).status_code == 422
    assert put("/v1/alarms/wake-song", json={"url": "javascript:x", "x": 1}).status_code == 422
    assert client.get("/v1/alarms/wake-song").json() == {"wake_song": None}


def test_the_wake_song_requires_an_owner_session(app_and_client) -> None:
    _, test_client = app_and_client
    assert test_client.get("/v1/alarms/wake-song").status_code == 401
