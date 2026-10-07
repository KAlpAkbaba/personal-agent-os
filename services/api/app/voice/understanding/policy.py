"""The threshold policy (ADR-0224 layer 3): what a ranked candidate list is allowed to DO.

* HIGH (``>= high``): do it, the receipt as today.
* MEDIUM (``medium <= c < high``): do it AND read it back in the same receipt speech
  ("Ofis cihazında Hesap Makinesi açıyorum efendim.") - the read-back is the receipt, never a
  second confirmation (owner rule 2026-09-18/19).
* LOW (``< medium``): ONE question naming the missing entity ("Hangi bilgisayarda: ofis mi,
  ev mi?") or the two best readings; no action and never the session's device by default.

The rule tables stay candidate #1 (``combine``). What this layer adds to them:

* a DEVICE comes from the owner's own words, from layers 1-2 or from the owner's answer to
  the question - never from the model (``device_slot_keys``) and never from a score alone:
  layer 2 fills the slot only above ``device_min`` AND when an alias WORD is in the sentence
  (a bare "bilgisayarda" scored 0.62-0.64 against "ev bilgisayarı");
* a reading no rule matched is RECORDED with its band and never acted on: the relay has no
  slots to act with, and "Bugün nasılsın" scores weather_query 0.6 on the lexical embedder;
* a negative imperative ("iptal etme") never lifts such a reading to HIGH.

The thresholds are package data beside this module, its one reader (``thresholds.json``; the
same decision as ``stt-confusions.json``, ADR-0224 addendum 2).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from app.devices import aliases as device_aliases
from app.logging import get_logger
from app.voice.understanding import fuzzy
from app.voice.understanding.combine import (
    MATCH_CONFUSION,
    MATCH_EXACT,
    MATCH_SUFFIX_DROPPED,
    RULE_CONFIDENCE,
    Candidate,
    RuleResult,
    combine,
    default_engine,
    rule_candidate,
    understand,
)
from app.voice.understanding.normalize import _VERBS, NOUN, Normalized, normalize
from app.voice.understanding.semantic import PREFERENCE_INTENT, SemanticEngine

logger = get_logger("app.voice.understanding.policy")

BAND_HIGH: Final = "high"
BAND_MEDIUM: Final = "medium"
BAND_LOW: Final = "low"

#: Which layer the decision rests on (the audit row's ``understanding.layer``).
LAYER_RULE: Final = "rule"
LAYER_NORMALIZE: Final = "normalize"
LAYER_SEMANTIC: Final = "semantic"
LAYER_ANSWER: Final = "answer"
LAYER_NONE: Final = "none"

THRESHOLDS_FILE: Final = "thresholds.json"
#: The slot a LOW question asks for (``Decision.missing``).
SLOT_DEVICE: Final = "device"

#: Readings that are not an action: nothing is dispatched for them and nobody is asked.
NON_ACTING_INTENTS: Final[frozenset[str]] = frozenset({"none", PREFERENCE_INTENT})
#: The intents whose tool runs on a device the sentence may name. Closed on purpose: "ofis
#: bilgisayarları hakkında araştır" matches the alias "ofis" at 1.0 and names no machine.
DEVICE_SLOT_INTENTS: Final[frozenset[str]] = frozenset({"app_open"})
#: How near a heard word must be to an alias word to count as the owner saying it.
ALIAS_WORD_MIN: Final = 0.75
#: Alias words this short are matched exactly ("ev" is one edit from "ve", "en", "et").
_ALIAS_WORD_FUZZY_CHARS: Final = 4
#: Words of an alias that name no particular machine.
_GENERIC_ALIAS_WORDS: Final[frozenset[str]] = frozenset(
    {"bilgisayar", "bilgisayari", "pc", "cihaz", "cihazi", "makine", "makinesi"}
)
_COMPUTER_STEM: Final = "bilgisayar"
#: How long a question waits for its answer.
ANSWER_WINDOW_S: Final = 120.0
#: A question is the turn's own: a tool call arriving later than this is not that turn's.
QUESTION_FRESH_S: Final = 60.0
_WORD: Final = re.compile(r"\w+", re.UNICODE)
_QUESTION_REPEAT: Final = "Tam anlayamadım efendim; tekrar söyler misiniz?"


# ------------------------------------------------------------------ the thresholds


@dataclass(frozen=True, slots=True)
class Thresholds:
    high: float
    medium: float
    #: Layer 2 fills the device slot only ABOVE this.
    device_min: float


def load_thresholds(path: Path | None = None) -> Thresholds:
    """The bands, from the file beside this module unless a path is given. A file that cannot
    be right (a missing key, a non-number, bands crossed or outside (0, 1]) is refused."""
    if path is None:
        path = Path(__file__).with_name(THRESHOLDS_FILE)
    data = json.loads(path.read_text(encoding="utf-8"))
    values: dict[str, float] = {}
    for key in ("high", "medium", "device_min"):
        raw = data.get(key) if isinstance(data, dict) else None
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise ValueError(f"{THRESHOLDS_FILE}: {key!r} must be a number")
        values[key] = float(raw)
    if not 0.0 < values["medium"] < values["high"] <= 1.0:
        raise ValueError(f"{THRESHOLDS_FILE}: need 0 < medium < high <= 1")
    if not 0.0 < values["device_min"] < 1.0:
        raise ValueError(f"{THRESHOLDS_FILE}: need 0 < device_min < 1")
    return Thresholds(**values)


@lru_cache(maxsize=1)
def _default_thresholds() -> Thresholds:
    return load_thresholds()


def band_of(confidence: float, thresholds: Thresholds | None = None) -> str:
    th = thresholds or _default_thresholds()
    if confidence >= th.high:
        return BAND_HIGH
    if confidence >= th.medium:
        return BAND_MEDIUM
    return BAND_LOW


# ------------------------------------------------------------------ the decision


@dataclass(frozen=True, slots=True)
class DeviceBinding:
    """The device a sentence named, as the owner's alias word, and how sure the binding is."""

    alias: str
    confidence: float
    layer: str


@dataclass(frozen=True, slots=True)
class Decision:
    band: str
    #: The reading the band is about (the rule's when one matched), or None.
    candidate: Candidate | None
    #: The ONE question of a LOW decision that has something to ask, else None.
    question: str | None
    confidence: float = 0.0
    layer: str = LAYER_NONE
    ranked: tuple[Candidate, ...] = field(default_factory=tuple)
    device: DeviceBinding | None = None
    #: The relay may act: a rule matched, the band is not LOW and nothing is being asked.
    acts: bool = False
    #: The slot the question asks for ("device"), when it asks for one.
    missing: str | None = None

    @property
    def read_back(self) -> bool:
        return self.acts and self.band == BAND_MEDIUM

    def candidate_names(self, limit: int = 3) -> tuple[tuple[str, float], ...]:
        return tuple((c.intent, round(c.confidence, 2)) for c in self.ranked[:limit])

    def audit_block(self) -> dict[str, Any]:
        """What the audit row keeps: the layer, the band, the confidence and the candidate
        INTENT names. Never ``Candidate.evidence`` - it carries the owner's own words."""
        return {
            "layer": self.layer,
            "band": self.band,
            "confidence": round(self.confidence, 2),
            "candidates": [
                {"intent": intent, "confidence": confidence}
                for intent, confidence in self.candidate_names()
            ],
        }


def _particle(word: str) -> str:
    """The question particle in harmony with ``word``: ofis mi, laptop mu."""
    vowel = next((c for c in reversed(word.casefold()) if c in "aeıioöuü"), "e")
    return {"a": "mı", "ı": "mı", "e": "mi", "i": "mi", "o": "mu", "u": "mu", "ö": "mü", "ü": "mü"}[
        vowel
    ]


def device_question(aliases: Iterable[str]) -> str:
    """ONE question naming the aliases the owner configured on their devices."""
    words: list[str] = []
    for alias in aliases:
        word = str(alias).strip()
        if word and word.casefold() not in (w.casefold() for w in words):
            words.append(word)
    if not words:
        return "Hangi bilgisayarda açayım efendim?"
    return f"Hangi bilgisayarda: {', '.join(f'{w} {_particle(w)}' for w in words)}?"


def _label(intent: str) -> str | None:
    from app.voice.intent_router import _FAMILY_TR  # the one family-name table (B51)

    return _FAMILY_TR.get(intent.split("_", 1)[0])


def _two_readings_question(first: Candidate, second: Candidate) -> str:
    a, b = _label(first.intent), _label(second.intent)
    if not a or not b or a == b:
        return _QUESTION_REPEAT
    return f"Hangisi efendim: {a} {_particle(a)}, {b} {_particle(b)}?"


def decide(
    candidates: Sequence[Candidate],
    *,
    thresholds: Thresholds | None = None,
    device: DeviceBinding | None = None,
    device_missing: bool = False,
    device_aliases: Iterable[str] = (),
    negated: bool = False,
    rival: str | None = None,
) -> Decision:
    """The band of a ranked list (``combine``) and what follows from it.

    ``device``: the machine the sentence named, when something bound it. ``device_missing``:
    it named one and nothing could. ``negated``: the sentence carries a negative imperative.
    ``rival``: another rule claimed the same words (the router's contested reading) - two
    claimants, neither exact, are never acted on: ONE question naming both.
    """
    th = thresholds or _default_thresholds()
    ranked = tuple(candidates)
    if not ranked:
        return Decision(BAND_LOW, None, None)
    top = ranked[0]
    rule = next((c for c in ranked if c.source == "rule"), None)
    if rule is None:
        # No rule matched: recorded for the calibration, never acted on.
        confidence = min(top.confidence, th.high - 0.01) if negated else top.confidence
        return Decision(
            band_of(confidence, th), top, None, confidence, LAYER_SEMANTIC, ranked, None, False
        )
    if (
        top is not rule
        and top.intent not in NON_ACTING_INTENTS
        and top.confidence >= th.high
        and top.confidence > rule.confidence
    ):
        # A repaired rule route against a stronger reading: a question, never a guess.
        question = _two_readings_question(top, rule)
        return Decision(BAND_LOW, rule, question, rule.confidence, LAYER_SEMANTIC, ranked)
    if rival and rival != rule.intent and rival not in NON_ACTING_INTENTS:
        other = Candidate(rival, rule.confidence, source="rule")
        question = _two_readings_question(rule, other)
        return Decision(
            BAND_LOW,
            rule,
            question,
            min(rule.confidence, th.high - 0.01),
            LAYER_RULE,
            (*ranked, other),
        )
    if device_missing:
        question = device_question(device_aliases)
        return Decision(BAND_LOW, rule, question, 0.0, LAYER_NONE, ranked, missing=SLOT_DEVICE)
    confidence = rule.confidence
    layer = LAYER_RULE
    if device is not None:
        confidence = min(confidence, device.confidence)
        layer = device.layer
    band = band_of(confidence, th)
    return Decision(band, rule, None, confidence, layer, ranked, device, band != BAND_LOW)


# ------------------------------------------------------------------ negation


def _negative_forms() -> frozenset[str]:
    forms: set[str] = set()
    for stem in (*_VERBS, "et", "yap", "ol", "git", "dur", "kes", "bırak"):
        vowel = next((c for c in reversed(stem) if c in "aeıioöuü"), "a")
        front = vowel in "eiöü"
        base = stem + ("me" if front else "ma")
        high = "i" if front else "ı"
        forms |= {base, f"{base}y{high}n", f"{base}y{high}n{high}z", f"{base}s{high}n"}
    return frozenset(forms)


_NEGATIVE_FORMS: Final[frozenset[str]] = _negative_forms()


def is_negated(text: str | Normalized) -> bool:
    """The sentence carries a negative imperative of a known verb ("iptal etme", "açmayın").
    A verbal noun spelled the same ("arama") counts too: the cap this feeds only lowers."""
    tokens = text.tokens if isinstance(text, Normalized) else normalize(text).tokens
    return any(token in _NEGATIVE_FORMS for token in tokens)


# ------------------------------------------------------------------ the device slot


def _alias_words(aliases: Iterable[str], vocabulary: Sequence[tuple[str, str, str]]) -> set[str]:
    surfaces = [*aliases, *(surface for kind, _, surface in vocabulary if kind == "device")]
    words = {fuzzy.fold(word) for surface in surfaces for word in _WORD.findall(str(surface))}
    return words - _GENERIC_ALIAS_WORDS


def _says_an_alias_word(heard: str, words: set[str]) -> bool:
    for said in _WORD.findall(heard):
        folded = fuzzy.fold(said)
        for word in words:
            if folded == word:
                return True
            if len(word) >= _ALIAS_WORD_FUZZY_CHARS and (
                fuzzy.similarity(folded, word) >= ALIAS_WORD_MIN
            ):
                return True
    return False


def _layer_one_binding(norm: Normalized) -> DeviceBinding | None:
    """The alias the rule parser reads once layer 1 has put the word back: a confusion entry
    ("ofisü" -> "ofis") or a suffixed alias before the computer word ("ofisi bilgisayarında")."""
    canonical = device_aliases.CANONICAL_ALIASES
    words: list[str] = []
    dropped = False
    for index, lemma in enumerate(norm.lemmas):
        following = norm.lemmas[index + 1].stem if index + 1 < len(norm.lemmas) else ""
        if (
            lemma.kind == NOUN
            and lemma.suffixes
            and lemma.stem in canonical
            and following == _COMPUTER_STEM
        ):
            words.append(lemma.stem)
            dropped = True
        else:
            words.append(lemma.surface)
    if not dropped and not norm.applied_confusions:
        return None  # the words as heard: the rule parser has already read them
    named = device_aliases.extract_aliases(" ".join(words))
    if len(named) != 1:
        return None
    kind = MATCH_CONFUSION if norm.applied_confusions else MATCH_SUFFIX_DROPPED
    return DeviceBinding(named[0], RULE_CONFIDENCE[kind], LAYER_NORMALIZE)


def bind_device(
    text: str,
    *,
    aliases: Sequence[str],
    engine: SemanticEngine | Any | None = None,
    vocabulary: Sequence[tuple[str, str, str]] = (),
    thresholds: Thresholds | None = None,
) -> DeviceBinding | None:
    """The device a sentence names when the rule parser bound none, or None - never a default.

    Layer 1 first (a confusion entry or a dropped suffix makes the alias readable); then
    layer 2, which fills the slot only ABOVE ``device_min`` and only when an alias word is in
    the sentence. A layer-2 binding is never HIGH - it is not a closed form the owner said,
    so it is always read back - and it is the canonical alias word the device port can be
    bound to, or nothing: a word the port would drop must not become "no device named"."""
    th = thresholds or _default_thresholds()
    norm = normalize(text)
    bound = _layer_one_binding(norm)
    if bound is not None or engine is None or not aliases:
        return bound
    index = engine.entity_index({alias: [alias] for alias in aliases}, vocabulary)
    match = index.match(norm.text).get("device")
    if match is None or match.confidence <= th.device_min:
        return None
    if not _says_an_alias_word(match.heard, _alias_words(aliases, vocabulary)):
        return None
    canonical = device_aliases.extract_alias(str(match.value))
    if canonical is None:
        return None
    ceiling = RULE_CONFIDENCE[MATCH_CONFUSION] if norm.applied_confusions else th.high - 0.01
    return DeviceBinding(canonical, min(float(match.confidence), ceiling), LAYER_SEMANTIC)


def device_slot_keys(arguments: Mapping[str, Any]) -> list[str]:
    """The keys of a tool call (or of a tool schema's properties) that name a DEVICE. The
    model may not fill one: a device comes from the owner's words or the owner's answer."""
    out: list[str] = []
    for key in arguments:
        folded = re.sub(r"[^a-z0-9]+", "", fuzzy.fold(str(key)))
        if any(part in folded for part in _DEVICE_KEY_PARTS):
            out.append(str(key))
    return out


_DEVICE_KEY_PARTS: Final[tuple[str, ...]] = (
    "device",
    "cihaz",
    "machine",
    "computer",
    "bilgisayar",
    "hostname",
)


# ------------------------------------------------------------------ the relay's entry


@dataclass(frozen=True, slots=True)
class _RuleReading:
    intent: str
    entities: Mapping[str, str]
    match_kind: str
    #: Another table's claim on the same words (the router's "contested:<intent>").
    rival: str | None = None


#: Route repairs that changed a WORD, not only a verb's ending: the router gives them the
#: confidence of a confusion, and so does its adapter (one number for one decision).
#: "repaired": layer 1's word repairs (a plural, one typo; ``lemma_reading(repair_words=True)``).
_REPAIRED_WORD_LABELS: Final[frozenset[str]] = frozenset({"fused", "invented", "repaired"})
_CONTESTED_PREFIX: Final = "contested:"


def rule_reading(
    intent: str, *, application: str | None = None, route_repair: str | None = None
) -> RuleResult | None:
    """A rule-table result as candidate #1: exact, or - when the words reached it only
    through a repair reading (polite request, folded letters) - a dropped suffix; a repaired
    word (a fused token split, an invented ending dropped) is a confusion. A contested
    reading carries the other claimant as ``rival``."""
    if intent == "none":
        return None
    entities = {"app": application} if application else {}
    labels = route_repair.split("+") if route_repair else []
    rival = next(
        (
            label[len(_CONTESTED_PREFIX) :]
            for label in labels
            if label.startswith(_CONTESTED_PREFIX)
        ),
        None,
    )
    if not labels:
        kind = MATCH_EXACT
    elif rival or _REPAIRED_WORD_LABELS.intersection(labels):
        kind = MATCH_CONFUSION
    else:
        kind = MATCH_SUFFIX_DROPPED
    return _RuleReading(intent, entities, kind, rival)


def configured_engine() -> SemanticEngine | None:
    """The process-wide layer-2 engine, or None when start-up configured none (the rule
    tables and layer 1 still decide)."""
    try:
        return default_engine()
    except RuntimeError:
        return None


def read_turn(
    text: str,
    *,
    rule: RuleResult | None,
    bound_devices: Sequence[str] = (),
    answered_device: str | None = None,
    names_machine: bool = False,
    aliases: Sequence[str] = (),
    engine: SemanticEngine | None = None,
    vocabulary: Sequence[tuple[str, str, str]] = (),
    thresholds: Thresholds | None = None,
) -> Decision:
    """One sentence through the three layers.

    ``bound_devices``: the alias words the rule parser read in the sentence.
    ``answered_device``: the alias the owner answered the question with.
    ``names_machine``: the sentence names a machine and the rule parser bound none.
    ``aliases``: the aliases the owner configured on the enrolled devices.
    """
    th = thresholds or _default_thresholds()
    norm = normalize(text)
    ranked: list[Candidate] | None = None
    if engine is not None and norm.text:
        try:
            ranked = understand(norm.text, device_aliases={}, rule_result=rule, engine=engine)
        except Exception:  # noqa: BLE001 - layer 2 failing is the rule tables alone, not a dead turn
            logger.warning("understanding_semantic_failed", exc_info=True)
    if ranked is None:
        ranked = combine(rule_candidate(rule), [])
    device: DeviceBinding | None = None
    missing = False
    if rule is not None and rule.intent in DEVICE_SLOT_INTENTS:
        if answered_device:
            device = DeviceBinding(answered_device, 1.0, LAYER_ANSWER)
        elif bound_devices:
            device = DeviceBinding(bound_devices[0], 1.0, LAYER_RULE)
        elif names_machine:
            device = bind_device(
                text, aliases=aliases, engine=engine, vocabulary=vocabulary, thresholds=th
            )
            missing = device is None
    return decide(
        ranked,
        thresholds=th,
        device=device,
        device_missing=missing,
        device_aliases=aliases,
        negated=is_negated(norm),
        rival=getattr(rule, "rival", None),
    )


# ------------------------------------------------------------------ question and answer


def answered_alias(text: str) -> str | None:
    """The alias a sentence that is ONLY a device phrase names ("Ofis.", "ev bilgisayarında"),
    or None: an answer to "Hangi bilgisayarda?" and nothing else."""
    words = _WORD.findall(device_aliases.normalize(text))
    if not words or len(words) > 3:
        return None
    alias = device_aliases.extract_alias(" ".join(words))
    if alias is None:
        return None
    for word in words:
        if word.startswith(_COMPUTER_STEM) or device_aliases.extract_alias(word) == alias:
            continue
        return None
    return alias


def _age_s(raw_at: Any, now: datetime) -> float | None:
    try:
        at = datetime.fromisoformat(str(raw_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=now.tzinfo)
    return (now - at).total_seconds()


def pending_answer(pending: Any, text: str, *, now: datetime) -> tuple[dict[str, Any], str] | None:
    """``(the question's own record, the alias answered)`` when ``text`` answers a device
    question asked within ``ANSWER_WINDOW_S``; None otherwise."""
    if not isinstance(pending, dict) or not isinstance(pending.get("intent"), str):
        return None
    age = _age_s(pending.get("at"), now)
    if age is None or not 0.0 <= age <= ANSWER_WINDOW_S:
        return None
    alias = answered_alias(text)
    return (pending, alias) if alias else None


def turn_question(turn: Any, *, now: datetime) -> str | None:
    """The question the latest utterance's LOW decision asks, while that turn is fresh: every
    tool call of the turn gets the question and runs nothing."""
    block = turn.get("understanding") if isinstance(turn, dict) else None
    if not isinstance(block, dict) or block.get("band") != BAND_LOW:
        return None
    question = block.get("question")
    if not isinstance(question, str) or not question:
        return None
    age = _age_s(turn.get("at"), now)
    if age is None or not 0.0 <= age <= QUESTION_FRESH_S:
        return None
    return question


def turn_reads_back(turn: Any) -> bool:
    """The latest utterance was a MEDIUM decision: its receipt reads the action back."""
    block = turn.get("understanding") if isinstance(turn, dict) else None
    return isinstance(block, dict) and block.get("band") == BAND_MEDIUM


def read_back(application_name: str, device_alias: str | None) -> str:
    """What a MEDIUM launch says before its outcome: the machine and the application."""
    where = f"{device_alias[:1].upper()}{device_alias[1:]} cihazında " if device_alias else ""
    return f"{where}{application_name} açıyorum efendim."


__all__ = [
    "BAND_HIGH",
    "BAND_LOW",
    "BAND_MEDIUM",
    "Decision",
    "DeviceBinding",
    "Thresholds",
    "answered_alias",
    "band_of",
    "bind_device",
    "configured_engine",
    "decide",
    "device_question",
    "device_slot_keys",
    "is_negated",
    "load_thresholds",
    "pending_answer",
    "read_back",
    "read_turn",
    "rule_reading",
    "turn_question",
    "turn_reads_back",
]
