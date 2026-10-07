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


def test_validate_cases_refuses_a_tag_the_question_does_not_bear_out(
    bench: Any, data: dict
) -> None:
    """``_tag_lies``: a case may not claim a shape its own text does not have (the
    inspector's mutation - ``_tag_lies`` returning [] - left every test green)."""
    broken = json.loads(json.dumps(data))
    dotted = next(c for c in broken["cases"] if "dotted_i" in c["tags"])
    dotted["query"] = dotted["query"].replace("İ", "i").replace("I", "ı")
    suffix = next(c for c in broken["cases"] if "suffix" in c["tags"] and c is not dotted)
    suffix["query"] = suffix["query"].replace("'", " ")
    typo = next(c for c in broken["cases"] if "typo" in c["tags"])
    typo["typo"] = "zzqq"
    errors = bench.validate_cases(broken)
    assert f"{dotted['id']}: tagged dotted_i but the question has no İ/I" in errors
    assert f"{suffix['id']}: tagged suffix but the question has no apostrophe suffix" in errors
    assert any(e.startswith(f"{typo['id']}: typo 'zzqq'") for e in errors)
    no_pair = json.loads(json.dumps(data))
    case = next(c for c in no_pair["cases"] if "typo" in c["tags"])
    del case["typo_of"]
    assert f"{case['id']}: tagged typo without typo/typo_of" in bench.validate_cases(no_pair)


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


# --------------------------------------------------------------------------- draws
#
# hybrid_search breaks equal scores on str(memory.id), and every load draws new uuid4 ids:
# the same seed, loaded twice, gave trgm a different rank on 11 of 33 questions (inspector,
# f7845e62) and flipped the decision. One draw may not decide; the pooled draws do.


def _draw(like: dict, trgm: dict, like_ms: float = 10.0, trgm_ms: float = 20.0) -> dict:
    return {
        "like": {"ranks": like, "timings_ms": [like_ms]},
        "trgm": {"ranks": trgm, "timings_ms": [trgm_ms]},
    }


def test_pool_draws_averages_the_rates_and_pools_the_timings(bench: Any) -> None:
    draws = [
        {"ranks": {"a": 1, "b": 4}, "timings_ms": [1.0, 2.0]},
        {"ranks": {"a": 4, "b": 2}, "timings_ms": [3.0, 4.0]},
        {"ranks": {"a": 2, "b": None}, "timings_ms": [5.0, 6.0]},
    ]
    out = bench.pool_draws("trgm", draws)
    assert out["draws"] == 3
    assert out["top3"] == pytest.approx((0.5 + 0.5 + 0.5) / 3)
    assert out["top1"] == pytest.approx((0.5 + 0 + 0) / 3)
    assert (out["top3_min"], out["top3_max"]) == (0.5, 0.5)
    assert out["top3_share"] == {"a": pytest.approx(2 / 3), "b": pytest.approx(1 / 3)}
    assert out["ranks_per_draw"] == {"a": [1, 4, 2], "b": [4, 2, None]}
    assert out["p95_ms"] == pytest.approx(bench.percentile([1, 2, 3, 4, 5, 6], 95))
    assert out["timed_queries"] == 6


def test_a_question_is_in_the_pooled_top3_when_a_majority_of_draws_has_it(bench: Any) -> None:
    like = bench.pool_draws("like", [{"ranks": {"a": 1}, "timings_ms": [1.0]}] * 2)
    split = bench.pool_draws(
        "trgm",
        [{"ranks": {"a": 1}, "timings_ms": [1.0]}, {"ranks": {"a": 5}, "timings_ms": [1.0]}],
    )
    minority = bench.pool_draws(
        "trgm",
        [{"ranks": {"a": r}, "timings_ms": [1.0]} for r in (1, 5, 5)],
    )
    assert bench.decide_default(like, split)["lost_in_trgm"] == []
    assert bench.decide_default(like, minority)["lost_in_trgm"] == ["a"]


def test_draws_that_disagree_are_caught_and_the_pool_decides(bench: Any) -> None:
    """Draw 1 alone says like (trgm only equal), draw 2 alone says trgm: the old code read
    whichever came first. The pooled rule decides and the split is reported."""
    draws = [
        _draw({"a": 1, "b": 5, "c": 1}, {"a": 1, "b": 5, "c": 2}),
        _draw({"a": 1, "b": 5, "c": 1}, {"a": 1, "b": 1, "c": 2}),
        _draw({"a": 1, "b": 5, "c": 1}, {"a": 2, "b": 3, "c": 1}),
    ]
    report = {
        "seeds": [7],
        "embedders": {"local": {}},
        "results": {"local": {"7": {"draws": draws}}},
    }
    out = bench.decide(report, {})
    assert out["per_draw"] == {"not_better": 1, "trgm": 2}
    assert out["draw_sensitive"] is True
    assert out["draws"] == 3
    assert out["default"] == "trgm"
    assert out["rule"] == "trgm"
    agreeing = {
        "seeds": [7],
        "embedders": {"local": {}},
        "results": {"local": {"7": {"draws": [draws[0], draws[0]]}}},
    }
    steady = bench.decide(agreeing, {})
    assert steady["draw_sensitive"] is False
    assert steady["default"] == "like"


def test_decide_reads_every_seed_and_draw_of_the_local_embedder(bench: Any) -> None:
    like_wins = _draw({"a": 1, "b": 1}, {"a": 5, "b": 1})
    trgm_wins = _draw({"a": 1, "b": 5}, {"a": 1, "b": 1})
    report = {
        "seeds": [7, 11],
        "embedders": {"local": {}, "deterministic": {}},
        "results": {
            "local": {"7": {"draws": [like_wins]}, "11": {"draws": [trgm_wins, trgm_wins]}},
            "deterministic": {"7": {"draws": [like_wins] * 5}},
        },
    }
    out = bench.decide(report, {})
    assert out["draws"] == 3
    assert out["per_draw"] == {"lost": 0, "not_better": 1, "trgm": 2} or out["per_draw"] == {
        "not_better": 1,
        "trgm": 2,
    }
    assert out["read_from"].startswith("local")


EVIDENCE = REPO / "docs" / "evidence" / "memory-lexical-turkish-measure.json"


def test_the_default_is_what_the_measurement_decided(monkeypatch: pytest.MonkeyPatch) -> None:
    """The card: lexical.py's default follows the pooled PostgreSQL measurement."""
    decision = json.loads(EVIDENCE.read_text(encoding="utf-8"))["decision"]
    assert decision["draws"] >= 2, "one load is one tie draw; the decision pools several"
    monkeypatch.delenv(lexical.MODE_ENV, raising=False)
    assert lexical.lexical_mode() == decision["default"]


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


def test_each_draw_loads_new_ids_and_an_id_tie_break_moves_the_ranks(
    bench: Any, data: dict, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The inspector's finding on SQLite: a search whose scores all tie and that breaks the
    tie on ``str(memory.id)`` (as hybrid_search does) ranks the same seed differently in each
    draw, because each draw is a fresh load with fresh uuid4 ids. measure_draws keeps every
    draw, and the pool sees the spread one draw cannot."""

    def tied_search(session: Any, embedder: Any, query: str, filters: Any, **_: Any) -> list:
        rows = session.query(Memory).filter(Memory.conversation_id == filters.conversation_id)
        return [type("R", (), {"memory": m})() for m in sorted(rows, key=lambda m: str(m.id))]

    monkeypatch.setattr(bench, "hybrid_search", tied_search)
    small = {"cases": data["cases"][:6], "noise": data["noise"][:20]}
    out = bench.measure_draws(
        db, DeterministicEmbedder(), small, seed=7, draws=6, rounds=1, warmup=0
    )
    assert len(out["draws"]) == 6
    id_sets = [frozenset(d["answer_ids"].values()) for d in out["draws"]]
    assert len(set(id_sets)) == 6
    assert db.query(Memory).filter(Memory.status == "active").count() == 0
    per_draw = [tuple(d["trgm"]["ranks"].values()) for d in out["draws"]]
    assert len(set(per_draw)) > 1
    assert out["trgm"]["draws"] == 6
    assert out["trgm"]["top3_min"] <= out["trgm"]["top3"] <= out["trgm"]["top3_max"]


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
