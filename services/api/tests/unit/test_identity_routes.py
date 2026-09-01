"""M9 unit tests: the /v1/identity REST surface (offline, SQLite-backed).

The app is built normally and `app.state.identity` is replaced with a runtime
on an in-memory SQLite engine and an in-memory credential root — deliberately
NOT bootstrapped, so these tests can drive the one-time bootstrap themselves.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.main import create_app
from tests.identity_support import bearer, make_identity_engine


def build(settings: Settings | None = None, **client_kw) -> tuple[TestClient, IdentityRuntime]:
    settings = settings or Settings(_env_file=None)
    app = create_app(settings)
    runtime = IdentityRuntime(
        settings, engine=make_identity_engine(), root=InMemoryCredentialRoot()
    )
    app.state.identity = runtime
    return TestClient(app, **client_kw), runtime


@pytest.fixture()
def virgin() -> tuple[TestClient, IdentityRuntime]:
    """An API with no owner credential yet."""
    client, runtime = build()
    yield client, runtime
    client.close()


@pytest.fixture()
def owned(virgin) -> tuple[TestClient, IdentityRuntime, str, str]:
    """Bootstrapped, with a live session on the client."""
    client, runtime = virgin
    credential = client.post("/v1/identity/bootstrap").json()["owner_credential"]
    body = client.post(
        "/v1/identity/sessions",
        json={"owner_credential": credential, "client_kind": "mobile", "label": "phone"},
    ).json()
    client.headers["Authorization"] = f"Bearer {body['token']}"
    return client, runtime, credential, body["token"]


# ------------------------------------------------------------------ bootstrap


def test_bootstrap_returns_the_credential_once_then_refuses(virgin) -> None:
    client, _ = virgin
    first = client.post("/v1/identity/bootstrap")
    assert first.status_code == 201
    body = first.json()
    assert body["owner_credential"].startswith("pagentos_ok_")
    assert body["created_at"]
    second = client.post("/v1/identity/bootstrap")
    assert second.status_code == 409


def test_bootstrap_requires_a_loopback_peer() -> None:
    client, _ = build(client=("203.0.113.9", 40000))
    try:
        response = client.post("/v1/identity/bootstrap")
        assert response.status_code == 403
        assert response.json()["detail"] == "forbidden"
    finally:
        client.close()


def test_bootstrap_loopback_gate_can_be_configured_off() -> None:
    settings = Settings(_env_file=None, identity_bootstrap_loopback_only=False)
    client, _ = build(settings, client=("203.0.113.9", 40000))
    try:
        assert client.post("/v1/identity/bootstrap").status_code == 201
    finally:
        client.close()


# ------------------------------------------------------------------- sessions


def test_session_exchange_returns_a_token_and_session_shape(virgin) -> None:
    client, _ = virgin
    credential = client.post("/v1/identity/bootstrap").json()["owner_credential"]
    response = client.post(
        "/v1/identity/sessions",
        json={"owner_credential": credential, "client_kind": "mobile", "label": "phone"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["token"].startswith("pagentos_st_")
    assert body["client_kind"] == "mobile"
    assert body["client_label"] == "phone"
    assert body["unrestricted"] is True
    assert body["idle_timeout_s"] > 0
    assert body["expires_at"] > body["created_at"]


def test_session_exchange_with_a_wrong_credential_is_a_coarse_401(virgin) -> None:
    client, _ = virgin
    client.post("/v1/identity/bootstrap")
    response = client.post(
        "/v1/identity/sessions",
        json={"owner_credential": "pagentos_ok_" + "z" * 43, "client_kind": "cli"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_session_exchange_before_bootstrap_is_the_same_401(virgin) -> None:
    """Fail closed, and indistinguishable from a wrong credential."""
    client, _ = virgin
    response = client.post(
        "/v1/identity/sessions",
        json={"owner_credential": "pagentos_ok_" + "z" * 43, "client_kind": "cli"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_session_exchange_is_throttled_after_repeated_failures(virgin) -> None:
    client, _ = virgin
    client.post("/v1/identity/bootstrap")
    body = {"owner_credential": "pagentos_ok_" + "z" * 43, "client_kind": "cli"}
    for _ in range(10):
        assert client.post("/v1/identity/sessions", json=body).status_code == 401
    throttled = client.post("/v1/identity/sessions", json=body)
    assert throttled.status_code == 429
    assert int(throttled.headers["retry-after"]) > 0


@pytest.mark.parametrize(
    "payload",
    [
        {"client_kind": "cli"},  # missing credential
        {"owner_credential": "x" * 8, "client_kind": "server"},  # unknown kind
        {"owner_credential": "x" * 8, "client_kind": "cli", "label": "l" * 129},
        {"owner_credential": "x" * 8, "client_kind": "cli", "ttl_s": 5},
        {"owner_credential": "x" * 300, "client_kind": "cli"},
        {"owner_credential": "x" * 8, "client_kind": "cli", "scopes": ["s"] * 17},
        {"owner_credential": "x" * 8, "client_kind": "cli", "device_id": "not-a-uuid"},
        {"owner_credential": "x" * 8, "client_kind": "cli", "extra": 1},
    ],
)
def test_session_request_inputs_are_bounded(virgin, payload: dict) -> None:
    client, _ = virgin
    client.post("/v1/identity/bootstrap")
    assert client.post("/v1/identity/sessions", json=payload).status_code == 422


def test_current_session_reports_who_is_connected(owned) -> None:
    client, _, _, _ = owned
    response = client.get("/v1/identity/sessions/current")
    assert response.status_code == 200
    body = response.json()
    assert body["client_kind"] == "mobile"
    assert "token" not in body


def test_refresh_rotates_the_token_and_kills_the_old_one(owned) -> None:
    client, _, _, old_token = owned
    response = client.post("/v1/identity/sessions/refresh")
    assert response.status_code == 200
    new_token = response.json()["token"]
    assert new_token != old_token
    assert response.json()["session_id"]
    assert (
        client.get("/v1/identity/sessions/current", headers=bearer(old_token)).status_code == 401
    )
    assert (
        client.get("/v1/identity/sessions/current", headers=bearer(new_token)).status_code == 200
    )


def test_list_sessions_marks_the_current_one(owned) -> None:
    client, runtime, credential, _ = owned
    client.post(
        "/v1/identity/sessions",
        json={"owner_credential": credential, "client_kind": "desktop"},
    )
    body = client.get("/v1/identity/sessions").json()
    assert len(body["sessions"]) == 2
    assert sum(1 for s in body["sessions"] if s["current"]) == 1
    assert all("token" not in s for s in body["sessions"])


def test_sign_out_revokes_only_this_session(owned) -> None:
    client, _, credential, token = owned
    other = client.post(
        "/v1/identity/sessions",
        json={"owner_credential": credential, "client_kind": "desktop"},
    ).json()["token"]
    assert client.delete("/v1/identity/sessions/current").json()["revoked"] is True
    assert client.get("/v1/identity/sessions/current", headers=bearer(token)).status_code == 401
    assert client.get("/v1/identity/sessions/current", headers=bearer(other)).status_code == 200


def test_owner_can_revoke_another_session_by_id(owned) -> None:
    client, _, credential, _ = owned
    other = client.post(
        "/v1/identity/sessions",
        json={"owner_credential": credential, "client_kind": "desktop", "label": "lost laptop"},
    ).json()
    assert client.post(f"/v1/identity/sessions/{other['session_id']}/revoke").status_code == 200
    assert (
        client.get("/v1/identity/sessions/current", headers=bearer(other["token"])).status_code
        == 401
    )
    # Revoking it again (or an unknown id) is a 404, not a silent success.
    assert client.post(f"/v1/identity/sessions/{other['session_id']}/revoke").status_code == 404
    assert client.post(f"/v1/identity/sessions/{uuid.uuid4()}/revoke").status_code == 404


# ---------------------------------------------------------------------- panic


def test_panic_revokes_everything_including_the_caller(owned) -> None:
    client, _, credential, _ = owned
    client.post(
        "/v1/identity/sessions",
        json={"owner_credential": credential, "client_kind": "desktop"},
    )
    response = client.post("/v1/identity/panic")
    assert response.status_code == 200
    assert response.json()["revoked"] == 2
    assert response.json()["own_session_revoked"] is True
    assert client.get("/v1/identity/sessions/current").status_code == 401
    # The owner credential survives: the owner can sign back in.
    again = client.post(
        "/v1/identity/sessions",
        json={"owner_credential": credential, "client_kind": "mobile"},
    )
    assert again.status_code == 201


# ---------------------------------------------------------------------- audit


def test_events_endpoint_is_bounded_and_token_free(owned) -> None:
    client, _, credential, token = owned
    client.get("/v1/identity/sessions/current", headers=bearer("pagentos_st_" + "z" * 43))
    body = client.get("/v1/identity/events", params={"limit": 100}).json()
    assert body["events"]
    blob = repr(body)
    assert token not in blob
    assert credential not in blob
    assert client.get("/v1/identity/events", params={"limit": 0}).status_code == 422
    assert client.get("/v1/identity/events", params={"limit": 10_000}).status_code == 422
    assert client.get("/v1/identity/events", params={"action": "bogus"}).status_code == 422
    filtered = client.get("/v1/identity/events", params={"action": "rejected"}).json()
    assert all(e["action"] == "rejected" for e in filtered["events"])


# ------------------------------------------------------- protected by itself


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/v1/identity/sessions"),
        ("get", "/v1/identity/sessions/current"),
        ("post", "/v1/identity/sessions/refresh"),
        ("delete", "/v1/identity/sessions/current"),
        ("post", "/v1/identity/panic"),
        ("get", "/v1/identity/events"),
    ],
)
def test_identity_management_endpoints_require_a_session(virgin, method, path) -> None:
    client, _ = virgin
    client.post("/v1/identity/bootstrap")
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}
    assert response.headers["www-authenticate"] == "Bearer"


# --------------------------------- M9 security review #5: a damaged root file


class _CorruptRoot(InMemoryCredentialRoot):
    """A root file that is present but unreadable — the state a truncated or
    hand-edited `owner_credential.json` leaves behind."""

    def exists(self) -> bool:
        return True

    def load(self):
        raise ValueError("unsupported identity root version: None")


def test_a_corrupt_root_refuses_cleanly_instead_of_crashing(virgin) -> None:
    client, runtime = virgin
    runtime.use_root(_CorruptRoot())

    # Session exchange: the same coarse 401 as any other refusal, not a 500.
    exchange = client.post(
        "/v1/identity/sessions",
        json={"owner_credential": "pagentos_ok_" + "a" * 43, "client_kind": "cli"},
    )
    assert exchange.status_code == 401
    assert exchange.json() == {"detail": "unauthorized"}

    reasons = [e["reason"] for e in runtime.service.list_events(action="rejected")]
    assert "identity_root_unreadable" in reasons


def test_a_corrupt_root_is_never_overwritten_by_bootstrap(virgin) -> None:
    """The security half of the same finding: "unreadable" must not read as
    "absent", or damaging the file would be a way to seize ownership."""
    client, runtime = virgin
    root = _CorruptRoot()
    runtime.use_root(root)

    response = client.post("/v1/identity/bootstrap")
    assert response.status_code == 409
    assert "recovery" in response.json()["detail"]
    # Nothing was minted over the damaged root.
    assert root._record is None
