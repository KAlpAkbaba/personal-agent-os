"""Deterministic retrieval evaluation (MEMORY_SPEC §9).

Seeds the fixed corpus at evals/agent/memory/corpus.jsonl (deterministic
uuid5 ids, DeterministicEmbedder vectors) and replays the known queries in
evals/agent/memory/queries.jsonl through the production hybrid retrieval,
producing a report with:

- per-query precision@k (relevant retrieved / min(k, |expected|)) and
  hit-rate (any expected memory retrieved);
- overall means (overall relevance);
- CROSS-PROJECT CONTAMINATION: over all project-scoped queries, the fraction
  of returned rows belonging to a DIFFERENT project. Must be 0.0 — project
  isolation is a hard invariant.

Fully offline and deterministic: fixed corpus, seeded embedder, tie-breaks on
memory id.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.memory import service
from app.memory.embedding import Embedder
from app.memory.lifecycle import upsert_embedding
from app.memory.models import Memory, MemoryEmbedding, MemoryEvidence, MemoryVersion
from app.memory.retrieval import RetrievalFilters, hybrid_search
from app.memory.types import MemoryStatus

# Fixed namespace so corpus slugs map to stable memory ids everywhere.
EVAL_NAMESPACE = uuid.UUID("6c1a4b52-9d1e-5f7a-8c3b-2c7e1d5a9b40")

# The eval clock is pinned so recency scoring is deterministic.
EVAL_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

DEFAULT_K = 5


def eval_dir() -> Path:
    """repo_root/evals/agent/memory (this file: services/api/app/memory/...)."""
    return Path(__file__).resolve().parents[4] / "evals" / "agent" / "memory"


def slug_id(slug: str) -> uuid.UUID:
    return uuid.uuid5(EVAL_NAMESPACE, f"pagentos-memory-eval:{slug}")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_corpus(path: Path | None = None) -> list[dict[str, Any]]:
    return _load_jsonl(path or eval_dir() / "corpus.jsonl")


def load_queries(path: Path | None = None) -> list[dict[str, Any]]:
    return _load_jsonl(path or eval_dir() / "queries.jsonl")


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def seed_corpus(
    session: Session, embedder: Embedder, corpus: list[dict[str, Any]]
) -> dict[str, uuid.UUID]:
    """Idempotently (re)seed the corpus. Returns project name -> entity id."""
    projects: dict[str, uuid.UUID] = {}
    for record in corpus:
        project = record.get("project")
        if project and project not in projects:
            entity = service.create_entity(session, kind="project", name=project)
            projects[project] = entity.id

    for record in corpus:
        memory_id = slug_id(record["slug"])
        existing = session.get(Memory, memory_id)
        if existing is not None:
            session.query(MemoryVersion).filter_by(memory_id=memory_id).delete()
            session.query(MemoryEvidence).filter_by(memory_id=memory_id).delete()
            session.query(MemoryEmbedding).filter_by(memory_id=memory_id).delete()
            session.query(Memory).filter(Memory.superseded_by == memory_id).update(
                {Memory.superseded_by: None}
            )
            session.delete(existing)
            session.flush()
        project = record.get("project")
        memory = Memory(
            id=memory_id,
            memory_class=record["memory_class"],
            key=record.get("key"),
            text=record["text"],
            value_json=record.get("value") or {},
            stage=record.get("stage", "candidate"),
            status=MemoryStatus.ACTIVE.value,
            explicit=bool(record.get("explicit", False)),
            pinned=False,
            confidence=float(record.get("confidence", 0.3)),
            evidence_count=int(record.get("evidence_count", 1)),
            retention_class=record.get("retention_class", "standard"),
            project_id=projects.get(project) if project else None,
            occurred_at=_parse_dt(record.get("occurred_at")),
            valid_from=_parse_dt(record.get("valid_from")),
            valid_until=_parse_dt(record.get("valid_until")),
            provenance_json={"origin": "eval_corpus", "slug": record["slug"]},
            created_at=_parse_dt(record.get("occurred_at")) or EVAL_NOW,
            updated_at=EVAL_NOW,
        )
        session.add(memory)
        session.flush()
        upsert_embedding(session, embedder, memory)
    session.commit()
    return projects


def _filters_from_query(
    query: dict[str, Any], projects: dict[str, uuid.UUID]
) -> RetrievalFilters:
    raw = query.get("filters") or {}
    project = raw.get("project")
    return RetrievalFilters(
        memory_class=raw.get("memory_class"),
        key=raw.get("key"),
        project_id=projects[project] if project else None,
        stage_min=raw.get("stage_min"),
        explicit=raw.get("explicit"),
        occurred_from=_parse_dt(raw.get("occurred_from")),
        occurred_to=_parse_dt(raw.get("occurred_to")),
        valid_at=_parse_dt(raw.get("valid_at")),
    )


def run_evaluation(
    session: Session,
    embedder: Embedder,
    *,
    corpus: list[dict[str, Any]] | None = None,
    queries: list[dict[str, Any]] | None = None,
    seed: bool = True,
) -> dict[str, Any]:
    """Seed (optional) and evaluate. Returns the full report."""
    corpus = corpus if corpus is not None else load_corpus()
    queries = queries if queries is not None else load_queries()

    if seed:
        projects = seed_corpus(session, embedder, corpus)
    else:
        projects = {
            e.name: e.id for e in service.list_entities(session, kind="project", limit=500)
        }

    id_to_slug = {slug_id(r["slug"]): r["slug"] for r in corpus}

    per_query: list[dict[str, Any]] = []
    scoped_returned = 0
    scoped_contaminated = 0

    for query in queries:
        k = int(query.get("k", DEFAULT_K))
        filters = _filters_from_query(query, projects)
        results = hybrid_search(
            session, embedder, query.get("q"), filters, k=k, now=EVAL_NOW
        )
        returned_ids = [r.memory.id for r in results]
        expected_ids = {slug_id(slug) for slug in query["expected"]}
        hits = [mid for mid in returned_ids if mid in expected_ids]
        precision = len(hits) / max(1, min(k, len(expected_ids)))
        hit_rate = 1.0 if hits else 0.0

        contamination_hits: list[str] = []
        if filters.project_id is not None:
            scoped_returned += len(results)
            for result in results:
                if result.memory.project_id != filters.project_id:
                    scoped_contaminated += 1
                    contamination_hits.append(str(result.memory.id))

        per_query.append(
            {
                "query_id": query["query_id"],
                "q": query.get("q"),
                "k": k,
                "expected": sorted(query["expected"]),
                "returned": [id_to_slug.get(mid, str(mid)) for mid in returned_ids],
                "precision_at_k": round(precision, 4),
                "hit_rate": hit_rate,
                "contamination_hits": contamination_hits,
            }
        )

    query_count = len(per_query)
    mean_precision = (
        sum(q["precision_at_k"] for q in per_query) / query_count if query_count else 0.0
    )
    mean_hit_rate = (
        sum(q["hit_rate"] for q in per_query) / query_count if query_count else 0.0
    )
    contamination_rate = (
        scoped_contaminated / scoped_returned if scoped_returned else 0.0
    )

    return {
        "corpus_size": len(corpus),
        "query_count": query_count,
        "embedder": {
            "model_id": embedder.model_id,
            "model_version": embedder.model_version,
            "dim": embedder.dim,
        },
        "queries": per_query,
        "overall": {
            "mean_precision_at_k": round(mean_precision, 4),
            "mean_hit_rate": round(mean_hit_rate, 4),
            "cross_project_contamination": round(contamination_rate, 6),
            "project_scoped_rows_returned": scoped_returned,
        },
    }


__all__ = [
    "EVAL_NAMESPACE",
    "EVAL_NOW",
    "eval_dir",
    "load_corpus",
    "load_queries",
    "run_evaluation",
    "seed_corpus",
    "slug_id",
]
