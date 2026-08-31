"""Unit tests: backend abstraction seam + deterministic evaluation (fallback).

- NativeMemoryBackend structurally satisfies the MemoryBackend Protocol.
- Mem0Backend documents the seam by raising the typed not-implemented error.
- The retrieval evaluation runs end-to-end on SQLite (cosine fallback) with
  the same corpus/queries the pgvector integration test uses.
"""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.memory.embedding import DeterministicEmbedder
from app.memory.errors import MemoryErrorClass, MemorySubsystemError
from app.memory.evaluation import load_corpus, load_queries, run_evaluation
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.retrieval import RetrievalFilters
from app.memory.store import Mem0Backend, MemoryBackend, NativeMemoryBackend
from app.memory.types import Actor, MemoryClass

MEMORY_TABLES = [
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
]

EMBEDDER = DeterministicEmbedder()


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in MEMORY_TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def backend(db: Session) -> NativeMemoryBackend:
    import contextlib

    @contextlib.contextmanager
    def scope():
        yield db

    return NativeMemoryBackend(scope, EMBEDDER)


def test_native_backend_satisfies_protocol(backend: NativeMemoryBackend) -> None:
    assert isinstance(backend, MemoryBackend)


def test_native_backend_roundtrip(backend: NativeMemoryBackend) -> None:
    result = backend.remember(
        text="I prefer the compact task list view.",
        memory_class=MemoryClass.PREFERENCE,
        key="tasklist.view",
        value={"value": "compact"},
    )
    assert result.action == "created"
    found = backend.search(
        query="compact task list",
        filters=RetrievalFilters(memory_class=MemoryClass.PREFERENCE.value),
    )
    assert found and found[0]["memory_id"] == str(result.memory_id)
    assert "score" in found[0] and "score_components" in found[0]
    inspection = backend.inspect(result.memory_id)
    assert inspection["provenance"]["origin"] == "owner_statement"
    forgotten = backend.forget(result.memory_id, actor=Actor.OWNER)
    assert forgotten["forgotten"] is True


def test_mem0_stub_raises_typed_not_implemented() -> None:
    stub = Mem0Backend()
    assert isinstance(stub, MemoryBackend)
    for call in (
        lambda: stub.search(),
        lambda: stub.inspect(uuid.uuid4()),
        lambda: stub.forget(uuid.uuid4()),
        lambda: stub.sweep_expired(),
    ):
        with pytest.raises(MemorySubsystemError) as excinfo:
            call()
        assert excinfo.value.error_class == MemoryErrorClass.BACKEND_NOT_IMPLEMENTED
        assert excinfo.value.details["backend"] == "mem0"


# ------------------------------------------------------------------ evaluation


def test_eval_corpus_shape() -> None:
    corpus = load_corpus()
    queries = load_queries()
    assert len(corpus) >= 40
    classes = {r["memory_class"] for r in corpus}
    assert classes == {
        "preference", "episodic", "project", "semantic", "procedural", "voice_preference"
    }
    projects = {r["project"] for r in corpus if r.get("project")}
    assert len(projects) >= 2
    occurred = [r["occurred_at"] for r in corpus if r.get("occurred_at")]
    assert len(set(o[:7] for o in occurred)) >= 4  # temporal spread over months
    assert len(queries) >= 15
    assert all(q["expected"] for q in queries)


def test_eval_runs_deterministically_on_fallback(db: Session) -> None:
    report_1 = run_evaluation(db, EMBEDDER)
    report_2 = run_evaluation(db, EMBEDDER)  # reseed is idempotent
    assert report_1["overall"] == report_2["overall"]
    assert report_1["overall"]["mean_precision_at_k"] >= 0.75
    assert report_1["overall"]["mean_hit_rate"] >= 0.9
    assert report_1["overall"]["cross_project_contamination"] == 0.0
