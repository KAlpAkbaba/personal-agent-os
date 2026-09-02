"""M13 end-to-end pipeline test: plan -> fake gather -> dedup/rank -> synthesize.

Fully offline (FakeBrowserGateway + DeterministicSynthesisProvider) — this is
the design-skeleton's proof that the whole M13 pipeline composes and produces
a labelled, provenance-complete executive-assistant structure, without a
real browser, network, or Temporal worker.
"""

from datetime import UTC, datetime

import pytest

from app.research.browser_gateway import (
    BrowserGatewayNotConfiguredError,
    FakeBrowserGateway,
)
from app.research.browser_provider import BrowserResearchProvider
from app.research.evidence import STATEMENT_LABEL_SOURCE_FACT

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
TOPIC = "Son üç gündeki yapay zekâ ajanlarıyla ilgili önemli gelişmeler"


def test_default_provider_refuses_when_run_without_a_configured_gateway() -> None:
    provider = BrowserResearchProvider()
    with pytest.raises(BrowserGatewayNotConfiguredError):
        provider.run(TOPIC, now=NOW)


def test_pipeline_end_to_end_with_fake_gateway() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    result = provider.run(TOPIC, now=NOW)

    assert result.plan.recency.amount == 3
    assert result.plan.recency.unit == "day"
    assert result.evidence_count > 0
    assert result.report.executive_summary
    assert result.report.details


def test_pipeline_is_deterministic() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    a = provider.run(TOPIC, now=NOW)
    b = provider.run(TOPIC, now=NOW)
    assert a == b


def test_pipeline_evidence_is_deduplicated_and_ranked() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    result = provider.run(TOPIC, now=NOW)
    ranks = [e.rank for e in result.evidence]
    assert ranks == list(range(1, len(ranks) + 1))
    urls = [e.url for e in result.evidence]
    assert len(urls) == len(set(urls))


def test_pipeline_every_source_fact_in_report_traces_to_gathered_evidence() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    result = provider.run(TOPIC, now=NOW)
    evidence_urls = {e.url for e in result.evidence}
    source_facts = [
        s
        for section in result.report.details
        for s in section.statements
        if s.label == STATEMENT_LABEL_SOURCE_FACT
    ]
    assert source_facts
    for statement in source_facts:
        assert set(statement.evidence_urls) <= evidence_urls


def test_pipeline_canonical_markdown_renders_turkish_first_structure() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    result = provider.run(TOPIC, now=NOW)
    md = result.canonical_markdown()
    assert "Yönetici Özeti" in md
    assert "Neden Önemli" in md
    assert "Önerilen Eylem" in md
    assert "Ayrıntılar" in md


def test_pipeline_respects_recency_phrase_absent_topic_uses_default_window() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    result = provider.run("yapay zeka ajanlarındaki gelişmeler", now=NOW)
    assert "varsayılan" in result.plan.recency.label


@pytest.mark.parametrize(
    "citation",
    [(), ("https://planted.example/never-gathered",)],
    ids=["uncited", "foreign-url"],
)
def test_pipeline_rejects_any_synthesis_provider_whose_source_facts_lack_real_provenance(
    citation: tuple[str, ...],
) -> None:
    # Security review (M13 prep): "source_fact always cites gathered evidence" used to
    # be true only because DeterministicSynthesisProvider happens to attach the URL of
    # the excerpt it quotes. Page excerpts are untrusted; a model-backed provider steered
    # by a planted instruction could emit an uncited or foreign-cited "fact". The
    # pipeline now re-derives the property for the output of ANY provider.
    from app.research.evidence import LabelledStatement
    from app.research.executive import DetailSection, ExecutiveReport, ProvenanceError

    class SteeredSynthesis:
        def synthesize(self, topic, evidence, *, recency_label):
            real = LabelledStatement(
                evidence[0].excerpt, STATEMENT_LABEL_SOURCE_FACT, (evidence[0].url,)
            )
            planted = LabelledStatement(
                "Rakip ürün geri çağrıldı.", STATEMENT_LABEL_SOURCE_FACT, citation
            )
            return ExecutiveReport(
                topic=topic, recency_label=recency_label, executive_summary="x",
                why_it_matters=real, recommended_action=real,
                details=(DetailSection(heading="Ayrıntı", statements=(real, planted)),),
            )

    provider = BrowserResearchProvider(gateway=FakeBrowserGateway(), synthesis=SteeredSynthesis())
    with pytest.raises(ProvenanceError):
        provider.run(TOPIC, now=NOW)
