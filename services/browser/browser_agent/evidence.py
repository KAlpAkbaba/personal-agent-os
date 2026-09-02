"""M13 evidence extraction: turn a fetched page into provenance-bearing evidence.

Design constraints (CLAUDE.md browser rule + M13 spec):

- Extraction goes through the semantic browser API only — DOM text
  (:meth:`~browser_agent.session.BrowserSession.page_text`) preferred, the
  accessibility snapshot as a structural fallback. Never coordinates/vision.
- Every :class:`PageEvidence` item is provenance-complete: the exact URL
  fetched, the page title, when it was fetched, a verbatim quoted excerpt
  (never a paraphrase — paraphrasing belongs to the synthesis layer, which
  runs on the API side and is explicitly labelled ``model_inference``), and
  which extraction method produced the excerpt.
- ``extract_page_evidence`` takes a narrow structural ``PageDriver`` protocol
  rather than ``BrowserSession`` directly, so unit tests exercise the real
  extraction/ranking logic with a lightweight fake and no Playwright browser
  process (:class:`~browser_agent.session.BrowserSession` satisfies the
  protocol as-is; this module never subclasses or mocks it).

This module is pure orchestration + pure functions: no Temporal, no HTTP, no
knowledge of the API service's artifact/task model. It is what would run
in-process on the Windows Browser Agent when a ``browser.fetch_evidence``
command (see docs/DECISIONS.md ADR-0035) is dispatched to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

from .errors import BrowserError

#: Provider-neutral source-class taxonomy (mirrors the categories the M3
#: deterministic corpus already uses informally, made explicit for planning).
SOURCE_CLASSES: tuple[str, ...] = (
    "news",
    "academic",
    "official",
    "technical",
    "community",
    "unknown",
)

#: How the excerpt text was obtained. Ordered best-first (semantic priority).
EXTRACTION_DOM_TEXT = "dom_text"
EXTRACTION_ACCESSIBILITY_SNAPSHOT = "accessibility_snapshot"
EXTRACTION_METHODS: tuple[str, ...] = (
    EXTRACTION_DOM_TEXT,
    EXTRACTION_ACCESSIBILITY_SNAPSHOT,
)

DEFAULT_EXCERPT_CHARS = 500


@runtime_checkable
class PageDriver(Protocol):
    """The minimal semantic surface evidence extraction needs.

    ``browser_agent.session.BrowserSession`` implements this structurally
    (navigate/title/page_text/accessibility_snapshot all exist with matching
    signatures); tests supply a small fake instead of a real browser.
    """

    async def navigate(self, url: str, *, timeout_ms: float = ...) -> None: ...

    async def title(self) -> str: ...

    async def page_text(self, *, timeout_ms: float = ...) -> str: ...

    async def accessibility_snapshot(self, *, timeout_ms: float = ...) -> str: ...


@dataclass(frozen=True, slots=True)
class PageEvidence:
    """One provenance-complete evidence item extracted from a live page."""

    url: str
    title: str
    excerpt: str
    fetched_at: datetime
    extraction_method: str
    source_class: str = "unknown"
    query: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "title": self.title,
            "excerpt": self.excerpt,
            "fetched_at": self.fetched_at.isoformat(),
            "extraction_method": self.extraction_method,
            "source_class": self.source_class,
            "query": self.query,
        }


@dataclass(frozen=True, slots=True)
class FetchFailure:
    """One target that could not be turned into evidence (typed, not raised)."""

    url: str
    error_class: str
    message: str
    query: str = ""


def _normalize_url(url: str) -> str:
    """Collapse trivial URL variants for dedup (query/fragment stripped)."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def _clean_excerpt(text: str, *, max_chars: int) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:max_chars].strip()


async def extract_page_evidence(
    driver: PageDriver,
    url: str,
    *,
    source_class: str = "unknown",
    query: str = "",
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    nav_timeout_ms: float = 15_000,
    clock: type[datetime] | None = None,
) -> PageEvidence:
    """Navigate to ``url`` and extract one provenance-complete evidence item.

    Extraction method preference: DOM text (:meth:`PageDriver.page_text`)
    first; if it comes back empty (e.g. a page that only exposes content via
    ARIA-labelled regions), fall back to the accessibility snapshot. Both are
    semantic reads, never coordinates. Raises whatever typed
    ``BrowserError`` the driver raises (navigation/timeout/etc.) — callers
    doing a multi-source batch should catch it and record a
    :class:`FetchFailure` instead of aborting the whole gather (see
    :mod:`browser_agent.research`).
    """
    clock = clock or datetime
    await driver.navigate(url, timeout_ms=nav_timeout_ms)
    title = await driver.title()
    text = await driver.page_text()
    method = EXTRACTION_DOM_TEXT
    if not text or not text.strip():
        text = await driver.accessibility_snapshot()
        method = EXTRACTION_ACCESSIBILITY_SNAPSHOT
    excerpt = _clean_excerpt(text, max_chars=excerpt_chars)
    return PageEvidence(
        url=url,
        title=title.strip() if title else url,
        excerpt=excerpt,
        fetched_at=clock.now(UTC),
        extraction_method=method,
        source_class=source_class if source_class in SOURCE_CLASSES else "unknown",
        query=query,
    )


@dataclass(frozen=True, slots=True)
class RankedEvidence:
    rank: int
    evidence: PageEvidence
    score: float
    reasons: tuple[str, ...] = field(default_factory=tuple)


# Deterministic per-source-class weight — a stable relative ordering hint,
# re-ranked (never re-scored blindly) by the composer, mirroring
# app.research.compose's provider-score-is-a-hint discipline.
_SOURCE_CLASS_WEIGHT: dict[str, float] = {
    "official": 1.00,
    "academic": 0.90,
    "news": 0.80,
    "technical": 0.75,
    "community": 0.55,
    "unknown": 0.40,
}


def _keyword_overlap(topic: str, text: str) -> float:
    """Deterministic, dependency-free relevance proxy: fraction of topic
    tokens (>=3 chars) that literally appear in the evidence text."""
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


def dedup_and_rank_evidence(
    items: list[PageEvidence],
    *,
    topic: str = "",
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[RankedEvidence]:
    """Dedup by normalized URL (keep the longer/richer excerpt) and rank.

    Deterministic given deterministic inputs: score = source-class weight
    (dominant term) + keyword-overlap term + an in-recency-window bonus,
    ties broken by a stable hash of the URL — never input order, never
    wall-clock. This mirrors ``app.research.compose.score_and_dedup``'s
    contract on the browser side of the M13 pipeline.
    """
    best: dict[str, PageEvidence] = {}
    for item in items:
        key = _normalize_url(item.url)
        current = best.get(key)
        if current is None or len(item.excerpt) > len(current.excerpt):
            best[key] = item

    scored: list[tuple[float, PageEvidence, tuple[str, ...]]] = []
    for item in best.values():
        class_weight = _SOURCE_CLASS_WEIGHT.get(item.source_class, _SOURCE_CLASS_WEIGHT["unknown"])
        overlap = _keyword_overlap(topic, f"{item.title} {item.excerpt}")
        recency = _recency_bonus(item.fetched_at, window_start=window_start, window_end=window_end)
        score = round(0.6 * class_weight + 0.25 * overlap + recency, 4)
        reasons = (
            f"source_class={item.source_class}({class_weight:.2f})",
            f"keyword_overlap={overlap:.2f}",
            f"recency_bonus={recency:.2f}",
        )
        scored.append((score, item, reasons))

    # Ties broken by normalized URL (stable, deterministic); the sha256-based
    # tiebreak helper exists for callers that want a shuffle-resistant order
    # independent of URL lexical order (see tests) but the default ranking
    # here favors the simpler, fully-reproducible URL ordering.
    ordered = sorted(scored, key=lambda t: (-t[0], _normalize_url(t[1].url)))
    return [
        RankedEvidence(rank=i + 1, evidence=item, score=score, reasons=reasons)
        for i, (score, item, reasons) in enumerate(ordered)
    ]


__all__ = [
    "DEFAULT_EXCERPT_CHARS",
    "EXTRACTION_ACCESSIBILITY_SNAPSHOT",
    "EXTRACTION_DOM_TEXT",
    "EXTRACTION_METHODS",
    "SOURCE_CLASSES",
    "BrowserError",
    "FetchFailure",
    "PageDriver",
    "PageEvidence",
    "RankedEvidence",
    "dedup_and_rank_evidence",
    "extract_page_evidence",
]
