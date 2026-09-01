"""Unit tests: /v1/memory REST surface against an injected SQLite runtime.

The app is built normally, then app.state.memory is replaced with a
MemoryRuntime bound to an in-memory SQLite engine (StaticPool so the
asyncio.to_thread workers share the connection). Everything stays offline.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.runtime import MemoryRuntime
from tests.identity_support import authenticate

MEMORY_TABLES = [
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
]


@pytest.fixture()
def client() -> TestClient:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in MEMORY_TABLES:
        table.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.state.memory = MemoryRuntime(settings, engine=engine)
    test_client = TestClient(app)
    # M9: every /v1/memory endpoint requires an owner session.
    authenticate(app, test_client, settings=settings)
    yield test_client
    engine.dispose()


def test_observe_ignores_chatty_and_creates_signal(client: TestClient) -> None:
    ignored = client.post("/v1/memory/observe", json={"text": "teşekkürler"})
    assert ignored.status_code == 200
    assert ignored.json()["action"] == "ignored"
    assert ignored.json()["memory_id"] is None

    created = client.post(
        "/v1/memory/observe",
        json={
            "text": "Owner prefers compact dashboards on the phone.",
            "memory_class": "preference",
            "key": "dashboard.density",
            "value": {"value": "compact"},
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["action"] == "created"
    assert body["stage"] == "candidate"
    assert body["memory_id"]


def test_observe_secret_rejected_422_with_typed_error(client: TestClient) -> None:
    response = client.post(
        "/v1/memory/observe",
        json={"text": "remember password=SuperSecret123", "explicit": True},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error_class"] == "secret_rejected"
    assert "SuperSecret123" not in str(detail)
    audit = client.get("/v1/memory/audit").json()
    assert audit["events"][0]["action"] == "refused_secret"


def test_remember_search_inspect_roundtrip(client: TestClient) -> None:
    remembered = client.post(
        "/v1/memory/remember",
        json={
            "text": "Bundan sonra raporlarda metrik birimler kullan.",
            "memory_class": "preference",
            "key": "units.system",
            "value": {"value": "metric"},
        },
    )
    assert remembered.status_code == 201
    memory_id = remembered.json()["memory_id"]
    assert remembered.json()["stage"] == "durable"

    found = client.get(
        "/v1/memory/search",
        params={"q": "metrik birimler rapor", "memory_class": "preference"},
    )
    assert found.status_code == 200
    results = found.json()["results"]
    assert results and results[0]["memory_id"] == memory_id
    assert results[0]["explicit"] is True

    inspected = client.get(f"/v1/memory/{memory_id}")
    assert inspected.status_code == 200
    body = inspected.json()
    assert body["provenance"]["origin"] == "owner_statement"
    assert body["evidence"]
    assert body["versions"]
    assert any(e["action"] == "created" for e in body["audit"])


def test_patch_edit_supersede_pin_endpoints(client: TestClient) -> None:
    memory_id = client.post(
        "/v1/memory/remember",
        json={"text": "I prefer weekly summaries on Monday.", "key": "summary.day"},
    ).json()["memory_id"]

    edited = client.patch(
        f"/v1/memory/{memory_id}",
        json={"text": "I prefer weekly summaries on Friday.", "change_reason": "correction"},
    )
    assert edited.status_code == 200
    assert edited.json()["version"] == 2

    pinned = client.post(f"/v1/memory/{memory_id}/pin")
    assert pinned.status_code == 200
    assert pinned.json()["pinned"] is True

    superseded = client.post(
        f"/v1/memory/{memory_id}/supersede",
        json={"text": "I prefer daily micro-summaries instead.", "reason": "changed habit"},
    )
    assert superseded.status_code == 201
    new_id = superseded.json()["memory_id"]
    assert new_id != memory_id

    old = client.get(f"/v1/memory/{memory_id}").json()
    assert old["status"] == "superseded"
    assert old["superseded_by"] == new_id


def test_delete_forget_endpoint_confirms_removal(client: TestClient) -> None:
    memory_id = client.post(
        "/v1/memory/remember",
        json={"text": "Remember the cabin door code story from July."},
    ).json()["memory_id"]
    deleted = client.delete(f"/v1/memory/{memory_id}")
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["forgotten"] is True
    assert body["memory_id"] == memory_id
    assert client.get(f"/v1/memory/{memory_id}").status_code == 404
    found = client.get("/v1/memory/search", params={"q": "cabin door code"})
    assert all(r["memory_id"] != memory_id for r in found.json()["results"])


def test_entities_and_edges_endpoints(client: TestClient) -> None:
    project = client.post(
        "/v1/memory/entities", json={"kind": "project", "name": "atlas"}
    )
    assert project.status_code == 201
    project_id = project.json()["entity_id"]
    task = client.post(
        "/v1/memory/entities", json={"kind": "task", "name": "voice-benchmark"}
    ).json()["entity_id"]

    edge = client.post(
        "/v1/memory/edges",
        json={"src_id": task, "dst_id": project_id, "relation": "belongs_to"},
    )
    assert edge.status_code == 201

    listed = client.get("/v1/memory/entities", params={"kind": "project"}).json()
    assert listed["count"] == 1

    detailed = client.get(f"/v1/memory/entities/{project_id}").json()
    assert detailed["edges"][0]["relation"] == "belongs_to"

    invalid = client.post("/v1/memory/entities", json={"kind": "planet", "name": "mars"})
    assert invalid.status_code == 422


def test_input_bounds_enforced(client: TestClient) -> None:
    too_long = client.post("/v1/memory/observe", json={"text": "x" * 4001})
    assert too_long.status_code == 422
    bad_class = client.post(
        "/v1/memory/observe", json={"text": "valid text here", "memory_class": "gossip"}
    )
    assert bad_class.status_code == 422
    extra_field = client.post(
        "/v1/memory/observe", json={"text": "valid text here", "hack": True}
    )
    assert extra_field.status_code == 422
    bad_limit = client.get("/v1/memory/search", params={"limit": 500})
    assert bad_limit.status_code == 422
    unknown = client.get(f"/v1/memory/{uuid.uuid4()}")
    assert unknown.status_code == 404
