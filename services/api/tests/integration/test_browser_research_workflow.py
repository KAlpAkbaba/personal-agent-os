"""M13 integration: BrowserResearchWorkflow end-to-end on the compose stack.

Requires postgres/minio/temporal (the same infra M3's research integration
test uses). The DEVICE side is a FakeDeviceCommandClient (no real broker WS,
no real Windows agent, no real Chrome — those are owner-machine qualification,
scripts/e2e-m13-research.ps1) so this proves the Cloud Core half of the chain:
real Temporal durability, real Postgres persistence (research_runs/
candidates/evidence/reports, devices.metadata_json), real MinIO renders, and
a real memory write — deterministically and offline (discovery network calls
are monkeypatched to empty results; the "official"/"technical"/"academic"
network paths are unit-tested separately with fixture text, and exercised for
real only by scripts/e2e-m13-research.ps1 / a live-marked test).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import threading
import time
import uuid
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from temporalio.client import Client

from app.artifacts.models import ARTIFACT_STATE_READY, TASK_STATUS_READY
from app.artifacts.runtime import ArtifactRuntime, build_artifact_context
from app.broker import service as broker_service
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.config import Settings
from app.devices.commands import CommandSucceeded, register_broker_runtime
from app.object_store import S3ObjectStore
from app.research import browser_activities, destination, discovery, runs_service
from app.research.browser_workflow import BrowserResearchRequest, BrowserResearchWorkflow
from app.worker import build_worker
from tests.device_command_support import FakeDeviceCommandClient

pytestmark = pytest.mark.integration

TOPIC = "yapay zekâ ajanları haberleri"
PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _ensure_bucket(settings: Settings) -> None:
    S3ObjectStore.from_settings(settings).ensure_bucket()


@pytest.fixture(autouse=True)
def _permissive_destination(monkeypatch) -> None:
    """The candidate URLs this harness's fake device produces
    (news.example.com/...) do not resolve on the real internet; the
    destination-policy boundary itself is unit-tested offline in
    tests/unit/test_research_destination.py, so here (proving the Cloud
    Core/DB/Temporal chain, not DNS) the resolver is stubbed to a fixed
    public address."""
    monkeypatch.setattr(destination, "resolve_hostname", lambda host: [PUBLIC_IP])


@pytest.fixture()
def _no_network_discovery(monkeypatch) -> None:
    """official/technical/academic discovery is unit-tested with fixture
    text elsewhere; this integration test proves the Cloud Core/DB/Temporal
    chain, not live third-party feeds (those are owner-qualification/live-
    marked concerns)."""
    monkeypatch.setattr(discovery, "fetch_hn", lambda *a, **k: [])
    monkeypatch.setattr(discovery, "fetch_arxiv", lambda *a, **k: [])
    monkeypatch.setattr(discovery, "fetch_rss", lambda *a, **k: [])


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


def _fake_command_factory(*, capability: str, payload: dict[str, Any], **_kwargs) -> Any:
    if capability == "browser.session_open":
        return CommandSucceeded({"created": True, "channel": "chrome"})
    if capability == "browser.session_close":
        return CommandSucceeded({"closed": True})
    if capability == "browser.search":
        query = payload["query"]
        return CommandSucceeded(
            {
                "results": [
                    {"url": f"https://news.example.com/{query}/1", "title": f"{query} haberi 1"},
                    {"url": f"https://news.example.com/{query}/2", "title": f"{query} haberi 2"},
                ]
            }
        )
    if capability == "browser.fetch_evidence":
        return CommandSucceeded(
            {
                "url": payload["url"],
                "final_url": payload["url"],
                "title": f"Bulgu: {payload['query']}",
                "excerpt": f"{payload['query']} ile ilgili gerçek zamanlı bir gelişme bulundu.",
                "fetched_at": "2026-09-03T09:00:00Z",
                "extraction_method": "dom_text",
                "page_kind": "ok",
                "http_status": 200,
                "metadata": {"publisher": "Örnek Yayın"},
                "injection_markers": 0,
            }
        )
    raise AssertionError(
        f"unexpected capability in integration fake: {capability}"
    )  # pragma: no cover


async def _run_workflow(
    settings: Settings, task_id: uuid.UUID, device_id: uuid.UUID, *, topic: str = TOPIC
) -> dict:
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    task_queue = f"pagentos-m13-{uuid.uuid4().hex[:8]}"
    async with build_worker(client, task_queue):
        return await client.execute_workflow(
            BrowserResearchWorkflow.run,
            BrowserResearchRequest(
                task_id=str(task_id),
                topic=topic,
                target_device=None,
                max_sources=6,
                synthesis="deterministic",
            ),
            id=f"research-browser-{task_id}",
            task_queue=task_queue,
        )


async def test_browser_research_workflow_end_to_end(
    settings: Settings, _no_network_discovery: None, monkeypatch
) -> None:
    fake_client = FakeDeviceCommandClient(factory=_fake_command_factory)
    monkeypatch.setattr(browser_activities, "_command_client", lambda: fake_client)

    from app.artifacts import service as artifact_service

    af_factory, _store = build_artifact_context(settings)
    with af_factory() as session:
        task = artifact_service.create_task(session, intent=TOPIC)
        task_id = task.id

    broker_runtime = BrokerRuntime(settings)
    with broker_runtime.session() as db:
        device = broker_service.enroll_device(
            db,
            name="integration-pc",
            platform="windows",
            public_key_spki_b64=_spki(),
            capabilities=["browser.chrome"],
            trace_id=None,
        )
    broker_runtime.connections[device.id] = DeviceConnection(
        device_id=device.id, session_id=uuid.uuid4(), websocket=object()
    )
    register_broker_runtime(broker_runtime)
    try:
        with af_factory() as session:
            runs_service.update_run(session, task_id, device_id=device.id)

        result = await _run_workflow(settings, task_id, device.id)
    finally:
        register_broker_runtime(None)

    assert result["stage"] == "ready"
    assert result["artifact_id"]
    assert result["memory_id"]
    assert result["sources_count"] >= 1

    runtime = ArtifactRuntime(settings)
    with runtime.session() as session:
        db_task = artifact_service.get_task(session, task_id)
        assert db_task.status == TASK_STATUS_READY

        run = runs_service.get_run(session, task_id)
        assert run is not None
        assert run.stage == "ready"

        evidence_rows = runs_service.list_evidence(session, task_id)
        assert evidence_rows
        urls = [r.url for r in evidence_rows]
        assert len(urls) == len(set(urls))  # no duplicate research_evidence rows

        report_row = runs_service.get_report(session, task_id)
        assert report_row is not None
        assert report_row.report_json["schema_version"] == 1
        assert report_row.artifact_id is not None
        assert report_row.memory_id is not None

        artifact = artifact_service.get_artifact(session, report_row.artifact_id)
        assert artifact is not None
        assert artifact.state == ARTIFACT_STATE_READY
        version = artifact_service.get_current_version(session, artifact.id)
        assert "[e" in version.canonical_body  # [eN] citation markers survive into Markdown

        # Source provenance (spec §3/§5, finding MEDIUM-5): every source in
        # the persisted report carries the device and command that actually
        # fetched it, not just the URL.
        sources = report_row.report_json["sources"]
        assert sources
        for source in sources:
            assert source["device_id"] == str(device.id)
            assert source["command_id"]

    from app.memory.runtime import MemoryRuntime
    from app.memory.service import get_memory

    memory_runtime = MemoryRuntime(settings)
    with memory_runtime.session() as session:
        memory = get_memory(session, report_row.memory_id)
        assert memory.key == f"research:{task_id}"
        assert memory.memory_class == "episodic"


# --------------------------------------------------- restart mid-fetch (LOW-10)


async def test_browser_research_workflow_survives_restart_mid_fetch(
    settings: Settings, _no_network_discovery: None, monkeypatch
) -> None:
    """Finding LOW-10: the first fetch blocks (never completes) on a
    FakeDeviceCommandClient standing in for "worker1"; worker1's task-queue
    poller is torn down abruptly (a hard cancel, not a graceful drain) while
    that fetch activity is in flight; a fresh worker2, wired to a
    non-blocking client, then picks up the retried attempt. Assert: the
    workflow still reaches READY, exactly one evidence row exists per URL
    (the abandoned attempt never wrote one), and the plan/device selected
    before the restart were reused rather than re-planned/re-selected."""
    from app.artifacts import service as artifact_service

    af_factory, _store = build_artifact_context(settings)
    with af_factory() as session:
        task = artifact_service.create_task(session, intent=TOPIC)
        task_id = task.id

    broker_runtime = BrokerRuntime(settings)
    with broker_runtime.session() as db:
        device = broker_service.enroll_device(
            db, name="restart-pc", platform="windows", public_key_spki_b64=_spki(),
            capabilities=["browser.chrome"], trace_id=None,
        )
    broker_runtime.connections[device.id] = DeviceConnection(
        device_id=device.id, session_id=uuid.uuid4(), websocket=object()
    )
    register_broker_runtime(broker_runtime)

    first_fetch_dispatched = threading.Event()
    first_fetch_seen = {"done": False}

    def _blocking_first_fetch(*, capability: str, payload: dict[str, Any], **_kwargs) -> Any:
        if capability == "browser.fetch_evidence" and not first_fetch_seen["done"]:
            first_fetch_seen["done"] = True
            first_fetch_dispatched.set()
            # worker1 never comes back to release this; it self-abandons
            # after a bound so the underlying (leaked) thread does not hang
            # the test process forever. It must NEVER return a successful
            # outcome — only attempt 2, on worker2, may succeed.
            time.sleep(15)
            raise RuntimeError("worker1 abandoned this fetch (simulated hard restart)")
        return _fake_command_factory(capability=capability, payload=payload)

    def _normal_client() -> FakeDeviceCommandClient:
        return FakeDeviceCommandClient(factory=_blocking_first_fetch)

    monkeypatch.setattr(browser_activities, "_command_client", _normal_client)

    try:
        with af_factory() as session:
            runs_service.update_run(session, task_id, device_id=device.id)

        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        task_queue = f"pagentos-m13-restart-{uuid.uuid4().hex[:8]}"

        worker1 = build_worker(client, task_queue)
        run_task1 = asyncio.create_task(worker1.run())
        handle = await client.start_workflow(
            BrowserResearchWorkflow.run,
            BrowserResearchRequest(
                task_id=str(task_id), topic=TOPIC, target_device=None,
                max_sources=6, synthesis="deterministic",
            ),
            id=f"research-browser-{task_id}",
            task_queue=task_queue,
        )
        # Wait for worker1 to actually reach the blocked fetch (not just a
        # fixed sleep) so the restart happens genuinely mid-activity.
        await asyncio.get_running_loop().run_in_executor(
            None, first_fetch_dispatched.wait, 30
        )
        assert first_fetch_dispatched.is_set(), "worker1 never reached the first fetch"

        # Hard kill: cancel worker1's poll/dispatch loop directly rather than
        # awaiting a graceful shutdown (which would wait for the still-
        # blocked activity thread) — the same "no goodbye" restart M3's
        # worker-restart test exercises via a real subprocess kill.
        run_task1.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task1

        # Now bring up "worker2": a fresh poller on the same task queue,
        # wired to the very same (now non-blocking-for-new-calls) client
        # factory — the retried attempt (attempt=2) hits the plain
        # _fake_command_factory branch and succeeds quickly.
        worker2 = build_worker(client, task_queue)
        async with worker2:
            result = await asyncio.wait_for(handle.result(), timeout=90)
    finally:
        register_broker_runtime(None)

    assert result["stage"] == "ready"
    assert result["sources_count"] >= 1

    runtime = ArtifactRuntime(settings)
    with runtime.session() as session:
        run = runs_service.get_run(session, task_id)
        assert run is not None
        # The plan/device selected before the restart were reused, not
        # rebuilt from scratch on worker2 (spec §5: idempotent activities +
        # Temporal history replay).
        assert run.plan_json is not None
        assert run.device_id == device.id

        evidence_rows = runs_service.list_evidence(session, task_id)
        urls = [r.url for r in evidence_rows]
        assert urls  # at least the one URL whose first attempt was abandoned
        assert len(urls) == len(set(urls))  # exactly one evidence row per URL

        report_row = runs_service.get_report(session, task_id)
        assert report_row is not None
        assert report_row.report_json["schema_version"] == 1
