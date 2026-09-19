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


# --------------------------------------------------------------------------- #
# ADR-0178 (owner incident 2026-09-19, item D3): a small registry of verified
# Turkish-language RSS feeds. Every ``feed_url`` here was probed live on 2026-09-19
# (HTTP 200/301 and a parseable RSS/Atom body) — see docs/DECISIONS.md ADR-0178 for
# the exact commands and responses, and for the feeds that were tried and DROPPED
# because they did not answer (TRT Haber's bilim-teknoloji-specific path, Anadolu
# Ajansı).
# --------------------------------------------------------------------------- #


def test_turkish_news_registry_entries_are_news_class_with_a_feed_url() -> None:
    assert sources.TURKISH_NEWS_REGISTRY
    assert all(e.source_class == "news" for e in sources.TURKISH_NEWS_REGISTRY)
    assert all(e.feed_url for e in sources.TURKISH_NEWS_REGISTRY)


def test_turkish_news_registry_covers_the_verified_publishers() -> None:
    names = {e.publisher for e in sources.TURKISH_NEWS_REGISTRY}
    for expected in (
        "Webrazzi",
        "ShiftDelete.Net",
        "DonanımHaber",
        "Webtekno",
        "NTV Teknoloji",
        "Evrim Ağacı",
        "BBC Türkçe",
    ):
        assert expected in names


def test_turkish_news_registry_does_not_include_unverifiable_feeds() -> None:
    """TRT Haber's bilim-teknoloji-specific feed and Anadolu Ajansı's RSS endpoints
    were tried and did not answer — "a feed you cannot verify is not added"."""
    names = {e.publisher for e in sources.TURKISH_NEWS_REGISTRY}
    assert "TRT Haber" not in names
    assert "Anadolu Ajansı" not in names


def test_turkish_news_for_topics_matches_relevant_publishers() -> None:
    matched = sources.turkish_news_for_topics(("bilim",))
    assert any(e.publisher == "Evrim Ağacı" for e in matched)


def test_turkish_news_for_topics_falls_back_to_the_whole_small_registry() -> None:
    assert sources.turkish_news_for_topics(("kelime-yok-boyle-bir-konu",)) == list(
        sources.TURKISH_NEWS_REGISTRY
    )
    assert sources.turkish_news_for_topics(()) == list(sources.TURKISH_NEWS_REGISTRY)


def test_turkish_news_registry_is_independent_of_the_official_registry() -> None:
    """The official REGISTRY (English-language company blogs) must stay exactly as
    it was — this is a SEPARATE registry, additive, never a replacement."""
    official_names = {e.publisher for e in sources.REGISTRY}
    turkish_names = {e.publisher for e in sources.TURKISH_NEWS_REGISTRY}
    assert not (official_names & turkish_names)
