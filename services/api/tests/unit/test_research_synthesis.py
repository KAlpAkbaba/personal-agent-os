"""M13 synthesis: findings/why-it-matters/watch-next/details + provenance + auto resolution."""

from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.research.evidence import (
    STATEMENT_LABEL_MODEL_INFERENCE,
    STATEMENT_LABEL_RECOMMENDATION,
    STATEMENT_LABEL_SOURCE_FACT,
    STATEMENT_LABEL_UNCERTAINTY,
    EvidenceRecord,
    dedup_and_rank,
)
from app.research.report import MIN_FINDINGS, assign_evidence_ids
from app.research.synthesis import (
    AnthropicSynthesisProvider,
    DeterministicSynthesisProvider,
    OpenAISynthesisProvider,
    SynthesisNotConfiguredError,
    build_prompt,
    parse_synthesis_response,
    resolve_synthesis_provider,
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
    return assign_evidence_ids(dedup_and_rank(raw, topic=TOPIC))


def _synth(n: int):
    return DeterministicSynthesisProvider().synthesize(
        TOPIC, _ranked(n), recency_label=RECENCY_LABEL
    )


def test_no_evidence_produces_uncertainty_only_result() -> None:
    result = DeterministicSynthesisProvider().synthesize(TOPIC, [], recency_label=RECENCY_LABEL)
    assert result.findings == ()
    assert result.uncertainty[0].label == STATEMENT_LABEL_UNCERTAINTY
    assert TOPIC in result.executive_summary


def test_synthesize_is_deterministic() -> None:
    a, b = _synth(3), _synth(3)
    assert a == b


def test_every_finding_is_a_source_fact_with_provenance() -> None:
    result = _synth(3)
    assert result.findings
    for f in result.findings:
        assert f.label == STATEMENT_LABEL_SOURCE_FACT
        assert f.evidence_ids


def test_findings_are_ordered_and_capped_at_seven() -> None:
    result = _synth(9)
    assert len(result.findings) == 7


def test_why_it_matters_and_watch_next_are_labelled_and_provenanced() -> None:
    result = _synth(3)
    assert result.why_it_matters[0].label == STATEMENT_LABEL_MODEL_INFERENCE
    assert result.why_it_matters[0].evidence_ids
    assert result.watch_next[0].label == STATEMENT_LABEL_RECOMMENDATION


def test_fewer_than_min_findings_adds_uncertainty_note_not_padding() -> None:
    result = _synth(1)
    assert len(result.findings) == 1
    assert result.uncertainty
    assert result.uncertainty[0].label == STATEMENT_LABEL_UNCERTAINTY


def test_min_findings_threshold_suppresses_uncertainty_note() -> None:
    result = _synth(MIN_FINDINGS)
    assert result.uncertainty == ()


def test_details_cover_every_evidence_item_ordered_by_rank() -> None:
    result = _synth(3)
    assert len(result.details) == 3
    headings = [d.heading for d in result.details]
    assert headings == sorted(headings)


# ------------------------------------------------------------ real providers


def test_openai_synthesis_provider_is_inert_without_configuration() -> None:
    provider = OpenAISynthesisProvider(
        "", model="gpt-x", base_url="https://api.openai.com/v1", timeout_s=5
    )
    with pytest.raises(SynthesisNotConfiguredError):
        provider.synthesize(TOPIC, [], recency_label=RECENCY_LABEL)


def test_anthropic_synthesis_provider_is_inert_without_configuration() -> None:
    provider = AnthropicSynthesisProvider(
        "", model="claude-x", base_url="https://api.anthropic.com", timeout_s=5
    )
    with pytest.raises(SynthesisNotConfiguredError):
        provider.synthesize(TOPIC, [], recency_label=RECENCY_LABEL)


def test_openai_provider_builds_request_without_any_io() -> None:
    provider = OpenAISynthesisProvider(
        "sk-test-key", model="gpt-x", base_url="https://api.openai.com/v1", timeout_s=5
    )
    evidence = _ranked(2)
    method, url, headers, body = provider.build_request(
        TOPIC, evidence, recency_label=RECENCY_LABEL
    )
    assert method == "POST"
    assert url.endswith("/chat/completions")
    assert headers["Authorization"] == "Bearer sk-test-key"
    assert "sk-test-key" not in str(body)  # the key never rides in the body


def test_build_prompt_wraps_evidence_in_untrusted_block() -> None:
    evidence = _ranked(1)
    prompt = build_prompt(TOPIC, evidence, recency_label=RECENCY_LABEL)
    assert "UNTRUSTED WEB CONTENT" in prompt
    assert evidence[0].id in prompt


def test_parse_synthesis_response_round_trips_minimal_payload() -> None:
    payload = {
        "executive_summary": "özet",
        "findings": [
            {
                "id": "f1",
                "title": "t",
                "summary": "s",
                "why_it_matters": "w",
                "importance": 3,
                "label": "source_fact",
                "evidence_ids": ["e1"],
                "first_seen": None,
            }
        ],
        "why_it_matters": [{"text": "x", "label": "model_inference", "evidence_ids": ["e1"]}],
        "watch_next": [{"text": "y", "label": "recommendation", "evidence_ids": []}],
        "details": [{"heading": "h", "statements": []}],
        "uncertainty": [],
    }
    result = parse_synthesis_response(payload)
    assert result.executive_summary == "özet"
    assert result.findings[0].id == "f1"
    assert result.details[0].heading == "h"


def test_resolve_auto_falls_back_to_deterministic_with_no_keys() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key="",
        voice_openai_api_key="",
        anthropic_api_key="",
    )
    provider = resolve_synthesis_provider("auto", settings)
    assert provider.name == "deterministic"


def test_resolve_auto_prefers_anthropic_over_openai() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-openai",
        anthropic_api_key="sk-anthropic",
    )
    provider = resolve_synthesis_provider("auto", settings)
    assert provider.name == "anthropic"


def test_resolve_auto_falls_back_to_openai_when_only_that_key_present() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-openai", anthropic_api_key="")
    provider = resolve_synthesis_provider("auto", settings)
    assert provider.name == "openai"


def test_resolve_openai_falls_back_to_voice_key() -> None:
    settings = Settings(_env_file=None, openai_api_key="", voice_openai_api_key="sk-voice-key")
    provider = resolve_synthesis_provider("openai", settings)
    assert provider.configured is True


def test_resolve_unknown_provider_raises() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(ValueError):
        resolve_synthesis_provider("not-a-provider", settings)
