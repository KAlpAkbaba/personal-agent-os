"""``GET /v1/state/now`` (docs/M18_ACTION_CONTRACT.md §4): the ``state.now`` tool's
answer over HTTP, owner-gated, so a harness can compare the runtime's view of the eye
with the browser's and with the receipt. Same composer, same shape, no writes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import EVENT_TYPE_VOICE_STATE_ANSWERED
from app.main import create_app
from app.presence.engine import PresenceFusionEngine, set_engine
from app.presence.eye import disable_eye
from app.state.now import SCOPES, compose_live_state
from app.voice.realtime_sessions.models import RealtimeSessionRow
from tests.identity_support import authenticate, install_identity

RESULT_KEYS = {
    "query_kind",
    "subsystem",
    "scope",
    "observed_at",
    "facts",
    "uncertainties",
    "speech",
}
FACT_KEYS = {"key", "value", "source", "observed_at", "age_s", "confidence", "stale"}


@pytest.fixture(autouse=True)
def _reset_presence_engine():
    set_engine(PresenceFusionEngine())
    yield
    set_engine(PresenceFusionEngine())


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(eng)
    RealtimeSessionRow.__table__.create(eng)  # the voice.session fact reads this table
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
    return app, TestClient(app)


@pytest.fixture()
def client(app_and_client) -> TestClient:
    app, test_client = app_and_client
    authenticate(app, test_client, settings=Settings(_env_file=None))
    return test_client


def test_state_now_requires_an_owner_session(app_and_client) -> None:
    _, test_client = app_and_client
    assert test_client.get("/v1/state/now").status_code == 401
    assert test_client.get("/v1/state/now?scope=eye").status_code == 401


def test_state_now_is_the_tools_shape(client: TestClient) -> None:
    r = client.get("/v1/state/now")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == RESULT_KEYS
    assert body["scope"] == "all"
    assert body["query_kind"] == "world_state"
    assert body["subsystem"] == "worldmodel"
    assert body["observed_at"].endswith("Z")
    for fact in body["facts"]:
        assert set(fact) == FACT_KEYS
    keys = {f["key"] for f in body["facts"]}
    assert {"cloud_core.health", "eye.enabled"} <= keys
    # no realtime session was named: the voice fact is an honest uncertainty
    assert {"voice.session": "no_session"} == {
        u["subject"]: u["reason"] for u in body["uncertainties"] if u["subject"] == "voice.session"
    }


def test_state_now_eye_scope_follows_the_durable_flag(client: TestClient, engine) -> None:
    r = client.get("/v1/state/now?scope=eye")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "eye" and body["query_kind"] == "eye_state"
    eye = {f["key"]: f for f in body["facts"]}["eye.enabled"]
    assert eye["value"] is True and eye["source"] == "ledger"

    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        disable_eye(session, reason="owner")
    body = client.get("/v1/state/now?scope=eye").json()
    assert {f["key"]: f["value"] for f in body["facts"]}["eye.enabled"] is False
    assert body["speech"] == "Göz kapalı efendim."


def test_state_now_matches_compose_live_state_for_every_scope(client: TestClient, engine) -> None:
    for scope in SCOPES:
        via_http = client.get(f"/v1/state/now?scope={scope}").json()
        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            direct = compose_live_state(session, scope=scope)
        assert via_http["scope"] == direct["scope"] == scope
        assert [f["key"] for f in via_http["facts"]] == [f["key"] for f in direct["facts"]]
        assert [u["subject"] for u in via_http["uncertainties"]] == [
            u["subject"] for u in direct["uncertainties"]
        ]
        assert via_http["speech"] == direct["speech"]


def test_state_now_validates_scope_and_session_id(client: TestClient) -> None:
    assert client.get("/v1/state/now?scope=camera").status_code == 422
    assert client.get("/v1/state/now?session_id=not-a-uuid").status_code == 422
    # an unknown but well-formed session is an uncertainty, not an error
    r = client.get("/v1/state/now?scope=voice&session_id=00000000-0000-0000-0000-000000000001")
    assert r.status_code == 200
    assert r.json()["uncertainties"] == [
        {"subject": "voice.session", "reason": "session_not_found"}
    ]


def test_state_now_over_http_writes_nothing(client: TestClient, engine) -> None:
    before = datetime.now(UTC)
    client.get("/v1/state/now")
    client.get("/v1/state/now?scope=eye")
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        rows = list(session.execute(select(ActivityEventRow)).scalars())
    assert [r for r in rows if r.event_type == EVENT_TYPE_VOICE_STATE_ANSWERED] == []
    assert [r for r in rows if r.occurred_at and r.occurred_at.replace(tzinfo=UTC) >= before] == []
