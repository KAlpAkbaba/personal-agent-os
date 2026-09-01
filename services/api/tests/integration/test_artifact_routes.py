"""M3 integration: the HTTP surface end to end.

POST /v1/tasks starts a durable workflow (worker runs as a subprocess on the
default task queue); we poll GET /v1/tasks/{id} to READY, then exercise the
artifact + render endpoints. Deterministic provider -> no network.
"""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.object_store import S3ObjectStore
from tests.integration import procs
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
TOPIC = "yapay zekâ ajanlarındaki son gelişmeler"


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module", autouse=True)
def _ensure_bucket(settings: Settings) -> None:
    S3ObjectStore.from_settings(settings).ensure_bucket()


@pytest.fixture()
def default_queue_worker(settings: Settings):
    """A worker on the default task queue so POST /v1/tasks workflows complete."""
    env = dict(os.environ)
    env["PAGENTOS_TEMPORAL_TASK_QUEUE"] = settings.temporal_task_queue
    proc = procs.spawn(
        [sys.executable, "-m", "app.worker"],
        cwd=str(API_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield proc
    finally:
        procs.stop(proc)


def _poll_ready(client: TestClient, task_id: str, timeout_s: float = 45.0) -> dict:
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        resp = client.get(f"/v1/tasks/{task_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] == "READY":
            return last
        time.sleep(1.0)
    raise AssertionError(f"task did not reach READY in time: {last}")


def test_task_and_artifact_http_flow(settings: Settings, default_queue_worker) -> None:
    with owner_client(settings) as client:
        # POST returns a durable task id immediately (202), does not block.
        resp = client.post("/v1/tasks", json={"input": TOPIC})
        assert resp.status_code == 202
        created = resp.json()
        task_id = created["task_id"]
        assert created["status"] == "CREATED"
        assert created["workflow_id"] == f"research-{task_id}"

        ready = _poll_ready(client, task_id)
        assert ready["status"] == "READY"
        artifact_id = ready["artifact_id"]
        assert artifact_id

        # Artifact metadata: executive summary + renders, but NOT the full body.
        meta = client.get(f"/v1/artifacts/{artifact_id}").json()
        assert meta["state"] == "READY"
        assert meta["executive_summary"]
        assert "canonical_body" not in meta
        formats = {r["format"] for r in meta["available_renders"]}
        assert {"pdf", "docx"} <= formats

        # Body only on explicit request.
        with_body = client.get(f"/v1/artifacts/{artifact_id}", params={"include": "body"}).json()
        assert with_body["canonical_body"]
        assert "## Yönetici Özeti (Executive Summary)" in with_body["canonical_body"]

        # Canonical markdown endpoint.
        canonical = client.get(f"/v1/artifacts/{artifact_id}/canonical")
        assert canonical.status_code == 200
        assert "text/markdown" in canonical.headers["content-type"]
        assert canonical.text.startswith("# ")

        # Stored renders stream back with correct magic bytes + content hash.
        pdf = client.get(f"/v1/artifacts/{artifact_id}/renders/pdf")
        assert pdf.status_code == 200
        assert pdf.content[:4] == b"%PDF"
        assert pdf.headers["X-Content-Hash"]

        docx = client.get(f"/v1/artifacts/{artifact_id}/renders/docx")
        assert docx.content[:2] == b"PK"

        # On-demand render of a not-yet-materialized format (HTML).
        made = client.post(f"/v1/artifacts/{artifact_id}/renders", json={"format": "html"})
        assert made.status_code == 201
        assert made.json()["format"] == "html"
        html = client.get(f"/v1/artifacts/{artifact_id}/renders/html")
        assert html.content[:9].lower() == b"<!doctype"

        # Inbox listing includes the artifact without dumping bodies.
        listing = client.get("/v1/artifacts").json()
        assert any(a["artifact_id"] == artifact_id for a in listing["artifacts"])
        assert all("canonical_body" not in a for a in listing["artifacts"])


def test_task_ends_ready_without_auto_reading_body(
    settings: Settings, default_queue_worker
) -> None:
    """Acceptance (M3): a completed research task ends READY and can be seen
    without the report body ever being pushed at the owner. Dedicated 1:1 test
    for the "notify briefly and wait" guarantee."""
    with owner_client(settings) as client:
        task_id = client.post("/v1/tasks", json={"input": TOPIC}).json()["task_id"]
        ready = _poll_ready(client, task_id)

        # The task status payload carries readiness + artifact id, never the body.
        assert ready["status"] == "READY"
        assert ready["artifact_id"]
        assert "canonical_body" not in ready
        assert "executive_summary" not in ready

        # The default artifact view is the executive summary, not the full report.
        meta = client.get(f"/v1/artifacts/{ready['artifact_id']}").json()
        assert meta["executive_summary"]
        assert "canonical_body" not in meta

        # The body exists but only when explicitly asked for.
        with_body = client.get(
            f"/v1/artifacts/{ready['artifact_id']}", params={"include": "body"}
        ).json()
        assert with_body["canonical_body"]


def test_unknown_task_and_artifact_404(settings: Settings) -> None:
    with owner_client(settings) as client:
        missing = uuid.uuid4()
        assert client.get(f"/v1/tasks/{missing}").status_code == 404
        assert client.get(f"/v1/artifacts/{missing}").status_code == 404
        assert client.get(f"/v1/artifacts/{missing}/renders/pdf").status_code == 404


def test_create_task_rejects_unwired_provider(settings: Settings) -> None:
    with owner_client(settings) as client:
        resp = client.post("/v1/tasks", json={"input": TOPIC, "provider": "web"})
        assert resp.status_code == 422
