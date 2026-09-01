"""Unit tests: the /v1/evolution REST surface against an injected SQLite runtime
(mirrors the selfhealing routes test discipline; everything offline)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Artifact, Task, TaskRun
from app.config import Settings
from app.evolution.models import Capability, CapabilityGap, SkillVersion
from app.evolution.runtime import EvolutionRuntime
from app.main import create_app
from tests.identity_support import authenticate

TABLES = [
    Capability.__table__,
    SkillVersion.__table__,
    CapabilityGap.__table__,
    Task.__table__,
    Artifact.__table__,
    TaskRun.__table__,
]

GAP_BODY = {
    "requested_capability": "text.slugify",
    "request_text": "Bir baslik verildiginde url slug uret.",
    "required_inputs": ["text"],
    "required_outputs": ["slug"],
    "spec": {
        "capability_id": "text.slugify",
        "operation": "slugify",
        "version": "0.1.0",
        "summary": "turn a title into a url slug",
    },
}


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PAGENTOS_EVOLUTION_SKILLS_ROOT", str(tmp_path / "skills"))
    monkeypatch.setenv("PAGENTOS_EVOLUTION_WORK_ROOT", str(tmp_path / "work"))
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.state.evolution = EvolutionRuntime(settings, engine=engine)
    test_client = TestClient(app)
    # M9: every /v1/evolution endpoint requires an owner session.
    authenticate(app, test_client, settings=settings)
    yield test_client
    test_client.close()


# ------------------------------------------------------------- capabilities


def test_registry_starts_empty(client: TestClient) -> None:
    response = client.get("/v1/evolution/capabilities")
    assert response.status_code == 200
    assert response.json() == {"capabilities": []}


def test_unknown_capability_is_404(client: TestClient) -> None:
    response = client.get("/v1/evolution/capabilities/text.slugify")
    assert response.status_code == 404
    assert response.json()["detail"]["error_class"] == "not_found"


# --------------------------------------------------------------------- gaps


def test_detect_records_a_gap_with_its_decision_trail(client: TestClient) -> None:
    response = client.post("/v1/evolution/gaps", json=GAP_BODY)
    assert response.status_code == 201
    body = response.json()
    assert body["resolution"] == "generation"
    assert body["status"] == "open"
    steps = [entry["step"] for entry in body["decision_trail"]]
    assert steps[:3] == ["request_received", "existing_capability", "composition"]
    composition = body["decision_trail"][2]["evidence"]
    assert composition["attempted"] is True and composition["satisfied"] is False

    listed = client.get("/v1/evolution/gaps", params={"status": "open"}).json()["gaps"]
    assert [gap["id"] for gap in listed] == [body["id"]]


@pytest.mark.parametrize(
    "override",
    [
        {"requested_capability": "Text.Slugify"},
        {"requested_capability": "x" * 200},
        {"request_text": ""},
        {"request_text": "x" * 5000},
        {"required_inputs": ["../etc"]},
        {"required_outputs": ["a" * 100]},
        {"spec": {"capability_id": "text.slugify", "operation": "arbitrary_python"}},
        {"unexpected_field": 1},
    ],
)
def test_bounded_and_hostile_gap_bodies_are_refused(client: TestClient, override) -> None:
    response = client.post("/v1/evolution/gaps", json={**GAP_BODY, **override})
    assert response.status_code == 422


def test_resolve_runs_the_pipeline_and_registers(client: TestClient) -> None:
    gap = client.post("/v1/evolution/gaps", json=GAP_BODY).json()
    response = client.post(f"/v1/evolution/gaps/{gap['id']}/resolve")
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "registered"
    names = [stage["name"] for stage in result["stages"]]
    assert names[-2:] == ["register_capability", "resume_task"]
    assert names.index("shadow") < names.index("canary") < names.index("register_capability")

    capability = client.get("/v1/evolution/capabilities/text.slugify").json()
    assert capability["status"] == "production"
    assert capability["dispatchable"] is True
    assert capability["manifest"]["entrypoint"] == "run"

    versions = client.get("/v1/evolution/skill-versions").json()["skill_versions"]
    assert [v["status"] for v in versions] == ["registered"]
    assert client.get("/v1/evolution/gaps").json()["gaps"][0]["status"] == "resolved"


def test_resolve_of_an_unknown_gap_is_404(client: TestClient) -> None:
    response = client.post(f"/v1/evolution/gaps/{uuid.uuid4()}/resolve")
    assert response.status_code == 404


def test_resolve_of_a_core_change_gap_is_refused(client: TestClient) -> None:
    gap = client.post(
        "/v1/evolution/gaps",
        json={
            **GAP_BODY,
            "requested_capability": "recovery.patch_supervisor",
            "request_text": "Supervisor icin yeni bir surum uret.",
            "target_component": "recovery-supervisor",
            "spec": None,
        },
    ).json()
    assert gap["resolution"] == "product_change_required"
    assert gap["status"] == "abandoned"

    result = client.post(f"/v1/evolution/gaps/{gap['id']}/resolve").json()
    assert result["status"] == "refused"
    assert result["stages"][-1]["detail"]["error_class"] == "product_change_required"
    assert client.get("/v1/evolution/skill-versions").json()["skill_versions"] == []


def test_second_request_for_a_registered_capability_needs_no_generation(
    client: TestClient,
) -> None:
    gap = client.post("/v1/evolution/gaps", json=GAP_BODY).json()
    client.post(f"/v1/evolution/gaps/{gap['id']}/resolve")

    again = client.post("/v1/evolution/gaps", json=GAP_BODY).json()
    assert again["resolution"] == "existing_capability"
    assert again["status"] == "resolved"
    versions = client.get("/v1/evolution/skill-versions").json()["skill_versions"]
    assert len(versions) == 1  # no second candidate was ever built


def test_health_reports_the_evolution_subsystem(client: TestClient) -> None:
    body = client.get("/v1/system/health").json()
    assert "evolution" in body["checks"]
    assert body["checks"]["evolution"]["skill_generator"] == "deterministic"


def test_audit_endpoint_answers_all_nine_questions(client: TestClient) -> None:
    gap = client.post("/v1/evolution/gaps", json=GAP_BODY).json()
    assert client.post(f"/v1/evolution/gaps/{gap['id']}/resolve").json()["status"] == (
        "registered"
    )

    response = client.get(f"/v1/evolution/gaps/{gap['id']}/audit")
    assert response.status_code == 200
    audit = response.json()
    assert audit["questions"] == [
        "why_needed",
        "what_triggered",
        "what_changed",
        "code_and_dependencies",
        "permissions_granted",
        "tests_that_ran",
        "who_reviewed",
        "why_promoted",
        "rollback_target",
    ]
    for question in audit["questions"]:
        assert audit["answers"][question], question
    assert audit["answers"]["who_reviewed"]["reviewer"] == "independent-skill-reviewer"
    assert audit["answers"]["permissions_granted"]["deny_by_default"] is True


def test_audit_of_an_unknown_gap_is_404(client: TestClient) -> None:
    assert client.get(f"/v1/evolution/gaps/{uuid.uuid4()}/audit").status_code == 404


def test_an_over_deep_request_is_refused_with_409(client: TestClient) -> None:
    response = client.post("/v1/evolution/gaps", json={**GAP_BODY, "depth": 5})
    assert response.status_code == 409
    assert response.json()["detail"]["error_class"] == "recursion_limit_exceeded"


def test_health_reports_the_component_catalog_and_budget(client: TestClient) -> None:
    check = client.get("/v1/system/health").json()["checks"]["evolution"]
    assert check["component_catalog"] >= 1
    assert check["resource_budget"]["timeout_s"] > 0
    assert check["resource_budget"]["max_depth"] >= 0
    assert check["sandbox_isolated_from_core"] is True
