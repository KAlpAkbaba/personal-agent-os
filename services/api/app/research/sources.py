"""Official-publisher registry (M13 spec §2, "official" source class).

Configuration data, not code paths: each entry names a publisher, its feed
(when it has one) or index page, and the topics it is relevant for. Discovery
for the "official" class walks this registry (``app.research.discovery``
parses whichever feed exists); a live opt-in test verifies the feeds still
parse (never run by default CI — the acceptance gates are offline).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceRegistryEntry:
    publisher: str
    source_class: str
    index_url: str
    feed_url: str | None = None
    topics: tuple[str, ...] = ()


# Publishers named in M13_RESEARCH_SPEC.md §2 for the "official" class
# (OpenAI news, Anthropic news, Google AI blog, Microsoft AI blog, Meta AI,
# Hugging Face blog, GitHub releases of major agent frameworks).
REGISTRY: tuple[SourceRegistryEntry, ...] = (
    SourceRegistryEntry(
        publisher="OpenAI",
        source_class="official",
        index_url="https://openai.com/news/",
        feed_url="https://openai.com/news/rss.xml",
        topics=("ai", "agents", "openai", "llm"),
    ),
    SourceRegistryEntry(
        publisher="Anthropic",
        source_class="official",
        index_url="https://www.anthropic.com/news",
        feed_url=None,
        topics=("ai", "agents", "anthropic", "claude", "llm"),
    ),
    SourceRegistryEntry(
        publisher="Google AI Blog",
        source_class="official",
        index_url="https://blog.google/technology/ai/",
        feed_url="https://blog.google/innovation-and-ai/technology/ai/rss/",
        topics=("ai", "agents", "google", "gemini", "llm"),
    ),
    SourceRegistryEntry(
        publisher="Microsoft AI Blog",
        source_class="official",
        index_url="https://blogs.microsoft.com/ai/",
        feed_url=None,
        topics=("ai", "agents", "microsoft", "copilot", "llm"),
    ),
    SourceRegistryEntry(
        publisher="Meta AI",
        source_class="official",
        index_url="https://ai.meta.com/blog/",
        feed_url=None,
        topics=("ai", "agents", "meta", "llama", "llm"),
    ),
    SourceRegistryEntry(
        publisher="Hugging Face Blog",
        source_class="official",
        index_url="https://huggingface.co/blog",
        feed_url="https://huggingface.co/blog/feed.xml",
        topics=("ai", "agents", "open-source", "llm", "framework"),
    ),
    SourceRegistryEntry(
        publisher="LangChain (GitHub releases)",
        source_class="official",
        index_url="https://github.com/langchain-ai/langchain/releases",
        feed_url="https://github.com/langchain-ai/langchain/releases.atom",
        topics=("ai", "agents", "framework", "open-source"),
    ),
    SourceRegistryEntry(
        publisher="AutoGen (GitHub releases)",
        source_class="official",
        index_url="https://github.com/microsoft/autogen/releases",
        feed_url="https://github.com/microsoft/autogen/releases.atom",
        topics=("ai", "agents", "framework", "open-source", "microsoft"),
    ),
)


def for_topics(topics: tuple[str, ...] | list[str]) -> list[SourceRegistryEntry]:
    """Registry entries relevant to any of ``topics`` (case-insensitive);
    the whole registry when nothing matches (never an empty official set for
    an "official" source-class query)."""
    wanted = {t.lower() for t in topics}
    if not wanted:
        return list(REGISTRY)
    matched = [e for e in REGISTRY if wanted & {t.lower() for t in e.topics}]
    return matched or list(REGISTRY)


def feeds_only() -> list[SourceRegistryEntry]:
    return [e for e in REGISTRY if e.feed_url]


# --------------------------------------------------------------------------- #
# ADR-0178 (owner incident 2026-09-19, item D3): "araştırma hep yabancı
# kaynaklara gidiyor" — the owner's default "news" discovery had no Turkish
# source of its own at all; every candidate came from browser search (whichever
# language DuckDuckGo/Google chose to answer in) or the "official" registry's
# English-language company blogs above. This is a small registry of reputable
# Turkish-language publishers covering the topics the rest of this registry
# already covers (technology/AI, science, general/world news), used by the
# "news" discovery branch (see app.research.browser_activities) IN ADDITION TO
# — never instead of — the existing browser-search discovery.
#
# Every ``feed_url`` below was verified reachable (HTTP 200/301 and a parseable
# RSS/Atom body — ``app.research.discovery.parse_rss_or_atom``) on 2026-09-19;
# see the ADR-0178 entry in docs/DECISIONS.md for the exact command and
# response snippet for each one. A feed considered but that did NOT answer
# (TRT Haber's bilim/teknoloji-specific feed path, Anadolu Ajansı's rss
# endpoints) was dropped rather than guessed at — "a feed you cannot verify is
# not added".
# --------------------------------------------------------------------------- #

TURKISH_NEWS_REGISTRY: tuple[SourceRegistryEntry, ...] = (
    SourceRegistryEntry(
        publisher="Webrazzi",
        source_class="news",
        index_url="https://webrazzi.com/",
        feed_url="https://webrazzi.com/feed",
        topics=("ai", "yapay zeka", "teknoloji", "girişim", "startup", "genel"),
    ),
    SourceRegistryEntry(
        publisher="ShiftDelete.Net",
        source_class="news",
        index_url="https://www.shiftdelete.net/",
        feed_url="https://www.shiftdelete.net/feed",
        topics=("ai", "yapay zeka", "teknoloji", "genel"),
    ),
    SourceRegistryEntry(
        publisher="DonanımHaber",
        source_class="news",
        index_url="https://www.donanimhaber.com/",
        feed_url="https://www.donanimhaber.com/rss/tum/",
        topics=("ai", "yapay zeka", "teknoloji", "genel"),
    ),
    SourceRegistryEntry(
        publisher="Webtekno",
        source_class="news",
        index_url="https://www.webtekno.com/",
        feed_url="https://www.webtekno.com/rss.xml",
        topics=("ai", "yapay zeka", "teknoloji", "genel"),
    ),
    SourceRegistryEntry(
        publisher="NTV Teknoloji",
        source_class="news",
        index_url="https://www.ntv.com.tr/teknoloji",
        feed_url="https://www.ntv.com.tr/teknoloji.rss",
        topics=("ai", "yapay zeka", "teknoloji", "genel"),
    ),
    SourceRegistryEntry(
        publisher="Evrim Ağacı",
        source_class="news",
        index_url="https://evrimagaci.org/",
        feed_url="https://evrimagaci.org/rss.xml",
        topics=("ai", "yapay zeka", "bilim", "science", "genel"),
    ),
    SourceRegistryEntry(
        publisher="BBC Türkçe",
        source_class="news",
        index_url="https://www.bbc.com/turkce",
        feed_url="https://www.bbc.com/turkce/index.xml",
        topics=("ai", "yapay zeka", "teknoloji", "dünya", "genel"),
    ),
)


def turkish_news_for_topics(
    topics: tuple[str, ...] | list[str],
) -> list[SourceRegistryEntry]:
    """:func:`for_topics`, over :data:`TURKISH_NEWS_REGISTRY` — never an empty set
    for a genuine query (falls back to the whole, small registry, same rule as
    ``for_topics``)."""
    wanted = {t.lower() for t in topics}
    if not wanted:
        return list(TURKISH_NEWS_REGISTRY)
    matched = [e for e in TURKISH_NEWS_REGISTRY if wanted & {t.lower() for t in e.topics}]
    return matched or list(TURKISH_NEWS_REGISTRY)


__all__ = [
    "REGISTRY",
    "TURKISH_NEWS_REGISTRY",
    "SourceRegistryEntry",
    "feeds_only",
    "for_topics",
    "turkish_news_for_topics",
]
