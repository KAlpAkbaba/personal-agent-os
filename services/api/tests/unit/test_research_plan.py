"""M13 research plan generation."""

from datetime import UTC, datetime, timedelta

import pytest

from app.research.plan import (
    DEFAULT_RECENCY_DAYS,
    build_plan,
    english_core_query,
    expand_queries,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
TOPIC = "Son üç gündeki yapay zekâ ajanlarıyla ilgili önemli gelişmeler"


def test_build_plan_is_deterministic() -> None:
    a = build_plan(TOPIC, now=NOW)
    b = build_plan(TOPIC, now=NOW)
    assert a == b


def test_build_plan_uses_recency_phrase_from_topic() -> None:
    plan = build_plan(TOPIC, now=NOW)
    assert plan.recency.amount == 3
    assert plan.recency.unit == "day"
    assert plan.recency.start == NOW - timedelta(days=3)


def test_build_plan_falls_back_to_default_window_without_recency_phrase() -> None:
    plan = build_plan("yapay zeka ajanlarındaki gelişmeler", now=NOW)
    assert plan.recency.amount == DEFAULT_RECENCY_DAYS
    assert "varsayılan" in plan.recency.label


def test_build_plan_generates_multiple_distinct_queries() -> None:
    plan = build_plan(TOPIC, now=NOW)
    assert len(plan.queries) >= 2
    assert len(plan.queries) == len(set(plan.queries))
    assert sum(1 for q in plan.queries if TOPIC.strip() in q) >= 2


def test_build_plan_short_topic_dedups_identical_query_templates() -> None:
    # "{topic}" and "{topic} haberleri" never collide, but this guards the
    # dict.fromkeys() dedup path itself stays exercised for a trivial topic.
    plan = build_plan("AI", now=NOW)
    assert len(plan.queries) == len(set(plan.queries))


def test_build_plan_default_source_classes_are_used() -> None:
    plan = build_plan(TOPIC, now=NOW)
    assert "news" in plan.source_classes
    assert "official" in plan.source_classes


def test_build_plan_custom_source_classes() -> None:
    plan = build_plan(TOPIC, now=NOW, source_classes=("news",))
    assert plan.source_classes == ("news",)


def test_build_plan_rejects_empty_topic() -> None:
    with pytest.raises(ValueError):
        build_plan("   ", now=NOW)


def test_build_plan_rejects_empty_source_classes() -> None:
    with pytest.raises(ValueError):
        build_plan(TOPIC, now=NOW, source_classes=())


def test_build_plan_rejects_non_positive_max_sources() -> None:
    with pytest.raises(ValueError):
        build_plan(TOPIC, now=NOW, max_sources_per_query=0)


def test_plan_as_dict_is_json_shaped() -> None:
    plan = build_plan(TOPIC, now=NOW)
    d = plan.as_dict()
    assert d["topic"] == TOPIC.strip()
    assert isinstance(d["queries"], list)
    assert isinstance(d["recency"], dict)
    assert "start" in d["recency"] and "end" in d["recency"]


def test_plan_adds_english_core_query_and_agent_entity_subqueries() -> None:
    from app.research.plan import english_core_query

    plan = build_plan("Son üç gündeki yapay zekâ ajanlarıyla ilgili önemli gelişmeleri araştır.")
    core = english_core_query(
        "Son üç gündeki yapay zekâ ajanlarıyla ilgili önemli gelişmeleri araştır."
    )
    assert core is not None and "AI agents" in core and "important" in core
    assert not any(ch in core for ch in "çğıöşü")
    assert core in plan.queries
    assert "OpenAI agents announcement" in plan.queries
    assert "AI agent security incident" in plan.queries
    assert len(plan.queries) == len(set(plan.queries))


def test_plan_without_a_mappable_term_stays_turkish_only() -> None:
    from app.research.plan import english_core_query

    assert english_core_query("Kadıköy'de iyi bir fırın") is None
    plan = build_plan("Kadıköy'de iyi bir fırın")
    assert all("announcement" not in q for q in plan.queries)


# ---------------------------------------------------------------------------
# ADR-0178 (owner incident 2026-09-19, item D1): "araştırma başarısız oldu" traced to
# a production plan whose queries included the doubled "yapay zeka ile ilgili
# haberleri haberleri" and "AI news news" — a live QUICK run (discovery_queries_max=2)
# spent one of its two discovery-query slots on a near-duplicate of another.
# ---------------------------------------------------------------------------

OWNER_UTTERANCE = "yapay zeka ile ilgili haberleri"


def test_expand_queries_never_doubles_a_word_the_topic_already_ends_with() -> None:
    from app.research.plan import expand_queries

    queries = expand_queries(OWNER_UTTERANCE)
    for q in queries:
        words = q.lower().split()
        assert all(a != b for a, b in zip(words, words[1:], strict=False)), q
    assert "yapay zeka ile ilgili haberleri haberleri" not in queries
    assert "AI news news" not in queries
    # The exact production query IS still one of the expansions (query 0, the
    # owner's own phrasing) — nothing here removes it, only what got APPENDED to it.
    assert OWNER_UTTERANCE in queries


def test_expand_queries_still_doubles_normally_when_the_topic_does_not_already_end_that_way() -> (
    None
):
    """The anti-doubling guard is specific to "the topic already ends with the exact
    word being appended" — an ordinary topic still gets both templates."""
    from app.research.plan import expand_queries

    queries = expand_queries("yapay zeka ajanları")
    assert "yapay zeka ajanları haberleri" in queries
    assert "yapay zeka ajanları son gelişmeler" in queries


def test_expand_queries_adds_the_bare_subject_shape_the_owner_asked_for() -> None:
    """docs/DECISIONS.md ADR-0178: the owner's own desired shape — the topic reduced
    to its bare subject, "haberleri"/"son gelişmeler" appended exactly once."""
    from app.research.plan import expand_queries

    queries = expand_queries(OWNER_UTTERANCE)
    assert "yapay zeka haberleri" in queries
    assert "yapay zeka son gelişmeler" in queries


def test_bare_subject_reduces_the_owners_utterance() -> None:
    from app.research.plan import _bare_subject

    assert _bare_subject("yapay zeka ile ilgili haberleri") == "yapay zeka"
    assert _bare_subject("yapay zeka ajanları hakkında") == "yapay zeka ajanları"


def test_bare_subject_never_returns_empty_or_touches_the_front() -> None:
    from app.research.plan import _bare_subject

    # A leading relative-date phrase ("son üç günde") is dates.py's job, not this
    # one's — it must survive untouched even though "son" also appears in the
    # trailing-filler vocabulary.
    subject = _bare_subject("son üç günde yapay zeka haberleri")
    assert subject.startswith("son üç günde")
    assert subject == "son üç günde yapay zeka"
    assert _bare_subject("haberleri") == "haberleri"  # entirely filler: unchanged


def test_looks_turkish_detects_ascii_typed_turkish_and_lets_english_through() -> None:
    from app.research.plan import looks_turkish

    assert looks_turkish("yapay zeka ile ilgili haberleri") is True
    assert looks_turkish("yapay zekâ ajanları") is True  # has a Turkish-specific letter
    assert looks_turkish("AI agents announcement") is False
    assert looks_turkish("") is False


def test_build_plan_narrows_source_classes_for_a_plain_turkish_news_topic() -> None:
    """D4: HN/arXiv discovery is skipped by default for a general Turkish news
    request with no technical/academic/agent marker — the production run spent two
    of its five source-class x query discovery passes on APIs that answered nothing
    usable for exactly this shape of request."""
    plan = build_plan(OWNER_UTTERANCE, now=NOW)
    assert "technical" not in plan.source_classes
    assert "academic" not in plan.source_classes
    assert "news" in plan.source_classes
    assert "official" in plan.source_classes


def test_build_plan_keeps_technical_and_academic_for_an_agent_topic_even_in_turkish() -> None:
    plan = build_plan("yapay zeka ajanlarıyla ilgili haberler", now=NOW)
    assert "technical" in plan.source_classes
    assert "academic" in plan.source_classes


def test_build_plan_keeps_technical_and_academic_for_an_english_topic() -> None:
    """The narrowing never applies to an English-worded topic — nothing about this
    heuristic should make an English request MORE restrictive than before."""
    plan = build_plan("AI news this week", now=NOW)
    assert "technical" in plan.source_classes
    assert "academic" in plan.source_classes


def test_build_plan_respects_an_explicit_source_classes_override() -> None:
    """D4's narrowing only ever applies to the DEFAULT — a caller naming explicit
    classes (e.g. REST's discovery_source_classes) always gets exactly that."""
    plan = build_plan(OWNER_UTTERANCE, now=NOW, source_classes=("news", "technical"))
    assert plan.source_classes == ("news", "technical")


# ---------------------------------------------- ADR-0182: the suffix the map ate around


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        # Production plan 59bdf846 (2026-09-20): the owner said "yapay zeka haberlerini
        # araştır", the topic kept the accusative suffix, and the term map replaced the
        # "haberleri" INSIDE "haberlerini" - leaving the orphan "ni" in a query that was
        # then sent to a search engine: "AI news ni", and "AI news ni news" after it.
        ("Yapay Zeka haberlerini", "AI news"),
        ("yapay zeka haberleri", "AI news"),
        ("yapay zeka ajanlarını", "AI agents"),
        ("yapay zekâ gelişmelerini", "AI developments"),
    ],
)
def test_a_turkish_suffix_is_eaten_with_the_word_not_left_behind(topic: str, expected: str) -> None:
    assert english_core_query(topic) == expected


@pytest.mark.parametrize("topic", ["sonuç raporu", "sonra gelen yapay zeka", "ajanda yapay zeka"])
def test_a_word_that_merely_begins_like_a_term_is_left_alone(topic: str) -> None:
    """The map's shortest entries ("son", "ajan") must not eat the start of another word:
    a query with "latestuç" or "agentda" in it is worse than no English query at all."""
    core = english_core_query(topic) or ""
    for mangled in ("latestuc", "latestuç", "agentda", "agentda", " ni", "news ni"):
        assert mangled not in core.lower(), (topic, core)


def test_the_suffixed_news_word_does_not_earn_a_second_news_query() -> None:
    """ "... haberlerini" is "... haberleri" with a suffix: the anti-doubling guard read the
    ENDING and so did not recognise it, and the plan spent a slot on
    "Yapay Zeka haberlerini haberleri" (production 2026-09-20)."""
    queries = expand_queries("Yapay Zeka haberlerini")
    for query in queries:
        lowered = query.lower()
        assert "haberlerini haberleri" not in lowered, queries
        assert "news ni" not in lowered, queries
        assert lowered.count("news") <= 1, queries
