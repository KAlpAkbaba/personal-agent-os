"""ADR-0224 layer 3: the threshold policy, as a pure function.

HIGH: do it, the receipt as today. MEDIUM: do it AND read it back. LOW: one question, no
action, never the session's device by default. Expected numbers are hand-written; the relay
half (the same rules through the real session) is ``test_understanding_relay.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.memory.embedding import DeterministicEmbedder
from app.voice.understanding import policy
from app.voice.understanding.combine import Candidate
from app.voice.understanding.policy import (
    BAND_HIGH,
    BAND_LOW,
    BAND_MEDIUM,
    DeviceBinding,
    Thresholds,
    decide,
)
from app.voice.understanding.semantic import EntityMatch, SemanticEngine, exemplars_from_cases
from tests.voice_corpus.corpus import all_cases


@dataclass
class Rule:
    intent: str
    match_kind: str = "exact"
    entities: dict[str, str] = field(default_factory=dict)


def _rule(confidence: float = 1.0, intent: str = "app_open") -> Candidate:
    return Candidate(intent=intent, confidence=confidence, source="rule", evidence=("rule",))


def _semantic(intent: str, confidence: float) -> Candidate:
    return Candidate(intent=intent, confidence=confidence, source="semantic", evidence=("sem",))


@pytest.fixture(scope="module")
def engine() -> SemanticEngine:
    return SemanticEngine(DeterministicEmbedder(), exemplars_from_cases(all_cases()))


# --- the thresholds file ----------------------------------------------------------------


def test_the_thresholds_file_sits_beside_its_reader_and_validates() -> None:
    beside = Path(policy.__file__).with_name("thresholds.json")
    assert beside.is_file()
    raw = json.loads(beside.read_text(encoding="utf-8"))
    assert raw["high"] == 0.85 and raw["medium"] == 0.60 and raw["device_min"] == 0.65
    assert policy.load_thresholds() == Thresholds(high=0.85, medium=0.60, device_min=0.65)


@pytest.mark.parametrize(
    "body",
    [
        {"high": 0.6, "medium": 0.85, "device_min": 0.65},  # the bands crossed
        {"high": 0.85, "medium": 0.85, "device_min": 0.65},  # no MEDIUM band at all
        {"high": 1.2, "medium": 0.6, "device_min": 0.65},  # out of [0, 1]
        {"high": 0.85, "medium": 0.0, "device_min": 0.65},  # LOW can never happen
        {"high": 0.85, "medium": 0.6},  # a missing key is not a default
        {"high": "0.85", "medium": 0.6, "device_min": 0.65},  # a string is not a number
        {"high": True, "medium": 0.6, "device_min": 0.65},  # nor is a bool
    ],
)
def test_a_thresholds_file_that_cannot_be_right_is_refused(tmp_path: Path, body: dict) -> None:
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError):
        policy.load_thresholds(path)


@pytest.mark.parametrize(
    ("confidence", "band"),
    [
        (1.0, BAND_HIGH),
        (0.85, BAND_HIGH),
        (0.8499, BAND_MEDIUM),
        (0.60, BAND_MEDIUM),
        (0.5999, BAND_LOW),
        (0.0, BAND_LOW),
    ],
)
def test_the_band_edges(confidence: float, band: str) -> None:
    assert policy.band_of(confidence) == band


# --- decide -----------------------------------------------------------------------------


def test_an_exact_rule_is_high_acts_and_asks_nothing() -> None:
    out = decide([_rule(1.0), _semantic("app_close", 0.79)])
    assert (out.band, out.acts, out.question, out.read_back) == (BAND_HIGH, True, None, False)
    assert out.candidate is not None and out.candidate.intent == "app_open"
    assert out.layer == "rule"


def test_a_confusion_match_is_medium_acts_and_reads_back() -> None:
    out = decide([_rule(0.75)])
    assert (out.band, out.acts, out.question, out.read_back) == (BAND_MEDIUM, True, None, True)


def test_nothing_understood_is_low_with_no_candidate_and_no_action() -> None:
    out = decide([])
    assert (out.band, out.acts, out.candidate, out.question) == (BAND_LOW, False, None, None)
    assert out.confidence == 0.0


def test_a_device_the_layers_bound_bounds_the_confidence() -> None:
    bound = DeviceBinding(alias="ofis", confidence=0.75, layer="normalize")
    out = decide([_rule(1.0)], device=bound)
    assert out.band == BAND_MEDIUM and out.read_back and out.acts
    assert out.confidence == 0.75
    assert out.layer == "normalize"
    assert out.device == bound


def test_a_device_the_owners_own_words_bound_stays_high() -> None:
    out = decide([_rule(1.0)], device=DeviceBinding("ofis", 1.0, "rule"))
    assert out.band == BAND_HIGH and not out.read_back


def test_a_named_machine_nothing_bound_is_one_question_and_no_action() -> None:
    out = decide([_rule(1.0)], device_missing=True, device_aliases=("ofis", "ev"))
    assert out.band == BAND_LOW
    assert out.acts is False
    assert out.device is None  # never the session's device by default
    assert out.question == "Hangi bilgisayarda: ofis mi, ev mi?"
    assert out.question.count("?") == 1


def test_the_question_particle_follows_the_alias() -> None:
    assert policy.device_question(["laptop", "bulut", "iş", "ofis"]) == (
        "Hangi bilgisayarda: laptop mu, bulut mu, iş mi, ofis mi?"
    )
    assert policy.device_question([]).count("?") == 1


def test_a_stronger_reading_than_a_repaired_rule_is_a_question_not_a_guess() -> None:
    out = decide([_semantic("media_play", 0.93), _rule(0.9, "research_open")])
    assert out.band == BAND_LOW and out.acts is False
    assert out.question is not None and out.question.count("?") == 1
    assert "medya" in out.question and "araştırma" in out.question


def test_an_exact_rule_is_never_contested() -> None:
    out = decide([_rule(1.0, "research_open"), _semantic("media_play", 1.0)])
    assert out.band == BAND_HIGH and out.acts and out.question is None


def test_a_semantic_reading_alone_is_recorded_and_never_acted_on() -> None:
    out = decide([_semantic("weather_query", 0.9), _semantic("none", 0.3)])
    assert out.band == BAND_HIGH
    assert out.acts is False  # no rule matched: the relay has no slots to act with
    assert out.question is None
    assert out.layer == "semantic"


def test_a_negated_verb_never_lifts_a_semantic_reading_to_high() -> None:
    plain = decide([_semantic("research_cancel", 0.86)])
    assert plain.band == BAND_HIGH
    negated = decide([_semantic("research_cancel", 0.86)], negated=True)
    assert negated.band == BAND_MEDIUM
    assert negated.confidence < 0.85
    assert negated.acts is False


def test_a_negated_form_the_rule_table_itself_matched_keeps_its_band() -> None:
    out = decide([_rule(1.0, "memory_remember")], negated=True)  # "bunu unutma"
    assert out.band == BAND_HIGH and out.acts


@pytest.mark.parametrize(
    ("sentence", "negated"),
    [
        ("Araştırmayı iptal etme", True),
        ("Hesap makinesini açma", True),
        ("Pencereyi kapatmayın", True),
        ("Bunu silmesin", True),
        ("Araştırmayı iptal et", False),
        ("Hesap makinesini aç", False),
        ("Makineyi kapat", False),  # "makine" ends in -ne, not a negative
    ],
)
def test_negative_imperatives_are_recognised(sentence: str, negated: bool) -> None:
    assert policy.is_negated(sentence) is negated


# --- the audit block (KVKK: no word of the owner's sentence) ------------------------------


def test_the_audit_block_holds_names_and_numbers_and_no_evidence() -> None:
    spoken = "Ofisü bilgisayarında hesap makinesini açın"
    ranked = [
        Candidate(
            "app_open", 1.0, {"app": "calc"}, {}, (f"exemplar {spoken!r} cosine 0.9",), "rule"
        ),
        Candidate("app_close", 0.4, {}, {}, ("device: ofisu~ofis fuzzy 0.80",), "semantic"),
        Candidate("shell_query", 0.3, {}, {}, ("x",), "semantic"),
        Candidate("macro_run", 0.2, {}, {}, ("y",), "semantic"),
    ]
    block = decide(ranked, device=DeviceBinding("ofis", 0.75, "normalize")).audit_block()
    assert block == {
        "layer": "normalize",
        "band": "medium",
        "confidence": 0.75,
        "candidates": [
            {"intent": "app_open", "confidence": 1.0},
            {"intent": "app_close", "confidence": 0.4},
            {"intent": "shell_query", "confidence": 0.3},
        ],
    }
    dumped = json.dumps(block, ensure_ascii=False).casefold()
    for word in ("ofisü", "ofisu", "bilgisayar", "hesap", "makine", "açın", "exemplar", "fuzzy"):
        assert word not in dumped


# --- the device slot (carried from the inspection of layer 2) ----------------------------


class _Engine:
    """An engine whose entity index answers with one hand-written device match."""

    def __init__(self, match: EntityMatch | None) -> None:
        self._match = match

    def entity_index(self, aliases: Any, vocabulary: Any = ()) -> Any:
        found = {"device": self._match} if self._match is not None else {}
        return SimpleNamespace(match=lambda text: found)


def _device(
    heard: str, confidence: float, surface: str = "ofis", value: str = "ofis"
) -> EntityMatch:
    return EntityMatch("device", value, heard, surface, confidence, confidence, confidence)


def test_layer_one_binds_the_device_through_the_confusion_list() -> None:
    bound = policy.bind_device("Ofisü bilgisayarında hesap makinesini açın", aliases=("ofis", "ev"))
    assert bound == DeviceBinding(alias="ofis", confidence=0.75, layer="normalize")


def test_layer_one_binds_a_suffixed_alias_before_the_computer_word() -> None:
    bound = policy.bind_device("Ofisi bilgisayarında hesap makinesini aç", aliases=("ofis", "ev"))
    assert bound == DeviceBinding(alias="ofis", confidence=0.9, layer="normalize")


def test_a_bare_computer_word_never_becomes_a_device(engine: SemanticEngine) -> None:
    """Measured at the inspection: "bilgisayarda" ~ "ev bilgisayarı" scores 0.62-0.64."""
    bound = policy.bind_device(
        "Bilgisayarda hesap makinesini aç",
        aliases=("ev", "ev bilgisayarı", "ofis"),
        engine=engine,
    )
    assert bound is None


def test_the_device_slot_needs_more_than_the_floor_and_an_alias_word() -> None:
    aliases = ("ofis", "ev bilgisayarı")
    text = "Ofsi makinesinde hesap makinesini aç"
    at_floor = policy.bind_device(text, aliases=aliases, engine=_Engine(_device("ofsi", 0.65)))
    assert at_floor is None  # above 0.65, not at it
    above = policy.bind_device(text, aliases=aliases, engine=_Engine(_device("ofsi", 0.66)))
    assert above == DeviceBinding(alias="ofis", confidence=0.66, layer="semantic")
    no_alias_word = policy.bind_device(
        "Bilgisayarda hesap makinesini aç",
        aliases=aliases,
        engine=_Engine(
            _device("bilgisayarda", 0.9, surface="ev bilgisayari", value="ev bilgisayarı")
        ),
    )
    assert no_alias_word is None  # a score alone is not the owner naming a machine


def test_two_aliases_in_one_sentence_bind_nothing() -> None:
    assert (
        policy.bind_device(
            "Ofisü bilgisayarında evdeki hesap makinesini aç", aliases=("ofis", "ev")
        )
        is None
    )


def test_a_device_layer_two_bound_is_never_high() -> None:
    """Not a closed form the owner said: it is always read back, whatever the score."""
    bound = policy.bind_device(
        "Ofsi makinesinde hesap makinesini aç",
        aliases=("ofis",),
        engine=_Engine(_device("ofsi", 0.97)),
    )
    assert bound == DeviceBinding(alias="ofis", confidence=0.84, layer="semantic")
    assert decide([_rule(1.0)], device=bound).band == BAND_MEDIUM


def test_a_confusion_entry_caps_the_layer_two_device(engine: SemanticEngine) -> None:
    """ "Ofisü" is "ofis" through the confusion list: an exact match on a corrected word."""
    bound = policy.bind_device("Ofisü hesap makinesini açın", aliases=("ofis", "ev"), engine=engine)
    assert bound == DeviceBinding(alias="ofis", confidence=0.75, layer="semantic")


def test_an_alias_the_device_port_cannot_bind_is_not_a_device() -> None:
    """The port takes the canonical alias words. A word it would drop must end as the
    question, never as "no device named" - which is the session's own machine."""
    engine = _Engine(_device("bulut", 0.9, surface="bulut", value="bulut"))  # the word, exactly
    assert (
        policy.bind_device(
            "Bulut makinesinde hesap makinesini aç", aliases=("bulut",), engine=engine
        )
        is None
    )
    spoken = _Engine(_device("evi", 0.9, surface="ev bilgisayari", value="ev bilgisayarı"))
    bound = policy.bind_device("Evi makinesinde aç", aliases=("ev bilgisayarı",), engine=spoken)
    assert bound is None  # "ev" is too short to be heard in "evi" by distance
    exact = _Engine(_device("ev", 0.9, surface="ev bilgisayari", value="ev bilgisayarı"))
    bound = policy.bind_device("Ev makinesinde aç", aliases=("ev bilgisayarı",), engine=exact)
    assert bound == DeviceBinding(alias="ev", confidence=0.84, layer="semantic")


# --- no tool schema carries a device slot the model can fill -----------------------------


def test_device_like_argument_keys_are_recognised() -> None:
    assert policy.device_slot_keys({"application": "calc"}) == []
    assert policy.device_slot_keys({"target": "Not Defteri", "targets": ["a"]}) == []
    assert policy.device_slot_keys(
        {
            "application": "calc",
            "device": "ofis",
            "Device-Id": "x",
            "cihaz": "ev",
            "bilgisayar": "ev",
        }
    ) == ["device", "Device-Id", "cihaz", "bilgisayar"]


def test_no_registered_tool_schema_has_a_device_slot_and_every_schema_is_closed() -> None:
    from app.voice.realtime_sessions.tools import default_registry

    registry = default_registry()
    offenders = {}
    open_schemas = []
    for name in registry.names():
        parameters = registry.get(name).parameters
        slots = policy.device_slot_keys(parameters.get("properties") or {})
        if slots:
            offenders[name] = slots
        if parameters.get("additionalProperties") is not False:
            open_schemas.append(name)
    assert offenders == {}
    assert open_schemas == []


# --- the whole reading, layers 1-3 (DeterministicEmbedder) --------------------------------


def _read(text: str, engine: SemanticEngine, rule: Rule | None, **kwargs: Any) -> policy.Decision:
    return policy.read_turn(text, rule=rule, aliases=("ofis", "iş", "ev"), engine=engine, **kwargs)


def test_the_third_trial_sentence_is_medium_on_the_office_pc(engine: SemanticEngine) -> None:
    out = _read(
        "Ofisü bilgisayarında hesap makinesini açın",
        engine,
        Rule("app_open", "exact", {"app": "calc"}),
        names_machine=True,
    )
    assert out.band == BAND_MEDIUM and out.acts and out.read_back
    assert out.device == DeviceBinding("ofis", 0.75, "normalize")
    assert out.question is None


def test_the_plain_sentence_is_high(engine: SemanticEngine) -> None:
    out = _read("Hesap makinesini aç", engine, Rule("app_open", "exact", {"app": "calc"}))
    assert out.band == BAND_HIGH and out.acts and not out.read_back
    assert out.device is None and out.question is None


def test_a_machine_no_layer_can_bind_is_the_question(engine: SemanticEngine) -> None:
    out = _read(
        "Mutfaktaki bilgisayarda hesap makinesini aç",
        engine,
        Rule("app_open", "exact", {"app": "calc"}),
        names_machine=True,
    )
    assert out.band == BAND_LOW and out.acts is False and out.device is None
    assert out.question == "Hangi bilgisayarda: ofis mi, iş mi, ev mi?"


def test_the_language_preference_acts_on_nothing(engine: SemanticEngine) -> None:
    out = _read("Bundan sonra araştırma raporlarını her zaman Türkçe oku.", engine, None)
    assert out.acts is False and out.question is None
    assert out.candidate is not None and out.candidate.intent in {"none", "preference"}


def test_dont_cancel_the_research_is_not_high_and_not_acted(engine: SemanticEngine) -> None:
    out = _read("Araştırmayı iptal etme", engine, None)
    assert out.candidate is not None and out.candidate.intent == "research_cancel"
    assert out.band != BAND_HIGH
    assert out.acts is False


def test_without_an_engine_the_rule_and_layer_one_still_decide() -> None:
    out = policy.read_turn(
        "Ofisü bilgisayarında hesap makinesini açın",
        rule=Rule("app_open", "exact", {"app": "calc"}),
        aliases=("ofis", "ev"),
        names_machine=True,
        engine=None,
    )
    assert out.band == BAND_MEDIUM and out.device is not None and out.device.alias == "ofis"


def test_a_repaired_route_is_a_suffix_dropped_rule() -> None:
    reading = policy.rule_reading("app_open", application="calc", route_repair="polite")
    assert reading is not None and reading.match_kind == "suffix_dropped"
    assert reading.entities == {"app": "calc"}
    assert policy.rule_reading("none") is None
    out = policy.read_turn("Hesap makinesini açar mısın", rule=reading, engine=None)
    assert out.band == BAND_HIGH and out.confidence == 0.9


# --- the owner's answer to the question ----------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "alias"),
    [
        ("Ofis.", "ofis"),
        ("ofiste", "ofis"),
        ("Ev bilgisayarında", "ev"),
        ("Ofis bilgisayarında aç", None),
        ("Hayır", None),
    ],
)
def test_an_answer_is_only_an_alias_phrase(answer: str, alias: str | None) -> None:
    assert policy.answered_alias(answer) == alias


def test_the_read_back_names_the_device_and_the_application() -> None:
    assert (
        policy.read_back("Hesap Makinesi", "ofis")
        == "Ofis cihazında Hesap Makinesi açıyorum efendim."
    )
    assert policy.read_back("Hesap Makinesi", None) == "Hesap Makinesi açıyorum efendim."
