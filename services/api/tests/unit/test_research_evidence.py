"""M13 evidence contracts: EvidenceRecord round-trip, labels, dedup/rank."""

from datetime import UTC, datetime

import pytest

from app.research.evidence import (
    STATEMENT_LABELS,
    EvidenceRecord,
    LabelledStatement,
    content_text,
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


# ---------------------------------------- near-duplicate-title syndication


def _titled(url: str, title: str, *, source_class: str = "news") -> EvidenceRecord:
    return EvidenceRecord(
        url=url,
        title=title,
        excerpt="içerik metni burada",
        fetched_at=NOW,
        extraction_method="dom_text",
        source_class=source_class,
    )


def test_near_duplicate_titles_across_distinct_urls_mark_syndication() -> None:
    """Two different URLs whose (normalized) titles share >=0.9 token
    Jaccard are near-duplicates (spec §2) — syndication, not a dedup-by-URL
    case (their normalized URLs differ)."""
    official = _titled(
        "https://openai.com/a",
        "OpenAI announces new agent framework today",
        source_class="official",
    )
    community = _titled(
        "https://forum.example.com/b",
        "OpenAI announces new agent framework today",
        source_class="community",
    )
    ranked = dedup_and_rank([official, community])
    assert len(ranked) == 2  # both kept (still stored/auditable), not dropped
    primary = next(r for r in ranked if r.source_class == "official")
    copy = next(r for r in ranked if r.source_class == "community")
    assert primary.syndicated_of is None
    assert copy.syndicated_of == primary.url


def test_near_duplicate_title_syndication_prefers_higher_priority_source_class() -> None:
    """official > technical > academic > news > community > unknown (spec
    §2) — the primary must always be the higher-priority class regardless of
    input order."""
    technical = _titled(
        "https://news.ycombinator.com/x",
        "Multi agent systems survey released",
        source_class="technical",
    )
    academic = _titled(
        "https://arxiv.org/abs/1",
        "Multi agent systems survey released",
        source_class="academic",
    )
    ranked = dedup_and_rank([academic, technical])  # input order deliberately reversed
    academic_r = next(r for r in ranked if r.source_class == "academic")
    technical_r = next(r for r in ranked if r.source_class == "technical")
    assert technical_r.syndicated_of is None  # technical outranks academic
    assert academic_r.syndicated_of == technical_r.url


def test_near_duplicate_title_syndication_is_order_independent() -> None:
    a = _titled(
        "https://gov.example.com/1", "Major policy update announced", source_class="official"
    )
    b = _titled(
        "https://forum.example.com/2", "Major policy update announced", source_class="community"
    )
    forward = dedup_and_rank([a, b])
    backward = dedup_and_rank([b, a])

    def primary_url(records: list[EvidenceRecord]) -> str:
        return next(r.url for r in records if r.syndicated_of is None)

    assert primary_url(forward) == primary_url(backward) == "https://gov.example.com/1"


def test_dissimilar_titles_are_not_marked_as_syndicated() -> None:
    a = _titled("https://a.example.com/1", "Completely unrelated headline about weather")
    b = _titled("https://b.example.com/2", "A totally different story on economics")
    ranked = dedup_and_rank([a, b])
    assert all(r.syndicated_of is None for r in ranked)


# ------------------------------------------- typed field contracts (2026-09-04 incident)


def _stored(**overrides) -> dict:
    base = {
        "url": "https://example.com/1",
        "title": "Kaynak başlığı",
        "excerpt": "Yapay zekâ ajanları hakkında bir cümle.",
        "fetched_at": "2026-09-04T10:00:00+00:00",
        "extraction_method": "dom_text",
        "source_class": "news",
        "rank": 2,
        "score": 1.5,
    }
    base.update(overrides)
    return base


def test_stored_evidence_round_trips_with_valid_numbers() -> None:
    record = EvidenceRecord.from_dict(_stored())
    assert record.rank == 2 and record.score == 1.5
    assert EvidenceRecord.from_dict(_stored(rank="3", score="0.5")).rank == 3


def test_prose_or_a_date_in_a_stored_numeric_field_is_a_named_violation() -> None:
    """A stored row whose rank/score carries text is quarantined by the caller, never turned
    into a ValueError that fails the whole research job (owner run f6eb5021)."""
    from app.research.contracts import ContractViolation

    for field_name, value in (("rank", "Bu model, yapay zeka..."), ("score", "2026-09-04")):
        with pytest.raises(ContractViolation) as excinfo:
            EvidenceRecord.from_dict(_stored(**{field_name: value}))
        detail = excinfo.value.as_dict()
        assert detail["field"] == field_name
        assert detail["entity"] == "evidence_item"
        assert detail["observed_class"] in ("prose_text", "date_like_string")
        assert detail["entity_id"] == "https://example.com/1"


def test_a_number_in_a_stored_text_field_is_a_named_violation() -> None:
    from app.research.contracts import ContractViolation

    with pytest.raises(ContractViolation) as excinfo:
        EvidenceRecord.from_dict(_stored(title=5))
    assert excinfo.value.as_dict()["reason"] == "number_is_not_text"


# ---------------------------------------------------------------------------
# content_text (2026-09-19 incident, docs/DECISIONS.md ADR addendum after ADR-0173)
#
# Production run 817c558a: every evidence excerpt was exactly 1200 chars and started
# with page chrome ("Hacker Newsnew | past | comments | ask ...", "Gaming Industry /
# Features / By Wes Fenlon / Published ...") because innerText extraction reads
# top-to-bottom and the fetch cap was spent entirely on nav/byline text before any
# real prose. These two fixtures are shaped exactly like those two pages.
# ---------------------------------------------------------------------------

_HN_REAL_SENTENCE = (
    "OpenAI duyurdu: yeni ajan çerçevesi büyük dil modellerini araç çağırma "
    "protokolüyle birleştiriyor. Şirket, geliştiricilerin otonom görev planlaması "
    "yapabilen ajanlar inşa etmesini kolaylaştırdığını belirtti. Kurumsal erişimin "
    "önümüzdeki hafta başlayacağı açıklandı."
)

_HN_SHAPED_EXCERPT = (
    "Hacker Newsnew | past | comments | ask | show | jobs | submit\n"
    "login\n"
    "OpenAI announces new agent framework\n"
    "128 points by someone 3 hours ago | hide | past | favorite | 42 comments\n"
    f"{_HN_REAL_SENTENCE}"
)

_PCGAMER_REAL_PARAGRAPH = (
    "AI vibe coding is reshaping how indie developers ship games faster than ever, "
    "according to several studio founders interviewed this week. The tools let a "
    "coding agent iteratively write and test small changes while a human reviews the "
    "diff, cutting weeks of engineering time down to days for some prototypes."
)

_PCGAMER_SHAPED_EXCERPT = (
    f"Gaming Industry / Features / By Wes Fenlon / Published 2 hours ago\n{_PCGAMER_REAL_PARAGRAPH}"
)


def test_content_text_drops_hacker_news_chrome_and_keeps_the_real_sentence() -> None:
    survivor = content_text(_HN_SHAPED_EXCERPT)
    assert _HN_REAL_SENTENCE in survivor
    assert "Hacker Newsnew" not in survivor
    assert "login" not in survivor.splitlines()
    assert "128 points" not in survivor


def test_content_text_drops_pcgamer_breadcrumb_byline_and_keeps_the_paragraph() -> None:
    survivor = content_text(_PCGAMER_SHAPED_EXCERPT)
    assert _PCGAMER_REAL_PARAGRAPH in survivor
    assert "Gaming Industry" not in survivor
    assert "By Wes Fenlon" not in survivor
    assert "Published" not in survivor


def test_content_text_falls_back_to_raw_excerpt_when_nothing_survives() -> None:
    all_chrome = "Home | Reviews | Deals\nBy Someone\nPublished today"
    assert content_text(all_chrome) == all_chrome


def test_content_text_is_a_noop_on_a_plain_single_line_excerpt() -> None:
    """A single-line excerpt with no newlines to filter on (e.g. the offline test
    gateway's synthetic pages) survives unchanged rather than being misclassified
    as one giant chrome line."""
    plain = (
        "Yapay zeka ajanları konusunda haber kaynağında yer alan gelişme duyuruldu ve "
        "ilk kullanıcılara açıldı, kurumsal erişimin bu hafta başlayacağı belirtildi."
    )
    assert content_text(plain) == plain


def test_content_text_handles_empty_and_blank_input() -> None:
    assert content_text("") == ""
    assert content_text("   ") == "   "
