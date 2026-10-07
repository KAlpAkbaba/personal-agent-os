"""ADR-0206: a local semantic reranker over ``hybrid_search``.

What these tests pin, and what each one would catch if it went missing:

* OFF IS THE SAME FUNCTION. With no reranker, ``hybrid_search`` returns the ids, the
  scores and the components it returned before this change, float for float - checked
  against the documented formula computed independently here, so a refactor that "only
  moved code" cannot shift a score in the fifteenth decimal and call it nothing.
* On, the cross-encoder's answer stands where the cosine stood, for the top-K rows only;
  the other signals keep their weight, so an explicit, recent memory is not thrown away
  by a model's opinion; rows below the top-K keep their place after the reranked ones.
* A reranker that fails costs the turn the rerank and nothing else.
* ``build_reranker`` never raises; a model that is not on the host is ``none`` with the
  reason on the health check; the API process loads with ``local_files_only`` and so can
  never start a download; an unmeasured model name is refused.

The model is a fake here. No test downloads a model or dials the network; the Turkish
quality of the real models is scripts/core/bench-memory-rerank.py and its evidence file.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.memory import lexical, rerank, retrieval
from app.memory.embedding import cosine_similarity
from app.memory.models import Base, Memory, MemoryEmbedding
from app.memory.rerank import (
    CUSTOM_MODELS,
    DEFAULT_LOCAL_MODEL,
    LocalReranker,
    RerankProviderError,
    build_reranker,
    squash,
)
from app.memory.retrieval import (
    RECENCY_HALF_SCALE_DAYS,
    W_CONFIDENCE,
    W_EXPLICIT,
    W_PROJECT,
    W_RECENCY,
    W_RERANK,
    W_SEMANTIC,
    RetrievalFilters,
    hybrid_search,
)
from app.memory.runtime import MemoryRuntime
from app.memory.types import MemoryClass, MemoryStatus, WriteStage

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
QUERY = "raporları hangi dilde okusun"

# ------------------------------------------------------------------ fakes


class _FakeReranker:
    """Scores by a table: the text that contains a key gets that key's score."""

    model_id = "fake-reranker"

    def __init__(self, table: dict[str, float], default: float = 0.0) -> None:
        self.table = table
        self.default = default
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        self.calls.append((query, list(documents)))
        return [
            next((v for key, v in self.table.items() if key in text), self.default)
            for text in documents
        ]


class _Broken:
    model_id = "broken"

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        raise RuntimeError("the text of a memory must never travel in this message")


class _WrongCount:
    model_id = "wrong-count"

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        return [0.9]


class _FakeCrossEncoder:
    def __init__(self, logits: dict[str, float] | None = None) -> None:
        self.logits = logits or {}

    def rerank(self, query: str, documents: Iterable[str], **_: Any) -> Iterable[float]:
        for text in documents:
            yield self.logits.get(text, 0.0)


def _factory(loads: list[tuple[str, str | None, bool]] | None = None, **kwargs: Any):
    def factory(name: str, cache_dir: str | None, local_only: bool) -> _FakeCrossEncoder:
        if loads is not None:
            loads.append((name, cache_dir, local_only))
        return _FakeCrossEncoder(**kwargs)

    return factory


def _raising(exc: Exception):
    def factory(*_: Any) -> Any:
        raise exc

    return factory


# ------------------------------------------------------------------ a small memory


def _runtime(**kwargs: Any) -> MemoryRuntime:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    reranker = kwargs.pop("reranker", None)
    factory = kwargs.pop("rerank_model_factory", None)
    return MemoryRuntime(
        Settings(_env_file=None, memory_embedding_provider="deterministic", **kwargs),
        engine=engine,
        reranker=reranker,
        rerank_model_factory=factory,
    )


ROWS: list[tuple[str, bool, float, int]] = [
    # text, explicit, confidence, age in days
    ("Sahip araştırma raporlarının her zaman Türkçe okunmasını istiyor", False, 0.6, 40),
    ("Araştırma tamamlandı: Türkiye'de elektrikli araç satışları raporları", True, 1.0, 1),
    ("Sahip raporların uzun halinin zorla okunmasını istemiyor", True, 0.9, 3),
    ("Yabancı kaynaklı haberler hangi dilde olursa olsun özetlensin", False, 0.7, 10),
    ("Sahip sabahları kahve içmeyi sever", True, 1.0, 2),
]


def _seed(runtime: MemoryRuntime, rows: list[tuple[str, bool, float, int]] = ROWS) -> None:
    with runtime.session() as session:
        for index, (text, explicit, confidence, age_days) in enumerate(rows):
            memory = Memory(
                id=uuid.UUID(int=index + 1),
                memory_class=MemoryClass.PREFERENCE.value,
                key=f"k-{index}",
                text=text,
                value_json={},
                stage=WriteStage.DURABLE.value,
                status=MemoryStatus.ACTIVE.value,
                explicit=explicit,
                pinned=False,
                confidence=confidence,
                evidence_count=1,
                occurred_at=NOW - timedelta(days=age_days),
                created_at=NOW - timedelta(days=age_days),
                updated_at=NOW - timedelta(days=age_days),
            )
            session.add(memory)
            session.add(
                MemoryEmbedding(
                    id=uuid.uuid4(),
                    memory_id=memory.id,
                    model_id=runtime.embedder.model_id,
                    model_version=runtime.embedder.model_version,
                    dim=runtime.embedder.dim,
                    embedding=runtime.embedder.embed(text),
                )
            )
        session.commit()


def _search(runtime: MemoryRuntime, **kwargs: Any) -> list[retrieval.ScoredMemory]:
    with runtime.session() as session:
        hits = hybrid_search(
            session,
            runtime.embedder,
            kwargs.pop("query", QUERY),
            RetrievalFilters(),
            now=NOW,
            **kwargs,
        )
        for hit in hits:
            session.expunge(hit.memory)
        return hits


def _documented_score(
    runtime: MemoryRuntime, row: tuple[str, bool, float, int]
) -> dict[str, float]:
    """The formula in retrieval.py's docstring, computed here and nowhere near its code."""
    text, explicit, confidence, age_days = row
    similarity = cosine_similarity(runtime.embedder.embed(QUERY), runtime.embedder.embed(text))
    return {
        "semantic": W_SEMANTIC * similarity,
        "recency": W_RECENCY * (1.0 / (1.0 + float(age_days) / RECENCY_HALF_SCALE_DAYS)),
        "confidence": W_CONFIDENCE * confidence,
        "explicit": W_EXPLICIT * (1.0 if explicit else 0.0),
        "project": W_PROJECT * 0.0,
    }


# ------------------------------------------------------------------ off is the same function


def test_off_is_the_same_function_float_for_float(monkeypatch: pytest.MonkeyPatch) -> None:
    # The documented formula is the 'like' leg's (a word-only hit scores 0.0 for meaning);
    # the trgm default fuses ranks instead (test_memory_lexical.py pins that).
    monkeypatch.setenv(lexical.MODE_ENV, "like")
    runtime = _runtime()
    _seed(runtime)
    assert runtime.reranker is None

    hits = _search(runtime)

    expected = []
    for index, row in enumerate(ROWS):
        components = _documented_score(runtime, row)
        expected.append((str(uuid.UUID(int=index + 1)), sum(components.values()), components))
    expected.sort(key=lambda item: (-item[1], item[0]))

    assert [str(h.memory.id) for h in hits] == [e[0] for e in expected]
    for hit, (_, score, components) in zip(hits, expected, strict=True):
        assert hit.score == score  # exact: not approx, that is the point
        assert hit.components == components
        assert list(hit.components) == ["semantic", "recency", "confidence", "explicit", "project"]


def test_off_through_the_backend_is_what_a_bare_call_returns() -> None:
    """The route and the voice tool go through objects that now CARRY a reranker slot;
    with the provider off, what they return is what the bare function returns."""
    runtime = _runtime(memory_rerank_provider="none")
    _seed(runtime)
    bare = _search(runtime)
    through = runtime.backend.search(query=QUERY, filters=RetrievalFilters())
    # The backend stamps its own clock, so recency differs by the age of this test; the
    # order and everything that does not read the clock must be identical.
    assert [p["memory_id"] for p in through] == [str(h.memory.id) for h in bare]
    for payload, hit in zip(through, bare, strict=True):
        assert set(payload["score_components"]) == set(hit.components)
        assert "rerank" not in payload["score_components"]
        for name in ("semantic", "confidence", "explicit", "project"):
            assert payload["score_components"][name] == round(hit.components[name], 6)


def test_a_reranker_is_never_asked_when_there_is_nothing_to_ask_about() -> None:
    runtime = _runtime()
    _seed(runtime)
    spy = _FakeReranker({})
    without_query = _search(runtime, query=None, reranker=spy)
    assert spy.calls == []
    assert [h.components for h in without_query] == [
        h.components for h in _search(runtime, query=None)
    ]
    empty = _runtime()
    assert _search(empty, reranker=spy) == [] and spy.calls == []


# ------------------------------------------------------------------ on


def test_the_cross_encoders_answer_stands_where_the_cosine_stood() -> None:
    runtime = _runtime()
    _seed(runtime)
    before = _search(runtime)
    # The right memory is the old, inferred one: the signals alone rank it low.
    right = str(uuid.UUID(int=1))
    assert [str(h.memory.id) for h in before].index(right) > 0

    reranker = _FakeReranker({"her zaman Türkçe": 0.98}, default=0.05)
    after = _search(runtime, reranker=reranker)

    assert str(after[0].memory.id) == right
    assert reranker.calls == [(QUERY, [h.memory.text for h in before])]
    top = after[0]
    assert "semantic" not in top.components
    assert top.components["rerank"] == W_RERANK * 0.98
    documented = _documented_score(runtime, ROWS[0])
    for name in ("recency", "confidence", "explicit", "project"):
        assert top.components[name] == documented[name]
    assert top.score == sum(top.components.values())


def test_the_other_signals_keep_their_weight() -> None:
    """A model's mild preference does not outvote an explicit, fresh, certain memory."""
    runtime = _runtime()
    _seed(runtime)
    reranker = _FakeReranker({"her zaman Türkçe": 0.50, "elektrikli araç": 0.45}, default=0.0)
    after = _search(runtime, reranker=reranker)
    # 0.55*(0.50-0.45) = 0.0275 of rerank advantage against explicit (0.10), confidence
    # (0.15*0.4) and recency: the explicit, day-old row stays first.
    assert str(after[0].memory.id) == str(uuid.UUID(int=2))


def test_only_the_top_k_are_read_and_the_rest_keep_their_place() -> None:
    runtime = _runtime()
    _seed(runtime)
    before = _search(runtime)
    reranker = _FakeReranker({}, default=0.0)
    reranker.table = {before[1].memory.text: 0.99}

    after = _search(runtime, reranker=reranker, rerank_top_k=2)

    assert len(reranker.calls) == 1 and len(reranker.calls[0][1]) == 2
    assert [str(h.memory.id) for h in after[:2]] == [
        str(before[1].memory.id),
        str(before[0].memory.id),
    ]
    assert [(str(h.memory.id), h.score, h.components) for h in after[2:]] == [
        (str(h.memory.id), h.score, h.components) for h in before[2:]
    ]
    assert all("rerank" not in h.components for h in after[2:])


def test_a_score_outside_the_range_is_clamped_not_trusted() -> None:
    runtime = _runtime()
    _seed(runtime)
    after = _search(runtime, reranker=_FakeReranker({"kahve": 7.0}, default=-3.0))
    by_text = {h.memory.text: h for h in after}
    assert by_text["Sahip sabahları kahve içmeyi sever"].components["rerank"] == W_RERANK * 1.0
    assert by_text[ROWS[0][0]].components["rerank"] == 0.0


@pytest.mark.parametrize("broken", [_Broken(), _WrongCount()])
def test_a_reranker_that_fails_costs_the_turn_nothing_but_the_rerank(broken: Any) -> None:
    runtime = _runtime()
    _seed(runtime)
    before = _search(runtime)
    after = _search(runtime, reranker=broken)
    assert [(str(h.memory.id), h.score, h.components) for h in after] == [
        (str(h.memory.id), h.score, h.components) for h in before
    ]


def test_the_backend_and_the_runtime_carry_the_reranker_to_the_search() -> None:
    plain = _runtime()
    _seed(plain)
    third = _search(plain)[2]  # the last row inside a top-K of three

    reranker = _FakeReranker({third.memory.text: 0.98}, default=0.05)
    runtime = _runtime(reranker=reranker, memory_rerank_top_k=3)
    _seed(runtime)
    payloads = runtime.backend.search(query=QUERY, filters=RetrievalFilters())
    assert payloads[0]["memory_id"] == str(third.memory.id)
    assert payloads[0]["score_components"]["rerank"] == round(W_RERANK * 0.98, 6)
    assert len(reranker.calls) == 1 and len(reranker.calls[0][1]) == 3
    assert runtime.health_check()["reranker"]["top_k"] == 3


# ------------------------------------------------------------------ the local model


def test_the_local_reranker_squashes_logits_and_keeps_the_models_order() -> None:
    local = LocalReranker(
        model_name=DEFAULT_LOCAL_MODEL,
        model_factory=_factory(logits={"a": 4.0, "b": -4.0, "c": 0.0}),
    )
    scores = local.score("q", ["a", "b", "c"])
    assert scores == [squash(4.0), squash(-4.0), 0.5]
    assert scores[0] > scores[2] > scores[1] and all(0.0 <= s <= 1.0 for s in scores)
    assert squash(-800.0) == 0.0 and squash(800.0) == 1.0  # no overflow at the edges
    assert local.model_id == f"local-{DEFAULT_LOCAL_MODEL}"
    assert local.score("q", []) == []


def test_the_api_process_loads_only_what_is_already_on_the_host() -> None:
    loads: list[tuple[str, str | None, bool]] = []
    reranker, report = build_reranker(
        Settings(
            _env_file=None,
            memory_rerank_provider="local",
            memory_rerank_cache_dir="/srv/pagentos/var/models",
        ),
        model_factory=_factory(loads),
    )
    assert reranker is not None and report.active == "local"
    assert loads == [(DEFAULT_LOCAL_MODEL, "/srv/pagentos/var/models", True)]
    assert report.as_dict() == {
        "requested": "local",
        "provider": "local",
        "active": True,
        "model_id": f"local-{DEFAULT_LOCAL_MODEL}",
        "top_k": 20,
        "fallback_reason": None,
    }


def test_none_is_the_default_and_loads_nothing() -> None:
    loads: list[tuple[str, str | None, bool]] = []
    reranker, report = build_reranker(Settings(_env_file=None), model_factory=_factory(loads))
    assert reranker is None and loads == []
    assert report.as_dict()["active"] is False and report.fallback_reason is None


@pytest.mark.parametrize(
    ("factory", "fragment"),
    [
        (_raising(ImportError("no module")), "needs the fastembed package"),
        (_raising(FileNotFoundError("/secret/path/model.onnx")), "FileNotFoundError"),
        (_raising(ValueError("Model x is not supported")), "ValueError"),
    ],
)
def test_a_model_that_is_not_there_is_none_with_its_reason_and_never_raises(
    factory: Any, fragment: str
) -> None:
    reranker, report = build_reranker(
        Settings(_env_file=None, memory_rerank_provider="local"), model_factory=factory
    )
    assert reranker is None
    assert report.requested == "local" and report.active == "none"
    assert fragment in str(report.fallback_reason)
    assert "/secret/path" not in str(report.fallback_reason)


def test_an_unknown_provider_is_none_with_its_reason() -> None:
    reranker, report = build_reranker(Settings(_env_file=None, memory_rerank_provider="cloud"))
    assert reranker is None and "unknown provider" in str(report.fallback_reason)


def test_a_model_that_loads_and_cannot_score_is_not_a_reranker() -> None:
    class _Silent:
        def rerank(self, query: str, documents: Iterable[str], **_: Any) -> Iterable[float]:
            return []

    with pytest.raises(RerankProviderError):
        LocalReranker(model_name=DEFAULT_LOCAL_MODEL, model_factory=lambda *_: _Silent())


@pytest.mark.parametrize(("asked", "served"), [(0, 20), (-4, 1), (3, 3), (500, 50)])
def test_top_k_stays_inside_its_bounds(asked: int, served: int) -> None:
    assert rerank.rerank_top_k(Settings(_env_file=None, memory_rerank_top_k=asked)) == served


def test_the_default_model_is_one_this_module_knows_how_to_load() -> None:
    assert DEFAULT_LOCAL_MODEL in CUSTOM_MODELS
    assert CUSTOM_MODELS[DEFAULT_LOCAL_MODEL].licence == "apache-2.0"
    assert Settings(_env_file=None).memory_rerank_model == DEFAULT_LOCAL_MODEL
    assert Settings(_env_file=None).memory_rerank_provider == "none"


# ------------------------------------------------------------------ health


def test_health_says_whether_a_search_is_reranked_and_why_not() -> None:
    off = _runtime().health_check()
    assert off["status"] == "ok"
    assert off["reranker"] == {
        "requested": "none",
        "provider": "none",
        "active": False,
        "model_id": None,
        "top_k": 20,
        "fallback_reason": None,
    }

    missing = _runtime(
        memory_rerank_provider="local",
        rerank_model_factory=_raising(FileNotFoundError("not prefetched")),
    ).health_check()
    assert missing["status"] == "ok"  # a missing refinement is not a failing memory
    assert missing["reranker"]["requested"] == "local"
    assert missing["reranker"]["active"] is False
    assert "FileNotFoundError" in missing["reranker"]["fallback_reason"]

    on = _runtime(memory_rerank_provider="local", rerank_model_factory=_factory()).health_check()
    assert on["reranker"]["active"] is True
    assert on["reranker"]["model_id"] == f"local-{DEFAULT_LOCAL_MODEL}"
    # The embedder's own report is untouched by any of this.
    assert on["embedder"] == off["embedder"]
