"""/v1/webpush/* over HTTP: owner-session auth, subscribe/list/delete, and the
public-key endpoint's honest states (task brief: "unsupported / denied / no server key /
subscribed" — this file covers the server's half of that: "no server key" vs "supported").
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.webpush import ece
from app.webpush.models import PushSubscriptionRow
from app.webpush.vapid import generate_key_pair
from tests.identity_support import authenticate, install_identity

VALID_ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _subscription_body(endpoint: str = VALID_ENDPOINT) -> dict:
    private = ec.generate_private_key(ece.CURVE)
    return {
        "endpoint": endpoint,
        "keys": {
            "p256dh": _b64url(ece.raw_public_key(private.public_key())),
            "auth": _b64url(b"\x02" * 16),
        },
        "user_agent": "pytest-browser/1.0",
    }


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    PushSubscriptionRow.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def app_and_client(engine):
    from app.artifacts.runtime import ArtifactRuntime

    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    client = TestClient(app)
    return app, client


# ------------------------------------------------------------- auth is required


def test_public_key_requires_owner_session(app_and_client) -> None:
    _app, client = app_and_client
    response = client.get("/v1/webpush/public-key")
    assert response.status_code in (401, 403)


def test_subscriptions_post_requires_owner_session(app_and_client) -> None:
    _app, client = app_and_client
    response = client.post("/v1/webpush/subscriptions", json=_subscription_body())
    assert response.status_code in (401, 403)


def test_subscriptions_get_requires_owner_session(app_and_client) -> None:
    _app, client = app_and_client
    response = client.get("/v1/webpush/subscriptions")
    assert response.status_code in (401, 403)


def test_subscriptions_delete_requires_owner_session(app_and_client) -> None:
    _app, client = app_and_client
    response = client.delete("/v1/webpush/subscriptions/00000000-0000-0000-0000-000000000000")
    assert response.status_code in (401, 403)


# ------------------------------------------------------------- public key: honest states


def test_public_key_honest_when_not_configured(app_and_client) -> None:
    app, client = app_and_client
    authenticate(app, client)
    response = client.get("/v1/webpush/public-key")
    assert response.status_code == 200
    body = response.json()
    assert body == {"supported": False, "public_key": None, "reason": "no_vapid_key"}


def test_public_key_returned_when_configured(app_and_client, engine) -> None:
    app, client = app_and_client
    authenticate(app, client)
    pair = generate_key_pair()
    app.state.settings.webpush_vapid_private_key = pair.private_key_b64url
    response = client.get("/v1/webpush/public-key")
    assert response.status_code == 200
    body = response.json()
    assert body == {"supported": True, "public_key": pair.public_key_b64url}


def test_public_key_honest_when_key_is_invalid(app_and_client) -> None:
    app, client = app_and_client
    authenticate(app, client)
    app.state.settings.webpush_vapid_private_key = "not-a-valid-key"
    response = client.get("/v1/webpush/public-key")
    assert response.status_code == 200
    assert response.json() == {
        "supported": False,
        "public_key": None,
        "reason": "invalid_vapid_key",
    }


# ------------------------------------------------------------- subscribe/list/delete


def test_subscribe_then_list_then_delete(app_and_client) -> None:
    app, client = app_and_client
    authenticate(app, client)

    created = client.post("/v1/webpush/subscriptions", json=_subscription_body())
    assert created.status_code == 200
    subscription_id = created.json()["id"]
    assert created.json()["endpoint_host"] == "fcm.googleapis.com"
    # The full endpoint (a bearer-credential-like value) must never come back over HTTP.
    assert VALID_ENDPOINT not in created.text

    listed = client.get("/v1/webpush/subscriptions")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["subscriptions"]] == [subscription_id]

    deleted = client.delete(f"/v1/webpush/subscriptions/{subscription_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"removed": True}

    listed_again = client.get("/v1/webpush/subscriptions")
    assert listed_again.json()["subscriptions"] == []


def test_delete_unknown_subscription_is_404(app_and_client) -> None:
    app, client = app_and_client
    authenticate(app, client)
    response = client.delete("/v1/webpush/subscriptions/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_subscribe_refuses_non_allowlisted_endpoint(app_and_client) -> None:
    app, client = app_and_client
    authenticate(app, client)
    response = client.post(
        "/v1/webpush/subscriptions", json=_subscription_body(endpoint="https://evil.example.com/x")
    )
    assert response.status_code == 422


def test_subscribe_rejects_unknown_fields(app_and_client) -> None:
    """``extra=\"forbid\"`` on the request models — a browser can only ever send the
    exact ``PushSubscription.toJSON()`` shape plus ``user_agent``, never anything else."""
    app, client = app_and_client
    authenticate(app, client)
    body = _subscription_body()
    body["unexpected"] = "field"
    response = client.post("/v1/webpush/subscriptions", json=body)
    assert response.status_code == 422
