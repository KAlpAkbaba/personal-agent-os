"""Unit tests: scoring/dedup and canonical report / executive-summary composition."""

from app.research.compose import (
    ScoredSource,
    build_source_manifest,
    compose_canonical_markdown,
    compose_executive_summary,
    score_and_dedup,
)
from app.research.provider import DeterministicResearchProvider, SourceRecord

TOPIC = "yapay zekâ ajanları"


def _sources() -> list[SourceRecord]:
    return DeterministicResearchProvider().gather(TOPIC, limit=6)


def test_dedup_collapses_duplicate_urls_keeping_highest_score() -> None:
    raw = [
        SourceRecord("https://a.example/x", "A", "s", 0.4, "deterministic"),
        SourceRecord("https://a.example/x/", "A", "s", 0.9, "deterministic"),  # dup, higher
        SourceRecord("https://b.example/y", "B", "s", 0.5, "deterministic"),
    ]
    scored = score_and_dedup(raw)
    assert len(scored) == 2
    top = scored[0]
    assert top.url in ("https://a.example/x", "https://a.example/x/")
    assert top.score == 0.9


def test_scoring_orders_by_score_desc_and_ranks() -> None:
    scored = score_and_dedup(_sources())
    scores = [s.score for s in scored]
    assert scores == sorted(scores, reverse=True)
    assert [s.rank for s in scored] == list(range(1, len(scored) + 1))


def test_dedup_is_order_independent() -> None:
    raw = _sources()
    a = score_and_dedup(raw)
    b = score_and_dedup(list(reversed(raw)))
    assert [(s.url, s.score, s.rank) for s in a] == [(s.url, s.score, s.rank) for s in b]


def test_executive_summary_is_short_and_mentions_topic() -> None:
    scored = score_and_dedup(_sources())
    summary = compose_executive_summary(TOPIC, scored)
    assert TOPIC in summary
    assert len(summary) < 600  # executive tier: seconds to read
    assert "\n" not in summary


def test_executive_summary_handles_no_sources() -> None:
    summary = compose_executive_summary(TOPIC, [])
    assert TOPIC in summary


def test_canonical_markdown_has_all_three_sections() -> None:
    scored = score_and_dedup(_sources())
    summary = compose_executive_summary(TOPIC, scored)
    md = compose_canonical_markdown(TOPIC, scored, summary)
    assert md.startswith("# ")
    assert "## Yönetici Özeti (Executive Summary)" in md
    assert "## Ayrıntılı Rapor (Detailed Report)" in md
    assert "## Kaynaklar (Sources / Citations)" in md
    assert summary in md
    # every source is cited
    for s in scored:
        assert f"[{s.rank}]" in md
        assert s.url in md


def test_composition_is_deterministic() -> None:
    scored = score_and_dedup(_sources())
    summary = compose_executive_summary(TOPIC, scored)
    md1 = compose_canonical_markdown(TOPIC, scored, summary)
    md2 = compose_canonical_markdown(TOPIC, scored, summary)
    assert md1 == md2


def test_source_manifest_shape() -> None:
    scored = score_and_dedup(_sources())
    manifest = build_source_manifest(TOPIC, scored)
    assert manifest["topic"] == TOPIC
    assert manifest["source_count"] == len(scored)
    assert len(manifest["sources"]) == len(scored)
    assert isinstance(scored[0], ScoredSource)
