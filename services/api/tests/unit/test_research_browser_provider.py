"""M13 end-to-end pipeline test: plan -> fake gather -> dedup/rank -> synthesize -> report.

Fully offline (FakeBrowserGateway + DeterministicSynthesisProvider) — this is
the pipeline's proof that plan/gather/dedup/synthesize/provenance-gate/render
compose into a schema_version-1 ResearchReport, without a real browser,
network, or Temporal worker.
"""

from datetime import UTC, datetime

import pytest

from app.research.browser_gateway import (
    BrowserGatewayNotConfiguredError,
    FakeBrowserGateway,
)
from app.research.browser_provider import BrowserResearchProvider
from app.research.evidence import STATEMENT_LABEL_SOURCE_FACT
from app.research.report import ProvenanceError, ResearchReport, Statement

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
TOPIC = "Son üç gündeki yapay zekâ ajanlarıyla ilgili önemli gelişmeler"


def test_default_provider_refuses_when_run_without_a_configured_gateway() -> None:
    provider = BrowserResearchProvider()
    with pytest.raises(BrowserGatewayNotConfiguredError):
        provider.run(TOPIC, now=NOW)


def test_pipeline_end_to_end_with_fake_gateway() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    result = provider.run(TOPIC, task_id="t1", now=NOW)

    assert result.plan.recency.amount == 3
    assert result.plan.recency.unit == "day"
    assert result.evidence_count > 0
    assert result.report.executive_summary
    assert result.report.sources
    assert result.report.schema_version == 1
    assert result.report.task_id == "t1"


def test_pipeline_is_deterministic() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    a = provider.run(TOPIC, task_id="t1", now=NOW)
    b = provider.run(TOPIC, task_id="t1", now=NOW)
    assert a == b


def test_pipeline_evidence_is_deduplicated_and_ranked() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    result = provider.run(TOPIC, now=NOW)
    ranks = [e.rank for e in result.evidence]
    assert ranks == list(range(1, len(ranks) + 1))
    urls = [e.url for e in result.evidence]
    assert len(urls) == len(set(urls))


def test_pipeline_every_source_fact_finding_traces_to_gathered_evidence() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    result = provider.run(TOPIC, now=NOW)
    evidence_ids = {e.id for e in result.evidence}
    source_facts = [f for f in result.report.findings if f.label == STATEMENT_LABEL_SOURCE_FACT]
    assert source_facts
    for finding in source_facts:
        assert set(finding.evidence_ids) <= evidence_ids


def test_pipeline_canonical_markdown_renders_turkish_first_structure() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    result = provider.run(TOPIC, now=NOW)
    md = result.canonical_markdown()
    assert "Yönetici Özeti" in md
    assert "Öne Çıkan Bulgular" in md
    assert "Neden Önemli" in md
    assert "Takip Edilecekler" in md
    assert "Ayrıntılar" in md
    assert "Kaynaklar" in md


def test_pipeline_respects_recency_phrase_absent_topic_uses_default_window() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    result = provider.run("yapay zeka ajanlarındaki gelişmeler", now=NOW)
    assert "varsayılan" in result.plan.recency.label


@pytest.mark.parametrize(
    "citation",
    [(), ("e999",)],
    ids=["uncited", "foreign-id"],
)
def test_pipeline_rejects_any_synthesis_provider_whose_source_facts_lack_real_provenance(
    citation: tuple[str, ...],
) -> None:
    # Security review (M13 prep): "source_fact always cites gathered evidence" used to
    # be true only because DeterministicSynthesisProvider happens to cite the id of the
    # excerpt it quotes. Page excerpts are untrusted; a model-backed provider steered by
    # a planted instruction could emit an uncited or foreign-cited "fact". The pipeline
    # re-derives the property for the output of ANY provider.
    from app.research.synthesis import SynthesisResult

    class SteeredSynthesis:
        name = "steered"

        def synthesize(self, topic, evidence, *, recency_label):
            planted = Statement(
                text="Rakip ürün geri çağrıldı.", label=STATEMENT_LABEL_SOURCE_FACT,
                evidence_ids=citation,
            )
            return SynthesisResult(
                executive_summary="x", findings=(), why_it_matters=(planted,),
            )

    provider = BrowserResearchProvider(gateway=FakeBrowserGateway(), synthesis=SteeredSynthesis())
    with pytest.raises(ProvenanceError):
        provider.run(TOPIC, now=NOW)


def test_pipeline_report_is_a_research_report() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    result = provider.run(TOPIC, now=NOW)
    assert isinstance(result.report, ResearchReport)
