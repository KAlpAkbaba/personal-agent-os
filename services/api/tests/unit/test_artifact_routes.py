"""Unit tests: the M22 factory REST surface (docs/M22_ARTIFACT_FACTORY_SPEC.md §4,
ADR-0085), against the real ``create_app`` with an injected SQLite engine and an
in-memory object store -- everything offline, the same pattern
tests/unit/test_security_routes.py already uses for its own object-store-backed
router.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import (
    Artifact,
    ArtifactRender,
    ArtifactVersion,
    ResearchSource,
    Task,
    TaskRun,
)
from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.main import create_app
from app.object_store import InMemoryObjectStore
from tests.identity_support import authenticate

ARTIFACT_TABLES = [
    Task.__table__,
    TaskRun.__table__,
    Artifact.__table__,
    ArtifactVersion.__table__,
    ArtifactRender.__table__,
    ResearchSource.__table__,
]


@pytest.fixture()
def client() -> TestClient:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ARTIFACT_TABLES:
        table.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)

    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifacts._store = InMemoryObjectStore()
    app.state.artifacts = artifacts

    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    yield test_client
    test_client.close()


def _spec_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


BUDGET = "tests/fixtures/artifacts/specs/butce-tablosu.json"
DOCUMENT = "tests/fixtures/artifacts/specs/toplanti-notlari.json"
PRESENTATION = "tests/fixtures/artifacts/specs/q3-sunum.json"


# ------------------------------------------------------------------ POST factory


def test_create_factory_artifact_returns_201_with_every_render(client: TestClient) -> None:
    resp = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)})
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "spreadsheet"
    assert body["title"] == "Bütçe 2026"
    assert body["all_valid"] is True
    assert body["created"] is True
    formats = {r["format"] for r in body["renders"]}
    assert formats == {"xlsx", "csv"}
    for r in body["renders"]:
        assert r["state"] == "valid"
        assert r["failing_refs"] == []
        assert r["download_url"].startswith("http")
        expected_suffix = f"/v1/artifacts/{body['artifact_id']}/renders/{r['format']}"
        assert r["download_url"].endswith(expected_suffix)


def test_create_factory_artifact_is_idempotent(client: TestClient) -> None:
    first = client.post("/v1/artifacts/factory", json={"spec": _spec_json(DOCUMENT)}).json()
    second = client.post("/v1/artifacts/factory", json={"spec": _spec_json(DOCUMENT)}).json()
    assert second["artifact_id"] == first["artifact_id"]
    assert second["created"] is False


def test_create_factory_artifact_rejects_an_invalid_spec(client: TestClient) -> None:
    bad = {"kind": "spreadsheet", "title": "T"}  # missing required "sheets"
    resp = client.post("/v1/artifacts/factory", json={"spec": bad})
    assert resp.status_code == 422


def test_create_factory_artifact_rejects_extra_request_fields(client: TestClient) -> None:
    resp = client.post(
        "/v1/artifacts/factory", json={"spec": _spec_json(BUDGET), "unexpected": 1}
    )
    assert resp.status_code == 422


def test_create_factory_artifact_requires_owner_session() -> None:
    from app.identity.root import InMemoryCredentialRoot
    from app.identity.runtime import IdentityRuntime
    from tests.identity_support import make_identity_engine

    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.state.identity = IdentityRuntime(
        settings, engine=make_identity_engine(), root=InMemoryCredentialRoot()
    )
    app.state.identity.service.bootstrap()
    unauthenticated = TestClient(app)
    resp = unauthenticated.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)})
    assert resp.status_code == 401


# ----------------------------------------------------------------- GET renders


def test_get_render_downloads_the_actual_bytes(client: TestClient) -> None:
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    artifact_id = created["artifact_id"]
    resp = client.get(f"/v1/artifacts/{artifact_id}/renders/xlsx")
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"
    assert resp.headers["X-Content-Hash"]


def test_get_render_refuses_a_format_the_kind_cannot_produce(client: TestClient) -> None:
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    artifact_id = created["artifact_id"]
    resp = client.get(f"/v1/artifacts/{artifact_id}/renders/pptx")
    assert resp.status_code == 422


def test_get_render_404s_for_unknown_artifact(client: TestClient) -> None:
    import uuid

    resp = client.get(f"/v1/artifacts/{uuid.uuid4()}/renders/xlsx")
    assert resp.status_code == 404


def test_create_render_route_also_works_for_factory_formats(client: TestClient) -> None:
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(PRESENTATION)}).json()
    artifact_id = created["artifact_id"]
    resp = client.post(f"/v1/artifacts/{artifact_id}/renders", json={"format": "pptx"})
    assert resp.status_code == 201
    assert resp.json()["state"] == "valid"


def test_legacy_render_route_still_rejects_truly_unsupported_formats(client: TestClient) -> None:
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    artifact_id = created["artifact_id"]
    resp = client.get(f"/v1/artifacts/{artifact_id}/renders/exe")
    assert resp.status_code == 422


# -------------------------------------------------------------- GET .../validation


def test_get_render_validation_reports_ok(client: TestClient) -> None:
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    artifact_id = created["artifact_id"]
    resp = client.get(f"/v1/artifacts/{artifact_id}/renders/xlsx/validation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "valid"
    assert body["validation"]["ok"] is True
    assert body["validation"]["checks"]


def test_get_render_validation_404s_for_unknown_render(client: TestClient) -> None:
    import uuid

    resp = client.get(f"/v1/artifacts/{uuid.uuid4()}/renders/xlsx/validation")
    assert resp.status_code == 404


# ------------------------------------------------------------- GET /artifacts


def test_list_artifacts_includes_factory_artifacts_with_state(client: TestClient) -> None:
    client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)})
    resp = client.get("/v1/artifacts")
    assert resp.status_code == 200
    items = resp.json()["artifacts"]
    assert len(items) == 1
    assert items[0]["canonical_format"] == "artifact_spec_json"
    assert items[0]["kind"] == "spreadsheet"
    assert all(r["state"] == "valid" for r in items[0]["available_renders"])


def test_get_artifact_with_body_returns_the_canonical_spec_json(client: TestClient) -> None:
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    resp = client.get(f"/v1/artifacts/{created['artifact_id']}?include=body")
    assert resp.status_code == 200
    body = resp.json()
    canonical = json.loads(body["canonical_body"])
    assert canonical["title"] == "Bütçe 2026"
    assert canonical["kind"] == "spreadsheet"


# ------------------------------------------------------------------- POST .../open


def _client_with_device(device):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ARTIFACT_TABLES:
        table.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifacts._store = InMemoryObjectStore()
    app.state.artifacts = artifacts
    app.state.device_action = device
    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    return test_client


def test_open_artifact_fetches_and_opens_through_the_device() -> None:
    from tests.alarms_support import FakeDeviceAction
    from tests.artifacts_support import file_fetch_ok

    device = FakeDeviceAction(results={"file.fetch": file_fetch_ok})
    client = _client_with_device(device)
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    artifact_id = created["artifact_id"]

    resp = client.post(f"/v1/artifacts/{artifact_id}/open")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "opened"
    assert body["error_class"] is None
    assert body["window_title"]
    assert device.capabilities_called() == ["file.fetch"]
    payload = device.payload_for("file.fetch")
    assert payload["open"] is True
    assert payload["sha256"] in {r["content_hash"] for r in created["renders"]}


def test_open_artifact_with_explicit_format() -> None:
    from tests.alarms_support import FakeDeviceAction
    from tests.artifacts_support import file_fetch_ok

    device = FakeDeviceAction(results={"file.fetch": file_fetch_ok})
    client = _client_with_device(device)
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    artifact_id = created["artifact_id"]

    resp = client.post(f"/v1/artifacts/{artifact_id}/open", json={"format": "csv"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["format"] == "csv"


def test_open_artifact_404s_for_unknown_artifact() -> None:
    import uuid

    from tests.alarms_support import FakeDeviceAction

    client = _client_with_device(FakeDeviceAction())
    resp = client.post(f"/v1/artifacts/{uuid.uuid4()}/open")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "not_found"


def test_open_artifact_with_no_device_action_is_capability_missing() -> None:
    client = _client_with_device(None)
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    resp = client.post(f"/v1/artifacts/{created['artifact_id']}/open")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "capability_missing"


def test_open_artifact_refuses_an_unproducable_format() -> None:
    from tests.alarms_support import FakeDeviceAction

    client = _client_with_device(FakeDeviceAction())
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(PRESENTATION)}).json()
    resp = client.post(f"/v1/artifacts/{created['artifact_id']}/open", json={"format": "xlsx"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "not_found"


def test_open_artifact_requires_owner_session() -> None:
    import uuid

    from app.identity.root import InMemoryCredentialRoot
    from app.identity.runtime import IdentityRuntime
    from tests.identity_support import make_identity_engine

    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.state.identity = IdentityRuntime(
        settings, engine=make_identity_engine(), root=InMemoryCredentialRoot()
    )
    app.state.identity.service.bootstrap()
    unauthenticated = TestClient(app)
    resp = unauthenticated.post(f"/v1/artifacts/{uuid.uuid4()}/open")
    assert resp.status_code == 401
