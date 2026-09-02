"""M13 evidence contracts: EvidenceRecord round-trip, labels, dedup/rank."""

from datetime import UTC, datetime

import pytest

from app.research.evidence import (
    STATEMENT_LABELS,
    EvidenceRecord,
    LabelledStatement,
    dedup_and_rank,
)

NOW = datetime(2026, 9, 2, tzinfo=UTC)


def _mk(
    url: str, *, excerpt: str = "bir bulgu metni", source_class: str = "news"
) -> EvidenceRecord:
    return EvidenceRecord(
        url=url,
        title="Başlık",
        excerpt=excerpt,
        fetched_at=NOW,
        extraction_method="dom_text",
        source_class=source_class,
    )


def test_evidence_record_round_trips_through_dict() -> None:
    record = _mk("https://example.com/a")
    restored = EvidenceRecord.from_dict(record.as_dict())
    assert restored == record


def test_evidence_record_from_dict_defaults_optional_fields() -> None:
    minimal = {
        "url": "https://example.com/x",
        "title": "T",
        "excerpt": "e",
        "fetched_at": NOW.isoformat(),
        "extraction_method": "dom_text",
    }
    record = EvidenceRecord.from_dict(minimal)
    assert record.source_class == "unknown"
    assert record.rank == 0
    assert record.score == 0.0


@pytest.mark.parametrize("label", list(STATEMENT_LABELS))
def test_labelled_statement_accepts_every_known_label(label: str) -> None:
    LabelledStatement(text="bir ifade", label=label)  # no raise


def test_labelled_statement_rejects_unknown_label() -> None:
    with pytest.raises(ValueError):
        LabelledStatement(text="bir ifade", label="opinion")


def test_labelled_statement_rejects_empty_text() -> None:
    with pytest.raises(ValueError):
        LabelledStatement(text="   ", label="source_fact")


def test_labelled_statement_has_provenance_property() -> None:
    with_prov = LabelledStatement(text="x", label="source_fact", evidence_urls=("https://a",))
    without_prov = LabelledStatement(text="x", label="uncertainty")
    assert with_prov.has_provenance is True
    assert without_prov.has_provenance is False


def test_dedup_and_rank_deduplicates_by_normalized_url() -> None:
    a = _mk("https://example.com/x/", excerpt="short")
    b = _mk("https://example.com/x?ref=1", excerpt="a longer richer excerpt of text")
    ranked = dedup_and_rank([a, b])
    assert len(ranked) == 1
    assert ranked[0].excerpt == "a longer richer excerpt of text"
    assert ranked[0].rank == 1


def test_dedup_and_rank_orders_by_source_class_weight() -> None:
    official = _mk("https://gov.example.com/1", source_class="official")
    community = _mk("https://forum.example.com/2", source_class="community")
    ranked = dedup_and_rank([community, official])
    assert ranked[0].source_class == "official"
    assert ranked[0].rank == 1
    assert ranked[0].score > ranked[1].score


def test_dedup_and_rank_is_deterministic_regardless_of_input_order() -> None:
    items = [_mk("https://a.example.com/1"), _mk("https://b.example.com/2")]
    forward = dedup_and_rank(items, topic="konu")
    backward = dedup_and_rank(list(reversed(items)), topic="konu")
    assert [r.url for r in forward] == [r.url for r in backward]


def test_dedup_and_rank_rewards_recency_within_window() -> None:
    from datetime import timedelta

    in_window = _mk("https://example.com/in", excerpt="konu bilgisi")
    out_of_window = EvidenceRecord(
        url="https://example.com/out",
        title="Başlık",
        excerpt="konu bilgisi",
        fetched_at=NOW - timedelta(days=30),
        extraction_method="dom_text",
        source_class="news",
    )
    ranked = dedup_and_rank(
        [out_of_window, in_window],
        topic="konu",
        window_start=NOW - timedelta(days=3),
        window_end=NOW,
    )
    assert ranked[0].url == "https://example.com/in"


def test_dedup_and_rank_rewards_keyword_overlap() -> None:
    relevant = _mk("https://example.com/rel", excerpt="yapay zeka ajanlari haberleri")
    irrelevant = _mk("https://example.com/irrel", excerpt="tamamen farkli bir konu")
    ranked = dedup_and_rank([irrelevant, relevant], topic="yapay zeka ajanlari")
    assert ranked[0].url == "https://example.com/rel"


def test_dedup_and_rank_empty_input() -> None:
    assert dedup_and_rank([]) == []
