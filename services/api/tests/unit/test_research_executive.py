"""M13 executive-assistant structure rendering."""

from app.research.evidence import LabelledStatement
from app.research.executive import DetailSection, ExecutiveReport, render_executive_markdown


def _report(details: tuple[DetailSection, ...] = ()) -> ExecutiveReport:
    return ExecutiveReport(
        topic="Test Konusu",
        recency_label="son 3 gün",
        executive_summary="Özet metni.",
        why_it_matters=LabelledStatement(
            text="Önemlidir çünkü X.", label="model_inference", evidence_urls=("https://a",)
        ),
        recommended_action=LabelledStatement(
            text="Y yapılması önerilir.", label="recommendation", evidence_urls=("https://a",)
        ),
        details=details,
    )


def test_render_contains_all_four_structural_sections_in_order() -> None:
    md = render_executive_markdown(_report())
    exec_i = md.index("Yönetici Özeti")
    why_i = md.index("Neden Önemli")
    action_i = md.index("Önerilen Eylem")
    details_i = md.index("Ayrıntılar")
    assert exec_i < why_i < action_i < details_i


def test_render_includes_labels_on_why_and_recommendation() -> None:
    md = render_executive_markdown(_report())
    assert "[model_inference]" in md
    assert "[recommendation]" in md


def test_render_no_details_says_so() -> None:
    md = render_executive_markdown(_report())
    assert "(Ayrıntı yok.)" in md


def test_render_details_include_heading_label_and_citation() -> None:
    section = DetailSection(
        heading="1. Kaynak Adı",
        statements=(
            LabelledStatement(
                text="Alıntılanan metin.", label="source_fact", evidence_urls=("https://x.example",)
            ),
        ),
    )
    md = render_executive_markdown(_report(details=(section,)))
    assert "1. Kaynak Adı" in md
    assert "[source_fact] Alıntılanan metin." in md
    assert "https://x.example" in md


def test_render_topic_and_recency_label_appear() -> None:
    md = render_executive_markdown(_report())
    assert "Test Konusu" in md
    assert "son 3 gün" in md
