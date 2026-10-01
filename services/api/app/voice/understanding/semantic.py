"""Semantic match with a confidence (ADR-0224 layer 2): the intent and entity indexes.

Both indexes are embedded with the ``Embedder`` the memory layer already runs
(``LocalEmbedder`` in production, ``DeterministicEmbedder`` in tests), once at build; the
per-sentence cost is one embedding of the sentence plus one per fuzzy-plausible word span.

* INTENT index - exemplar sentences per intent (the corpus's canonical cases, handed in by
  the caller: production code never imports ``tests/``) plus a small built-in ``preference``
  family, so a standing preference ("bundan sonra her zaman Türkçe oku") has a home that is
  not an action. Cosine against the sentence; the confidence is the cosine calibrated by the
  margin to the runner-up (``calibrate``).
* ENTITY index - applications (the allow-list and its Turkish names), device aliases (passed
  in by the caller from ``devices.metadata_json.aliases`` - never hard-coded) and vocabulary
  synonyms. A word span is matched by BOTH the vector and the fuzzy similarity, and the
  entity's confidence is ``min(vector, fuzzy)``: a lexically far entity cannot win on a near
  vector, and a span whose fuzzy similarity is below ``FUZZY_FLOOR`` is dropped, never
  defaulted.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from app.memory.embedding import Embedder
from app.voice.understanding import fuzzy

PREFERENCE_INTENT: Final = "preference"
#: A span whose fuzzy similarity is below this is not the entity (and a device is then absent).
FUZZY_FLOOR: Final = 0.6
#: A cosine margin this wide to the runner-up earns the full confidence factor.
MARGIN_FULL: Final = 0.10
_FACTOR_FLOOR: Final = 0.6
#: Words of this many folded characters or fewer never start an entity span.
_MIN_SPAN_CHARS: Final = 3
_MAX_SPAN_WORDS: Final = 3
#: Intents scored exactly after the centroid pass (the dependency-free stand-in for a matrix
#: product: thousands of exemplars x 256 floats in pure Python would cost ~20 ms a sentence).
_CENTROID_SHORTLIST: Final = 20
_TOP_HITS: Final = 3
_WORD = re.compile(r"\w+", re.UNICODE)

PREFERENCE_EXEMPLARS: Final[tuple[str, ...]] = (
    "bundan sonra raporları her zaman Türkçe oku",
    "bundan sonra her zaman Türkçe anlat",
    "artık hep Türkçe konuş",
    "bundan böyle cevapları daima kısa ver",
    "bundan sonra daha yavaş oku",
    "bundan sonra her zaman sesi kısık tut",
)


class _Case(Protocol):
    utterance: str
    expected_intent: str | None


def exemplars_from_cases(cases: Iterable[_Case]) -> list[tuple[str, str]]:
    """One exemplar per distinct sentence (by folded form); a case with no intent is skipped."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for case in cases:
        if case.expected_intent is None:
            continue
        key = fuzzy.fold(case.utterance)
        if key in seen:
            continue
        seen.add(key)
        out.append((case.expected_intent, case.utterance))
    return out


def calibrate(cosine: float, runner_up: float) -> float:
    """``cosine`` scaled by how far it leads the runner-up: factor 0.6 with no lead, rising
    linearly to 1.0 at a lead of ``MARGIN_FULL``. Clamped to [0, 1]."""
    margin = max(0.0, cosine - runner_up)
    factor = _FACTOR_FLOOR + (1.0 - _FACTOR_FLOOR) * min(1.0, margin / MARGIN_FULL)
    return max(0.0, min(1.0, cosine * factor))


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


@dataclass(frozen=True, slots=True)
class IntentHit:
    intent: str
    cosine: float
    confidence: float
    exemplar: str


@dataclass(frozen=True, slots=True)
class EntityMatch:
    kind: str
    value: str
    heard: str
    surface: str
    vector: float
    fuzzy: float
    confidence: float  # min(vector, fuzzy)


class IntentIndex:
    def __init__(self, embedder: Embedder, exemplars: Iterable[tuple[str, str]]) -> None:
        by_intent: dict[str, list[tuple[str, list[float]]]] = {}
        for intent, text in exemplars:
            by_intent.setdefault(intent, []).append((text, embedder.embed(text)))
        self._rows = by_intent
        self._centroids: dict[str, list[float]] = {}
        for intent, rows in by_intent.items():
            mean = [sum(col) / len(rows) for col in zip(*(v for _, v in rows), strict=True)]
            norm = sum(x * x for x in mean) ** 0.5 or 1.0
            self._centroids[intent] = [x / norm for x in mean]

    def __len__(self) -> int:
        return sum(len(r) for r in self._rows.values())

    def hits(self, vec: Sequence[float], top: int = _TOP_HITS) -> list[IntentHit]:
        if not self._rows:
            return []
        shortlist = sorted(self._centroids, key=lambda i: -_dot(vec, self._centroids[i]))
        best: list[tuple[float, str, str]] = []
        for intent in shortlist[:_CENTROID_SHORTLIST]:
            cos, text = max(((_dot(vec, v), t) for t, v in self._rows[intent]), key=lambda p: p[0])
            best.append((cos, intent, text))
        best.sort(key=lambda p: -p[0])
        out: list[IntentHit] = []
        for i, (cos, intent, text) in enumerate(best[:top]):
            runner = best[i + 1][0] if i + 1 < len(best) else 0.0
            out.append(IntentHit(intent, cos, calibrate(cos, runner), text))
        return out


class EntityIndex:
    def __init__(self, embedder: Embedder, entries: Iterable[tuple[str, str, str]]) -> None:
        """``entries``: (kind, value, surface) - kind is ``app``, ``device``, ..."""
        self._embedder = embedder
        self._entries = [
            (kind, value, fuzzy.fold(surface), embedder.embed(surface))
            for kind, value, surface in entries
            if fuzzy.fold(surface)
        ]
        self._span_vectors: dict[str, list[float]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def _span_vector(self, span: str) -> list[float]:
        vec = self._span_vectors.get(span)
        if vec is None:
            vec = self._embedder.embed(span)
            self._span_vectors[span] = vec
        return vec

    def match(self, text: str) -> dict[str, EntityMatch]:
        """The best match per kind for the word spans of ``text``."""
        words = _WORD.findall(_lower(text))
        found: dict[str, EntityMatch] = {}
        for start in range(len(words)):
            for size in range(1, _MAX_SPAN_WORDS + 1):
                chunk = words[start : start + size]
                if len(chunk) < size:
                    break
                span = " ".join(chunk)
                heard = fuzzy.fold(span)
                if len(heard) <= _MIN_SPAN_CHARS - 1:
                    continue
                for kind, value, surface, surface_vec in self._entries:
                    sim = fuzzy.similarity(heard, surface)
                    if sim < FUZZY_FLOOR:
                        continue
                    vector = max(0.0, _dot(self._span_vector(span), surface_vec))
                    conf = min(vector, sim)
                    cur = found.get(kind)
                    if cur is None or (conf, size) > (cur.confidence, cur.heard.count(" ") + 1):
                        found[kind] = EntityMatch(kind, value, heard, surface, vector, sim, conf)
        return found


class SemanticEngine:
    """The two indexes behind one object: built once per process, reloadable."""

    def __init__(
        self,
        embedder: Embedder,
        exemplars: Iterable[tuple[str, str]],
        *,
        applications: Iterable[tuple[str, str]] | None = None,
    ) -> None:
        self.embedder = embedder
        self._applications = list(applications) if applications is not None else None
        self._entity_cache: dict[str, EntityIndex] = {}
        self.entity_builds = 0
        self.intent_builds = 0
        self.reload(exemplars)

    def reload(self, exemplars: Iterable[tuple[str, str]]) -> None:
        rows = list(exemplars) + [(PREFERENCE_INTENT, t) for t in PREFERENCE_EXEMPLARS]
        self._intents = IntentIndex(self.embedder, rows)
        self.intent_builds += 1

    def intent_hits(self, text: str, top: int = _TOP_HITS) -> list[IntentHit]:
        return self._intents.hits(self.embedder.embed(text), top)

    def _application_surfaces(self) -> list[tuple[str, str]]:
        if self._applications is not None:
            return self._applications
        from app.operator import allowlists

        out: list[tuple[str, str]] = []
        for app in allowlists.APPLICATIONS:
            out.append((app.id, app.name_tr))
            out.extend((app.id, alias) for alias in app.aliases)
        return out

    def entity_index(
        self,
        device_aliases: Mapping[str, Sequence[str]],
        vocabulary: Sequence[tuple[str, str, str]] = (),
    ) -> EntityIndex:
        """The entity index for these aliases/vocabulary; rebuilt only when they changed."""
        key = _digest(device_aliases, vocabulary)
        index = self._entity_cache.get(key)
        if index is None:
            entries: list[tuple[str, str, str]] = [
                ("app", app_id, surface) for app_id, surface in self._application_surfaces()
            ]
            for device, aliases in device_aliases.items():
                entries.extend(("device", device, alias) for alias in aliases)
            entries.extend(vocabulary)
            index = EntityIndex(self.embedder, entries)
            if len(self._entity_cache) >= 8:
                self._entity_cache.clear()
            self._entity_cache[key] = index
            self.entity_builds += 1
        return index


def _digest(device_aliases: Mapping[str, Sequence[str]], vocabulary: Sequence[Any]) -> str:
    payload = {
        "devices": sorted((d, sorted(a)) for d, a in device_aliases.items()),
        "vocabulary": sorted(list(v) for v in vocabulary),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
