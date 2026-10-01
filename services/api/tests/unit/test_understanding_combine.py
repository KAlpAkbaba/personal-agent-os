"""ADR-0224 layer 2: the combiner and ``understand()`` (the acceptance cases of the card).

Expected confidences are hand-written. The corpus is the exemplar set, exactly as the ADR
says ("a corpus case is an exemplar"); production code never imports it, so the seeding
happens here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest

from app.memory.embedding import DeterministicEmbedder
from app.voice.understanding import combine
from app.voice.understanding.combine import Candidate, understand
from app.voice.understanding.combine import combine as combine_candidates
from app.voice.understanding.semantic import (
    SemanticEngine,
    exemplars_from_cases,
)
from tests.voice_corpus.corpus import all_cases

HIGH = 0.85


@dataclass
class Rule:
    intent: str
    match_kind: str = "exact"
    entities: dict[str, str] = field(default_factory=dict)


@pytest.fixture(scope="module")
def engine() -> SemanticEngine:
    return SemanticEngine(DeterministicEmbedder(), exemplars_from_cases(all_cases()))


# --- the rule candidate ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected"), [("exact", 1.0), ("suffix_dropped", 0.9), ("confusion", 0.75)]
)
def test_rule_candidate_confidence_by_match_kind(kind: str, expected: float) -> None:
    cand = combine.rule_candidate(Rule("app_open", kind, {"app": "calc"}))
    assert cand is not None
    assert cand.confidence == expected
    assert cand.intent == "app_open"
    assert cand.entities == {"app": "calc"}
    assert cand.source == "rule"


def test_rule_candidate_none_when_no_rule_matched() -> None:
    assert combine.rule_candidate(None) is None


def test_an_unknown_match_kind_is_refused_not_trusted() -> None:
    with pytest.raises(ValueError):
        combine.rule_candidate(Rule("app_open", "whatever"))


def test_an_exact_rule_result_keeps_one_and_ranks_first(engine: SemanticEngine) -> None:
    out = understand(
        "hesap makinesini aç",
        device_aliases={},
        rule_result=Rule("app_open", "exact", {"app": "calc"}),
        engine=engine,
    )
    assert out[0].source == "rule"
    assert out[0].confidence == 1.0
    assert all(c.confidence <= 1.0 for c in out)


def test_ranking_is_by_confidence_and_the_rule_wins_a_tie() -> None:
    rule = combine.rule_candidate(Rule("alarm_create", "confusion"))  # 0.75
    sem = Candidate(intent="media_play", confidence=0.8, source="semantic")
    ranked = combine_candidates(rule, [sem])
    assert [c.intent for c in ranked] == ["media_play", "alarm_create"]  # 0.8 > 0.75
    rule = combine.rule_candidate(Rule("alarm_create", "exact"))  # 1.0
    assert combine_candidates(rule, [sem])[0].intent == "alarm_create"
    tie = Candidate(intent="media_play", confidence=1.0, source="semantic")
    assert combine_candidates(rule, [tie])[0].source == "rule"


def test_the_same_intent_is_merged_and_the_semantic_slot_fills_the_gap() -> None:
    rule = combine.rule_candidate(Rule("app_open", "suffix_dropped", {"app": "calc"}))
    sem = Candidate(
        intent="app_open",
        confidence=0.6,
        entities={"app": "notepad", "device": "ofis"},
        entity_confidence={"app": 0.9, "device": 0.8},
        evidence=("fuzzy ofisu~ofis",),
        source="semantic",
    )
    ranked = combine_candidates(rule, [sem])
    assert len(ranked) == 1
    assert ranked[0].confidence == 0.9
    assert ranked[0].entities == {"app": "calc", "device": "ofis"}  # the rule's slot wins
    assert "fuzzy ofisu~ofis" in ranked[0].evidence


# --- acceptance: the trial sentences ---------------------------------------------------


def test_the_third_trial_sentence_reads_as_app_open_calc_on_the_office_pc(
    engine: SemanticEngine,
) -> None:
    out = understand(
        "Ofisü bilgisayarında hesap makinesini açın",
        device_aliases={"ofis": ["ofis"]},
        rule_result=None,
        engine=engine,
    )
    top = out[0]
    assert top.intent == "app_open"
    assert top.entities["app"] == "calc"
    assert top.entities["device"] == "ofis"
    assert any("ofisu~ofis" in line for line in top.evidence)


def test_the_language_preference_is_not_a_research_command(engine: SemanticEngine) -> None:
    out = understand(
        "Bundan sonra araştırma raporlarını her zaman Türkçe oku",
        device_aliases={},
        rule_result=None,
        engine=engine,
    )
    top = out[0]
    wrong = {"research_open", "research_answer_mode"}
    # Either a preference reading leads, or the wrong reading is not confident enough to act.
    assert top.intent not in wrong or top.confidence < HIGH
    assert top.intent == "preference"


def test_every_candidate_carries_evidence_and_a_bounded_confidence(engine: SemanticEngine) -> None:
    out = understand("bir şarkı çal", device_aliases={}, rule_result=None, engine=engine)
    assert out
    for cand in out:
        assert 0.0 <= cand.confidence <= 1.0
        assert cand.evidence


def test_understand_without_an_engine_or_configuration_refuses() -> None:
    combine.reset_default_engine()
    with pytest.raises(RuntimeError):
        understand("aç", device_aliases={}, rule_result=None)


def test_the_default_engine_is_built_once_and_reloadable() -> None:
    combine.reset_default_engine()
    try:
        first = combine.configure_default_engine(DeterministicEmbedder(), [("app_open", "aç")])
        assert combine.default_engine() is first
        second = combine.configure_default_engine(DeterministicEmbedder(), [("app_open", "aç")])
        assert combine.default_engine() is second
        assert second is not first  # reload replaces
    finally:
        combine.reset_default_engine()


# --- timing is reported, not asserted --------------------------------------------------


def test_understand_wall_time_on_the_corpus_is_measured(engine: SemanticEngine) -> None:
    sentences = [c.utterance for c in all_cases()][:400]
    start = time.perf_counter()
    for s in sentences:
        understand(s, device_aliases={"ofis": ["ofis"]}, rule_result=None, engine=engine)
    per = (time.perf_counter() - start) / len(sentences) * 1000
    print(f"understand() per sentence: {per:.2f} ms over {len(sentences)} corpus sentences")
    assert per < 500  # a hang guard only; the number is what the report carries
