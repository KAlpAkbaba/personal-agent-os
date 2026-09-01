"""M5 integration: memory persistence against the real Postgres + pgvector.

Requires the compose stack and the schema at head (0005_memory). Proves the
invariants that need a real database:
- durable memories cross session/client/device boundaries;
- forgetting removes the pgvector row too (semantic index cannot resurrect);
- project isolation holds on the pgvector retrieval path;
- the hnsw cosine path ranks lexically-related text first.

Rows are namespaced with per-run unique tokens/keys so reruns never collide.
"""

import uuid

import pytest
from sqlalchemy import text as sql_text

from app.config import Settings
from app.memory import service
from app.memory.embedding import DeterministicEmbedder
from app.memory.policy import Observation
from app.memory.retrieval import RetrievalFilters, hybrid_search, semantic_candidates
from app.memory.runtime import MemoryRuntime
from app.memory.service import MemoryLinks
from app.memory.types import Actor, MemoryClass
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

EMBEDDER = DeterministicEmbedder()


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


def _token() -> str:
    return uuid.uuid4().hex[:10]


def test_preference_survives_new_conversation_and_client(settings: Settings) -> None:
    """Owner teaches a preference through one client; a completely fresh
    client/runtime (new conversation) retrieves it."""
    token = _token()
    with owner_client(settings) as first_client:
        created = first_client.post(
            "/v1/memory/remember",
            json={
                "text": f"Bundan sonra {token} panosunu koyu temada göster.",
                "memory_class": "preference",
                "key": f"test.pref.{token}",
                "value": {"value": "dark"},
            },
        )
        assert created.status_code == 201
        memory_id = created.json()["memory_id"]

    # Fresh app = fresh engine/session/state: nothing carried over in memory.
    with owner_client(settings) as second_client:
        found = second_client.get(
            "/v1/memory/search",
            params={"q": f"{token} koyu tema", "memory_class": "preference"},
        )
        assert found.status_code == 200
        results = found.json()["results"]
        assert results and results[0]["memory_id"] == memory_id
        assert results[0]["stage"] == "durable"

        # cleanup (also exercises DELETE over HTTP)
        assert second_client.delete(f"/v1/memory/{memory_id}").status_code == 200


def test_deleted_memory_absent_from_semantic_and_structured(settings: Settings) -> None:
    """Forget must remove the pgvector row as well: semantic retrieval,
    structured retrieval AND the raw memory_embeddings table all come up
    empty (MEMORY_SPEC §6: deletion propagates to embeddings/indexes)."""
    token = _token()
    runtime = MemoryRuntime(settings)
    with runtime.session() as session:
        result = service.remember_explicit(
            session,
            EMBEDDER,
            text=f"Owner keeps the {token} telescope in the attic closet.",
            memory_class=MemoryClass.SEMANTIC,
        )
        memory_id = result.memory_id
        # Present in the vector index and retrievable via pgvector.
        count = session.execute(
            sql_text("SELECT count(*) FROM memory_embeddings WHERE memory_id = :m"),
            {"m": str(memory_id)},
        ).scalar_one()
        assert count == 1
        hits = semantic_candidates(
            session, EMBEDDER, f"{token} telescope attic", RetrievalFilters()
        )
        assert hits and hits[0][0].id == memory_id

    fresh = MemoryRuntime(settings)
    with fresh.session() as session:
        service.forget_memory(session, memory_id, actor=Actor.OWNER)

    verify = MemoryRuntime(settings)
    with verify.session() as session:
        semantic = semantic_candidates(
            session, EMBEDDER, f"{token} telescope attic", RetrievalFilters()
        )
        assert all(m.id != memory_id for m, _sim in semantic)
        structured = hybrid_search(
            session, EMBEDDER, None,
            RetrievalFilters(memory_class=MemoryClass.SEMANTIC.value), k=50,
        )
        assert all(h.memory.id != memory_id for h in structured)
        count = session.execute(
            sql_text("SELECT count(*) FROM memory_embeddings WHERE memory_id = :m"),
            {"m": str(memory_id)},
        ).scalar_one()
        assert count == 0, "vector row must be gone after forget"


def test_project_isolation_on_pgvector_path(settings: Settings) -> None:
    token = _token()
    runtime = MemoryRuntime(settings)
    with runtime.session() as session:
        project_a = service.create_entity(session, kind="project", name=f"iso-a-{token}")
        project_b = service.create_entity(session, kind="project", name=f"iso-b-{token}")
        in_a = service.record_observation(
            session,
            EMBEDDER,
            Observation(
                text=f"The {token} pipeline always deploys from branch alpha.",
                memory_class=MemoryClass.PROJECT,
                key=f"{token}.deploy.a",
            ),
            MemoryLinks(project_id=project_a.id),
        )
        in_b = service.record_observation(
            session,
            EMBEDDER,
            Observation(
                text=f"The {token} pipeline always deploys from branch beta.",
                memory_class=MemoryClass.PROJECT,
                key=f"{token}.deploy.b",
            ),
            MemoryLinks(project_id=project_b.id),
        )
        hits = hybrid_search(
            session, EMBEDDER, f"{token} pipeline deploys",
            RetrievalFilters(project_id=project_a.id),
        )
        assert hits, "project-scoped query must find its own rows"
        assert {h.memory.id for h in hits} == {in_a.memory_id}
        assert all(h.memory.project_id == project_a.id for h in hits)
        # cleanup
        service.forget_memory(session, in_a.memory_id, actor=Actor.OWNER)
        service.forget_memory(session, in_b.memory_id, actor=Actor.OWNER)


def test_memory_crosses_device_and_session_boundaries(settings: Settings) -> None:
    """A memory written under device A / one session is retrievable from a
    fresh runtime with no device filter (cross-device continuity)."""
    token = _token()
    device_a = uuid.uuid4()
    writer = MemoryRuntime(settings)
    with writer.session() as session:
        result = service.record_observation(
            session,
            EMBEDDER,
            Observation(
                text=f"Owner always exports the {token} ledger before travelling.",
                memory_class=MemoryClass.SEMANTIC,
                key=f"test.ledger.{token}",
            ),
            MemoryLinks(device_id=device_a),
        )
    reader = MemoryRuntime(settings)
    with reader.session() as session:
        hits = hybrid_search(
            session, EMBEDDER, f"{token} ledger export", RetrievalFilters()
        )
        assert hits and hits[0].memory.id == result.memory_id
        assert hits[0].memory.device_id == device_a
        service.forget_memory(session, result.memory_id, actor=Actor.OWNER)


def test_audit_trail_persisted_with_trace_ids(settings: Settings) -> None:
    with owner_client(settings) as client:
        created = client.post(
            "/v1/memory/remember",
            json={"text": f"Remember the {_token()} maintenance window on Sundays."},
            headers={"X-Trace-Id": "trace-memory-it"},
        )
        assert created.status_code == 201
        memory_id = created.json()["memory_id"]
        inspected = client.get(f"/v1/memory/{memory_id}").json()
        created_events = [a for a in inspected["audit"] if a["action"] == "created"]
        assert created_events and created_events[0]["trace_id"] == "trace-memory-it"
        assert client.delete(f"/v1/memory/{memory_id}").status_code == 200
