"""ResearchReport (spec §3): rendering, provenance gate, excerpt-overlap downgrade."""

import re
from datetime import UTC, datetime

import pytest

from app.research.evidence import (
    STATEMENT_LABEL_MODEL_INFERENCE,
    STATEMENT_LABEL_RECOMMENDATION,
    STATEMENT_LABEL_SOURCE_FACT,
    STATEMENT_LABEL_UNCERTAINTY,
    EvidenceRecord,
)
from app.research.report import (
    DetailSection,
    Finding,
    ProvenanceError,
    ReportStats,
    ReportWindow,
    ResearchReport,
    SourceItem,
    Statement,
    apply_excerpt_overlap_downgrade,
    assign_evidence_ids,
    render_research_markdown,
    require_source_fact_provenance,
    run_provenance_gate,
    strip_dangling_citations,
)

NOW = datetime(2026, 9, 2, tzinfo=UTC)


def _evidence(
    url: str = "https://example.com/a", excerpt: str = "kaynak metni bilgisi"
) -> EvidenceRecord:
    return EvidenceRecord(
        url=url,
        title="Başlık",
        excerpt=excerpt,
        fetched_at=NOW,
        extraction_method="dom_text",
        source_class="news",
    )


def _report(
    *, findings=(), why_it_matters=(), watch_next=(), details=(), uncertainty=(), sources=()
) -> ResearchReport:
    return ResearchReport(
        task_id="t1",
        topic="Test Konusu",
        window=ReportWindow(start=NOW.isoformat(), end=NOW.isoformat(), label="son 3 gün"),
        generated_at=NOW.isoformat(),
        synthesis_provider="deterministic",
        executive_summary="Özet metni.",
        findings=findings,
        why_it_matters=why_it_matters,
        watch_next=watch_next,
        details=details,
        uncertainty=uncertainty,
        sources=sources,
        stats=ReportStats(),
    )


def test_assign_evidence_ids_is_stable_and_idempotent() -> None:
    records = [_evidence("https://a"), _evidence("https://b")]
    first = assign_evidence_ids(records)
    assert [r.id for r in first] == ["e1", "e2"]
    again = assign_evidence_ids(first)
    assert [r.id for r in again] == ["e1", "e2"]  # already-assigned ids are kept


def test_render_contains_structural_sections_in_order() -> None:
    md = render_research_markdown(_report())
    exec_i = md.index("Yönetici Özeti")
    findings_i = md.index("Öne Çıkan Bulgular")
    why_i = md.index("Neden Önemli")
    watch_i = md.index("Takip Edilecekler")
    details_i = md.index("Ayrıntılar")
    sources_i = md.index("Kaynaklar")
    assert exec_i < findings_i < why_i < watch_i < details_i < sources_i


def test_render_findings_carry_eN_citation_markers() -> None:
    report = _report(
        findings=(
            Finding(
                id="f1",
                title="Bulgu 1",
                summary="özet",
                why_it_matters="önemli",
                importance=5,
                label=STATEMENT_LABEL_SOURCE_FACT,
                evidence_ids=("e1",),
            ),
        ),
        sources=(
            SourceItem(
                id="e1",
                url="https://a",
                final_url="https://a",
                title="A",
                publisher="A Yayın",
                source_class="news",
                published_at=None,
                retrieved_at=NOW.isoformat(),
                excerpt="x",
            ),
        ),
    )
    md = render_research_markdown(report)
    assert "[e1]" in md
    assert "Bulgu 1" in md


def test_render_no_details_says_so() -> None:
    md = render_research_markdown(_report())
    assert "(Ayrıntı yok.)" in md


def test_require_source_fact_provenance_rejects_uncited() -> None:
    report = _report(
        why_it_matters=(Statement(text="x", label=STATEMENT_LABEL_SOURCE_FACT, evidence_ids=()),)
    )
    with pytest.raises(ProvenanceError):
        require_source_fact_provenance(report, set())


def test_require_source_fact_provenance_rejects_foreign_evidence_id() -> None:
    report = _report(
        why_it_matters=(
            Statement(text="x", label=STATEMENT_LABEL_SOURCE_FACT, evidence_ids=("e99",)),
        )
    )
    with pytest.raises(ProvenanceError):
        require_source_fact_provenance(report, {"e1"})


def test_require_source_fact_provenance_allows_uncertainty_without_citation() -> None:
    report = _report(uncertainty=(Statement(text="x", label=STATEMENT_LABEL_UNCERTAINTY),))
    require_source_fact_provenance(report, set())  # no raise


def test_excerpt_overlap_downgrade_relabels_unsupported_source_fact() -> None:
    evidence = _evidence(excerpt="tamamen alakasiz bir metin")
    evidence_by_id = {"e1": evidence}
    report = _report(
        findings=(
            Finding(
                id="f1",
                title="Bulgu",
                summary="Ekonomi büyüdü yüzde beş",
                why_it_matters="önemli",
                importance=3,
                label=STATEMENT_LABEL_SOURCE_FACT,
                evidence_ids=("e1",),
            ),
        ),
    )
    downgraded = apply_excerpt_overlap_downgrade(report, evidence_by_id)
    assert downgraded.findings[0].label == STATEMENT_LABEL_MODEL_INFERENCE
    assert downgraded.findings[0].provenance_note


def test_excerpt_overlap_keeps_supported_source_fact() -> None:
    evidence = _evidence(excerpt="Ekonomi büyüdü yüzde beş oranında")
    evidence_by_id = {"e1": evidence}
    report = _report(
        findings=(
            Finding(
                id="f1",
                title="Bulgu",
                summary="Ekonomi büyüdü yüzde beş",
                why_it_matters="önemli",
                importance=3,
                label=STATEMENT_LABEL_SOURCE_FACT,
                evidence_ids=("e1",),
            ),
        ),
    )
    kept = apply_excerpt_overlap_downgrade(report, evidence_by_id)
    assert kept.findings[0].label == STATEMENT_LABEL_SOURCE_FACT
    assert kept.findings[0].provenance_note is None


def test_run_provenance_gate_applies_both_steps() -> None:
    evidence = _evidence(excerpt="alakasiz")
    report = _report(
        findings=(
            Finding(
                id="f1",
                title="B",
                summary="tamamen farklı içerik burada",
                why_it_matters="x",
                importance=2,
                label=STATEMENT_LABEL_SOURCE_FACT,
                evidence_ids=("e1",),
            ),
        ),
    )
    fixed = run_provenance_gate(report, {"e1": evidence})
    assert fixed.findings[0].label == STATEMENT_LABEL_MODEL_INFERENCE


def test_report_as_dict_has_schema_version_1() -> None:
    assert _report().as_dict()["schema_version"] == 1


def test_detail_section_round_trips_through_as_dict() -> None:
    section = DetailSection(
        heading="1. Kaynak",
        statements=(Statement(text="x", label=STATEMENT_LABEL_SOURCE_FACT, evidence_ids=("e1",)),),
    )
    payload = section.as_dict()
    assert payload["heading"] == "1. Kaynak"
    assert payload["statements"][0]["evidence_ids"] == ["e1"]


# --------------------------------------------- dangling citations (MEDIUM-4)


def test_strip_dangling_citations_removes_unknown_ids_from_model_inference() -> None:
    report = _report(
        why_it_matters=(
            Statement(
                text="bu önemli bir gelişme",
                label=STATEMENT_LABEL_MODEL_INFERENCE,
                evidence_ids=("e1", "e99"),
            ),
        ),
    )
    fixed = strip_dangling_citations(report, {"e1"})
    stmt = fixed.why_it_matters[0]
    assert stmt.label == STATEMENT_LABEL_MODEL_INFERENCE  # unchanged: not source_fact
    assert stmt.evidence_ids == ("e1",)  # dangling id removed
    assert stmt.provenance_note and "e99" in stmt.provenance_note


def test_strip_dangling_citations_removes_unknown_ids_from_recommendation() -> None:
    report = _report(
        watch_next=(
            Statement(
                text="bunu takip edin",
                label=STATEMENT_LABEL_RECOMMENDATION,
                evidence_ids=("e42",),
            ),
        ),
    )
    fixed = strip_dangling_citations(report, {"e1"})
    stmt = fixed.watch_next[0]
    assert stmt.label == STATEMENT_LABEL_RECOMMENDATION  # unchanged: not source_fact
    assert stmt.evidence_ids == ()
    assert stmt.provenance_note and "e42" in stmt.provenance_note


def test_strip_dangling_citations_downgrades_source_fact_emptied_by_stripping() -> None:
    report = _report(
        findings=(
            Finding(
                id="f1", title="B", summary="özet", why_it_matters="x", importance=3,
                label=STATEMENT_LABEL_SOURCE_FACT, evidence_ids=("e99",),
            ),
        ),
    )
    fixed = strip_dangling_citations(report, {"e1"})
    finding = fixed.findings[0]
    assert finding.label == STATEMENT_LABEL_MODEL_INFERENCE
    assert finding.evidence_ids == ()
    assert finding.provenance_note and "e99" in finding.provenance_note


def test_strip_dangling_citations_leaves_valid_citations_untouched() -> None:
    report = _report(
        why_it_matters=(
            Statement(text="x", label=STATEMENT_LABEL_MODEL_INFERENCE, evidence_ids=("e1",)),
        ),
    )
    fixed = strip_dangling_citations(report, {"e1", "e2"})
    assert fixed.why_it_matters[0].evidence_ids == ("e1",)
    assert fixed.why_it_matters[0].provenance_note is None


def test_run_provenance_gate_still_hard_rejects_a_source_fact_citing_nothing_at_all() -> None:
    """Stripping dangling ids must not mask the (still hard, unchanged)
    failure mode of a source_fact that never cited anything in the first
    place — that is a stronger signal than a stray bad id and stays a
    ProvenanceError."""
    report = _report(
        why_it_matters=(
            Statement(text="x", label=STATEMENT_LABEL_SOURCE_FACT, evidence_ids=()),
        ),
    )
    with pytest.raises(ProvenanceError):
        run_provenance_gate(report, {})


def test_run_provenance_gate_dangling_id_never_crashes_and_markdown_has_no_orphan_citations() -> (
    None
):
    """End-to-end: a report whose findings/statements cite a mix of real and
    fabricated evidence ids must render Markdown where every [eN] marker has
    a matching Sources entry — never an uncaught ProvenanceError, never a
    dangling [eN] with nothing backing it."""
    evidence = _evidence(url="https://a", excerpt="ekonomi büyüdü yüzde beş oranında")
    report = _report(
        findings=(
            Finding(
                id="f1", title="Başlık", summary="Ekonomi büyüdü yüzde beş", why_it_matters="x",
                importance=4, label=STATEMENT_LABEL_SOURCE_FACT, evidence_ids=("e1", "e404"),
            ),
        ),
        why_it_matters=(
            Statement(
                text="model çıkarımı burada", label=STATEMENT_LABEL_MODEL_INFERENCE,
                evidence_ids=("e404",),
            ),
        ),
        watch_next=(
            Statement(
                text="öneri burada", label=STATEMENT_LABEL_RECOMMENDATION,
                evidence_ids=("e404",),
            ),
        ),
        sources=(
            SourceItem(
                id="e1", url="https://a", final_url="https://a", title="A", publisher="A Yayın",
                source_class="news", published_at=None, retrieved_at=NOW.isoformat(), excerpt="x",
            ),
        ),
    )
    fixed = run_provenance_gate(report, {"e1": evidence})  # never raises despite "e404"

    md = render_research_markdown(fixed)
    source_ids = {s.id for s in fixed.sources}
    for marker in re.findall(r"\[e\d+\]", md):
        eid = marker.strip("[]")
        assert eid in source_ids, f"{marker} in rendered Markdown has no Sources entry"
    # The fabricated id was actually removed, not just tolerated.
    assert "e404" not in fixed.why_it_matters[0].evidence_ids
    assert "e404" not in fixed.watch_next[0].evidence_ids
    assert fixed.findings[0].evidence_ids == ("e1",)  # real id kept, fabricated one dropped
