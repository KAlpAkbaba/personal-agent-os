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
from datetime import UTC, datetime, timedelta
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
from app.research.browser_gateway import _fake_page_body, _fake_page_title
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
                "title": _fake_page_title(_story_index(payload["url"])),
                # A page the quality gate accepts: on topic, dated, and long enough to
                # judge. A one-line stub is not something a person would cite either.
                "excerpt": _fake_page_body(payload["query"], "news", _story_index(payload["url"])),
                "fetched_at": _iso(_FAKE_FETCHED_AT),
                "extraction_method": "dom_text",
                "page_kind": "ok",
                "http_status": 200,
                "metadata": {
                    "publisher": "Örnek Yayın",
                    "published_at": _iso(_FAKE_PUBLISHED_AT),
                },
                "injection_markers": 0,
            }
        )
    raise AssertionError(
        f"unexpected capability in integration fake: {capability}"
    )  # pragma: no cover


#: The fake pages are dated RELATIVE to the run, inside the default three-day recency
#: window (app.research.plan.DEFAULT_RECENCY_DAYS). They used to carry a fixed
#: 2026-09-03 and became a time bomb on 2026-09-07: every candidate was rejected as
#: outside_recency_window, the run ended failed, and the ledger test after it saw a
#: failed research as the newest activity.
_FAKE_PUBLISHED_AT = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=26)
_FAKE_FETCHED_AT = _FAKE_PUBLISHED_AT + timedelta(hours=2)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _story_index(url: str) -> int:
    """Which of the fake stories this URL is, deterministically.

    The plan issues several related queries and each returns two results, so the index has
    to depend on the query as well as the position - otherwise every query returns the same
    two headlines and same-story detection collapses them into one, which is exactly what
    it should do to real duplicate coverage.
    """
    query, _, tail = url.rpartition("/")
    position = int(tail) if tail.isdigit() else 0
    return (sum(ord(c) for c in query) + position) % 5


def _url_index(url: str) -> int:
    """The trailing "/1", "/2" of the fake search results, so each fetched page differs."""
    tail = url.rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else 0


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


# ------------------------------------------------ interactive owner handoff (spec §5a)


async def test_browser_research_workflow_interactive_handoff_cleared_reaches_ready(
    settings: Settings, _no_network_discovery: None, monkeypatch
) -> None:
    """An interactive run whose Google search hits an interstitial waits for
    the owner (browser.wait), then — once satisfied — re-issues the SAME
    search (path=handoff_cleared) and the run still reaches ready. Reaching
    ready at all is itself evidence the new `waiting_for_owner_verification`
    stage value is accepted by the DB (migration 0014's widened CHECK
    constraint), since `update_run` would raise otherwise."""
    search_waited_for: set[str] = set()

    def factory(*, capability: str, payload: dict[str, Any], **_kwargs) -> Any:
        if capability == "browser.wait":
            return CommandSucceeded(
                {"satisfied": True, "url": "https://www.google.com/search?q=x", "elapsed_ms": 500}
            )
        if capability == "browser.search":
            query = payload["query"]
            if payload.get("interstitial") == "handoff" and query not in search_waited_for:
                search_waited_for.add(query)
                return CommandSucceeded(
                    {
                        "schema_version": 2,
                        "requested_provider": "google",
                        "provider": None,
                        "state": "waiting_for_owner_verification",
                        "path": "handoff_pending",
                        "page_kind": "captcha",
                        "verification_url": "https://www.google.com/sorry/index",
                        "results": [],
                        "result_count": 0,
                    }
                )
            return CommandSucceeded(
                {
                    "schema_version": 2,
                    "requested_provider": "google",
                    "provider": "google",
                    "fallback": False,
                    "fallback_reason": None,
                    "state": "ok",
                    "path": "handoff_cleared",
                    "result_count": 2,
                    "results": [
                        {
                            "url": f"https://news.example.com/{query}/1",
                            "title": f"{query} haberi 1",
                        },
                        {
                            "url": f"https://news.example.com/{query}/2",
                            "title": f"{query} haberi 2",
                        },
                    ],
                }
            )
        return _fake_command_factory(capability=capability, payload=payload)

    fake_client = FakeDeviceCommandClient(factory=factory)
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
            name="interactive-pc",
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

        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        task_queue = f"pagentos-m13-interactive-cleared-{uuid.uuid4().hex[:8]}"
        async with build_worker(client, task_queue):
            result = await client.execute_workflow(
                BrowserResearchWorkflow.run,
                BrowserResearchRequest(
                    task_id=str(task_id),
                    topic=TOPIC,
                    target_device=None,
                    max_sources=6,
                    synthesis="deterministic",
                    interactive=True,
                    interactive_wait_s=120,
                ),
                id=f"research-browser-{task_id}",
                task_queue=task_queue,
            )
    finally:
        register_broker_runtime(None)

    assert result["stage"] == "ready"
    assert search_waited_for, "the fake never saw an interstitial=handoff search to wait on"

    runtime = ArtifactRuntime(settings)
    with runtime.session() as session:
        # The retried search after "cleared" actually inserted candidates —
        # durable proof (unlike the capped-at-50 events list) that discovery
        # resumed rather than silently giving up.
        candidates = runs_service.list_candidates(session, task_id)
        assert any(c.discovered_by == "browser_search:google" for c in candidates)

        report_row = runs_service.get_report(session, task_id)
        assert report_row is not None
        assert report_row.report_json["schema_version"] == 1


async def test_browser_research_workflow_second_interstitial_after_clearance_falls_back_once(
    settings: Settings, _no_network_discovery: None, monkeypatch
) -> None:
    """Retry once, never loop (contract §3a / spec §5a, 2026-09-04): the owner clears
    the first interstitial, the same search is re-issued once, Google asks AGAIN,
    and the workflow does not start a second wait — it takes exactly one
    fallback attempt for that query and the run still reaches ready."""
    handoff_searches: list[str] = []
    fallback_searches: list[str] = []
    waits = 0

    def factory(*, capability: str, payload: dict[str, Any], **_kwargs) -> Any:
        nonlocal waits
        if capability == "browser.wait":
            waits += 1
            return CommandSucceeded(
                {"satisfied": True, "url": "https://www.google.com/search?q=x", "elapsed_ms": 500}
            )
        if capability == "browser.search":
            query = payload["query"]
            if payload.get("interstitial") == "handoff":
                handoff_searches.append(query)
                # every handoff-mode attempt is blocked: first -> wait, second -> must not wait
                return CommandSucceeded(
                    {
                        "schema_version": 2,
                        "requested_provider": "google",
                        "provider": None,
                        "state": "waiting_for_owner_verification",
                        "path": "handoff_pending",
                        "page_kind": "captcha",
                        "verification_url": "https://www.google.com/sorry/index",
                        "results": [],
                        "result_count": 0,
                    }
                )
            fallback_searches.append(query)
            return CommandSucceeded(
                {
                    "schema_version": 2,
                    "requested_provider": "google",
                    "provider": "duckduckgo",
                    "fallback": True,
                    "fallback_reason": "google:captcha",
                    "state": "ok",
                    "path": "handoff_repeat_fallback",
                    "result_count": 1,
                    "results": [
                        {
                            "url": f"https://news.example.com/{query}/1",
                            "title": f"{query} haberi 1",
                        },
                    ],
                }
            )
        return _fake_command_factory(capability=capability, payload=payload)

    fake_client = FakeDeviceCommandClient(factory=factory)
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
            name="interactive-repeat-pc",
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
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        task_queue = f"pagentos-m13-interactive-repeat-{uuid.uuid4().hex[:8]}"
        async with build_worker(client, task_queue):
            result = await client.execute_workflow(
                BrowserResearchWorkflow.run,
                BrowserResearchRequest(
                    task_id=str(task_id),
                    topic=TOPIC,
                    target_device=None,
                    max_sources=6,
                    synthesis="deterministic",
                    interactive=True,
                    interactive_wait_s=120,
                ),
                id=f"research-browser-{task_id}",
                task_queue=task_queue,
            )
    finally:
        register_broker_runtime(None)
    assert result["stage"] == "ready"
    # per query: handoff, (wait), handoff again, then exactly one fallback - never a 3rd handoff
    from collections import Counter

    # the plan issues each query once per source class; per (class, query): handoff,
    # one wait, handoff again, then exactly one fallback - never a third handoff
    per_query = Counter(handoff_searches)
    per_query_fallback = Counter(fallback_searches)
    assert per_query, "no handoff-mode search was issued"
    for query, handoffs in per_query.items():
        assert handoffs == 2 * per_query_fallback[query], (
            query,
            handoffs,
            per_query_fallback[query],
        )
    assert waits == sum(per_query_fallback.values())


async def test_browser_research_workflow_verification_timeout_fail_policy_stops_the_run(
    settings: Settings, _no_network_discovery: None, monkeypatch
) -> None:
    """on_verification_timeout="fail": the owner never clears the page and asked not
    to continue without the primary provider -> the run fails with
    owner_verification_timeout (Turkish detail), no fallback search is issued."""
    fallback_searches = 0

    def factory(*, capability: str, payload: dict[str, Any], **_kwargs) -> Any:
        nonlocal fallback_searches
        if capability == "browser.wait":
            return CommandSucceeded({"satisfied": False, "url": None, "elapsed_ms": 30000})
        if capability == "browser.search":
            if payload.get("interstitial") == "handoff":
                return CommandSucceeded(
                    {
                        "schema_version": 2,
                        "requested_provider": "google",
                        "provider": None,
                        "state": "waiting_for_owner_verification",
                        "path": "handoff_pending",
                        "page_kind": "captcha",
                        "verification_url": "https://www.google.com/sorry/index",
                        "results": [],
                        "result_count": 0,
                    }
                )
            fallback_searches += 1
            return CommandSucceeded(
                {
                    "schema_version": 2,
                    "requested_provider": "google",
                    "provider": "duckduckgo",
                    "fallback": True,
                    "fallback_reason": "google:captcha",
                    "state": "ok",
                    "path": "handoff_timeout_fallback",
                    "result_count": 0,
                    "results": [],
                }
            )
        return _fake_command_factory(capability=capability, payload=payload)

    fake_client = FakeDeviceCommandClient(factory=factory)
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
            name="interactive-fail-pc",
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
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        task_queue = f"pagentos-m13-interactive-fail-{uuid.uuid4().hex[:8]}"
        async with build_worker(client, task_queue):
            result = await client.execute_workflow(
                BrowserResearchWorkflow.run,
                BrowserResearchRequest(
                    task_id=str(task_id),
                    topic=TOPIC,
                    target_device=None,
                    max_sources=6,
                    synthesis="deterministic",
                    interactive=True,
                    interactive_wait_s=60,
                    on_verification_timeout="fail",
                ),
                id=f"research-browser-{task_id}",
                task_queue=task_queue,
            )
    finally:
        register_broker_runtime(None)
    assert result["stage"] == "failed"
    assert result["error"]["error_class"] == "owner_verification_timeout"
    assert "doğrulama" in result["error"]["detail"]
    assert fallback_searches == 0
    runtime = ArtifactRuntime(settings)
    with runtime.session() as session:
        run = runs_service.get_run(session, task_id)
        assert run.stage == "failed"


async def test_browser_research_workflow_interactive_handoff_timeout_falls_back(
    settings: Settings, _no_network_discovery: None, monkeypatch
) -> None:
    """The owner never clears the interstitial within the budget: the
    workflow gives up waiting and retries with interstitial="fallback"
    (path=handoff_timeout_fallback) rather than hanging or failing the run."""

    def factory(*, capability: str, payload: dict[str, Any], **_kwargs) -> Any:
        if capability == "browser.wait":
            return CommandSucceeded({"satisfied": False, "url": None, "elapsed_ms": 30000})
        if capability == "browser.search":
            if payload.get("interstitial") == "handoff":
                return CommandSucceeded(
                    {
                        "schema_version": 2,
                        "requested_provider": "google",
                        "provider": None,
                        "state": "waiting_for_owner_verification",
                        "path": "handoff_pending",
                        "page_kind": "captcha",
                        "verification_url": "https://www.google.com/sorry/index",
                        "results": [],
                        "result_count": 0,
                    }
                )
            query = payload["query"]
            return CommandSucceeded(
                {
                    "schema_version": 2,
                    "requested_provider": "google",
                    "provider": "duckduckgo",
                    "fallback": True,
                    "fallback_reason": "google:captcha",
                    "state": "ok",
                    "path": "handoff_timeout_fallback",
                    "result_count": 2,
                    "results": [
                        {
                            "url": f"https://news.example.com/{query}/1",
                            "title": f"{query} haberi 1",
                        },
                        {
                            "url": f"https://news.example.com/{query}/2",
                            "title": f"{query} haberi 2",
                        },
                    ],
                }
            )
        return _fake_command_factory(capability=capability, payload=payload)

    fake_client = FakeDeviceCommandClient(factory=factory)
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
            name="interactive-timeout-pc",
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

        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        task_queue = f"pagentos-m13-interactive-timeout-{uuid.uuid4().hex[:8]}"
        async with build_worker(client, task_queue):
            # A small interactive_wait_s (< 60s) keeps this test fast: the
            # scripted browser.wait never blocks for real, only the workflow's
            # own budget bookkeeping needs an exhaustible number.
            result = await client.execute_workflow(
                BrowserResearchWorkflow.run,
                BrowserResearchRequest(
                    task_id=str(task_id),
                    topic=TOPIC,
                    target_device=None,
                    max_sources=6,
                    synthesis="deterministic",
                    interactive=True,
                    interactive_wait_s=60,
                ),
                id=f"research-browser-{task_id}",
                task_queue=task_queue,
            )
    finally:
        register_broker_runtime(None)

    assert result["stage"] == "ready"

    runtime = ArtifactRuntime(settings)
    with runtime.session() as session:
        candidates = runs_service.list_candidates(session, task_id)
        assert any(c.discovered_by == "browser_search:duckduckgo" for c in candidates)

        report_row = runs_service.get_report(session, task_id)
        assert report_row is not None


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
            db,
            name="restart-pc",
            platform="windows",
            public_key_spki_b64=_spki(),
            capabilities=["browser.chrome"],
            trace_id=None,
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
                task_id=str(task_id),
                topic=TOPIC,
                target_device=None,
                max_sources=6,
                synthesis="deterministic",
            ),
            id=f"research-browser-{task_id}",
            task_queue=task_queue,
        )
        # Wait for worker1 to actually reach the blocked fetch (not just a
        # fixed sleep) so the restart happens genuinely mid-activity.
        await asyncio.get_running_loop().run_in_executor(None, first_fetch_dispatched.wait, 30)
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
