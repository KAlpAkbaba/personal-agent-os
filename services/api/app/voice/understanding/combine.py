"""The combiner (ADR-0224 layer 2): ONE ranked candidate list from the rule tables and the
semantic indexes. Not wired into ``resolve_intent`` yet - the policy task does that.

The rule-table result is candidate #1 with the confidence its match earned: 1.0 for an exact
closed form, 0.9 when layer 1 had to drop a suffix, 0.75 when an STT confusion entry was
used. A semantic intent candidate's confidence is its cosine calibrated by the margin to the
runner-up; an entity's is ``min(vector, fuzzy)`` (see ``semantic``). Evidence names the
words, the exemplar and the distances, so a wrong reading can be read off the trace.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Final, Protocol

from app.memory.embedding import Embedder
from app.voice.understanding.semantic import SemanticEngine

MATCH_EXACT: Final = "exact"
MATCH_SUFFIX_DROPPED: Final = "suffix_dropped"
MATCH_CONFUSION: Final = "confusion"

#: The confidence a rule-table result earns, by how it matched.
RULE_CONFIDENCE: Final[Mapping[str, float]] = {
    MATCH_EXACT: 1.0,
    MATCH_SUFFIX_DROPPED: 0.9,
    MATCH_CONFUSION: 0.75,
}


class RuleResult(Protocol):
    """What the rule tables hand over (duck-typed; ``ResolvedIntent`` is adapted by the
    policy task)."""

    intent: str
    entities: Mapping[str, str]
    match_kind: str


@dataclass(frozen=True, slots=True)
class Candidate:
    intent: str
    confidence: float
    entities: Mapping[str, str] = field(default_factory=dict)
    #: Per-slot confidence (``min(vector, fuzzy)``); a rule-table slot is not listed.
    entity_confidence: Mapping[str, float] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()
    source: str = "semantic"  # "rule" | "semantic"


def rule_candidate(rule_result: RuleResult | None) -> Candidate | None:
    if rule_result is None:
        return None
    kind = rule_result.match_kind
    if kind not in RULE_CONFIDENCE:
        raise ValueError(f"unknown rule match kind {kind!r}")
    return Candidate(
        intent=str(rule_result.intent),
        confidence=RULE_CONFIDENCE[kind],
        entities=dict(rule_result.entities),
        evidence=(f"rule table: {kind} match",),
        source="rule",
    )


def combine(rule: Candidate | None, semantic: Sequence[Candidate]) -> list[Candidate]:
    """Rank by confidence, the rule candidate first among equals. A semantic candidate for the
    rule's own intent is folded into it: the rule's slots win, the semantic ones fill gaps."""
    ranked: list[Candidate] = []
    merged = rule
    for cand in semantic:
        if rule is not None and cand.intent == rule.intent:
            gaps = {k: v for k, v in cand.entities.items() if k not in rule.entities}
            merged = replace(
                merged if merged is not None else rule,
                entities={**rule.entities, **gaps},
                entity_confidence={k: c for k, c in cand.entity_confidence.items() if k in gaps},
                evidence=(merged or rule).evidence + cand.evidence,
            )
        else:
            ranked.append(cand)
    if merged is not None:
        ranked.insert(0, merged)
    # sorted() is stable, so the rule candidate (inserted first) wins ties.
    return sorted(ranked, key=lambda c: -c.confidence)


_default_engine: SemanticEngine | None = None


def configure_default_engine(
    embedder: Embedder, exemplars: Sequence[tuple[str, str]]
) -> SemanticEngine:
    """Build the process-wide engine (once at start-up; call again to reload)."""
    global _default_engine
    _default_engine = SemanticEngine(embedder, exemplars)
    return _default_engine


def default_engine() -> SemanticEngine:
    if _default_engine is None:
        raise RuntimeError("understanding engine not configured: call configure_default_engine")
    return _default_engine


def reset_default_engine() -> None:
    global _default_engine
    _default_engine = None


def understand(
    text: str,
    *,
    device_aliases: Mapping[str, Sequence[str]],
    rule_result: RuleResult | None,
    engine: SemanticEngine | None = None,
    vocabulary: Sequence[tuple[str, str, str]] = (),
) -> list[Candidate]:
    """Rank the readings of ``text``: the rule result (if any) plus the semantic candidates.

    ``device_aliases`` maps a device id to its spoken aliases (from
    ``devices.metadata_json.aliases``; the caller reads them, this never hard-codes one).
    A device the words do not support is absent from ``entities`` - never defaulted.
    """
    eng = engine or default_engine()
    found = eng.entity_index(device_aliases, vocabulary).match(text)
    entities = {kind: m.value for kind, m in found.items()}
    entity_confidence = {kind: m.confidence for kind, m in found.items()}
    entity_evidence = tuple(
        f"{kind}: {m.heard}~{m.surface} fuzzy {m.fuzzy:.2f} vector {m.vector:.2f}"
        f" -> {m.confidence:.2f}"
        for kind, m in found.items()
    )
    semantic = [
        Candidate(
            intent=hit.intent,
            confidence=hit.confidence,
            entities=entities,
            entity_confidence=entity_confidence,
            evidence=(f"exemplar {hit.exemplar!r} cosine {hit.cosine:.2f}", *entity_evidence),
            source="semantic",
        )
        for hit in eng.intent_hits(text)
    ]
    return combine(rule_candidate(rule_result), semantic)
