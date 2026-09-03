"""app.research.sources: the official-publisher registry (configuration data)."""

from app.research import sources


def test_registry_entries_have_official_source_class() -> None:
    assert all(e.source_class == "official" for e in sources.REGISTRY)


def test_registry_is_non_empty_and_covers_named_publishers() -> None:
    names = {e.publisher for e in sources.REGISTRY}
    assert "OpenAI" in names
    assert "Anthropic" in names
    assert "Google AI Blog" in names


def test_for_topics_matches_relevant_publishers() -> None:
    matched = sources.for_topics(("anthropic",))
    assert any(e.publisher == "Anthropic" for e in matched)


def test_for_topics_falls_back_to_full_registry_when_nothing_matches() -> None:
    matched = sources.for_topics(("kelime-yok-boyle-bir-konu",))
    assert matched == list(sources.REGISTRY)


def test_for_topics_empty_input_returns_full_registry() -> None:
    assert sources.for_topics(()) == list(sources.REGISTRY)


def test_feeds_only_returns_entries_with_a_feed_url() -> None:
    feeds = sources.feeds_only()
    assert feeds
    assert all(e.feed_url for e in feeds)
