"""Summary mode (docs/M27_LATEST_NEWS_MODE_SPEC.md §6): "Haberleri özetle." routes
through the EXISTING M13 research pipeline — never a second research engine, never
playback. This module holds only the small, pure part specific to news: the topic text
and the tight recency window; ``app.research.service.start_browser_research`` /
``start_browser_research_workflow`` do everything else (device selection, discovery,
synthesis, provenance, the durable artifact) exactly as they do for any other research
request, so a news summary gets the SAME source-provenance guarantees a spoken
"araştır" already has.
"""

from __future__ import annotations

from app.news.sources_service import NewsSourceView

#: A tight window: "today's news", not a multi-day digest. The owner can still ask for
#: a wider one in ordinary research ("son üç günün haberlerini araştır").
DEFAULT_SUMMARY_RECENCY_DAYS = 1

DEFAULT_SUMMARY_TOPIC_TR = (
    "Bugünkü önemli gündem ve haber gelişmelerini araştır ve özetle; kaynaklarını ver."
)


def summary_topic(source: NewsSourceView | None) -> str:
    """The research topic for a news summary. Naming the configured source (when one
    is given and resolved) keeps the run on-topic rather than "the news" in general;
    a caller with no source (or an unresolved one) still gets a legitimate general
    current-events summary rather than a refusal — summarizing does not need a
    channel identity the way playback does."""
    if source is not None and source.channel_id is not None:
        return (
            f"{source.display_name} kanalının bugünkü önemli haberlerini ve gündemini "
            "araştır ve özetle; kaynaklarını ver."
        )
    return DEFAULT_SUMMARY_TOPIC_TR


__all__ = ["DEFAULT_SUMMARY_RECENCY_DAYS", "DEFAULT_SUMMARY_TOPIC_TR", "summary_topic"]
