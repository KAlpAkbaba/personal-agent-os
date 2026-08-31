"""M3 integration: durable research -> canonical artifact -> renders in MinIO,
persistence across a simulated service restart, and worker-restart durability.

Requires the compose stack (postgres/minio/temporal). All research runs against
the DeterministicResearchProvider, so there is NO network dependency.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from temporalio.client import Client

from app.artifacts import service
from app.artifacts.models import (
    ARTIFACT_STATE_READY,
    TASK_STATUS_READY,
)
from app.artifacts.runtime import ArtifactRuntime, build_artifact_context
from app.config import Settings
from app.object_store import S3ObjectStore
from app.research.workflow import ResearchRequest, ResearchWorkflow
from app.worker import build_worker

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
TOPIC = "yapay zekâ ajanlarındaki son gelişmeler"


@pytest.fixture(autouse=True)
def _ensure_bucket(settings: Settings) -> None:
    S3ObjectStore.from_settings(settings).ensure_bucket()


async def _connect(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


def _create_task(settings: Settings, topic: str = TOPIC) -> uuid.UUID:
    factory, _ = build_artifact_context(settings)
    with factory() as session:
        task = service.create_task(session, intent=topic)
        return task.id


async def _run_workflow(
    settings: Settings, task_id: uuid.UUID, *, topic: str = TOPIC
) -> dict:
    client = await _connect(settings)
    task_queue = f"pagentos-m3-{uuid.uuid4().hex[:8]}"
    async with build_worker(client, task_queue):
        return await client.execute_workflow(
            ResearchWorkflow.run,
            ResearchRequest(task_id=str(task_id), topic=topic),
            id=f"research-{task_id}",
            task_queue=task_queue,
        )


async def test_research_task_end_to_end(settings: Settings) -> None:
    task_id = _create_task(settings)
    result = await _run_workflow(settings, task_id)

    # Workflow returns compact metadata + executive summary only (no full body):
    # this is the READY-without-auto-read guarantee at the workflow boundary.
    assert result["state"] == "READY"
    assert result["executive_summary"]
    assert "canonical_body" not in result
    assert set(result["render_formats"]) == {"pdf", "docx"}

    # Verify durable state through a FRESH runtime (independent engine + store).
    runtime = ArtifactRuntime(settings)
    with runtime.session() as session:
        task = service.get_task(session, task_id)
        assert task is not None
        assert task.status == TASK_STATUS_READY
        assert task.ready_at is not None

        sources = service.list_research_sources(session, task_id)
        assert len(sources) >= 2  # multiple sources recorded

        artifact = service.get_artifact_for_task(session, task_id)
        assert artifact is not None
        assert artifact.state == ARTIFACT_STATE_READY
        assert artifact.executive_summary
        assert artifact.current_version >= 1

        version = service.get_current_version(session, artifact.id)
        assert version is not None
        assert "## Yönetici Özeti (Executive Summary)" in version.canonical_body
        assert "## Kaynaklar (Sources / Citations)" in version.canonical_body
        # executive summary is stored apart from the (larger) body
        assert artifact.executive_summary != version.canonical_body
        assert len(version.canonical_body) > len(artifact.executive_summary)

        renders = service.list_renders(session, version.id)
        by_format = {r.format: r for r in renders}
        assert {"pdf", "docx"} <= set(by_format)

        # Render bytes are in MinIO and re-readable with a matching content hash.
        for fmt, magic in (("pdf", b"%PDF"), ("docx", b"PK")):
            row = by_format[fmt]
            data = runtime.store.get(row.object_key)
            assert data[:len(magic)] == magic
            from app.artifacts.renderers import content_hash

            assert content_hash(data) == row.content_hash
            assert row.size_bytes == len(data)


async def test_artifact_persists_after_service_restart(settings: Settings) -> None:
    """Create artifact + renders, then simulate a service restart by building a
    brand-new runtime/session/store against the SAME Postgres + MinIO and assert
    everything still resolves and render bytes still fetch with matching hash."""
    task_id = _create_task(settings)
    await _run_workflow(settings, task_id)

    # --- "restart": nothing shared with the run above except Postgres + MinIO ---
    fresh = ArtifactRuntime(settings)
    from app.artifacts import render_store
    from app.artifacts.renderers import content_hash

    with fresh.session() as session:
        artifact = service.get_artifact_for_task(session, task_id)
        assert artifact is not None
        assert artifact.state == ARTIFACT_STATE_READY
        assert artifact.executive_summary

        version = service.get_current_version(session, artifact.id)
        assert version is not None

        renders = service.list_renders(session, version.id)
        assert {"pdf", "docx"} <= {r.format for r in renders}

        for r in renders:
            # bytes survive and re-read identically after "restart"
            data, mime, chash = render_store.fetch_render_bytes(
                session, fresh.store, version=version, title=artifact.title, fmt=r.format
            )
            assert chash == r.content_hash
            assert content_hash(data) == r.content_hash
            assert mime == r.mime_type


# ------------------------------------------------------- worker-restart variant


def _spawn_worker(task_queue: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["PAGENTOS_TEMPORAL_TASK_QUEUE"] = task_queue
    return subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=str(API_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def test_research_workflow_survives_worker_restart(settings: Settings) -> None:
    """The durable workflow resumes on a fresh worker (M0 durability pattern,
    reused for research): kill worker1 during a durable pause, start worker2,
    assert the artifact still reaches READY."""
    task_id = _create_task(settings)
    client = await _connect(settings)
    task_queue = f"pagentos-m3-restart-{uuid.uuid4().hex[:8]}"

    worker1 = _spawn_worker(task_queue)
    try:
        handle = await client.start_workflow(
            ResearchWorkflow.run,
            ResearchRequest(task_id=str(task_id), topic=TOPIC, pause_seconds=8.0),
            id=f"research-{task_id}",
            task_queue=task_queue,
        )
        # Let worker1 run plan + start the durable timer, then die mid-pause.
        await asyncio.sleep(3.0)
        worker1.kill()
        worker1.wait(timeout=15)

        worker2 = _spawn_worker(task_queue)
        try:
            result = await asyncio.wait_for(handle.result(), timeout=90)
        finally:
            worker2.kill()
            worker2.wait(timeout=15)
    finally:
        if worker1.poll() is None:
            worker1.kill()
            worker1.wait(timeout=15)

    assert result["state"] == "READY"

    runtime = ArtifactRuntime(settings)
    with runtime.session() as session:
        task = service.get_task(session, task_id)
        assert task.status == TASK_STATUS_READY
        artifact = service.get_artifact_for_task(session, task_id)
        assert artifact is not None and artifact.state == ARTIFACT_STATE_READY
        version = service.get_current_version(session, artifact.id)
        renders = service.list_renders(session, version.id)
        assert {"pdf", "docx"} <= {r.format for r in renders}
