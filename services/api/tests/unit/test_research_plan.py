"""M13 research plan generation."""

from datetime import UTC, datetime, timedelta

import pytest

from app.research.plan import DEFAULT_RECENCY_DAYS, build_plan

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
    assert all(TOPIC.strip() in q or TOPIC.strip() == q for q in plan.queries)


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
