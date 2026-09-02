"""M13 evidence + labelled-statement contracts, and dedup/ranking (API side).

``EvidenceRecord`` is the JSON-serializable shape evidence crosses the
API<->Windows-Browser-Agent boundary in. It mirrors
``browser_agent.evidence.PageEvidence`` field-for-field, but this module does
NOT import the browser package: the two services run as separate
processes/machines (Hetzner Cloud Core vs. the owner's Windows box) and only
ever agree on a wire shape, exactly like the device-command envelope in
``packages/protocol/DEVICE_PROTOCOL.md`` agrees on JSON, not Python types.
The ranking formula is likewise intentionally duplicated (not imported) from
``browser_agent.evidence.dedup_and_rank_evidence`` for the same reason; keep
the two in sync by hand if the formula changes (see docs/DECISIONS.md
ADR-0035).

``LabelledStatement`` is the M13 spec's core content-integrity requirement:
"Every statement is labelled source_fact | model_inference | recommendation |
uncertainty and carries provenance." The label is validated at construction;
provenance (``evidence_urls``) is NOT hard-required by the dataclass itself
(an ``uncertainty`` statement about a total absence of sources has nothing to
cite) — instead each synthesis provider is responsible for actually attaching
provenance, and the synthesis unit tests assert it does.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

STATEMENT_LABEL_SOURCE_FACT = "source_fact"
STATEMENT_LABEL_MODEL_INFERENCE = "model_inference"
STATEMENT_LABEL_RECOMMENDATION = "recommendation"
STATEMENT_LABEL_UNCERTAINTY = "uncertainty"
STATEMENT_LABELS: tuple[str, ...] = (
    STATEMENT_LABEL_SOURCE_FACT,
    STATEMENT_LABEL_MODEL_INFERENCE,
    STATEMENT_LABEL_RECOMMENDATION,
    STATEMENT_LABEL_UNCERTAINTY,
)

SOURCE_CLASSES: tuple[str, ...] = (
    "news",
    "academic",
    "official",
    "technical",
    "community",
    "unknown",
)


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One provenance-complete evidence item (wire shape)."""

    url: str
    title: str
    excerpt: str
    fetched_at: datetime
    extraction_method: str
    source_class: str = "unknown"
    query: str = ""
    rank: int = 0
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "excerpt": self.excerpt,
            "fetched_at": self.fetched_at.isoformat(),
            "extraction_method": self.extraction_method,
            "source_class": self.source_class,
            "query": self.query,
            "rank": self.rank,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceRecord:
        return cls(
            url=str(data["url"]),
            title=str(data["title"]),
            excerpt=str(data["excerpt"]),
            fetched_at=datetime.fromisoformat(str(data["fetched_at"])),
            extraction_method=str(data["extraction_method"]),
            source_class=str(data.get("source_class", "unknown")),
            query=str(data.get("query", "")),
            rank=int(data.get("rank", 0)),
            score=float(data.get("score", 0.0)),
        )


def validate_label(label: str) -> str:
    if label not in STATEMENT_LABELS:
        raise ValueError(f"unknown statement label {label!r}; must be one of {STATEMENT_LABELS}")
    return label


@dataclass(frozen=True, slots=True)
class LabelledStatement:
    """One synthesized claim, always labelled, ideally carrying provenance."""

    text: str
    label: str
    evidence_urls: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        validate_label(self.label)
        if not self.text or not self.text.strip():
            raise ValueError("statement text must be a non-empty string")

    @property
    def has_provenance(self) -> bool:
        return bool(self.evidence_urls)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "label": self.label,
            "evidence_urls": list(self.evidence_urls),
        }


# --------------------------------------------------------------- dedup/rank


def _normalize_url(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


_SOURCE_CLASS_WEIGHT: dict[str, float] = {
    "official": 1.00,
    "academic": 0.90,
    "news": 0.80,
    "technical": 0.75,
    "community": 0.55,
    "unknown": 0.40,
}


def _keyword_overlap(topic: str, text: str) -> float:
    topic_tokens = {t for t in topic.lower().split() if len(t) >= 3}
    if not topic_tokens:
        return 0.0
    lowered = text.lower()
    hits = sum(1 for t in topic_tokens if t in lowered)
    return hits / len(topic_tokens)


def _recency_bonus(
    fetched_at: datetime, *, window_start: datetime | None, window_end: datetime | None
) -> float:
    if window_start is None or window_end is None:
        return 0.0
    if window_start <= fetched_at <= window_end:
        return 0.15
    return 0.0


def dedup_and_rank(
    records: list[EvidenceRecord],
    *,
    topic: str = "",
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[EvidenceRecord]:
    """Dedup by normalized URL (keep the richer excerpt) and rank.

    Deterministic given deterministic inputs — see the module docstring for
    why this duplicates (rather than imports) ``browser_agent.evidence``'s
    formula. Returns NEW records with ``rank``/``score`` populated; input
    records are never mutated (frozen dataclass).
    """
    best: dict[str, EvidenceRecord] = {}
    for r in records:
        key = _normalize_url(r.url)
        current = best.get(key)
        if current is None or len(r.excerpt) > len(current.excerpt):
            best[key] = r

    scored: list[tuple[float, EvidenceRecord]] = []
    for r in best.values():
        weight = _SOURCE_CLASS_WEIGHT.get(r.source_class, _SOURCE_CLASS_WEIGHT["unknown"])
        overlap = _keyword_overlap(topic, f"{r.title} {r.excerpt}")
        recency = _recency_bonus(r.fetched_at, window_start=window_start, window_end=window_end)
        score = round(0.6 * weight + 0.25 * overlap + recency, 4)
        scored.append((score, r))

    ordered = sorted(scored, key=lambda t: (-t[0], _normalize_url(t[1].url)))
    return [replace(r, rank=i + 1, score=score) for i, (score, r) in enumerate(ordered)]


__all__ = [
    "SOURCE_CLASSES",
    "STATEMENT_LABELS",
    "STATEMENT_LABEL_MODEL_INFERENCE",
    "STATEMENT_LABEL_RECOMMENDATION",
    "STATEMENT_LABEL_SOURCE_FACT",
    "STATEMENT_LABEL_UNCERTAINTY",
    "EvidenceRecord",
    "LabelledStatement",
    "dedup_and_rank",
    "validate_label",
]
