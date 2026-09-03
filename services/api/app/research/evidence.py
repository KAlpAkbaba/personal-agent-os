"""M13 evidence + labelled-statement contracts, and dedup/rank/syndication.

``EvidenceRecord`` is the JSON-serializable shape evidence crosses the
API<->Windows-Browser-Agent boundary in (M13_RESEARCH_SPEC.md §1: "Evidence —
EvidenceRecord extended with id, final_url, publisher, published_at,
modified_at, retrieved_at, page_kind, http_status, injection_suspected,
device_id, command_id"). This module does NOT import the browser package: the
two services run as separate processes/machines (Hetzner Cloud Core vs. the
owner's Windows box) and only ever agree on a wire shape, exactly like the
device-command envelope in ``packages/protocol/DEVICE_PROTOCOL.md`` agrees on
JSON, not Python types. The ranking formula is likewise intentionally
duplicated (not imported) from ``browser_agent.evidence.dedup_and_rank_evidence``
for the same reason; keep the two in sync by hand if the formula changes (see
docs/DECISIONS.md ADR-0035/ADR-0050).

``LabelledStatement`` is the M13 spec's core content-integrity requirement:
"Every statement is labelled source_fact | model_inference | recommendation |
uncertainty and carries provenance." The label is validated at construction;
provenance (``evidence_urls``) is NOT hard-required by the dataclass itself
(an ``uncertainty`` statement about a total absence of sources has nothing to
cite) — instead each synthesis provider is responsible for actually attaching
provenance, and the synthesis unit tests assert it does.

Dedup/ranking (spec §2): canonical-URL dedup keeping the richer excerpt, then
near-duplicate TITLE detection (normalized token Jaccard >= 0.9) across
different URLs — the lower-priority copy (official > technical > academic >
news > community > unknown) is kept in the output (still stored, still
scored) but marked ``syndicated_of`` the primary's URL, so downstream report
building can exclude it from findings/sources while the record itself stays
auditable. Ranking: ``0.6*source_class_weight + 0.25*keyword_overlap +
0.15*recency_bonus``, ties broken by normalized URL.
"""

from __future__ import annotations

import re
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
    "official",
    "technical",
    "academic",
    "news",
    "community",
    "unknown",
)

# Primary-source priority for syndication tie-breaks (spec §2): lower is
# preferred (kept as primary when two evidence items share a near-duplicate
# title).
_PUBLISHER_PRIORITY: dict[str, int] = {cls: i for i, cls in enumerate(SOURCE_CLASSES)}

PAGE_KIND_OK = "ok"
PAGE_KIND_AUTH_WALL = "auth_wall"
PAGE_KIND_CAPTCHA = "captcha"
PAGE_KIND_ERROR_PAGE = "error_page"
PAGE_KIND_BLOCKED = "blocked"
PAGE_KIND_EMPTY = "empty"
PAGE_KINDS: tuple[str, ...] = (
    PAGE_KIND_OK,
    PAGE_KIND_AUTH_WALL,
    PAGE_KIND_CAPTCHA,
    PAGE_KIND_ERROR_PAGE,
    PAGE_KIND_BLOCKED,
    PAGE_KIND_EMPTY,
)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))


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
    # ---- M13 spec §1 provenance extension ----
    id: str = ""
    final_url: str = ""
    publisher: str = ""
    published_at: datetime | None = None
    modified_at: datetime | None = None
    retrieved_at: datetime | None = None
    page_kind: str = PAGE_KIND_OK
    http_status: int | None = None
    injection_suspected: bool = False
    device_id: str | None = None
    command_id: str | None = None
    syndicated_of: str | None = None

    def __post_init__(self) -> None:
        if self.retrieved_at is None:
            object.__setattr__(self, "retrieved_at", self.fetched_at)
        if not self.final_url:
            object.__setattr__(self, "final_url", self.url)

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
            "id": self.id,
            "final_url": self.final_url,
            "publisher": self.publisher,
            "published_at": _iso(self.published_at),
            "modified_at": _iso(self.modified_at),
            "retrieved_at": _iso(self.retrieved_at),
            "page_kind": self.page_kind,
            "http_status": self.http_status,
            "injection_suspected": self.injection_suspected,
            "device_id": self.device_id,
            "command_id": self.command_id,
            "syndicated_of": self.syndicated_of,
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
            id=str(data.get("id", "")),
            final_url=str(data.get("final_url", "")),
            publisher=str(data.get("publisher", "")),
            published_at=_parse_dt(data.get("published_at")),
            modified_at=_parse_dt(data.get("modified_at")),
            retrieved_at=_parse_dt(data.get("retrieved_at")),
            page_kind=str(data.get("page_kind", PAGE_KIND_OK)),
            http_status=data.get("http_status"),
            injection_suspected=bool(data.get("injection_suspected", False)),
            device_id=data.get("device_id"),
            command_id=data.get("command_id"),
            syndicated_of=data.get("syndicated_of"),
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
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/")
    # Strip common tracking params (spec §2: "strip tracking params").
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"ref", "fbclid", "gclid"}
    ]
    query = urlencode(kept)
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def _normalize_title_tokens(title: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9çğıöşü]+", title.lower()))


def _title_jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


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


TITLE_DEDUP_JACCARD_THRESHOLD = 0.9


def _mark_syndication(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Near-duplicate-title detection across distinct (already URL-deduped)
    records. The lower-priority source_class copy is marked syndicated_of the
    higher-priority one; ties broken by normalized URL for determinism."""
    tokens = {r.url: _normalize_title_tokens(r.title) for r in records}
    ordered = sorted(
        records,
        key=lambda r: (
            _PUBLISHER_PRIORITY.get(r.source_class, len(SOURCE_CLASSES)),
            _normalize_url(r.url),
        ),
    )
    kept: list[EvidenceRecord] = []
    syndicated_of: dict[str, str] = {}
    for record in ordered:
        primary = next(
            (
                k
                for k in kept
                if _title_jaccard(tokens[record.url], tokens[k.url])
                >= TITLE_DEDUP_JACCARD_THRESHOLD
            ),
            None,
        )
        if primary is not None:
            syndicated_of[record.url] = primary.url
        else:
            kept.append(record)
    return [
        replace(r, syndicated_of=syndicated_of[r.url]) if r.url in syndicated_of else r
        for r in records
    ]


def dedup_and_rank(
    records: list[EvidenceRecord],
    *,
    topic: str = "",
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[EvidenceRecord]:
    """Dedup by normalized URL (keep the richer excerpt), mark near-duplicate
    titles as syndicated (spec §2), then rank.

    Deterministic given deterministic inputs. Returns NEW records with
    ``rank``/``score``/``syndicated_of`` populated; input records are never
    mutated (frozen dataclass).
    """
    best: dict[str, EvidenceRecord] = {}
    for r in records:
        key = _normalize_url(r.final_url or r.url)
        current = best.get(key)
        if current is None or len(r.excerpt) > len(current.excerpt):
            best[key] = r

    deduped = _mark_syndication(list(best.values()))

    scored: list[tuple[float, EvidenceRecord]] = []
    for r in deduped:
        weight = _SOURCE_CLASS_WEIGHT.get(r.source_class, _SOURCE_CLASS_WEIGHT["unknown"])
        overlap = _keyword_overlap(topic, f"{r.title} {r.excerpt}")
        recency = _recency_bonus(r.fetched_at, window_start=window_start, window_end=window_end)
        score = round(0.6 * weight + 0.25 * overlap + recency, 4)
        scored.append((score, r))

    ordered = sorted(scored, key=lambda t: (-t[0], _normalize_url(t[1].url)))
    return [replace(r, rank=i + 1, score=score) for i, (score, r) in enumerate(ordered)]


__all__ = [
    "PAGE_KINDS",
    "PAGE_KIND_AUTH_WALL",
    "PAGE_KIND_BLOCKED",
    "PAGE_KIND_CAPTCHA",
    "PAGE_KIND_EMPTY",
    "PAGE_KIND_ERROR_PAGE",
    "PAGE_KIND_OK",
    "SOURCE_CLASSES",
    "STATEMENT_LABELS",
    "STATEMENT_LABEL_MODEL_INFERENCE",
    "STATEMENT_LABEL_RECOMMENDATION",
    "STATEMENT_LABEL_SOURCE_FACT",
    "STATEMENT_LABEL_UNCERTAINTY",
    "TITLE_DEDUP_JACCARD_THRESHOLD",
    "EvidenceRecord",
    "LabelledStatement",
    "dedup_and_rank",
    "validate_label",
]
