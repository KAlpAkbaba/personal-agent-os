"""Unit tests: /v1/research REST surface against injected SQLite runtimes.

The Temporal client is monkeypatched (AsyncMock) so these tests stay fully
offline — the 409 no_capable_device path never even reaches it (device
selection happens synchronously in the route, before the workflow starts).
"""

from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
from app.broker import service as broker_service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.config import Settings
from app.main import create_app
from app.research.models import (
    ResearchCandidateRow,
    ResearchEvidenceRow,
    ResearchReportRow,
    ResearchRunRow,
)
from tests.identity_support import authenticate

ALL_TABLES = [
    Device.__table__,
    DeviceSession.__table__,
    DeviceCommand.__table__,
    EnrollmentToken.__table__,
    AuditEvent.__table__,
    Task.__table__,
    TaskRun.__table__,
    Artifact.__table__,
    ArtifactVersion.__table__,
    ArtifactRender.__table__,
    ResearchSource.__table__,
    ResearchRunRow.__table__,
    ResearchCandidateRow.__table__,
    ResearchEvidenceRow.__table__,
    ResearchReportRow.__table__,
]


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ALL_TABLES:
        table.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def client(engine) -> TestClient:
    settings = Settings(_env_file=None)
    app = create_app(settings)

    broker = BrokerRuntime(settings)
    broker._engine = engine
    from sqlalchemy.orm import sessionmaker

    broker._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.broker = broker

    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts

    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    yield test_client


def _enroll_online_device(client: TestClient, *, capabilities=("browser.chrome",)) -> uuid.UUID:
    broker: BrokerRuntime = client.app.state.broker
    with broker.session() as db:
        device = broker_service.enroll_device(
            db,
            name="ev-pc",
            platform="windows",
            public_key_spki_b64=_spki(),
            capabilities=list(capabilities),
            trace_id=None,
        )
    broker.connections[device.id] = DeviceConnection(
        device_id=device.id, session_id=uuid.uuid4(), websocket=object()
    )
    return device.id


def _patched_temporal_client():
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    fake_handle = AsyncMock()
    fake_client.get_workflow_handle = lambda *_a, **_k: fake_handle
    return patch("app.research.routes.Client.connect", AsyncMock(return_value=fake_client))


def test_create_research_without_any_device_returns_409_no_capable_device(
    client: TestClient,
) -> None:
    response = client.post("/v1/research", json={"input": "yapay zeka ajanları"})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error_class"] == "no_capable_device"
    assert detail["detail"]  # Turkish, non-empty


def test_create_research_marks_task_failed_terminal_on_no_capable_device(
    client: TestClient,
) -> None:
    response = client.post("/v1/research", json={"input": "yapay zeka ajanları"})
    task_id = response.json()["detail"]["task_id"]
    detail_response = client.get(f"/v1/research/{task_id}")
    assert detail_response.status_code == 200
    body = detail_response.json()
    assert body["status"] == "FAILED_TERMINAL"
    assert body["error"]["error_class"] == "no_capable_device"


def test_create_research_with_online_device_returns_202_and_device(client: TestClient) -> None:
    _enroll_online_device(client)
    with _patched_temporal_client():
        response = client.post("/v1/research", json={"input": "yapay zeka ajanları"})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "planned"
    assert body["device"]["device_id"]
    assert body["task_id"]
    assert body["workflow_id"] == f"research-browser-{body['task_id']}"


def test_temporal_down_is_a_typed_503_and_the_task_is_closed_not_orphaned(
    client: TestClient,
) -> None:
    """Phase 8: this was an untyped 500, and the task opened for the run stayed CREATED for
    ever - read as work still to come by the task list, the ledger and the announcers."""
    _enroll_online_device(client)
    with patch("app.research.routes.Client.connect", AsyncMock(side_effect=OSError("down"))):
        response = client.post("/v1/research", json={"input": "yapay zeka ajanları"})
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["error_class"] == "dependency_unavailable"
    assert detail["detail"]  # the Turkish sentence
    body = client.get(f"/v1/research/{detail['task_id']}").json()
    assert body["status"] == "FAILED_TERMINAL"
    assert body["error"]["error_class"] == "workflow_start_failed"


def test_create_research_rejects_max_sources_over_ceiling(client: TestClient) -> None:
    response = client.post("/v1/research", json={"input": "konu", "max_sources": 31})
    assert response.status_code == 422


def test_create_research_accepts_interactive_and_forwards_wait_budget(
    client: TestClient,
) -> None:
    _enroll_online_device(client)
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    with patch("app.research.routes.Client.connect", AsyncMock(return_value=fake_client)):
        response = client.post(
            "/v1/research",
            json={"input": "konu", "interactive": True, "interactive_wait_s": 120},
        )
    assert response.status_code == 202
    request_arg = fake_client.start_workflow.call_args.args[1]
    assert request_arg.interactive is True
    assert request_arg.interactive_wait_s == 120


def test_create_research_defaults_interactive_false_and_wait_600(client: TestClient) -> None:
    _enroll_online_device(client)
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    with patch("app.research.routes.Client.connect", AsyncMock(return_value=fake_client)):
        response = client.post("/v1/research", json={"input": "konu"})
    assert response.status_code == 202
    request_arg = fake_client.start_workflow.call_args.args[1]
    assert request_arg.interactive is False
    assert request_arg.interactive_wait_s == 600


def test_create_research_defaults_search_provider_to_duckduckgo(client: TestClient) -> None:
    """PRODUCT DECISION (owner, 2026-09-04): DuckDuckGo is the default
    production search provider — a default research POST must carry
    search_provider="duckduckgo" on the workflow request without the caller
    naming it explicitly."""
    _enroll_online_device(client)
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    with patch("app.research.routes.Client.connect", AsyncMock(return_value=fake_client)):
        response = client.post("/v1/research", json={"input": "konu"})
    assert response.status_code == 202
    request_arg = fake_client.start_workflow.call_args.args[1]
    assert request_arg.search_provider == "duckduckgo"


def test_create_research_accepts_google_search_provider(client: TestClient) -> None:
    """Google stays fully selectable — its CAPTCHA/owner-handoff machinery is
    preserved, just no longer the automatic default."""
    _enroll_online_device(client)
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    with patch("app.research.routes.Client.connect", AsyncMock(return_value=fake_client)):
        response = client.post("/v1/research", json={"input": "konu", "search_provider": "google"})
    assert response.status_code == 202
    request_arg = fake_client.start_workflow.call_args.args[1]
    assert request_arg.search_provider == "google"


def test_create_research_rejects_unknown_search_provider(client: TestClient) -> None:
    response = client.post("/v1/research", json={"input": "konu", "search_provider": "bing"})
    assert response.status_code == 422


def test_create_research_mode_alias_wins_over_interactive(client: TestClient) -> None:
    """contract §3a search modes: `mode` is the owner-facing spelling and wins."""
    _enroll_online_device(client)
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    with patch("app.research.routes.Client.connect", AsyncMock(return_value=fake_client)):
        response = client.post(
            "/v1/research",
            json={"input": "konu", "interactive": True, "mode": "unattended"},
        )
    assert response.status_code == 202
    request_arg = fake_client.start_workflow.call_args.args[1]
    assert request_arg.interactive is False
    assert request_arg.on_verification_timeout == "fallback"

    with patch("app.research.routes.Client.connect", AsyncMock(return_value=fake_client)):
        response = client.post(
            "/v1/research",
            json={"input": "konu", "mode": "interactive", "on_verification_timeout": "fail"},
        )
    assert response.status_code == 202
    request_arg = fake_client.start_workflow.call_args.args[1]
    assert request_arg.interactive is True
    assert request_arg.on_verification_timeout == "fail"


def test_create_research_default_research_mode_is_quick(client: TestClient) -> None:
    """M18.2 (ADR-0068): the REST route's speed mode defaults to QUICK for an
    ordinary request that names none - existing callers (every test above, and
    every production caller before this change) are unaffected."""
    from app.research.policy import MODE_QUICK

    _enroll_online_device(client)
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    with patch("app.research.routes.Client.connect", AsyncMock(return_value=fake_client)):
        response = client.post("/v1/research", json={"input": "konu"})
    assert response.status_code == 202
    request_arg = fake_client.start_workflow.call_args.args[1]
    assert request_arg.mode == MODE_QUICK


def test_create_research_accepts_an_explicit_research_mode(client: TestClient) -> None:
    from app.research.policy import MODE_DEEP

    _enroll_online_device(client)
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    with patch("app.research.routes.Client.connect", AsyncMock(return_value=fake_client)):
        response = client.post("/v1/research", json={"input": "konu", "research_mode": "deep"})
    assert response.status_code == 202
    request_arg = fake_client.start_workflow.call_args.args[1]
    assert request_arg.mode == MODE_DEEP


def test_create_research_rejects_unknown_research_mode(client: TestClient) -> None:
    response = client.post("/v1/research", json={"input": "konu", "research_mode": "turbo"})
    assert response.status_code == 422


def test_create_research_rejects_unknown_mode_and_timeout_policy(client: TestClient) -> None:
    assert client.post("/v1/research", json={"input": "konu", "mode": "stealth"}).status_code == 422
    assert (
        client.post(
            "/v1/research", json={"input": "konu", "on_verification_timeout": "retry"}
        ).status_code
        == 422
    )


def test_create_research_rejects_interactive_wait_s_below_minimum(client: TestClient) -> None:
    # 30 s is the floor so an owner QUALIFICATION run need not wait ten minutes (ADR-0050
    # item 18); production requests keep the 600 s default.
    response = client.post("/v1/research", json={"input": "konu", "interactive_wait_s": 29})
    assert response.status_code == 422


def test_create_research_accepts_a_short_qualification_wait(client: TestClient) -> None:
    _enroll_online_device(client)
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=None)
    with patch("app.research.routes.Client.connect", AsyncMock(return_value=fake_client)):
        response = client.post(
            "/v1/research", json={"input": "konu", "mode": "interactive", "interactive_wait_s": 30}
        )
    assert response.status_code == 202
    assert fake_client.start_workflow.call_args.args[1].interactive_wait_s == 30


def test_create_research_rejects_interactive_wait_s_above_maximum(client: TestClient) -> None:
    response = client.post("/v1/research", json={"input": "konu", "interactive_wait_s": 1801})
    assert response.status_code == 422


def test_get_research_unknown_task_404(client: TestClient) -> None:
    response = client.get(f"/v1/research/{uuid.uuid4()}")
    assert response.status_code == 404


def test_get_research_report_404_until_synthesized(client: TestClient) -> None:
    _enroll_online_device(client)
    with _patched_temporal_client():
        started = client.post("/v1/research", json={"input": "konu"})
    task_id = started.json()["task_id"]
    response = client.get(f"/v1/research/{task_id}/report")
    assert response.status_code == 404


def test_list_research_returns_only_research_tasks_newest_first(client: TestClient) -> None:
    _enroll_online_device(client)
    with _patched_temporal_client():
        first = client.post("/v1/research", json={"input": "konu 1"}).json()
        second = client.post("/v1/research", json={"input": "konu 2"}).json()
    response = client.get("/v1/research")
    assert response.status_code == 200
    task_ids = [t["task_id"] for t in response.json()["tasks"]]
    assert first["task_id"] in task_ids
    assert second["task_id"] in task_ids


def test_cancel_unknown_task_404(client: TestClient) -> None:
    response = client.post(f"/v1/research/{uuid.uuid4()}/cancel")
    assert response.status_code == 404


def test_cancel_research_marks_run_cancelled(client: TestClient) -> None:
    _enroll_online_device(client)
    with _patched_temporal_client():
        started = client.post("/v1/research", json={"input": "konu"})
        task_id = started.json()["task_id"]
        response = client.post(f"/v1/research/{task_id}/cancel")
    assert response.status_code == 200
    detail = client.get(f"/v1/research/{task_id}").json()
    assert detail["stage"] == "cancelled"


def test_research_endpoints_require_owner_session(engine) -> None:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    from sqlalchemy.orm import sessionmaker

    broker = BrokerRuntime(settings)
    broker._engine = engine
    broker._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.broker = broker
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts

    unauthenticated_client = TestClient(app)
    response = unauthenticated_client.post("/v1/research", json={"input": "konu"})
    assert response.status_code == 401


def test_research_policy_reports_the_effective_provider(client: TestClient) -> None:
    """The owner research command probes this route: its absence (404 on an older Cloud
    Core) is the signal to release, and its search_provider is what a default run uses."""
    response = client.get("/v1/research/policy")
    assert response.status_code == 200
    body = response.json()
    assert body["search_provider"] == "duckduckgo"
    assert body["policy_version"] >= 1
    assert body["worker_search_contract"] >= 2
    assert set(body["search_providers"]) == {"duckduckgo", "google", "auto"}
    assert body["interactive_wait_s"]["min"] == 30
    assert body["modes"] == ["interactive", "unattended"]


def test_research_policy_publishes_the_speed_modes(client: TestClient) -> None:
    """M18.2 (ADR-0068): a client can see the QUICK/STANDARD/DEEP modes and their
    budgets without guessing - distinct from `modes` (owner-handoff interactive/
    unattended), which stays exactly as it was."""
    from app.research.policy import DEFAULT_MODE, RESEARCH_MODES

    response = client.get("/v1/research/policy")
    body = response.json()
    assert body["modes"] == ["interactive", "unattended"]  # unchanged
    assert body["research_modes"] == list(RESEARCH_MODES)
    assert body["research_mode_default"] == DEFAULT_MODE
    assert body["research_policies"]["quick"]["hard_budget_s"] == 120.0
    assert (
        body["research_policies"]["standard"]["hard_budget_s"]
        > body["research_policies"]["quick"]["hard_budget_s"]
    )


def test_research_policy_publishes_the_quality_gate_and_findings_floor(
    client: TestClient,
) -> None:
    """Policy 4 (owner incident, 2026-09-04): the owner command compares the Cloud Core it is
    talking to against the release it expects, so what the gate refuses and what a report
    must contain have to be visible from outside, not only in the code."""
    body = client.get("/v1/research/policy").json()
    assert body["policy_version"] >= 4
    gate = body["quality_gate"]
    assert {"off_topic", "outside_recency_window", "interstitial"} <= set(gate["rejection_reasons"])
    assert 0 < gate["min_topic_relevance"] < 1
    assert "normal_content" in gate["page_validity_kinds"]
    findings = body["findings_contract"]
    assert findings["min_findings"] == 3
    assert findings["target_findings"] == 5
    assert findings["attribution_required"] is True
    assert findings["failure_error_class"] == "insufficient_valid_findings"
