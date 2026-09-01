"""M9 integration: owner identity against real PostgreSQL.

What only this suite can prove:

- sessions and their audit are durable rows (migration 0009), so a session
  survives an application restart and a revocation cannot be forgotten;
- the file-backed identity root works end to end through HTTP — bootstrap,
  credential exchange, protected call — which is the "authenticated native
  client connects" acceptance bullet;
- **device revocation invalidates session**: revoking an enrolled device
  through the existing broker endpoint kills every session bound to it, and
  the phone's bearer token stops working on the REST API, not just its
  WebSocket.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.identity.models import (
    EVENT_ISSUED,
    EVENT_REVOKED,
    SESSION_STATUS_REVOKED,
    OwnerSession,
    SessionEvent,
)
from app.identity.root import FileCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.main import create_app
from tests.identity_support import bearer
from tests.integration.broker_agent import AgentKey, rest_enroll
from tests.integration.conftest import owner_client, shared_identity

pytestmark = pytest.mark.integration


@pytest.fixture()
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    with owner_client(settings) as test_client:
        yield test_client


@pytest.fixture()
def identity(settings: Settings) -> IdentityRuntime:
    return shared_identity(settings)


# ------------------------------------------------------- durability of state


def test_session_survives_an_application_restart(settings: Settings) -> None:
    """A phone does not have to sign in again because the API was redeployed."""
    runtime = shared_identity(settings)
    issued = runtime.service.issue_session(client_kind="mobile", label="phone")

    with owner_client(settings) as first:
        first.headers["Authorization"] = f"Bearer {issued.token}"
        assert first.get("/v1/identity/sessions/current").json()["client_label"] == "phone"

    # A completely separate app instance, same database.
    with owner_client(settings) as second:
        second.headers["Authorization"] = f"Bearer {issued.token}"
        body = second.get("/v1/identity/sessions/current")
        assert body.status_code == 200
        assert body.json()["session_id"] == str(issued.context.session_id)


def test_session_and_audit_rows_are_real_and_token_free(
    client: TestClient, identity: IdentityRuntime
) -> None:
    issued = identity.service.issue_session(client_kind="desktop", label="workstation")
    whoami = client.get("/v1/identity/sessions/current", headers=bearer(issued.token))
    assert whoami.status_code == 200

    with identity.session() as db:
        row = db.get(OwnerSession, issued.context.session_id)
        assert row is not None
        assert row.client_kind == "desktop"
        assert row.last_seen_at is not None
        assert row.token_hash != issued.token  # only the hash is stored
        assert len(row.token_hash) == 64

        events = (
            db.execute(
                select(SessionEvent)
                .where(SessionEvent.session_id == issued.context.session_id)
                .order_by(SessionEvent.id)
            )
            .scalars()
            .all()
        )
    assert [e.action for e in events][0] == EVENT_ISSUED
    assert issued.token not in repr([(e.reason, e.detail_json) for e in events])


def test_sweep_expires_stale_rows_in_postgres(identity: IdentityRuntime) -> None:
    issued = identity.service.issue_session(client_kind="cli", label="short", ttl_s=60)
    with identity.session() as db:
        row = db.get(OwnerSession, issued.context.session_id)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=5)
        db.commit()
    assert identity.service.sweep_expired() >= 1
    assert not identity.service.verify(issued.token).ok


# ---------------------------------------------- the real file-backed root


def test_bootstrap_exchange_and_protected_call_over_http(settings, tmp_path) -> None:
    """The M9 bullet: an authenticated native client connects, end to end."""
    app = create_app(settings)
    # Real FileCredentialRoot (throwaway directory), real PostgreSQL sessions —
    # only the location of the identity root is test-specific.
    app.state.identity = IdentityRuntime(
        settings,
        engine=shared_identity(settings).engine,
        root=FileCredentialRoot(tmp_path / "identity"),
    )
    with TestClient(app) as native:
        assert native.get("/v1/identity/sessions/current").status_code == 401

        credential = native.post("/v1/identity/bootstrap").json()["owner_credential"]
        assert native.post("/v1/identity/bootstrap").status_code == 409

        session = native.post(
            "/v1/identity/sessions",
            json={
                "owner_credential": credential,
                "client_kind": "mobile",
                "label": "itest native client",
            },
        )
        assert session.status_code == 201
        token = session.json()["token"]

        whoami = native.get("/v1/identity/sessions/current", headers=bearer(token))
        assert whoami.status_code == 200
        assert whoami.json()["client_kind"] == "mobile"

        # ...and the session actually opens the protected API.
        assert native.get("/v1/artifacts", headers=bearer(token)).status_code == 200

    assert (tmp_path / "identity" / "owner_credential.json").is_file()
    assert credential not in (tmp_path / "identity" / "owner_credential.json").read_text(
        encoding="utf-8"
    )


# ------------------------------------- device revocation invalidates session


def test_device_revocation_invalidates_the_session_end_to_end(
    client: TestClient, identity: IdentityRuntime
) -> None:
    # 1. An enrolled device (the owner's phone), through the real broker path.
    key = AgentKey()
    device_id = uuid.UUID(rest_enroll(client, key, name="itest-identity-phone"))

    # 2. ...holding an owner session bound to that device.
    phone = identity.service.issue_session(
        client_kind="mobile", label="phone", device_id=device_id
    )
    unbound = identity.service.issue_session(client_kind="web", label="browser")

    def phone_get(path: str) -> int:
        return client.get(path, headers=bearer(phone.token)).status_code

    assert phone_get("/v1/identity/sessions/current") == 200
    assert phone_get("/v1/artifacts") == 200

    # 3. The owner revokes the device (lost phone).
    revoke = client.post(f"/v1/devices/{device_id}/revoke")
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "revoked"
    assert revoke.json()["sessions_revoked"] == 1

    # 4. The phone's bearer token is dead everywhere, not just on its socket.
    assert phone_get("/v1/identity/sessions/current") == 401
    assert phone_get("/v1/artifacts") == 401
    dead_write = client.post(
        "/v1/memory/observe", json={"text": "x"}, headers=bearer(phone.token)
    )
    assert dead_write.status_code == 401

    # 5. Sessions not bound to that device are untouched.
    survivor = client.get("/v1/identity/sessions/current", headers=bearer(unbound.token))
    assert survivor.status_code == 200

    # 6. The revocation is durable and explained in the audit.
    with identity.session() as db:
        row = db.get(OwnerSession, phone.context.session_id)
        assert row is not None
        assert row.status == SESSION_STATUS_REVOKED
        assert row.revoked_reason == "device_revoked"
        events = (
            db.execute(
                select(SessionEvent).where(
                    SessionEvent.session_id == phone.context.session_id,
                    SessionEvent.action == EVENT_REVOKED,
                )
            )
            .scalars()
            .all()
        )
    assert [e.reason for e in events] == ["device_revoked"]


def test_a_crash_mid_revocation_never_leaves_live_sessions_behind(
    client: TestClient, identity: IdentityRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M9 security review #4: the two transactions are ordered, not atomic.

    Device revocation and session revocation cannot share a transaction (two
    runtimes, two engines), so the ordering decides which half survives a crash
    between them. Sessions must go first: the recoverable failure is "the
    revoke did not take, retry it", never "the device is revoked but its token
    still works".
    """
    key = AgentKey()
    device_id = rest_enroll(client, key, name="itest-identity-crash-order")
    phone = identity.service.issue_session(
        client_kind="mobile", label="crash-phone", device_id=uuid.UUID(device_id)
    )
    def phone_status() -> int:
        return client.get(
            "/v1/identity/sessions/current", headers=bearer(phone.token)
        ).status_code

    assert phone_status() == 200

    from app.broker import service as broker_service

    def explode(*_args, **_kwargs):
        raise RuntimeError("simulated crash while writing the device row")

    monkeypatch.setattr(broker_service, "revoke_device", explode)
    with pytest.raises(RuntimeError):
        client.post(f"/v1/devices/{device_id}/revoke")

    # The device row is untouched (the owner can and should retry), but the
    # authority it carried is already gone.
    assert phone_status() == 401
    listed = {d["device_id"]: d for d in client.get("/v1/devices").json()["devices"]}
    assert listed[device_id]["status"] != "revoked"

    # Retrying completes it, and revocation stays idempotent.
    monkeypatch.undo()
    retry = client.post(f"/v1/devices/{device_id}/revoke")
    assert retry.status_code == 200
    assert retry.json()["status"] == "revoked"


def test_revoking_a_device_with_no_sessions_is_a_no_op(client: TestClient) -> None:
    key = AgentKey()
    device_id = rest_enroll(client, key, name="itest-identity-sessionless")
    revoke = client.post(f"/v1/devices/{device_id}/revoke")
    assert revoke.status_code == 200
    assert revoke.json()["sessions_revoked"] == 0


def test_panic_revokes_every_session_in_the_database(
    client: TestClient, identity: IdentityRuntime
) -> None:
    live = [identity.service.issue_session(client_kind="cli") for _ in range(3)]
    response = client.post("/v1/identity/panic")
    assert response.status_code == 200
    assert response.json()["revoked"] >= 3
    for issued in live:
        assert not identity.service.verify(issued.token).ok
    assert identity.service.count_active_sessions() == 0
