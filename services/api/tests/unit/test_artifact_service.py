"""Unit tests: artifact/task service + render-store against SQLite + fake store."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.artifacts import render_store, service
from app.artifacts.models import (
    TASK_STATUS_PLANNED,
    TASK_STATUS_READY,
    TASK_STATUS_RENDERING,
    TASK_STATUS_RUNNING,
    Artifact,
    ArtifactRender,
    ArtifactVersion,
    ResearchSource,
    Task,
    TaskRun,
)
from app.artifacts.renderers import content_hash
from app.object_store import InMemoryObjectStore
from app.research.compose import (
    build_source_manifest,
    compose_canonical_markdown,
    compose_executive_summary,
    score_and_dedup,
)
from app.research.provider import DeterministicResearchProvider

ARTIFACT_TABLES = [
    Task.__table__,
    TaskRun.__table__,
    Artifact.__table__,
    ArtifactVersion.__table__,
    ArtifactRender.__table__,
    ResearchSource.__table__,
]

TOPIC = "yapay zekâ ajanları"


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in ARTIFACT_TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


def _scored():
    return score_and_dedup(DeterministicResearchProvider().gather(TOPIC, limit=6))


# ------------------------------------------------------------------ tasks


def test_create_task_starts_in_created(db: Session) -> None:
    task = service.create_task(db, intent=TOPIC)
    assert task.status == "CREATED"
    assert service.get_task(db, task.id).intent == TOPIC


def test_task_transitions_set_timestamps(db: Session) -> None:
    task = service.create_task(db, intent=TOPIC)
    service.transition_task(db, task.id, TASK_STATUS_PLANNED)
    service.transition_task(db, task.id, TASK_STATUS_RUNNING)
    service.transition_task(db, task.id, TASK_STATUS_RENDERING)
    updated = service.transition_task(db, task.id, TASK_STATUS_READY)
    assert updated.status == TASK_STATUS_READY
    assert updated.ready_at is not None


def test_task_run_is_idempotent_per_attempt(db: Session) -> None:
    task = service.create_task(db, intent=TOPIC)
    r1 = service.start_task_run(db, task_id=task.id, attempt=1, status="PLANNED", plan={"a": 1})
    r2 = service.start_task_run(db, task_id=task.id, attempt=1, status="RUNNING", plan=None)
    assert r1.id == r2.id
    service.finish_task_run(db, task_id=task.id, attempt=1, status="READY", telemetry={"n": 2})
    assert r2.status == "READY"


# --------------------------------------------------------------- sources


def test_replace_research_sources_is_idempotent(db: Session) -> None:
    task = service.create_task(db, intent=TOPIC)
    scored = _scored()
    service.replace_research_sources(db, task_id=task.id, sources=scored)
    service.replace_research_sources(db, task_id=task.id, sources=scored)
    rows = service.list_research_sources(db, task.id)
    assert len(rows) == len(scored)
    assert [r.rank for r in rows] == list(range(1, len(scored) + 1))


# -------------------------------------------------------------- artifacts


def test_get_or_create_artifact_for_task_is_idempotent(db: Session) -> None:
    task = service.create_task(db, intent=TOPIC)
    a1 = service.get_or_create_artifact_for_task(db, task_id=task.id, title="T")
    a2 = service.get_or_create_artifact_for_task(db, task_id=task.id, title="T")
    assert a1.id == a2.id


def test_artifact_versioning_and_content_hash(db: Session) -> None:
    task = service.create_task(db, intent=TOPIC)
    artifact = service.get_or_create_artifact_for_task(db, task_id=task.id, title="T")
    body1 = "# One\n"
    v1 = service.add_artifact_version(
        db, artifact_id=artifact.id, canonical_body=body1,
        content_hash=content_hash(body1.encode()),
    )
    assert v1.version == 1
    # same hash -> same version (idempotent re-compose)
    v1b = service.add_artifact_version(
        db, artifact_id=artifact.id, canonical_body=body1,
        content_hash=content_hash(body1.encode()),
    )
    assert v1b.version == 1
    # new content -> new version
    body2 = "# Two\n"
    v2 = service.add_artifact_version(
        db, artifact_id=artifact.id, canonical_body=body2,
        content_hash=content_hash(body2.encode()),
    )
    assert v2.version == 2
    assert service.get_artifact(db, artifact.id).current_version == 2
    assert service.get_current_version(db, artifact.id).content_hash == content_hash(body2.encode())


def test_executive_summary_stored_apart_from_body(db: Session) -> None:
    task = service.create_task(db, intent=TOPIC)
    scored = _scored()
    summary = compose_executive_summary(TOPIC, scored)
    md = compose_canonical_markdown(TOPIC, scored, summary)
    artifact = service.get_or_create_artifact_for_task(db, task_id=task.id, title="T")
    service.add_artifact_version(
        db, artifact_id=artifact.id, canonical_body=md,
        content_hash=content_hash(md.encode()),
        source_manifest=build_source_manifest(TOPIC, scored),
    )
    service.set_executive_summary(db, artifact.id, summary)
    got = service.get_artifact(db, artifact.id)
    assert got.executive_summary == summary
    assert got.executive_summary != md  # summary is not the whole body


# ------------------------------------------------------ render-store bridge


def _seed_version(db: Session) -> tuple[Artifact, ArtifactVersion]:
    task = service.create_task(db, intent=TOPIC)
    scored = _scored()
    summary = compose_executive_summary(TOPIC, scored)
    md = compose_canonical_markdown(TOPIC, scored, summary)
    artifact = service.get_or_create_artifact_for_task(db, task_id=task.id, title="Rapor")
    version = service.add_artifact_version(
        db, artifact_id=artifact.id, canonical_body=md, content_hash=content_hash(md.encode())
    )
    return artifact, version


def test_ensure_render_stores_and_is_idempotent(db: Session) -> None:
    store = InMemoryObjectStore()
    artifact, version = _seed_version(db)
    r1 = render_store.ensure_render(db, store, version=version, title=artifact.title, fmt="pdf")
    assert store.exists(r1.object_key)
    assert store.get(r1.object_key)[:4] == b"%PDF"
    r2 = render_store.ensure_render(db, store, version=version, title=artifact.title, fmt="pdf")
    assert r1.id == r2.id
    assert r1.content_hash == r2.content_hash


def test_ensure_renders_default_formats(db: Session) -> None:
    store = InMemoryObjectStore()
    artifact, version = _seed_version(db)
    rows = render_store.ensure_renders(
        db, store, version=version, title=artifact.title, formats=("pdf", "docx")
    )
    assert {r.format for r in rows} == {"pdf", "docx"}
    for r in rows:
        assert r.size_bytes > 0
        assert content_hash(store.get(r.object_key)) == r.content_hash


def test_fetch_render_regenerates_when_object_missing(db: Session) -> None:
    store = InMemoryObjectStore()
    artifact, version = _seed_version(db)
    row = render_store.ensure_render(db, store, version=version, title=artifact.title, fmt="pdf")
    store.delete(row.object_key)  # simulate lost object
    data, mime, chash = render_store.fetch_render_bytes(
        db, store, version=version, title=artifact.title, fmt="pdf"
    )
    assert data[:4] == b"%PDF"
    assert chash == row.content_hash
    assert store.exists(row.object_key)
