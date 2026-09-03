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


def _steered_provider(citation: tuple[str, ...]) -> BrowserResearchProvider:
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

    return BrowserResearchProvider(gateway=FakeBrowserGateway(), synthesis=SteeredSynthesis())


def test_pipeline_rejects_a_source_fact_that_cites_nothing_at_all() -> None:
    """A source_fact that never cited anything to begin with is still a hard
    failure — the strongest possible signal that a provider fabricated a
    "fact" with zero grounding (report.py's module docstring, step 1)."""
    provider = _steered_provider(())
    with pytest.raises(ProvenanceError):
        provider.run(TOPIC, now=NOW)


def test_pipeline_a_source_fact_citing_only_a_foreign_id_is_downgraded_not_crashed() -> None:
    """finding MEDIUM-4: a source_fact citing an id the pipeline never
    gathered (a model fabricating a citation, most dangerously while reading
    untrusted page text) no longer crashes the whole research run — the
    dangling id is stripped and, since that empties its citations, the
    statement is downgraded to model_inference and the removal is recorded
    via provenance_note; it is never silently dropped or presented as a
    fact."""
    provider = _steered_provider(("e999",))
    result = provider.run(TOPIC, now=NOW)  # must not raise
    statement = result.report.why_it_matters[0]
    assert statement.label == "model_inference"
    assert statement.evidence_ids == ()
    assert statement.provenance_note and "e999" in statement.provenance_note


def test_pipeline_report_is_a_research_report() -> None:
    provider = BrowserResearchProvider(gateway=FakeBrowserGateway())
    result = provider.run(TOPIC, now=NOW)
    assert isinstance(result.report, ResearchReport)
