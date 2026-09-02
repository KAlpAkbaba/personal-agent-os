"""M13 synthesis: executive-assistant structure + labelling + provenance."""

from datetime import UTC, datetime

import pytest

from app.research.evidence import (
    STATEMENT_LABEL_MODEL_INFERENCE,
    STATEMENT_LABEL_RECOMMENDATION,
    STATEMENT_LABEL_SOURCE_FACT,
    STATEMENT_LABEL_UNCERTAINTY,
    EvidenceRecord,
    dedup_and_rank,
)
from app.research.synthesis import (
    ClaudeSynthesisProvider,
    DeterministicSynthesisProvider,
    SynthesisNotConfiguredError,
)

NOW = datetime(2026, 9, 2, tzinfo=UTC)
TOPIC = "yapay zekâ ajanlarındaki gelişmeler"
RECENCY_LABEL = "son 3 gün"


def _ranked(n: int) -> list[EvidenceRecord]:
    raw = [
        EvidenceRecord(
            url=f"https://example.com/{i}",
            title=f"Kaynak {i}",
            excerpt=f"{TOPIC} hakkında bulgu {i}",
            fetched_at=NOW,
            extraction_method="dom_text",
            source_class="news",
        )
        for i in range(n)
    ]
    return dedup_and_rank(raw, topic=TOPIC)


def _synth(n: int):
    return DeterministicSynthesisProvider().synthesize(
        TOPIC, _ranked(n), recency_label=RECENCY_LABEL
    )


def test_no_evidence_produces_uncertainty_labelled_report() -> None:
    report = DeterministicSynthesisProvider().synthesize(
        TOPIC, [], recency_label=RECENCY_LABEL
    )
    assert report.why_it_matters.label == STATEMENT_LABEL_UNCERTAINTY
    assert report.details == ()
    assert TOPIC in report.executive_summary


def test_synthesize_is_deterministic() -> None:
    assert _synth(3) == _synth(3)


def test_every_source_fact_statement_carries_provenance() -> None:
    report = _synth(3)
    source_facts = [
        s
        for section in report.details
        for s in section.statements
        if s.label == STATEMENT_LABEL_SOURCE_FACT
    ]
    assert source_facts, "expected at least one source_fact statement"
    for statement in source_facts:
        assert statement.evidence_urls, f"source_fact without provenance: {statement}"


def test_why_it_matters_and_recommendation_are_labelled_and_provenanced() -> None:
    report = _synth(3)
    assert report.why_it_matters.label == STATEMENT_LABEL_MODEL_INFERENCE
    assert report.why_it_matters.evidence_urls
    assert report.recommended_action.label == STATEMENT_LABEL_RECOMMENDATION
    assert report.recommended_action.evidence_urls


def test_single_source_adds_scope_warning_uncertainty_section() -> None:
    report = _synth(1)
    headings = [d.heading for d in report.details]
    assert "Kapsam Uyarısı" in headings
    warning = next(d for d in report.details if d.heading == "Kapsam Uyarısı")
    assert warning.statements[0].label == STATEMENT_LABEL_UNCERTAINTY


def test_two_or_more_sources_do_not_trigger_scope_warning() -> None:
    report = _synth(2)
    headings = [d.heading for d in report.details]
    assert "Kapsam Uyarısı" not in headings


def test_details_are_ordered_by_evidence_rank() -> None:
    report = _synth(3)
    ranked_headings = [d.heading for d in report.details if d.heading != "Kapsam Uyarısı"]
    assert ranked_headings == sorted(ranked_headings)  # "1. ...", "2. ...", "3. ..."


def test_claude_synthesis_provider_is_inert_without_configuration() -> None:
    with pytest.raises(SynthesisNotConfiguredError):
        ClaudeSynthesisProvider().synthesize(TOPIC, [], recency_label=RECENCY_LABEL)


def test_claude_synthesis_provider_build_command_is_pure() -> None:
    provider = ClaudeSynthesisProvider(cli_path="/usr/bin/claude", model="claude-x")
    command = provider.build_command("prompt text")
    assert command[0] == "/usr/bin/claude"
    assert "--model" in command and "claude-x" in command
