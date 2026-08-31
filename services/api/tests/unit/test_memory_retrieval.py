"""Unit tests: hybrid retrieval semantics on the SQLite cosine fallback.

Covers project isolation, temporal windows, stage floors, superseded
exclusion, explicit boost and deterministic ordering. The pgvector/hnsw path
is exercised by the integration suite.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.memory import service
from app.memory.embedding import DeterministicEmbedder
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.policy import Observation
from app.memory.retrieval import (
    RetrievalFilters,
    hybrid_search,
    semantic_candidates,
)
from app.memory.service import MemoryLinks
from app.memory.types import Actor, MemoryClass, WriteStage

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


def _observe(db: Session, text: str, **kwargs):
    links = kwargs.pop("links", None)
    return service.record_observation(db, EMBEDDER, Observation(text=text, **kwargs), links)


def test_semantic_fallback_ranks_related_text_first(db: Session) -> None:
    hit = _observe(
        db,
        "Owner always compiles the Turkish narration benchmark before release.",
        memory_class=MemoryClass.SEMANTIC,
    )
    _observe(
        db,
        "The garden sprinkler never runs on rainy days.",
        memory_class=MemoryClass.SEMANTIC,
    )
    ranked = semantic_candidates(
        db, EMBEDDER, "Turkish narration benchmark", RetrievalFilters()
    )
    assert ranked[0][0].id == hit.memory_id
    assert ranked[0][1] > ranked[1][1]


def test_project_isolation_query_scoped_to_a_never_returns_b(db: Session) -> None:
    """Behavior: project A queries must not return project B memories."""
    project_a = service.create_entity(db, kind="project", name="atlas")
    project_b = service.create_entity(db, kind="project", name="borealis")
    _observe(
        db,
        "Atlas deploy pipeline always signs the agent binary.",
        memory_class=MemoryClass.PROJECT,
        key="atlas.deploy",
        links=MemoryLinks(project_id=project_a.id),
    )
    in_b = _observe(
        db,
        "Borealis deploy pipeline always exports the invoice data first.",
        memory_class=MemoryClass.PROJECT,
        key="borealis.deploy",
        links=MemoryLinks(project_id=project_b.id),
    )
    hits = hybrid_search(
        db,
        EMBEDDER,
        "deploy pipeline",
        RetrievalFilters(project_id=project_b.id),
    )
    assert hits, "scoped query must still find its own project"
    assert {h.memory.id for h in hits} == {in_b.memory_id}
    assert all(h.memory.project_id == project_b.id for h in hits)


def test_episodic_time_window_search(db: Session) -> None:
    """Behavior: occurred_at windows filter episodic memories."""
    may = _observe(
        db,
        "Invoice import failed with duplicate rows.",
        memory_class=MemoryClass.EPISODIC,
        links=MemoryLinks(occurred_at=datetime(2026, 5, 18, tzinfo=UTC)),
    )
    _observe(
        db,
        "Invoice import ran clean after the fix.",
        memory_class=MemoryClass.EPISODIC,
        links=MemoryLinks(occurred_at=datetime(2026, 7, 2, tzinfo=UTC)),
    )
    hits = hybrid_search(
        db,
        EMBEDDER,
        "invoice import",
        RetrievalFilters(
            memory_class=MemoryClass.EPISODIC.value,
            occurred_from=datetime(2026, 5, 1, tzinfo=UTC),
            occurred_to=datetime(2026, 5, 31, tzinfo=UTC),
        ),
    )
    assert [h.memory.id for h in hits] == [may.memory_id]


def test_superseded_rows_always_excluded(db: Session) -> None:
    old = service.remember_explicit(
        db,
        EMBEDDER,
        text="I prefer the blue dashboard layout.",
        memory_class=MemoryClass.PREFERENCE,
        key="dashboard.layout",
        value={"value": "blue"},
    )
    new = service.supersede_memory(
        db, EMBEDDER, old.memory_id, actor=Actor.OWNER,
        text="I prefer the compact dashboard layout.", value={"value": "compact"},
    )
    for query in ("dashboard layout", None):
        hits = hybrid_search(
            db, EMBEDDER, query, RetrievalFilters(memory_class=MemoryClass.PREFERENCE.value)
        )
        ids = {h.memory.id for h in hits}
        assert old.memory_id not in ids
        assert new.id in ids


def test_stage_min_filter(db: Session) -> None:
    weak = _observe(db, "Owner glanced at the metrics panel this morning.")
    strong = service.remember_explicit(
        db, EMBEDDER, text="I prefer metrics panels pinned to the sidebar.",
        memory_class=MemoryClass.PREFERENCE,
    )
    assert weak.stage == WriteStage.SESSION
    hits = hybrid_search(
        db, EMBEDDER, None, RetrievalFilters(stage_min=WriteStage.CANDIDATE.value)
    )
    ids = {h.memory.id for h in hits}
    assert strong.memory_id in ids
    assert weak.memory_id not in ids


def test_hybrid_rerank_boosts_explicit_over_similar_inferred(db: Session) -> None:
    """Same lexical content: the explicit durable row outranks the inferred
    session row through the explicit + confidence weights."""
    inferred = _observe(db, "Owner skimmed a few release notes with breakfast coffee once.")
    explicit = service.remember_explicit(
        db, EMBEDDER, text="Owner studies the full release notes during breakfast every day.",
        memory_class=MemoryClass.SEMANTIC,
    )
    hits = hybrid_search(db, EMBEDDER, "release notes at breakfast", RetrievalFilters())
    assert hits[0].memory.id == explicit.memory_id
    assert {h.memory.id for h in hits} >= {explicit.memory_id, inferred.memory_id}
    top = hits[0].components
    assert top["explicit"] > 0.0


def test_search_results_are_deterministic(db: Session) -> None:
    for i in range(5):
        _observe(
            db,
            f"Owner archived report number {i} into the project vault.",
            memory_class=MemoryClass.SEMANTIC,
        )
    filters = RetrievalFilters()
    first = [h.memory.id for h in hybrid_search(db, EMBEDDER, "archived report", filters)]
    second = [h.memory.id for h in hybrid_search(db, EMBEDDER, "archived report", filters)]
    assert first == second
