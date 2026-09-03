"""M13 research plan generation: topic -> queries, source classes, recency.

Pure and deterministic: same ``(topic, now)`` always yields the same
:class:`ResearchPlan`. This is the first stage of the M13 pipeline
(app.research.browser_provider.BrowserResearchProvider); it never touches a
browser, the network or a database.
"""

from __future__ import annotations

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
    queries = tuple(
        dict.fromkeys(template.format(topic=clean_topic).strip() for template in _QUERY_TEMPLATES)
    )
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
    "DEFAULT_MAX_SOURCES_PER_QUERY",
    "DEFAULT_RECENCY_DAYS",
    "DEFAULT_SOURCE_CLASSES",
    "ResearchPlan",
    "build_plan",
]
