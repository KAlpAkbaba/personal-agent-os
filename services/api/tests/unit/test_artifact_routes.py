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
from app.artifacts.render_fetch_store import RenderFetchStore
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
    # A fresh store per test client (mirrors test_alarms_routes.py's
    # ``app.state.alarm_audio_store = AudioStore()``): the process-wide default would
    # otherwise leak tokens/entries across tests in this file.
    app.state.artifact_render_fetch_store = RenderFetchStore()
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


# --------------------------------------------------- device render-fetch token route
#
# ADR-0085 addendum 5 (closing the gap addendum 4 decision 5 recorded): the device's
# file.fetch (DEVICE_PROTOCOL.md §6k step 6) carries no owner token, cookie or header of
# its own. ``POST .../open`` now mints a single-use render-fetch token instead of pointing
# the device at the bearer-gated M13 download route, and
# ``GET /v1/artifacts/renders/fetch/{token}`` is the ONLY thing that token can redeem
# against.


def test_open_artifact_url_is_a_device_fetch_token_the_device_can_actually_redeem() -> None:
    """The URL the device is handed lives under ``/v1/artifacts/`` on THIS request's own
    origin (never a configured one — ADR-0069's rule), names the exact sha256/size of the
    render, and a caller with NO Authorization header at all (standing in for the
    companion, which carries none of its own) can redeem it for the exact bytes."""
    import hashlib
    from urllib.parse import urlparse

    from tests.alarms_support import FakeDeviceAction
    from tests.artifacts_support import file_fetch_ok

    device = FakeDeviceAction(results={"file.fetch": file_fetch_ok})
    client = _client_with_device(device)
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    artifact_id = created["artifact_id"]

    resp = client.post(f"/v1/artifacts/{artifact_id}/open", json={"format": "xlsx"})
    assert resp.status_code == 200, resp.text

    payload = device.payload_for("file.fetch")
    url = payload["url"]
    parsed = urlparse(url)
    assert parsed.path.startswith("/v1/artifacts/")
    assert parsed.path.startswith("/v1/artifacts/renders/fetch/")
    assert url.startswith(str(client.base_url).rstrip("/"))
    # Never the bearer-gated M13 download route -- that route cannot authenticate a
    # device with no session.
    assert "/renders/xlsx" not in parsed.path

    xlsx_meta = next(r for r in created["renders"] if r["format"] == "xlsx")
    assert payload["sha256"] == xlsx_meta["content_hash"]
    assert payload["size"] == xlsx_meta["size_bytes"]

    # A bare, unauthenticated client -- no Authorization header, no cookie -- stands in
    # for the device's own GET (DEVICE_PROTOCOL.md §6k step 6).
    device_client = TestClient(client.app)
    fetched = device_client.get(parsed.path)
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == xlsx_meta["mime_type"]
    assert fetched.headers["content-length"] == str(xlsx_meta["size_bytes"])
    assert fetched.headers["cache-control"] == "no-store"
    assert hashlib.sha256(fetched.content).hexdigest() == payload["sha256"]
    assert len(fetched.content) == payload["size"]


def test_render_fetch_token_is_redeemable_exactly_once() -> None:
    from urllib.parse import urlparse

    from tests.alarms_support import FakeDeviceAction
    from tests.artifacts_support import file_fetch_ok

    device = FakeDeviceAction(results={"file.fetch": file_fetch_ok})
    client = _client_with_device(device)
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    client.post(f"/v1/artifacts/{created['artifact_id']}/open", json={"format": "xlsx"})
    path = urlparse(device.payload_for("file.fetch")["url"]).path

    device_client = TestClient(client.app)
    first = device_client.get(path)
    assert first.status_code == 200

    # Spent. A token captured from a log after the fact is already useless.
    second = device_client.get(path)
    assert second.status_code == 404


def test_render_fetch_token_route_404s_for_an_unknown_token() -> None:
    client = _client_with_device(None)
    resp = client.get("/v1/artifacts/renders/fetch/definitely-not-a-token")
    assert resp.status_code == 404


def test_render_fetch_token_route_404s_for_an_expired_token() -> None:
    import uuid
    from datetime import UTC, datetime, timedelta

    client = _client_with_device(None)
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    render = next(r for r in created["renders"] if r["format"] == "xlsx")

    store: RenderFetchStore = client.app.state.artifact_render_fetch_store
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    handle = store.put(
        artifact_id=uuid.UUID(created["artifact_id"]),
        fmt="xlsx",
        content_hash=render["content_hash"],
        now=long_ago,
    )
    resp = client.get(handle.path())
    assert resp.status_code == 404


def test_render_fetch_token_route_404s_for_a_tampered_token() -> None:
    from tests.alarms_support import FakeDeviceAction
    from tests.artifacts_support import file_fetch_ok

    device = FakeDeviceAction(results={"file.fetch": file_fetch_ok})
    client = _client_with_device(device)
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    client.post(f"/v1/artifacts/{created['artifact_id']}/open", json={"format": "xlsx"})
    url = device.payload_for("file.fetch")["url"]
    path = url[url.index("/v1/artifacts/") :]
    tampered = path[:-1] + ("a" if path[-1] != "a" else "b")

    device_client = TestClient(client.app)
    assert device_client.get(tampered).status_code == 404
    # The real token is untouched and still spends normally -- tampering with a copy
    # never invalidates the original.
    assert device_client.get(path).status_code == 200


def test_render_fetch_route_needs_no_owner_session_even_when_one_exists() -> None:
    """The device carries no session; an authenticated caller must not be the only one
    who can redeem the token (mirrors the alarm greeting-audio precedent)."""
    from tests.alarms_support import FakeDeviceAction
    from tests.artifacts_support import file_fetch_ok

    device = FakeDeviceAction(results={"file.fetch": file_fetch_ok})
    client = _client_with_device(device)
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    client.post(f"/v1/artifacts/{created['artifact_id']}/open", json={"format": "xlsx"})
    url = device.payload_for("file.fetch")["url"]
    path = url[url.index("/v1/artifacts/") :]

    # ``client`` already carries a bearer token (tests.identity_support.authenticate);
    # the route must accept the request on its own authority, not because of it.
    assert client.get(path).status_code == 200


def test_render_fetch_route_never_appears_in_the_open_ledger_or_receipt() -> None:
    """ADR-0085 addendum 5: the token must never surface in a receipt, a ledger row, a
    log line or UI metadata -- only inside the URL handed to the device port, which the
    receipt never repeats."""
    from tests.alarms_support import FakeDeviceAction
    from tests.artifacts_support import file_fetch_ok

    device = FakeDeviceAction(results={"file.fetch": file_fetch_ok})
    client = _client_with_device(device)
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(BUDGET)}).json()
    resp = client.post(f"/v1/artifacts/{created['artifact_id']}/open", json={"format": "xlsx"})
    body = resp.json()

    url = device.payload_for("file.fetch")["url"]
    token = url.rsplit("/", 1)[-1]
    assert token not in json.dumps(body)


def test_get_render_route_still_requires_an_owner_session() -> None:
    """The ordinary bearer-gated download route is unchanged by the device token route
    added alongside it -- a caller with no session still gets 401, never the bytes."""
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
    resp = unauthenticated.get(f"/v1/artifacts/{uuid.uuid4()}/renders/xlsx")
    assert resp.status_code == 401
