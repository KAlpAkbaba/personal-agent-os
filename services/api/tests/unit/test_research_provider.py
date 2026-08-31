"""Unit tests: DeterministicResearchProvider is reproducible and offline."""

import pytest

from app.research.provider import (
    DeterministicResearchProvider,
    SourceRecord,
    WebResearchProvider,
)

TOPIC = "yapay zekâ ajanlarındaki son gelişmeler"


def test_provider_is_deterministic() -> None:
    p = DeterministicResearchProvider()
    a = p.gather(TOPIC, limit=6)
    b = p.gather(TOPIC, limit=6)
    assert [(s.url, s.title, s.score) for s in a] == [(s.url, s.title, s.score) for s in b]


def test_provider_topic_specific() -> None:
    p = DeterministicResearchProvider()
    a = p.gather("konu bir", limit=6)
    b = p.gather("konu iki", limit=6)
    assert {s.url for s in a} != {s.url for s in b}


def test_provider_returns_source_records_with_scores_in_range() -> None:
    p = DeterministicResearchProvider()
    records = p.gather(TOPIC, limit=6)
    assert records, "provider returned no sources"
    for s in records:
        assert isinstance(s, SourceRecord)
        assert s.url.startswith("https://")
        assert 0.0 <= s.score <= 1.0
        assert s.provider == "deterministic"


def test_provider_injects_a_duplicate_for_dedup_coverage() -> None:
    p = DeterministicResearchProvider()
    records = p.gather(TOPIC, limit=6)
    urls = [s.url for s in records]
    assert len(urls) != len(set(urls)), "expected at least one duplicate URL"


def test_provider_rejects_empty_topic() -> None:
    with pytest.raises(ValueError):
        DeterministicResearchProvider().gather("   ", limit=6)


def test_web_provider_is_seam_only() -> None:
    with pytest.raises(NotImplementedError):
        WebResearchProvider().gather(TOPIC)
