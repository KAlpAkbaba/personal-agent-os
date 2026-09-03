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


__all__ = ["REGISTRY", "SourceRegistryEntry", "feeds_only", "for_topics"]
