"""M5 integration: deterministic retrieval evaluation on real pgvector.

Seeds evals/agent/memory/corpus.jsonl (fixed uuid5 ids, DeterministicEmbedder)
and replays evals/agent/memory/queries.jsonl through the production hybrid
retrieval on PostgreSQL (hnsw cosine index). Asserts the quality floors and
the hard zero-contamination invariant (MEMORY_SPEC §9: project
disambiguation, temporal retrieval, factual retrieval).
"""

import pytest

from app.config import Settings
from app.memory.embedding import DeterministicEmbedder
from app.memory.evaluation import run_evaluation
from app.memory.runtime import MemoryRuntime

pytestmark = pytest.mark.integration

# Quality floors (stated): mean precision@k over the corpus must be >= 0.75,
# every query must find at least one expected memory in nearly all cases
# (mean hit-rate >= 0.9), and cross-project contamination must be exactly 0.
PRECISION_FLOOR = 0.75
HIT_RATE_FLOOR = 0.9


def test_eval_precision_and_zero_contamination() -> None:
    runtime = MemoryRuntime(Settings())
    embedder = DeterministicEmbedder()
    with runtime.session() as session:
        report = run_evaluation(session, embedder)

    assert report["corpus_size"] >= 40
    assert report["query_count"] >= 15
    overall = report["overall"]
    assert overall["mean_precision_at_k"] >= PRECISION_FLOOR, report["queries"]
    assert overall["mean_hit_rate"] >= HIT_RATE_FLOOR, report["queries"]
    assert overall["cross_project_contamination"] == 0.0, [
        q for q in report["queries"] if q["contamination_hits"]
    ]
    assert overall["project_scoped_rows_returned"] > 0

    # The structured temporal query (no q) must return exactly the May window.
    q15 = next(q for q in report["queries"] if q["query_id"] == "q15")
    assert q15["hit_rate"] == 1.0
    assert q15["precision_at_k"] == 1.0


def test_eval_is_deterministic_across_runs() -> None:
    runtime = MemoryRuntime(Settings())
    embedder = DeterministicEmbedder()
    with runtime.session() as session:
        first = run_evaluation(session, embedder)
        second = run_evaluation(session, embedder)
    assert first["overall"] == second["overall"]
    assert [q["returned"] for q in first["queries"]] == [
        q["returned"] for q in second["queries"]
    ]
