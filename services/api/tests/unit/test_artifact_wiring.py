"""M22 end to end, through the REAL application object (docs/DECISIONS.md ADR-0078's
own discipline: a component built, tested and never wired is this repository's
recurring defect class -- named again here for the Artifact Factory).

Every other M22 suite proves ONE layer against a fake: test_artifact_spec.py proves
the model, test_artifact_renderers.py the renderers in isolation, test_artifact_
validation.py validate() called directly, test_artifact_factory.py factory.create()
against a bare SQLite+InMemoryObjectStore harness, test_artifact_routes.py the route
contracts. None of them drive a request through create_app the way a real owner
turn would: HTTP in, the router's own dependency-injected runtime, the real service/
render_store/factory/renderers/validation modules, object storage, back out as HTTP.
This module does exactly that, twice (a spreadsheet and a presentation), and adds
the ONE thing no other suite checks: that content_hash advertised by the create
response is the sha256 of the EXACT bytes a second GET later returns -- the promise
ADR-0085 decision 4 makes to the device track's file.fetch (it verifies the render's
sha256 before keeping the file; this proves that hash is honest end to end through
this half of the system).
"""

from __future__ import annotations

import hashlib
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


def test_a_budget_spreadsheet_turn_end_to_end(client: TestClient) -> None:
    """"Bana bir bütçe tablosu yap" -> factory.create over HTTP -> the owner asks
    "is this file correct?" -> downloads it -> the bytes match what was promised."""
    spec = _spec_json("tests/fixtures/artifacts/specs/butce-tablosu.json")

    created = client.post("/v1/artifacts/factory", json={"spec": spec}).json()
    assert created["all_valid"] is True
    artifact_id = created["artifact_id"]

    # "Bu dosya doğru mu?" -- the validation route the voice tool would call.
    for render in created["renders"]:
        validation = client.get(
            f"/v1/artifacts/{artifact_id}/renders/{render['format']}/validation"
        ).json()
        assert validation["state"] == "valid"
        assert validation["validation"]["ok"] is True
        assert validation["validation"]["failing_refs"] == []

    # "Bunu aç" -- the device's file.fetch would GET this URL and check the hash
    # BEFORE keeping the file (ADR-0085 decision 4). Prove the hash is honest.
    xlsx_meta = next(r for r in created["renders"] if r["format"] == "xlsx")
    downloaded = client.get(f"/v1/artifacts/{artifact_id}/renders/xlsx")
    assert downloaded.status_code == 200
    assert hashlib.sha256(downloaded.content).hexdigest() == xlsx_meta["content_hash"]
    assert downloaded.headers["X-Content-Hash"] == xlsx_meta["content_hash"]

    # "Neler ürettin?" -- the list route the voice tool's artifact.list would call.
    listing = client.get("/v1/artifacts").json()["artifacts"]
    assert any(a["artifact_id"] == artifact_id for a in listing)
    mine = next(a for a in listing if a["artifact_id"] == artifact_id)
    assert mine["kind"] == "spreadsheet"
    assert {r["format"] for r in mine["available_renders"]} == {"xlsx", "csv"}
    assert all(r["state"] == "valid" for r in mine["available_renders"])


def test_a_presentation_turn_end_to_end(client: TestClient) -> None:
    """"Üç slaytlık bir sunum hazırla" -- a different kind, through the same wiring."""
    spec = _spec_json("tests/fixtures/artifacts/specs/q3-sunum.json")
    created = client.post("/v1/artifacts/factory", json={"spec": spec}).json()
    assert created["kind"] == "presentation"
    assert created["all_valid"] is True
    assert {r["format"] for r in created["renders"]} == {"pptx"}

    artifact_id = created["artifact_id"]
    downloaded = client.get(f"/v1/artifacts/{artifact_id}/renders/pptx")
    assert downloaded.content[:2] == b"PK"
    assert hashlib.sha256(downloaded.content).hexdigest() == created["renders"][0]["content_hash"]


def test_object_store_loss_is_repaired_and_still_validated_on_next_fetch(
    client: TestClient,
) -> None:
    """A render's bytes go missing from the object store between requests (the
    self-healing path render_store.fetch_render_bytes already proves in isolation
    for M13); the wiring test proves the SAME behaviour survives a real HTTP round
    trip for a factory artifact, re-validating on regeneration."""
    spec = _spec_json("tests/fixtures/artifacts/specs/musteri-listesi.json")
    created = client.post("/v1/artifacts/factory", json={"spec": spec}).json()
    artifact_id = created["artifact_id"]

    store: InMemoryObjectStore = client.app.state.artifacts.store
    # Find and delete the stored object directly (simulating a lost object).
    keys = list(store._objects)  # noqa: SLF001 - test-only introspection
    assert keys
    for key in keys:
        store.delete(key)

    resp = client.get(f"/v1/artifacts/{artifact_id}/renders/csv")
    assert resp.status_code == 200
    assert resp.content  # regenerated, not empty

    validation = client.get(f"/v1/artifacts/{artifact_id}/renders/csv/validation").json()
    assert validation["state"] == "valid"


def test_creating_the_same_spec_twice_over_http_is_one_artifact(client: TestClient) -> None:
    spec = _spec_json("tests/fixtures/artifacts/specs/hosgeldin-sayfasi.json")
    first = client.post("/v1/artifacts/factory", json={"spec": spec}).json()
    second = client.post("/v1/artifacts/factory", json={"spec": spec}).json()
    assert first["artifact_id"] == second["artifact_id"]
    assert first["created"] is True
    assert second["created"] is False
    listing = client.get("/v1/artifacts").json()["artifacts"]
    assert len([a for a in listing if a["artifact_id"] == first["artifact_id"]]) == 1
