"""M13 research plan generation: topic -> queries, source classes, recency.

Pure and deterministic: same ``(topic, now)`` always yields the same
:class:`ResearchPlan`. This is the first stage of the M13 pipeline
(app.research.browser_provider.BrowserResearchProvider); it never touches a
browser, the network or a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.research.dates import RecencyWindow, default_window, parse_recency_window

DEFAULT_RECENCY_DAYS = 3
DEFAULT_SOURCE_CLASSES: tuple[str, ...] = (
    "news",
    "official",
    "technical",
    "academic",
    "community",
)
DEFAULT_MAX_SOURCES_PER_QUERY = 3

# Query expansion templates. `{topic}` is substituted with the trimmed topic;
# duplicates after substitution are removed (keeps a bare short topic from
# producing three identical queries).
_QUERY_TEMPLATES: tuple[str, ...] = (
    "{topic}",
    "{topic} haberleri",
    "{topic} son gelişmeler",
)

# The owner phrases the topic in Turkish; the primary sources for most technology topics
# publish in English, and the first live run showed that Turkish-only queries fill the
# fetch budget with Turkish news listing pages. Two deterministic expansions follow:
# an English core query built from a small Turkish -> English term map (only when at
# least one mapped term appears; nothing is guessed), and entity sub-queries for the
# AI-agents domain (spec §1: OpenAI / Anthropic / Google-Gemini / Microsoft-Copilot /
# open-source frameworks / launches-papers-incidents). Both are data, extendable
# without touching the pipeline.
_TERM_MAP: tuple[tuple[str, str], ...] = (
    ("yapay zekâ ajanları", "AI agents"),
    ("yapay zeka ajanları", "AI agents"),
    ("yapay zekâ ajanlarıyla", "AI agents"),
    ("yapay zeka ajanlarıyla", "AI agents"),
    ("yapay zekâ ajanı", "AI agent"),
    ("yapay zeka ajanı", "AI agent"),
    ("ajan", "agent"),
    ("yapay zekâ", "AI"),
    ("yapay zeka", "AI"),
    ("büyük dil modeli", "large language model"),
    ("büyük dil modelleri", "large language models"),
    ("dil modeli", "language model"),
    ("siber güvenlik", "cybersecurity"),
    ("gelişmeleri", "developments"),
    ("gelişmeler", "developments"),
    ("gelişme", "development"),
    ("haberleri", "news"),
    ("haberler", "news"),
    ("önemli", "important"),
    ("son", "latest"),
)

# Words that carry no search value once the date phrase and verb are removed.
_DROP_WORDS = frozenset({
    "ilgili", "ile", "ve", "araştır", "araştırın", "bul", "incele", "gündeki", "günde",
    "gün", "üç", "iki", "bir", "dört", "beş", "hafta", "haftadaki", "saat", "saatteki",
    "bugün", "dün", "the", "of", "in", "about", "latest",
})

_AGENT_DOMAIN_MARKERS = ("ajan", "agent")
_AGENT_ENTITY_QUERIES: tuple[str, ...] = (
    "OpenAI agents announcement",
    "Anthropic Claude agents announcement",
    "Google Gemini agents announcement",
    "Microsoft Copilot agents announcement",
    "open-source AI agent framework release",
    "AI agent security incident",
    "AI agents paper",
)


def english_core_query(topic: str) -> str | None:
    """A compact English query for ``topic``, or ``None`` when no known term maps."""
    lowered = " ".join(topic.lower().replace("’", "'").split())
    mapped = False
    for turkish, english in sorted(_TERM_MAP, key=lambda pair: -len(pair[0])):
        if turkish in lowered:
            lowered = lowered.replace(turkish, f" {english} ")
            mapped = True
    if not mapped:
        return None
    words = []
    for raw in lowered.replace(",", " ").replace(".", " ").split():
        word = raw.strip("'\"()[]{}:;!?")
        if not word or word.lower() in _DROP_WORDS:
            continue
        if any(ch in word for ch in "çğıöşü"):
            continue  # an unmapped Turkish word would only mislead an English engine
        words.append(word)
    seen: dict[str, str] = {}
    for w in words:
        seen.setdefault(w.lower(), w)
    query = " ".join(seen.values())
    return query or None


def expand_queries(topic: str) -> tuple[str, ...]:
    """Base Turkish templates + English core query + domain entity sub-queries."""
    base = [template.format(topic=topic).strip() for template in _QUERY_TEMPLATES]
    core = english_core_query(topic)
    if core:
        base.append(core)
        base.append(f"{core} news")
    if any(marker in topic.lower() for marker in _AGENT_DOMAIN_MARKERS):
        base.extend(_AGENT_ENTITY_QUERIES)
    return tuple(dict.fromkeys(q for q in base if q))


def _query_tokens(query: str) -> frozenset[str]:
    lowered = query.lower()
    for ch in ",.:;!?\"'()[]{}":
        lowered = lowered.replace(ch, " ")
    return frozenset(w for w in lowered.split() if w)


def _similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard overlap of two queries' word sets — 1.0 for the same words, 0.0 for
    none in common. Deterministic and language-agnostic: it needs no term list to notice
    that "X" and "X haberleri" ask a search engine almost the same thing."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def diversify_queries(queries: Sequence[str], limit: int) -> tuple[str, ...]:
    """The ``limit`` most DIFFERENT queries from an expansion, first one kept.

    :func:`expand_queries` returns its expansions in a fixed order whose first three
    entries are all Turkish variants of the same phrase ("X", "X haberleri", "X son
    gelişmeler"). Taking the first ``limit`` of that — which is exactly what a QUICK
    run's ``discovery_queries_max`` of 2 did — issues two near-identical searches and
    gets back one search engine's view of one language's coverage. On 2026-09-06 that
    is how a QUICK run reached ~100 candidates dominated by a handful of domains
    (ADR-0074 decision 4).

    Greedy and deterministic: query 0 (the owner's own phrasing) always leads; every
    later pick is the remaining query with the LOWEST word overlap against everything
    already picked, ties broken by the expansion's own order. For a Turkish topic
    naming an English entity this naturally yields a Turkish query and the English
    core query rather than two Turkish ones — no language list, no provider change.
    """
    ordered = [q for q in queries if q and q.strip()]
    if limit <= 0:
        return ()
    if len(ordered) <= limit:
        return tuple(ordered)
    tokens = {q: _query_tokens(q) for q in ordered}
    picked = [ordered[0]]
    remaining = ordered[1:]
    while len(picked) < limit and remaining:
        best_index = 0
        best_score = None
        for index, candidate in enumerate(remaining):
            score = max(_similarity(tokens[candidate], tokens[p]) for p in picked)
            if best_score is None or score < best_score:
                best_score, best_index = score, index
        picked.append(remaining.pop(best_index))
    return tuple(picked)


@dataclass(frozen=True, slots=True)
class ResearchPlan:
    """The M13 plan: what to search for, where, and in what time window."""

    topic: str
    recency: RecencyWindow
    queries: tuple[str, ...]
    source_classes: tuple[str, ...]
    max_sources_per_query: int = DEFAULT_MAX_SOURCES_PER_QUERY

    def as_dict(self) -> dict[str, object]:
        return {
            "topic": self.topic,
            "recency": self.recency.as_dict(),
            "queries": list(self.queries),
            "source_classes": list(self.source_classes),
            "max_sources_per_query": self.max_sources_per_query,
        }


def build_plan(
    topic: str,
    *,
    now: datetime | None = None,
    source_classes: tuple[str, ...] = DEFAULT_SOURCE_CLASSES,
    max_sources_per_query: int = DEFAULT_MAX_SOURCES_PER_QUERY,
    recency_days_override: int | None = None,
) -> ResearchPlan:
    """Build a deterministic research plan for ``topic``.

    The recency window is parsed out of the topic text itself (the owner's
    own phrasing, e.g. "son üç günde ..."); when the topic carries no
    recognizable Turkish relative-date phrase, :func:`app.research.dates.default_window`
    (``DEFAULT_RECENCY_DAYS``) applies instead of guessing. ``recency_days_override``
    (REST ``recency_days``) is the owner's explicit, structured override and
    always wins over both the parsed phrase and the default.
    """
    if not topic or not topic.strip():
        raise ValueError("topic must be a non-empty string")
    if max_sources_per_query < 1:
        raise ValueError("max_sources_per_query must be >= 1")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    clean_topic = topic.strip()
    if recency_days_override is not None:
        if recency_days_override < 1:
            raise ValueError("recency_days_override must be >= 1")
        window = RecencyWindow(
            start=now - timedelta(days=recency_days_override), end=now,
            label=f"son {recency_days_override} gün", amount=recency_days_override, unit="day",
        )
    else:
        window = parse_recency_window(clean_topic, now=now) or default_window(
            now, days=DEFAULT_RECENCY_DAYS
        )
    queries = expand_queries(clean_topic)
    if not source_classes:
        raise ValueError("source_classes must be non-empty")

    return ResearchPlan(
        topic=clean_topic,
        recency=window,
        queries=queries,
        source_classes=tuple(source_classes),
        max_sources_per_query=max_sources_per_query,
    )


__all__ = [
    "diversify_queries",
    "english_core_query",
    "expand_queries",
    "DEFAULT_MAX_SOURCES_PER_QUERY",
    "DEFAULT_RECENCY_DAYS",
    "DEFAULT_SOURCE_CLASSES",
    "ResearchPlan",
    "build_plan",
]
