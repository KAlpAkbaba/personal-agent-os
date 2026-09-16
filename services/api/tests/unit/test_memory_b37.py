"""B37 - semantic memory and the memory surface (req 51, 53, 54, 57-60, 149).

Measured before: the only embedder was the seeded n-gram hash (not semantic), no provider
could be chosen by configuration, ``reindex`` rebuilt every row with no notion of "missing
for this model", the memory page listed rows with nothing the owner could press, and a
question over a document was answered by word overlap only, with no search across the
indexed documents.

Each seam proven by executing it: the OpenAI provider against a recorded transport (the
request it sends, the vector it normalises, the key it never leaks), the selection matrix,
the coverage and the missing-only re-index on the real backend, the routes through the
real application object, and semantic document search over indexed rows.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.documents import semantic
from app.documents.retrieval import top_k
from app.main import create_app
from app.memory import lifecycle, providers
from app.memory.embedding import DeterministicEmbedder, cosine_similarity
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
from app.memory.runtime import MemoryRuntime
from app.memory.store import NativeMemoryBackend
from app.memory.types import EMBEDDING_DIM, Actor, MemoryClass
from tests.identity_support import authenticate

MEMORY_TABLES = [
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
]


# ------------------------------------------------------------- the provider (51)


def _transport(vector: list[float] | None = None, status: int = 200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "nope"}})
        vec = vector if vector is not None else [1.0] + [0.0] * (EMBEDDING_DIM - 1)
        return httpx.Response(200, json={"data": [{"embedding": vec}], "usage": {}})

    return httpx.MockTransport(handler)


def test_the_openai_embedder_asks_for_the_index_width_normalises_and_caches() -> None:
    seen: list[httpx.Request] = []
    raw = [3.0] + [4.0] + [0.0] * (EMBEDDING_DIM - 2)
    embedder = providers.OpenAIEmbedder(
        api_key="sk-test-key-000", transport=_transport(raw, seen=seen)
    )
    assert embedder.model_id == "openai-text-embedding-3-small"
    assert embedder.dim == EMBEDDING_DIM
    vector = embedder.embed("Kahveyi sütsüz severim")
    body = json.loads(seen[0].content)
    assert body == {
        "model": "text-embedding-3-small",
        "input": "Kahveyi sütsüz severim",
        "dimensions": EMBEDDING_DIM,
    }
    assert seen[0].headers["authorization"] == "Bearer sk-test-key-000"
    assert vector[:2] == pytest.approx([0.6, 0.8]) and len(vector) == EMBEDDING_DIM
    # The same text is not asked twice.
    assert embedder.embed("Kahveyi sütsüz severim") == vector
    assert len(seen) == 1 and embedder.calls == 1


def test_a_provider_failure_names_the_status_and_never_the_key_or_the_text() -> None:
    embedder = providers.OpenAIEmbedder(api_key="sk-secret-xyz", transport=_transport(status=429))
    with pytest.raises(providers.EmbeddingProviderError) as failed:
        embedder.embed("gizli metin")
    assert "429" in str(failed.value)
    assert "sk-secret" not in str(failed.value) and "gizli" not in str(failed.value)
    short = providers.OpenAIEmbedder(api_key="k", transport=_transport([0.5, 0.5]))
    with pytest.raises(providers.EmbeddingProviderError, match="dimensions"):
        short.embed("x")
    with pytest.raises(providers.EmbeddingProviderError):
        providers.OpenAIEmbedder(api_key="")


# ------------------------------------------------------------- the selection (53)


@pytest.mark.parametrize(
    ("provider", "key", "active", "semantic", "reason_part"),
    [
        ("deterministic", "sk-x", "deterministic", False, None),
        ("auto", "", "deterministic", False, "no dedicated OpenAI key"),
        ("openai", "", "deterministic", False, "no OpenAI key"),
        ("auto", "sk-x", "openai", True, None),
        ("openai", "sk-x", "openai", True, None),
        ("nonsense", "sk-x", "deterministic", False, "unknown provider"),
    ],
)
def test_the_provider_is_chosen_by_configuration_and_every_fallback_has_a_reason(
    provider, key, active, semantic, reason_part
) -> None:
    settings = Settings(
        _env_file=None,
        memory_embedding_provider=provider,
        openai_api_key=key,
        voice_openai_api_key="",
    )
    embedder, report = providers.build_embedder(settings, transport=_transport())
    assert report.active == active and report.semantic is semantic
    assert report.requested == provider
    if reason_part is None:
        assert report.fallback_reason is None
    else:
        assert reason_part in (report.fallback_reason or "")
    assert embedder.model_id == report.model_id
    # The voice key the owner already installed serves an EXPLICIT openai, never auto:
    # a memory write must not start billing the voice key behind the owner's back.
    if provider in ("auto", "openai") and not key:
        _e, again = providers.build_embedder(
            Settings(
                _env_file=None, memory_embedding_provider=provider, voice_openai_api_key="sk-v"
            ),
            transport=_transport(),
        )
        assert again.active == ("openai" if provider == "openai" else "deterministic")


def test_the_runtime_reports_its_provider_and_the_health_check_says_semantic_or_not() -> None:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    runtime = MemoryRuntime(
        Settings(_env_file=None, memory_embedding_provider="deterministic"), engine=engine
    )
    health = runtime.health_check()
    assert health["embedder"]["model_id"] == "deterministic-ngram"
    assert health["embedder"]["provider"] == "deterministic"
    assert health["embedder"]["semantic"] is False
    injected = MemoryRuntime(
        Settings(_env_file=None), engine=engine, embedder=DeterministicEmbedder()
    )
    assert injected.embedder_report.active == "deterministic"


# ------------------------------------------------------------- coverage and re-index (54)


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


def _backend(db: Session, embedder) -> NativeMemoryBackend:
    @contextlib.contextmanager
    def scope():
        yield db

    return NativeMemoryBackend(scope, embedder)


class _OtherEmbedder(DeterministicEmbedder):
    model_id = "other-model"
    model_version = "2"


def test_coverage_counts_the_active_models_rows_and_reindex_missing_fills_only_the_gaps(
    db: Session,
) -> None:
    first = _backend(db, DeterministicEmbedder())
    for text in (
        "Kahveyi sütsüz severim",
        "Toplantı notlarını Belgeler'e koy",
        "Kedimin adı Duman",
    ):
        first.remember(text=text, memory_class=MemoryClass.PREFERENCE, source={"kind": "test"})
    before = lifecycle.embedding_coverage(db, DeterministicEmbedder())
    assert before["memories"] == 3 and before["embedded"] == 3 and before["missing"] == 0
    other = _OtherEmbedder()
    switched = lifecycle.embedding_coverage(db, other)
    assert switched["memories"] == 3 and switched["embedded"] == 0 and switched["missing"] == 3
    assert switched["models"] == {"deterministic-ngram": 3}
    # One memory is written under the new model, the two others are the gaps.
    second = _backend(db, other)
    second.remember(
        text="Sabahları erken kalkarım",
        memory_class=MemoryClass.PREFERENCE,
        source={"kind": "test"},
    )
    assert lifecycle.embedding_coverage(db, other)["missing"] == 3
    filled = lifecycle.reindex_missing(db, other)
    assert filled == 3
    after = lifecycle.embedding_coverage(db, other)
    assert after["missing"] == 0 and after["embedded"] == 4
    assert after["models"] == {"deterministic-ngram": 3, "other-model": 4}
    # A second pass changes nothing (the derivation does not feed on itself).
    assert lifecycle.reindex_missing(db, other) == 0
    # The old model's rows are untouched by the new model's index.
    assert (
        db.scalar(
            select(MemoryEmbedding)
            .where(MemoryEmbedding.model_id == "deterministic-ngram")
            .limit(1)
        )
        is not None
    )
    # And semantic retrieval under the new model finds the new row by meaning of its words.
    hits = second.search(query="kedi", filters=RetrievalFilters(), k=3)
    assert any("Duman" in h["text"] for h in hits)


def test_unpin_returns_a_memory_to_standard_retention(db: Session) -> None:
    backend = _backend(db, DeterministicEmbedder())
    row = backend.remember(
        text="Anahtarlar kapının yanında",
        memory_class=MemoryClass.SEMANTIC,
        source={"kind": "test"},
    )
    memory_id = row.memory_id
    pinned = backend.pin(memory_id, actor=Actor.OWNER)
    assert pinned["pinned"] is True and pinned["retention_class"] == "pinned"
    unpinned = backend.unpin(memory_id, actor=Actor.OWNER)
    assert unpinned["pinned"] is False and unpinned["retention_class"] == "standard"
    actions = [e["action"] for e in backend.audit_events(limit=10)]
    assert "unpinned" in actions


# ------------------------------------------------------------- the routes (54, 57-60)


@pytest.fixture()
def client() -> TestClient:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in MEMORY_TABLES:
        table.create(engine)
    settings = Settings(_env_file=None, memory_embedding_provider="deterministic")
    app = create_app(settings)
    app.state.memory = MemoryRuntime(settings, engine=engine)
    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    yield test_client
    engine.dispose()


def test_the_embedding_status_reindex_and_unpin_routes(client: TestClient) -> None:
    created = client.post(
        "/v1/memory/remember", json={"text": "Çayı demli severim", "memory_class": "preference"}
    )
    assert created.status_code == 201, created.text
    memory_id = created.json()["memory_id"]

    status = client.get("/v1/memory/embedding")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["provider"]["active"] == "deterministic" and body["provider"]["semantic"] is False
    assert body["coverage"] == {
        "model_id": "deterministic-ngram",
        "memories": 1,
        "embedded": 1,
        "missing": 0,
        "models": {"deterministic-ngram": 1},
    }

    reindexed = client.post("/v1/memory/reindex", json={"only_missing": True})
    assert reindexed.status_code == 200 and reindexed.json() == {
        "rows": 0,
        "model_id": "deterministic-ngram",
        "only_missing": True,
    }
    everything = client.post("/v1/memory/reindex", json={"only_missing": False})
    assert everything.json()["rows"] == 1

    pinned = client.post(f"/v1/memory/{memory_id}/pin")
    assert pinned.status_code == 200 and pinned.json()["pinned"] is True
    unpinned = client.post(f"/v1/memory/{memory_id}/unpin")
    assert unpinned.status_code == 200 and unpinned.json()["pinned"] is False
    missing = client.post(f"/v1/memory/{uuid.uuid4()}/unpin")
    assert missing.status_code == 404


def test_the_memory_routes_require_an_owner_session() -> None:
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as anonymous:
        assert anonymous.get("/v1/memory/embedding").status_code == 401
        assert anonymous.post("/v1/memory/reindex", json={}).status_code == 401
        assert anonymous.post(f"/v1/memory/{uuid.uuid4()}/unpin").status_code == 401


# ------------------------------------------------------------- semantic document search (149)


class _Row:
    def __init__(self, name: str, blocks: list[dict]) -> None:
        self.doc_id = f"doc:{name}"
        self.file_id = f"file:{name}"
        self.name = name
        self.path = f"C:/Users/owner/Documents/{name}"
        self.kind = "txt"
        self.blocks = blocks


def test_blocks_rank_by_words_and_by_meaning_with_the_components_named() -> None:
    embedder = DeterministicEmbedder()
    rows = [
        _Row(
            "bütçe.txt",
            [
                {"ref": "p1", "text": "2026 bütçesi: pazarlama harcaması 40 bin"},
                {"ref": "p2", "text": "Personel maaşları ocakta ödenir"},
            ],
        ),
        _Row("notlar.txt", [{"ref": "p1", "text": "Toplantı: pazarlamanın bütçesi görüşüldü"}]),
    ]
    hits = semantic.search_rows("pazarlama bütçesi", rows, embedder, k=3)
    assert hits and hits[0].components["lexical"] > 0 and hits[0].components["semantic"] > 0
    assert {h.name for h in hits[:2]} == {"bütçe.txt", "notlar.txt"}
    assert all(h.preview for h in hits)
    # With no embedder the ranking is lexical only and says so in its components.
    lexical = semantic.search_rows("pazarlama bütçesi", rows, None, k=3)
    assert all(h.components["semantic"] == 0.0 for h in lexical)
    # A block with no matching word can still rank when its meaning is close under the
    # embedder (the same text differing by a suffix the tokenizer normalises differently).
    close = semantic.score_blocks(
        "maaş ödemesi", [{"ref": "p2", "text": "Personel maaşları ocakta ödenir"}], embedder
    )
    assert close and close[0][2]["semantic"] > 0
    # The answer path's own ranker takes the embedder as a reranker without losing a literal hit.
    ranked = top_k(rows[0].blocks, "pazarlama harcaması", kind="txt", k=2, embedder=embedder)
    assert ranked[0].ref == "p1"
    # The embedder CHANGES the score (a mutation that ignores it turns this red), and the
    # literal-word order is kept.
    plain = top_k(rows[0].blocks, "pazarlama harcaması", kind="txt", k=2)
    assert ranked[0].score > plain[0].score
    assert cosine_similarity(embedder.embed("a"), embedder.embed("a")) == pytest.approx(1.0)


def test_the_document_search_route_over_the_indexed_rows() -> None:
    from tests.voice_corpus.corpus import CTX_DOCUMENT_FOCUSED
    from tests.voice_corpus.harness import build_harness

    h = build_harness()
    h.seed(CTX_DOCUMENT_FOCUSED)
    found = h.client.get("/v1/documents/search", params={"q": "rapor", "limit": 3})
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["query"] == "rapor" and body["embedder"]["model_id"]
    assert body["hits"] and body["hits"][0]["doc_id"] and body["hits"][0]["ref"]
    assert "components" in body["hits"][0]
    empty = h.client.get("/v1/documents/search", params={"q": "zzzz-hiçbir-şey-eşleşmez-qqq"})
    assert empty.status_code == 200 and empty.json()["hits"] == []
    assert h.client.get("/v1/documents/search").status_code == 422
