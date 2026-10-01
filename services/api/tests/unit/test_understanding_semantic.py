"""ADR-0224 layer 2: fuzzy distance, the intent index, the entity index, the calibration.

Every expected number below is written by hand; nothing is computed by the function it
checks. The embedder is ``DeterministicEmbedder`` (a lexical n-gram hash) or a scripted one
whose cosines are fixed by construction.
"""

from __future__ import annotations

import pytest

from app.memory.embedding import DeterministicEmbedder
from app.voice.understanding import fuzzy
from app.voice.understanding.semantic import (
    PREFERENCE_INTENT,
    SemanticEngine,
    calibrate,
    exemplars_from_cases,
)

DIM = DeterministicEmbedder.dim


def _unit(*weights: float) -> list[float]:
    """A vector with the given leading components (already unit length when they are)."""
    vec = [0.0] * DIM
    for i, w in enumerate(weights):
        vec[i] = w
    return vec


class ScriptedEmbedder:
    """Cosines fixed by construction; every text not scripted is hash noise (~0 cosine)."""

    model_id = "scripted"
    model_version = "1"
    dim = DIM

    def __init__(self, script: dict[str, list[float]]) -> None:
        self._script = script
        self._noise = DeterministicEmbedder()

    def embed(self, text: str) -> list[float]:
        return self._script.get(text) or self._noise.embed(text)


# --- fuzzy -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("ofis", "ofis", 1.0),
        ("ofisü", "ofis", 0.8),  # one insertion after folding ü->u: 1 - 1/5
        ("ofsi", "ofis", 0.75),  # an adjacent transposition costs ONE edit: 1 - 1/4
        ("kıtap", "kitap", 1.0),  # Turkish dotless ı folds
        ("ISIK", "ışık", 1.0),  # capital I is dotless: ı, then folded
        ("İSTANBUL", "istanbul", 1.0),
        ("calc", "xyzw", 0.0),
        ("", "ofis", 0.0),
    ],
)
def test_similarity_table(a: str, b: str, expected: float) -> None:
    assert fuzzy.similarity(a, b) == pytest.approx(expected)


def test_similarity_skips_the_table_when_lengths_are_too_far_apart() -> None:
    # Three characters is the most the floor can forgive; beyond it the answer is 0.0
    # without running the dynamic programme.
    assert fuzzy.similarity("ofis", "ofis bilgisayari") == 0.0


def test_fold_is_turkish_aware() -> None:
    assert fuzzy.fold("IŞIK Çağrı İş") == "isik cagri is"


# --- calibration -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cosine", "runner_up", "expected"),
    [
        (0.9, 0.7, 0.9),  # margin 0.2 >= 0.10: full factor
        (0.9, 0.85, 0.72),  # margin 0.05: factor 0.8
        (0.9, 0.9, 0.54),  # no margin: factor 0.6
        (0.5, 0.0, 0.5),
        (1.0, 0.0, 1.0),
        (-0.2, 0.0, 0.0),  # never negative
    ],
)
def test_calibrate_table(cosine: float, runner_up: float, expected: float) -> None:
    assert calibrate(cosine, runner_up) == pytest.approx(expected)


# --- the entity index ------------------------------------------------------------------


def test_fuzzy_bounds_a_lexically_far_entity_with_a_near_vector() -> None:
    script = {
        "bilgisaxxx": _unit(1.0),  # the heard token
        "bilgisayar": _unit(1.0),  # device A: vector 1.0, fuzzy 0.7 (three substitutions)
        "bilgisaxxy": _unit(0.8, 0.6),  # device B: vector 0.8, fuzzy 0.9 (one substitution)
    }
    engine = SemanticEngine(ScriptedEmbedder(script), exemplars=[])
    index = engine.entity_index({"a": ["bilgisayar"], "b": ["bilgisaxxy"]})
    found = index.match("bilgisaxxx ac")
    assert found["device"].value == "b"
    assert found["device"].confidence == pytest.approx(0.8)  # min(0.8, 0.9)


def test_entity_confidence_is_the_minimum_of_vector_and_fuzzy() -> None:
    script = {"bilgisaxxx": _unit(1.0), "bilgisayar": _unit(1.0)}
    engine = SemanticEngine(ScriptedEmbedder(script), exemplars=[])
    found = engine.entity_index({"a": ["bilgisayar"]}).match("bilgisaxxx")
    assert found["device"].vector == pytest.approx(1.0)
    assert found["device"].fuzzy == pytest.approx(0.7)
    assert found["device"].confidence == pytest.approx(0.7)


def test_a_device_below_the_fuzzy_floor_is_dropped_never_defaulted() -> None:
    script = {"bilgisaxxx": _unit(1.0), "zzzzzzzzzz": _unit(1.0)}  # vector 1.0, fuzzy 0.0
    engine = SemanticEngine(ScriptedEmbedder(script), exemplars=[])
    found = engine.entity_index({"a": ["zzzzzzzzzz"]}).match("bilgisaxxx ac")
    assert "device" not in found


def test_the_entity_match_names_the_words_and_the_distance() -> None:
    engine = SemanticEngine(DeterministicEmbedder(), exemplars=[])
    found = engine.entity_index({"ofis": ["ofis"]}).match("Ofisü bilgisayarında hesap aç")
    hit = found["device"]
    assert hit.value == "ofis"
    assert (hit.heard, hit.surface) == ("ofisu", "ofis")
    assert hit.fuzzy == pytest.approx(0.8)


def test_applications_come_from_the_allow_list_with_their_turkish_names() -> None:
    engine = SemanticEngine(DeterministicEmbedder(), exemplars=[])
    found = engine.entity_index({}).match("hesap makinesini aç")
    assert found["app"].value == "calc"


def test_short_tokens_do_not_match_entities() -> None:
    engine = SemanticEngine(DeterministicEmbedder(), exemplars=[])
    assert "device" not in engine.entity_index({"x": ["ev"]}).match("ev ac")


def test_vocabulary_synonyms_are_entities_too() -> None:
    engine = SemanticEngine(DeterministicEmbedder(), exemplars=[])
    index = engine.entity_index({}, vocabulary=[("app", "calc", "hesapmak")])
    assert index.match("hesapmak aç")["app"].value == "calc"


def test_the_index_rebuilds_when_aliases_change_and_not_otherwise() -> None:
    engine = SemanticEngine(DeterministicEmbedder(), exemplars=[])
    engine.entity_index({"ofis": ["ofis"]})
    assert engine.entity_builds == 1
    engine.entity_index({"ofis": ["ofis"]})
    assert engine.entity_builds == 1  # same aliases: cached
    engine.entity_index({"ofis": ["ofis", "iş yeri"]})
    assert engine.entity_builds == 2  # an alias was added
    engine.entity_index({"ofis": ["iş yeri", "ofis"]})
    assert engine.entity_builds == 2  # order is not a change
    engine.entity_index({"ofis": ["ofis"]}, vocabulary=[("app", "calc", "hesapmak")])
    assert engine.entity_builds == 3  # a vocabulary entry is a change


# --- the intent index ------------------------------------------------------------------


def test_intent_hits_are_ranked_with_the_exemplar_that_matched() -> None:
    engine = SemanticEngine(
        DeterministicEmbedder(),
        exemplars=[
            ("app_open", "hesap makinesini aç"),
            ("alarm_create", "yarın sabah yedi için alarm kur"),
            ("media_play", "bir şarkı çal"),
        ],
    )
    hits = engine.intent_hits("hesap makinesini açar mısın")
    assert hits[0].intent == "app_open"
    assert hits[0].exemplar == "hesap makinesini aç"
    assert [h.intent for h in hits] == sorted(
        [h.intent for h in hits], key=lambda i: -next(h.cosine for h in hits if h.intent == i)
    )


def test_exemplars_from_cases_takes_one_per_distinct_sentence_and_skips_no_intent() -> None:
    class Case:
        def __init__(self, utterance: str, expected_intent: str | None) -> None:
            self.utterance = utterance
            self.expected_intent = expected_intent

    ex = exemplars_from_cases(
        [
            Case("Aç", "app_open"),
            Case("aç", "app_open"),
            Case("x y z", None),
            Case("Kapat", "close"),
        ]
    )
    assert ex == [("app_open", "Aç"), ("close", "Kapat")]


def test_the_preference_family_is_built_in() -> None:
    engine = SemanticEngine(DeterministicEmbedder(), exemplars=[])
    hits = engine.intent_hits("bundan sonra her zaman kısa anlat")
    assert hits[0].intent == PREFERENCE_INTENT


def test_the_centroid_shortlist_keeps_an_intent_whose_centroid_ranks_fifteenth() -> None:
    # The target's own exemplar is an exact match (cosine 1.0) but its centroid is diluted by a
    # second, orthogonal exemplar (dot 0.71), so 14 decoys (dot 0.8) outrank it. A shortlist of 1
    # would lose it; the shipped shortlist must keep it.
    script = {"query": _unit(1.0), "t near": _unit(1.0), "t far": _unit(0.0, 1.0)}
    exemplars = [("target", "t near"), ("target", "t far")]
    for k in range(14):
        text = f"decoy {k}"
        script[text] = _unit(0.8, 0.0, *([0.0] * k), 0.6)
        exemplars.append((f"decoy{k}", text))
    engine = SemanticEngine(ScriptedEmbedder(script), exemplars=exemplars)
    hits = engine.intent_hits("query")
    assert hits[0].intent == "target"
    assert hits[0].exemplar == "t near"
    assert hits[0].cosine == pytest.approx(1.0)


def test_the_entity_cache_is_bounded_and_evicts_when_full() -> None:
    engine = SemanticEngine(DeterministicEmbedder(), exemplars=[])
    for n in range(8):
        engine.entity_index({"ofis": [f"ofis{n}"]})
    assert engine.entity_builds == 8
    engine.entity_index({"ofis": ["ofis7"]})
    assert engine.entity_builds == 8  # still cached at the limit
    engine.entity_index({"ofis": ["ofis8"]})  # the ninth: the cache is cleared
    assert engine.entity_builds == 9
    engine.entity_index({"ofis": ["ofis0"]})
    assert engine.entity_builds == 10  # the first was evicted, so it is rebuilt
