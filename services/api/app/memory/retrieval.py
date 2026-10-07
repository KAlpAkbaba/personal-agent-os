"""Memory retrieval (MEMORY_SPEC §8): structured filters + pgvector semantic
search + deterministic hybrid rerank.

Semantic path:
- PostgreSQL: cosine via pgvector (`embedding <=> query`), served by the hnsw
  index created in migration 0005.
- SQLite (unit tests): in-memory cosine over the stored vectors using the
  frozen `cosine_similarity` helper — identical scoring, no ANN.

Hybrid rerank (documented weight formula):

    score = 0.55 * semantic_similarity
          + 0.15 * recency            # 1 / (1 + age_days / 30)
          + 0.15 * confidence
          + 0.10 * explicit           # 1.0 if explicit else 0.0
          + 0.05 * project_match      # 1.0 if row.project_id == scope project

Candidates are the union of semantic top-N and keyword/structured hits; ties
break on memory id so ordering is fully deterministic.

Word leg mode (card memory-lexical-turkish-rrf, ``PAGENTOS_MEMORY_LEXICAL``): ``like``
(default) is the above, unchanged - a word-only hit scores 0.0 for meaning. ``trgm`` folds
Turkish case, drops stop words, ranks the word hits (pg_trgm + ``turkish`` text search on
PostgreSQL) and puts in the semantic term's place

    semantic_similarity = (1/(60 + semantic_rank) + 1/(60 + word_rank)) / (2/61)

with ``1/(60 + word_rank)`` absent for a row the words did not find.

Semantic rerank (ADR-0206), only when a reranker is passed AND there is query text:
the top ``rerank_top_k`` rows of the ordering above are read, query and memory together,
by a cross-encoder, and for those rows its answer stands where the cosine stood:

    score = 0.55 * rerank             # 0..1, the cross-encoder's logit squashed
          + the recency / confidence / explicit / project terms, unchanged

Rows below the top-K keep their score and their place after the reranked ones. With no
reranker the function is the one it has always been - pinned by a test, not by intent.
A reranker that fails while scoring costs the turn nothing but the rerank.

Invariants:
- status != active (superseded) rows are ALWAYS excluded.
- A project-scoped query returns ONLY rows of that project (strict equality;
  cross-project contamination must be 0 — see evaluation.py). Global rows
  (project_id NULL) are excluded from project-scoped queries by default and
  reachable via unscoped queries.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Select, bindparam, func, literal_column, or_, select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.memory import lexical
from app.memory.embedding import Embedder, cosine_similarity
from app.memory.lifecycle import aware, utcnow
from app.memory.models import Memory, MemoryEmbedding
from app.memory.rerank import DEFAULT_TOP_K, Reranker
from app.memory.types import MemoryStatus, WriteStage

logger = get_logger("app.memory.retrieval")

SEMANTIC_CANDIDATES = 50
KEYWORD_CANDIDATES = 50
DEFAULT_K = 10

W_SEMANTIC = 0.55
#: ADR-0206: the cross-encoder's answer takes the cosine's place, at the cosine's weight.
W_RERANK = W_SEMANTIC
W_RECENCY = 0.15
W_CONFIDENCE = 0.15
W_EXPLICIT = 0.10
W_PROJECT = 0.05
RECENCY_HALF_SCALE_DAYS = 30.0

_STAGE_ORDER = {WriteStage.SESSION: 0, WriteStage.CANDIDATE: 1, WriteStage.DURABLE: 2}
_QUERY_WORD = re.compile(r"[a-zA-Z0-9çğıöşüÇĞİÖŞÜ]{3,}")


@dataclass(slots=True)
class RetrievalFilters:
    """Structured/relational retrieval filters."""

    memory_class: str | None = None
    key: str | None = None
    project_id: uuid.UUID | None = None
    stage_min: str | None = None
    explicit: bool | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    valid_at: datetime | None = None
    task_id: uuid.UUID | None = None
    artifact_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None


@dataclass(slots=True)
class ScoredMemory:
    memory: Memory
    score: float
    components: dict[str, float] = field(default_factory=dict)


def _stages_at_least(stage_min: str) -> list[str]:
    floor = _STAGE_ORDER[WriteStage(stage_min)]
    return [s.value for s, order in _STAGE_ORDER.items() if order >= floor]


def apply_filters(stmt: Select, filters: RetrievalFilters) -> Select:
    """Compose the shared WHERE clause. Active-only is unconditional."""
    stmt = stmt.where(Memory.status == MemoryStatus.ACTIVE.value)
    if filters.memory_class is not None:
        stmt = stmt.where(Memory.memory_class == filters.memory_class)
    if filters.key is not None:
        stmt = stmt.where(Memory.key == filters.key)
    if filters.project_id is not None:
        # Strict project isolation: only rows of the scoped project.
        stmt = stmt.where(Memory.project_id == filters.project_id)
    if filters.stage_min is not None:
        stmt = stmt.where(Memory.stage.in_(_stages_at_least(filters.stage_min)))
    if filters.explicit is not None:
        stmt = stmt.where(Memory.explicit.is_(filters.explicit))
    if filters.occurred_from is not None:
        stmt = stmt.where(Memory.occurred_at >= filters.occurred_from)
    if filters.occurred_to is not None:
        stmt = stmt.where(Memory.occurred_at <= filters.occurred_to)
    if filters.valid_at is not None:
        stmt = stmt.where(
            or_(Memory.valid_from.is_(None), Memory.valid_from <= filters.valid_at),
            or_(Memory.valid_until.is_(None), Memory.valid_until >= filters.valid_at),
        )
    if filters.task_id is not None:
        stmt = stmt.where(Memory.task_id == filters.task_id)
    if filters.artifact_id is not None:
        stmt = stmt.where(Memory.artifact_id == filters.artifact_id)
    if filters.conversation_id is not None:
        stmt = stmt.where(Memory.conversation_id == filters.conversation_id)
    if filters.device_id is not None:
        stmt = stmt.where(Memory.device_id == filters.device_id)
    return stmt


def structured_candidates(
    session: Session, filters: RetrievalFilters, *, limit: int = KEYWORD_CANDIDATES
) -> list[Memory]:
    stmt = apply_filters(select(Memory), filters).order_by(Memory.created_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars().all())


def keyword_candidates(
    session: Session,
    query_text: str,
    filters: RetrievalFilters,
    *,
    limit: int = KEYWORD_CANDIDATES,
) -> list[Memory]:
    """Memories holding the query's words, best word match first.

    ``like`` mode (default) is the leg as it has always been, newest first. ``trgm`` mode
    (card memory-lexical-turkish-rrf) folds Turkish case, drops stop words and ranks: on
    PostgreSQL by pg_trgm word similarity and the ``turkish`` text search, elsewhere by how
    many query words a memory holds."""
    if lexical.lexical_mode() == "trgm":
        if session.get_bind().dialect.name == "postgresql":
            return _keyword_candidates_pg(session, query_text, filters, limit=limit)
        return _keyword_candidates_python(session, query_text, filters, limit=limit)
    # Both sides through the conversation search's fold (card memory-search-ascii-fold):
    # "sukru" finds "Şükrü" and the other way round.
    terms = [lexical.search_key(t) for t in _QUERY_WORD.findall(query_text)][:8]
    if not terms:
        return []
    folded_text = lexical.search_key_sql(Memory.text)
    stmt = apply_filters(select(Memory), filters).where(
        or_(*[folded_text.like(f"%{term}%") for term in terms])
    )
    stmt = stmt.order_by(Memory.created_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars().all())


#: ``lexical.FOLD_SQL`` as an expression; character for character the migration's index.
_FOLD_TEXT = func.translate(func.lower(Memory.text), literal_column("'ı'"), literal_column("'i'"))
_TURKISH = literal_column("'turkish'::regconfig")


def _keyword_candidates_pg(
    session: Session, query_text: str, filters: RetrievalFilters, *, limit: int
) -> list[Memory]:
    """pg_trgm ``<%`` on the folded text (name with its suffix: "Ahmet'e", "Ahmete") OR the
    ``turkish`` text search on root prefixes ("vermiştim" -> ``ver:*`` -> "verdim"). Ranked by
    the better of the best word similarity and the text-search rank; ties on id."""
    terms = lexical.query_terms(query_text)
    if not terms:
        return []
    similarities = [
        func.word_similarity(bindparam(f"kw_term_{i}", term), _FOLD_TEXT)
        for i, term in enumerate(terms)
    ]
    matches = [
        bindparam(f"kw_match_{i}", term).op("<%")(_FOLD_TEXT) for i, term in enumerate(terms)
    ]
    best = func.greatest(*similarities) if len(similarities) > 1 else similarities[0]
    tsquery_text = lexical.tsquery_text(query_text)
    if tsquery_text:
        document = func.to_tsvector(_TURKISH, Memory.text)
        tsquery = func.to_tsquery(_TURKISH, bindparam("kw_tsquery", tsquery_text))
        matches.append(document.op("@@")(tsquery))
        best = func.greatest(best, func.ts_rank(document, tsquery))
    stmt = (
        apply_filters(select(Memory), filters)
        .where(or_(*matches))
        .order_by(best.desc(), Memory.id)
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def _keyword_candidates_python(
    session: Session, query_text: str, filters: RetrievalFilters, *, limit: int
) -> list[Memory]:
    """SQLite (unit tests) has no Turkish case folding: count the query's words in
    ``lexical.fold(text)`` in Python. Ranked by that count, newest first, then id."""
    terms = lexical.query_terms(query_text)
    if not terms:
        return []
    scored: list[tuple[int, Memory]] = []
    for memory in session.execute(apply_filters(select(Memory), filters)).scalars():
        folded = lexical.fold(memory.text or "")
        hits = sum(1 for term in terms if lexical.term_matches(term, folded))
        if hits:
            scored.append((hits, memory))
    scored.sort(
        key=lambda pair: (
            -pair[0],
            -(aware(pair[1].created_at) or utcnow()).timestamp(),
            str(pair[1].id),
        )
    )
    return [memory for _hits, memory in scored[:limit]]


def semantic_candidates(
    session: Session,
    embedder: Embedder,
    query_text: str,
    filters: RetrievalFilters,
    *,
    limit: int = SEMANTIC_CANDIDATES,
    query_vec: list[float] | None = None,
) -> list[tuple[Memory, float]]:
    """Top-N (memory, cosine similarity) for the query under `filters`. ``query_vec`` is the
    query already embedded, so a caller that needs it twice pays for it once."""
    if query_vec is None:
        query_vec = embedder.embed(query_text)
    dialect = session.get_bind().dialect.name

    if dialect == "postgresql":
        distance = MemoryEmbedding.embedding.cosine_distance(query_vec)
        stmt = (
            apply_filters(
                select(Memory, distance.label("distance")).join(
                    MemoryEmbedding, MemoryEmbedding.memory_id == Memory.id
                ),
                filters,
            )
            .where(MemoryEmbedding.model_id == embedder.model_id)
            .order_by(distance)
            .limit(limit)
        )
        rows = session.execute(stmt).all()
        return [(memory, 1.0 - float(dist)) for memory, dist in rows]

    # Fallback (SQLite unit tests): cosine in Python over stored vectors.
    stmt = apply_filters(
        select(Memory, MemoryEmbedding.embedding).join(
            MemoryEmbedding, MemoryEmbedding.memory_id == Memory.id
        ),
        filters,
    ).where(MemoryEmbedding.model_id == embedder.model_id)
    scored: list[tuple[Memory, float]] = []
    for memory, vector in session.execute(stmt).all():
        vec = list(vector) if not isinstance(vector, list) else vector
        scored.append((memory, cosine_similarity(query_vec, [float(x) for x in vec])))
    scored.sort(key=lambda pair: (-pair[1], str(pair[0].id)))
    return scored[:limit]


def _recency(memory: Memory, now: datetime) -> float:
    anchor = (
        aware(memory.last_confirmed_at)
        or aware(memory.occurred_at)
        or aware(memory.updated_at)
        or aware(memory.created_at)
        or now
    )
    age_days = max(0.0, (now - anchor).total_seconds() / 86400.0)
    return 1.0 / (1.0 + age_days / RECENCY_HALF_SCALE_DAYS)


def hybrid_search(
    session: Session,
    embedder: Embedder,
    query_text: str | None,
    filters: RetrievalFilters,
    *,
    k: int = DEFAULT_K,
    now: datetime | None = None,
    reranker: Reranker | None = None,
    rerank_top_k: int = DEFAULT_TOP_K,
) -> list[ScoredMemory]:
    """Union of semantic + keyword/structured candidates, reranked by the
    documented weighted score. With no query text this degrades to structured
    retrieval ranked by recency/confidence/explicit. ``reranker`` (ADR-0206) re-reads
    the top ``rerank_top_k`` of that ordering; ``None`` changes nothing."""
    now = now or utcnow()
    candidates: dict[uuid.UUID, tuple[Memory, float]] = {}

    if query_text and lexical.lexical_mode() == "trgm":
        candidates = _fused_candidates(session, embedder, query_text, filters)
    elif query_text:
        for memory, sim in semantic_candidates(session, embedder, query_text, filters):
            candidates[memory.id] = (memory, sim)
        for memory in keyword_candidates(session, query_text, filters):
            if memory.id not in candidates:
                candidates[memory.id] = (memory, 0.0)
    else:
        for memory in structured_candidates(session, filters):
            candidates[memory.id] = (memory, 0.0)

    results: list[ScoredMemory] = []
    for memory, sim in candidates.values():
        components = {
            "semantic": W_SEMANTIC * sim,
            "recency": W_RECENCY * _recency(memory, now),
            "confidence": W_CONFIDENCE * memory.confidence,
            "explicit": W_EXPLICIT * (1.0 if memory.explicit else 0.0),
            "project": W_PROJECT
            * (
                1.0
                if filters.project_id is not None and memory.project_id == filters.project_id
                else 0.0
            ),
        }
        results.append(
            ScoredMemory(memory=memory, score=sum(components.values()), components=components)
        )
    results.sort(key=lambda r: (-r.score, str(r.memory.id)))
    if reranker is not None and query_text and results:
        results = _rerank_head(results, reranker, query_text, rerank_top_k)
    return results[:k]


def _fused_candidates(
    session: Session, embedder: Embedder, query_text: str, filters: RetrievalFilters
) -> dict[uuid.UUID, tuple[Memory, float]]:
    """``trgm`` mode: every candidate's semantic term is the normalised RRF of its two ranks.

    A word-only candidate's cosine is read too, so it has a semantic rank of its own (after
    the semantic top-N, by its cosine) - without it a word-first memory and a meaning-first
    look-alike would tie at 1/61. The value stands where the cosine stood, so the weights,
    the other terms and the cross-encoder's place (ADR-0206) are what they were."""
    query_vec = embedder.embed(query_text)
    cosines: dict[uuid.UUID, tuple[Memory, float]] = {
        memory.id: (memory, sim)
        for memory, sim in semantic_candidates(
            session, embedder, query_text, filters, query_vec=query_vec
        )
    }
    keyword = keyword_candidates(session, query_text, filters)
    keyword_rank = {memory.id: rank for rank, memory in enumerate(keyword, start=1)}
    word_only = [memory for memory in keyword if memory.id not in cosines]
    for memory, sim in _cosines_of(session, embedder, query_vec, word_only):
        cosines[memory.id] = (memory, sim)
    ordered = sorted(cosines.values(), key=lambda pair: (-pair[1], str(pair[0].id)))
    return {
        memory.id: (memory, lexical.rrf_score(rank, keyword_rank.get(memory.id)))
        for rank, (memory, _sim) in enumerate(ordered, start=1)
    }


def _cosines_of(
    session: Session, embedder: Embedder, query_vec: list[float], memories: list[Memory]
) -> list[tuple[Memory, float]]:
    """The query's cosine to each of ``memories``; 0.0 for one with no vector of this model."""
    if not memories:
        return []
    ids = [memory.id for memory in memories]
    found: dict[uuid.UUID, float] = {}
    if session.get_bind().dialect.name == "postgresql":
        distance = MemoryEmbedding.embedding.cosine_distance(query_vec)
        stmt = select(MemoryEmbedding.memory_id, distance).where(
            MemoryEmbedding.memory_id.in_(ids), MemoryEmbedding.model_id == embedder.model_id
        )
        found = {memory_id: 1.0 - float(dist) for memory_id, dist in session.execute(stmt)}
    else:
        stmt = select(MemoryEmbedding.memory_id, MemoryEmbedding.embedding).where(
            MemoryEmbedding.memory_id.in_(ids), MemoryEmbedding.model_id == embedder.model_id
        )
        for memory_id, vector in session.execute(stmt):
            found[memory_id] = cosine_similarity(query_vec, [float(x) for x in vector])
    return [(memory, found.get(memory.id, 0.0)) for memory in memories]


def _rerank_head(
    ordered: list[ScoredMemory], reranker: Reranker, query_text: str, top_k: int
) -> list[ScoredMemory]:
    """The top ``top_k`` of ``ordered``, re-scored by the cross-encoder and re-sorted; the
    rest follow in the order they had. Any failure returns ``ordered`` untouched: a turn
    must never lose its memories because the model that refines their order fell over."""
    head, tail = ordered[: max(1, top_k)], ordered[max(1, top_k) :]
    try:
        scores = reranker.score(query_text, [str(item.memory.text or "") for item in head])
    except Exception as exc:  # noqa: BLE001 - the reason is logged, the text never is
        logger.warning("memory_rerank_failed", reason=type(exc).__name__, candidates=len(head))
        return ordered
    if len(scores) != len(head):
        logger.warning("memory_rerank_failed", reason="score_count_mismatch", candidates=len(head))
        return ordered
    reranked: list[ScoredMemory] = []
    for item, score in zip(head, scores, strict=True):
        components = {name: value for name, value in item.components.items() if name != "semantic"}
        components["rerank"] = W_RERANK * max(0.0, min(1.0, float(score)))
        reranked.append(
            ScoredMemory(memory=item.memory, score=sum(components.values()), components=components)
        )
    reranked.sort(key=lambda r: (-r.score, str(r.memory.id)))
    return reranked + tail


def best_similarity(
    session: Session,
    embedder: Embedder,
    text: str,
    *,
    memory_class: str,
) -> tuple[Memory, float] | None:
    """Highest-similarity ACTIVE memory of `memory_class` for dedup checks."""
    hits = semantic_candidates(
        session,
        embedder,
        text,
        RetrievalFilters(memory_class=memory_class),
        limit=1,
    )
    return hits[0] if hits else None


def to_payload(memory: Memory) -> dict[str, Any]:
    """JSON-safe representation shared by routes and inspection."""

    def _iso(dt: datetime | None) -> str | None:
        return aware(dt).isoformat() if dt is not None else None

    return {
        "memory_id": str(memory.id),
        "memory_class": memory.memory_class,
        "key": memory.key,
        "text": memory.text,
        "value": memory.value_json,
        "stage": memory.stage,
        "status": memory.status,
        "explicit": memory.explicit,
        "pinned": memory.pinned,
        "confidence": memory.confidence,
        "evidence_count": memory.evidence_count,
        "retention_class": memory.retention_class,
        "project_id": str(memory.project_id) if memory.project_id else None,
        "conversation_id": str(memory.conversation_id) if memory.conversation_id else None,
        "task_id": str(memory.task_id) if memory.task_id else None,
        "artifact_id": str(memory.artifact_id) if memory.artifact_id else None,
        "device_id": str(memory.device_id) if memory.device_id else None,
        "occurred_at": _iso(memory.occurred_at),
        "valid_from": _iso(memory.valid_from),
        "valid_until": _iso(memory.valid_until),
        "version": memory.version,
        "superseded_by": str(memory.superseded_by) if memory.superseded_by else None,
        "created_at": _iso(memory.created_at),
        "updated_at": _iso(memory.updated_at),
        "last_confirmed_at": _iso(memory.last_confirmed_at),
    }


__all__ = [
    "DEFAULT_K",
    "RetrievalFilters",
    "ScoredMemory",
    "W_CONFIDENCE",
    "W_EXPLICIT",
    "W_PROJECT",
    "W_RECENCY",
    "W_RERANK",
    "W_SEMANTIC",
    "apply_filters",
    "best_similarity",
    "hybrid_search",
    "keyword_candidates",
    "semantic_candidates",
    "structured_candidates",
    "to_payload",
]
