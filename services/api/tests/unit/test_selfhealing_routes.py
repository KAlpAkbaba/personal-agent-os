"""Unit tests: /v1/selfhealing REST surface against an injected SQLite runtime
(mirrors the memory routes test discipline; everything offline)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.selfhealing.models import Incident, Release
from app.selfhealing.runtime import SelfHealingRuntime


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PAGENTOS_SELFHEALING_WORKSPACE_ROOT", str(tmp_path / "root"))
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (Release.__table__, Incident.__table__):
        table.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.state.selfhealing = SelfHealingRuntime(settings, engine=engine)
    test_client = TestClient(app)
    yield test_client
    test_client.close()


def make_report(error_class: str = "wrong_error_mapping") -> dict:
    return {
        "schema": "pagentos.selfhealing.incident.v1",
        "component": "browser-agent-demo",
        "severity": "critical",
        "fingerprint_material": {
            "component": "browser-agent-demo",
            "error_class": error_class,
            "failing_check": "selftest",
        },
        "workspace": "E:/tmp/ws",
        "active_version": "1.1.0",
        "active_manifest_digest": "ab" * 32,
        "last_known_good": "1.0.0",
        "rolled_back_to": "1.0.0",
        "evidence": {
            "selftest": {
                "status": "fail",
                "error_class": error_class,
                "check": "map_error",
                "input": "net::ERR_NAME_NOT_RESOLVED",
                "expected": "dependency_unavailable",
                "actual": "internal_bug",
            }
        },
        "detected_at": "2026-08-31T00:00:00+00:00",
        "recovered_at": "2026-08-31T00:00:05+00:00",
    }


def test_ingest_creates_then_dedups(client: TestClient) -> None:
    first = client.post("/v1/selfhealing/incidents/ingest", json=make_report())
    assert first.status_code == 201
    body = first.json()
    assert body["created"] is True
    assert body["occurrence_count"] == 1
    assert len(body["fingerprint"]) == 64
    assert body["status"] == "recovered"

    second = client.post("/v1/selfhealing/incidents/ingest", json=make_report())
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["occurrence_count"] == 2
    assert second.json()["incident_id"] == body["incident_id"]

    incidents = client.get("/v1/selfhealing/incidents").json()["incidents"]
    assert len(incidents) == 1
    assert incidents[0]["occurrence_count"] == 2


def test_ingest_rejects_bad_schema_and_shape(client: TestClient) -> None:
    report = make_report()
    report["schema"] = "someone.elses.schema"
    assert client.post("/v1/selfhealing/incidents/ingest", json=report).status_code == 422
    assert (
        client.post("/v1/selfhealing/incidents/ingest", json={"nonsense": 1}).status_code == 422
    )
    report = make_report()
    report["fingerprint_material"].pop("error_class")
    assert client.post("/v1/selfhealing/incidents/ingest", json=report).status_code == 422


def test_releases_listing_includes_introduced_release(client: TestClient) -> None:
    client.post("/v1/selfhealing/incidents/ingest", json=make_report())
    releases = client.get(
        "/v1/selfhealing/releases", params={"component": "browser-agent-demo"}
    ).json()["releases"]
    assert len(releases) == 1
    assert releases[0]["version"] == "1.1.0"
    assert releases[0]["status"] == "rolled_back"


def test_incident_filters(client: TestClient) -> None:
    client.post("/v1/selfhealing/incidents/ingest", json=make_report())
    client.post("/v1/selfhealing/incidents/ingest", json=make_report("self_test_failed"))
    recovered = client.get(
        "/v1/selfhealing/incidents", params={"status": "recovered"}
    ).json()["incidents"]
    assert len(recovered) == 2
    none = client.get("/v1/selfhealing/incidents", params={"status": "fixed"}).json()[
        "incidents"
    ]
    assert none == []


def test_pipeline_run_unknown_incident_404(client: TestClient, tmp_path) -> None:
    root = tmp_path / "root"
    response = client.post(
        "/v1/selfhealing/pipeline/run",
        json={
            "incident_id": str(uuid.uuid4()),
            "staging_workspace": str(root / "staging"),
            "production_workspace": str(root / "prod"),
            "staging_port": 18801,
            "production_port": 18802,
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error_class"] == "not_found"


def test_pipeline_run_rejects_workspace_outside_root(client: TestClient, tmp_path) -> None:
    ingest = client.post("/v1/selfhealing/incidents/ingest", json=make_report())
    incident_id = ingest.json()["incident_id"]
    response = client.post(
        "/v1/selfhealing/pipeline/run",
        json={
            "incident_id": incident_id,
            "staging_workspace": str(tmp_path / "outside-root"),
            "production_workspace": str(tmp_path / "root" / "prod"),
            "staging_port": 18801,
            "production_port": 18802,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error_class"] == "validation_error"
