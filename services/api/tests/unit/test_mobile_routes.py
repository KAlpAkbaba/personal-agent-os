"""M9 unit tests: the /v1/mobile HTTP surface.

Offline: identity and `push_registrations` share one SQLite engine, so
registration, the live-target join and the notification inbox are all exercised
over real HTTP with a real owner session. Anything that needs PostgreSQL, the
object store or a rendered artifact (share/export, the artifact-ready trigger's
READY check) lives in the integration suite; what is asserted here is the part
of those endpoints that refuses *before* it reaches a database — which is also
the part a caller can reach without being the owner.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.main import create_app
from app.mobile.models import PushRegistration
from app.mobile.providers import FakePushProvider, build_registry
from app.mobile.runtime import MobileRuntime
from tests.identity_support import IDENTITY_TABLES, bearer

PUSH_TOKEN = "reference-client-push-token"


@pytest.fixture()
def wired():
    """App with identity + mobile on one in-memory engine, and a live session."""
    settings = Settings(_env_file=None)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in IDENTITY_TABLES:
        table.create(engine)
    PushRegistration.__table__.create(engine)

    app = create_app(settings)
    identity = IdentityRuntime(settings, engine=engine, root=InMemoryCredentialRoot())
    identity.service.bootstrap()
    app.state.identity = identity
    fake = FakePushProvider()
    mobile = MobileRuntime(settings, engine=engine, providers=build_registry(fake=fake))
    app.state.mobile = mobile

    issued = identity.service.issue_session(client_kind="mobile", label="unit phone")
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issued.token}"
    try:
        yield client, identity, mobile, fake, issued
    finally:
        client.close()


# ------------------------------------------------------------------- refusal


MOBILE_ENDPOINTS = [
    ("get", "/v1/mobile/push/providers"),
    ("post", "/v1/mobile/push/registrations"),
    ("get", "/v1/mobile/push/registrations"),
    ("delete", f"/v1/mobile/push/registrations/{uuid.uuid4()}"),
    ("post", "/v1/mobile/notifications/artifact-ready"),
    ("get", "/v1/mobile/notifications"),
    ("get", f"/v1/mobile/share/{uuid.uuid4()}"),
    ("get", f"/v1/mobile/share/{uuid.uuid4()}/pdf"),
]


@pytest.mark.parametrize(("method", "path"), MOBILE_ENDPOINTS)
def test_every_mobile_endpoint_refuses_an_unauthenticated_call(wired, method, path) -> None:
    client, *_ = wired
    kwargs = {} if method in {"get", "delete"} else {"json": {}}
    response = getattr(client, method)(path, headers={"Authorization": ""}, **kwargs)
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


@pytest.mark.parametrize(("method", "path"), MOBILE_ENDPOINTS)
def test_every_mobile_endpoint_refuses_a_revoked_session(wired, method, path) -> None:
    client, identity, _, _, issued = wired
    identity.service.revoke_session(issued.context.session_id)
    kwargs = {} if method in {"get", "delete"} else {"json": {}}
    assert getattr(client, method)(path, **kwargs).status_code == 401


# ----------------------------------------------------------------- providers


def test_providers_endpoint_declares_capabilities_and_activation(wired) -> None:
    client, *_ = wired
    payload = client.get("/v1/mobile/push/providers").json()
    by_name = {p["name"]: p for p in payload["providers"]}
    assert set(by_name) == {"apns", "fake", "fcm", "webpush"}
    assert by_name["fake"]["activated"] is True
    assert by_name["apns"]["activated"] is False
    assert "ios" in by_name["apns"]["platforms"]


# -------------------------------------------------------------- registration


def test_register_binds_the_push_token_to_the_current_session(wired) -> None:
    client, _, _, fake, issued = wired
    created = client.post(
        "/v1/mobile/push/registrations",
        json={"provider": "fake", "token": PUSH_TOKEN, "platform": "ios"},
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["session_id"] == str(issued.context.session_id)
    assert payload["status"] == "active"
    assert payload["locale"] == "tr-TR"
    # The response never echoes the token back.
    assert PUSH_TOKEN not in created.text
    assert fake.has_live_token(payload["registration_id"])


def test_register_rejects_a_short_token_and_an_unknown_provider(wired) -> None:
    client, *_ = wired
    assert (
        client.post(
            "/v1/mobile/push/registrations", json={"provider": "fake", "token": "x"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/mobile/push/registrations", json={"provider": "smoke", "token": PUSH_TOKEN}
        ).status_code
        == 422
    )


def test_register_rejects_a_platform_the_transport_cannot_reach(wired) -> None:
    client, *_ = wired
    response = client.post(
        "/v1/mobile/push/registrations",
        json={"provider": "apns", "token": PUSH_TOKEN, "platform": "android"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error_class"] == "validation_error"


def test_register_forbids_unknown_fields(wired) -> None:
    client, *_ = wired
    response = client.post(
        "/v1/mobile/push/registrations",
        json={"provider": "fake", "token": PUSH_TOKEN, "silent": True},
    )
    assert response.status_code == 422


def test_list_marks_the_current_session(wired) -> None:
    client, identity, _, _, issued = wired
    client.post("/v1/mobile/push/registrations", json={"provider": "fake", "token": PUSH_TOKEN})
    other = identity.service.issue_session(client_kind="mobile", label="tablet")
    client.post(
        "/v1/mobile/push/registrations",
        json={"provider": "fake", "token": "tablet-token-value"},
        headers=bearer(other.token),
    )
    rows = client.get("/v1/mobile/push/registrations").json()["registrations"]
    assert len(rows) == 2
    assert sum(1 for r in rows if r["current"]) == 1
    mine = client.get("/v1/mobile/push/registrations", params={"mine_only": True}).json()
    assert len(mine["registrations"]) == 1
    assert mine["registrations"][0]["session_id"] == str(issued.context.session_id)


def test_unregister_is_scoped_and_404s_twice(wired) -> None:
    client, *_ = wired
    registration_id = client.post(
        "/v1/mobile/push/registrations", json={"provider": "fake", "token": PUSH_TOKEN}
    ).json()["registration_id"]
    assert client.delete(f"/v1/mobile/push/registrations/{registration_id}").status_code == 200
    assert client.delete(f"/v1/mobile/push/registrations/{registration_id}").status_code == 404
    assert client.delete(f"/v1/mobile/push/registrations/{uuid.uuid4()}").status_code == 404


# --------------------------------------------------------------- notifications


def test_the_inbox_starts_empty_and_shows_what_was_delivered(wired) -> None:
    client, _, mobile, _, _ = wired
    assert client.get("/v1/mobile/notifications").json()["notifications"] == []

    client.post("/v1/mobile/push/registrations", json={"provider": "fake", "token": PUSH_TOKEN})
    artifact_id = uuid.uuid4()
    mobile.service.notify_artifact_ready(artifact_id=artifact_id, title="Rapor")

    payload = client.get("/v1/mobile/notifications").json()
    assert payload["durable"] is False
    assert payload["source"] == "provider_delivery_log"
    [item] = payload["notifications"]
    assert item["data"]["kind"] == "artifact_ready"
    assert item["data"]["artifact_id"] == str(artifact_id)
    assert item["title"] == "Araştırma tamamlandı"


def test_a_revoked_session_stops_being_notified(wired) -> None:
    client, identity, mobile, _, issued = wired
    client.post("/v1/mobile/push/registrations", json={"provider": "fake", "token": PUSH_TOKEN})
    identity.service.revoke_session(issued.context.session_id, reason="owner_revoked")
    result = mobile.service.notify_artifact_ready(artifact_id=uuid.uuid4(), title="Rapor")
    assert result.delivered == 0


def test_artifact_ready_requires_exactly_one_identifier(wired) -> None:
    client, *_ = wired
    for body in ({}, {"artifact_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4())}):
        response = client.post("/v1/mobile/notifications/artifact-ready", json=body)
        assert response.status_code == 422, body


# ------------------------------------------------------------- share bounds


def test_share_rejects_an_unsupported_format_before_touching_storage(wired) -> None:
    client, *_ = wired
    response = client.get(f"/v1/mobile/share/{uuid.uuid4()}/exe")
    assert response.status_code == 422
    assert "unsupported format" in response.json()["detail"]


def test_share_index_refuses_a_malformed_artifact_id(wired) -> None:
    client, *_ = wired
    assert client.get("/v1/mobile/share/not-a-uuid").status_code == 422


# --------------------------------------------------------------- filenames


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Araştırma Raporu: yapay zekâ", "arastirma-raporu-yapay-zeka"),
        ("   ", "fallback"),
        ("///", "fallback"),
        ("İzmir Şehir Ağı", "izmir-sehir-agi"),
    ],
)
def test_ascii_slug_survives_turkish(title: str, expected: str) -> None:
    from app.mobile.routes import _ascii_slug

    assert _ascii_slug(title, fallback="fallback") == expected


def test_content_disposition_carries_both_filename_forms() -> None:
    from app.mobile.routes import _content_disposition

    header = _content_disposition("rapor-v1.pdf", "Araştırma Raporu v1.pdf")
    assert 'filename="rapor-v1.pdf"' in header
    assert "filename*=UTF-8''" in header
    # The UTF-8 form is percent-encoded, so the header stays ASCII-safe.
    assert header.isascii()
