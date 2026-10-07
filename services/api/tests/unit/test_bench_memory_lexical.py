"""scripts/core/bench-memory-lexical.py and its fixed Turkish set (memory-lexical-turkish-measure).

The script is loaded by FILE PATH (``test_bench_memory_embedding.py``'s pattern). Pinned here:

* the set ``tests/fixtures/memory_lexical/names_tr.json``: >= 30 questions, every answer in
  the set and not among its own distractors, no text twice, >= 3 of each of the hard shapes
  (a suffixed name, a capital İ/I, a typo) - and each tag is checked against the text, so a
  tag cannot claim a shape the question does not have; no real owner/company/machine name;
* the arithmetic the decision reads: ``rank_of``, ``top_k_rate``, ``mrr``, ``percentile``
  (numpy's linear definition, values worked by hand);
* ``decide_default``: one rule, four outcomes, each its own reason;
* ``run_mode`` on SQLite with the deterministic embedder, both modes, no database server.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.memory import lexical
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

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "core" / "bench-memory-lexical.py"
FIXTURE = REPO / "services" / "api" / "tests" / "fixtures" / "memory_lexical" / "names_tr.json"

#: Names that belong to the owner's real life or machines; the set is invented, always.
BANNED_WORDS = ("aktivra", "akbaba", "kadir", "melissa", "gmkadirakbaba", "alp")


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("bench_memory_lexical", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # A dataclass reads its module from sys.modules while it is being built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bench() -> Any:
    return _load()


@pytest.fixture(scope="module")
def data() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- the set


def test_the_set_has_thirty_questions_and_validates_clean(bench: Any, data: dict) -> None:
    assert len(data["cases"]) >= 30
    assert bench.validate_cases(data) == []


def test_every_answer_is_in_the_set_and_not_among_its_distractors(data: dict) -> None:
    for case in data["cases"]:
        assert case["answer"].strip(), case["id"]
        assert case["answer"] not in case["distractors"], case["id"]
        assert len(case["distractors"]) >= 3, case["id"]


def test_no_id_question_or_memory_text_appears_twice(data: dict) -> None:
    ids = [c["id"] for c in data["cases"]]
    queries = [c["query"] for c in data["cases"]]
    texts = [t for c in data["cases"] for t in [c["answer"], *c["distractors"]]] + data["noise"]
    assert len(ids) == len(set(ids))
    assert len(queries) == len(set(queries))
    assert len(texts) == len(set(texts))


def _tagged(data: dict, tag: str) -> list[dict]:
    return [c for c in data["cases"] if tag in c["tags"]]


def test_at_least_three_capital_i_questions_and_the_tag_is_true(data: dict) -> None:
    cases = _tagged(data, "dotted_i")
    assert len(cases) >= 3
    for case in cases:
        assert re.search("[İI]", case["query"]), case["id"]


def test_at_least_three_suffixed_name_questions_and_the_tag_is_true(data: dict) -> None:
    cases = _tagged(data, "suffix")
    assert len(cases) >= 3
    for case in cases:
        assert re.search(r"\w'\w", case["query"]), case["id"]


def test_at_least_three_typo_questions_and_the_typo_is_real(data: dict) -> None:
    cases = _tagged(data, "typo")
    assert len(cases) >= 3
    for case in cases:
        right, wrong = case["typo_of"], case["typo"]
        assert lexical.fold(wrong) in lexical.fold(case["query"]), case["id"]
        assert lexical.fold(right) not in lexical.fold(case["query"]), case["id"]
        assert lexical.fold(right) in lexical.fold(case["answer"]), case["id"]


def test_the_set_names_no_real_person_company_or_machine(data: dict) -> None:
    texts = [t for c in data["cases"] for t in [c["query"], c["answer"], *c["distractors"]]]
    for text in texts + data["noise"]:
        for word in BANNED_WORDS:
            assert not re.search(rf"\b{word}\b", lexical.fold(text)), (word, text)


def test_validate_cases_names_what_is_wrong(bench: Any, data: dict) -> None:
    broken = json.loads(json.dumps(data))
    broken["cases"][1]["id"] = broken["cases"][0]["id"]
    broken["cases"][2]["distractors"].append(broken["cases"][2]["answer"])
    broken["cases"] = broken["cases"][:29]
    errors = bench.validate_cases(broken)
    assert any("30" in e for e in errors)
    assert any("id" in e for e in errors)
    assert any("distractor" in e for e in errors)


# --------------------------------------------------------------------------- arithmetic


def test_rank_of_is_one_based_and_none_when_missing(bench: Any) -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert bench.rank_of([a, b, c], b) == 2
    assert bench.rank_of([a, b], c) is None


def test_top_k_rate_counts_none_as_a_miss(bench: Any) -> None:
    ranks = [1, 2, 3, 4, None]
    assert bench.top_k_rate(ranks, 1) == pytest.approx(0.2)
    assert bench.top_k_rate(ranks, 3) == pytest.approx(0.6)
    assert bench.top_k_rate(ranks, 10) == pytest.approx(0.8)
    assert bench.top_k_rate([], 3) == 0.0


def test_mrr(bench: Any) -> None:
    assert bench.mrr([1, 2, None, 4]) == pytest.approx((1 + 0.5 + 0 + 0.25) / 4)


@pytest.mark.parametrize(
    ("values", "p", "expected"),
    [
        # numpy.percentile(values, p) with method="linear", worked by hand.
        ([1.0, 2.0, 3.0, 4.0], 50, 2.5),
        ([1.0, 2.0, 3.0, 4.0], 95, 3.85),
        ([10.0, 1.0, 7.0, 3.0, 5.0], 50, 5.0),
        ([10.0, 1.0, 7.0, 3.0, 5.0], 95, 9.4),
        ([4.0], 95, 4.0),
        ([1.0, 2.0], 0, 1.0),
        ([1.0, 2.0], 100, 2.0),
    ],
)
def test_percentile_is_numpys_linear(bench: Any, values: list, p: float, expected: float) -> None:
    assert bench.percentile(values, p) == pytest.approx(expected)


def test_percentile_of_nothing_is_an_error(bench: Any) -> None:
    with pytest.raises(ValueError):
        bench.percentile([], 50)


def test_summarize(bench: Any) -> None:
    out = bench.summarize("trgm", {"a": 1, "b": 4, "c": None}, [1.0, 2.0, 3.0, 4.0])
    assert out["mode"] == "trgm"
    assert out["top1"] == pytest.approx(1 / 3)
    assert out["top3"] == pytest.approx(1 / 3)
    assert out["top10"] == pytest.approx(2 / 3)
    assert out["p50_ms"] == pytest.approx(2.5)
    assert out["p95_ms"] == pytest.approx(3.85)
    assert out["max_ms"] == 4.0
    assert out["ranks"] == {"a": 1, "b": 4, "c": None}


# --------------------------------------------------------------------------- the decision


def _mode(bench: Any, name: str, ranks: dict, p95: float) -> dict:
    return bench.summarize(name, ranks, [p95])


def test_decide_trgm_when_better_nothing_lost_and_fast(bench: Any) -> None:
    like = _mode(bench, "like", {"a": 1, "b": 5, "c": None}, 10.0)
    trgm = _mode(bench, "trgm", {"a": 2, "b": 1, "c": 3}, 60.0)
    out = bench.decide_default(like, trgm)
    assert out["default"] == "trgm"
    assert out["lost_in_trgm"] == []
    assert out["rule"] == "trgm"


def test_decide_like_when_a_case_is_lost_and_not_accepted(bench: Any) -> None:
    like = _mode(bench, "like", {"a": 1, "b": 5, "c": None}, 10.0)
    trgm = _mode(bench, "trgm", {"a": 4, "b": 1, "c": 2}, 12.0)
    out = bench.decide_default(like, trgm)
    assert out["default"] == "like"
    assert out["lost_in_trgm"] == ["a"]
    assert out["rule"] == "lost"
    accepted = bench.decide_default(like, trgm, accepted_lost={"a": "isim tuzağı, kabul"})
    assert accepted["default"] == "trgm"
    assert accepted["rule"] == "trgm"
    assert accepted["accepted_lost"] == {"a": "isim tuzağı, kabul"}


def test_decide_like_when_p95_is_51_ms_slower(bench: Any) -> None:
    like = _mode(bench, "like", {"a": 5, "b": 5}, 10.0)
    trgm = _mode(bench, "trgm", {"a": 1, "b": 1}, 61.0)
    out = bench.decide_default(like, trgm)
    assert out["default"] == "like"
    assert out["rule"] == "slow"
    at_limit = bench.decide_default(like, _mode(bench, "trgm", {"a": 1, "b": 1}, 60.0))
    assert at_limit["default"] == "trgm"


def test_decide_like_when_top3_is_only_equal(bench: Any) -> None:
    like = _mode(bench, "like", {"a": 1, "b": 2}, 10.0)
    trgm = _mode(bench, "trgm", {"a": 2, "b": 1}, 10.0)
    out = bench.decide_default(like, trgm)
    assert out["default"] == "like"
    assert out["rule"] == "not_better"


def test_the_four_outcomes_are_four_different_reasons(bench: Any) -> None:
    like = _mode(bench, "like", {"a": 1, "b": 5, "c": 5}, 10.0)
    outcomes = [
        bench.decide_default(like, _mode(bench, "trgm", {"a": 1, "b": 1, "c": 1}, 10.0)),
        bench.decide_default(like, _mode(bench, "trgm", {"a": 9, "b": 1, "c": 1}, 10.0)),
        bench.decide_default(like, _mode(bench, "trgm", {"a": 1, "b": 1, "c": 1}, 99.0)),
        bench.decide_default(like, _mode(bench, "trgm", {"a": 1, "b": 5, "c": 5}, 10.0)),
    ]
    assert [o["rule"] for o in outcomes] == ["trgm", "lost", "slow", "not_better"]
    assert len({o["reasons"][0] for o in outcomes}) == 4


# --------------------------------------------------------------------------- run_mode on SQLite

MEMORY_TABLES = [
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
]


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    for table in MEMORY_TABLES:
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def test_a_second_seed_loads_whole_after_the_first_is_retired(
    bench: Any, data: dict, db: Session
) -> None:
    """The first PostgreSQL run stopped here: dedup reads every ACTIVE memory, not one
    conversation's, so seed 11's copy of a text was 'corroborated' into seed 7's."""
    small = {"cases": data["cases"][:3], "noise": data["noise"][:5]}
    first = bench.load_corpus(db, DeterministicEmbedder(), small, seed=7)
    assert len(first.ids) == first.loaded
    bench.retire_corpus(db, first)
    second = bench.load_corpus(db, DeterministicEmbedder(), small, seed=11)
    assert second.loaded == first.loaded
    assert db.query(Memory).count() == second.loaded


def test_run_mode_on_sqlite_in_both_modes(
    bench: Any, data: dict, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = {"cases": data["cases"][:5], "noise": data["noise"][:10]}
    corpus = bench.load_corpus(db, DeterministicEmbedder(), small, seed=7)
    expected = 5 * 1 + sum(len(c["distractors"]) for c in small["cases"]) + 10
    assert corpus.loaded == expected
    assert set(corpus.answers) == {c["id"] for c in small["cases"]}
    for mode in ("like", "trgm"):
        out = bench.run_mode(db, DeterministicEmbedder(), small["cases"], corpus, mode)
        assert set(out["ranks"]) == set(corpus.answers)
        assert len(out["timings_ms"]) == 5
        assert all(t >= 0 for t in out["timings_ms"])
        assert any(r is not None for r in out["ranks"].values())
    # run_mode puts the environment back the way it found it.
    monkeypatch.delenv(lexical.MODE_ENV, raising=False)
    bench.run_mode(db, DeterministicEmbedder(), small["cases"][:1], corpus, "trgm")
    assert lexical.MODE_ENV not in os.environ
