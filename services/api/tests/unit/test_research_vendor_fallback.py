"""A vendor that is down does not throw a finished research away.

Production 2026-09-19 17:56 (run d2c374eb, the first research started from the free local
mode): 124 candidates, 14 fetches, 3 verified sources - and then ``SynthesisVendorError:
anthropic request failed: 404 Not Found``. The configured Anthropic model id had been retired.
Nobody caught the vendor error, so the run ended "failed" and the owner was told so, with the
evidence sitting in the database and a second configured provider never asked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.config import Settings
from app.research import browser_activities
from app.research.evidence import EvidenceRecord, dedup_and_rank
from app.research.report import assign_evidence_ids
from app.research.synthesis import DeterministicSynthesisProvider, SynthesisVendorError

TOPIC = "yapay zeka ile ilgili güncel haberler"
NOW = datetime(2026, 9, 19, 17, 56, tzinfo=UTC)


def _evidence(n: int = 3) -> list[EvidenceRecord]:
    raw = [
        EvidenceRecord(
            url=f"https://example.com/{i}",
            title=f"Kaynak {i}",
            excerpt=f"{TOPIC} hakkında doğrulanmış bulgu {i}. Bu cümle içerik satırıdır.",
            fetched_at=NOW,
            extraction_method="dom_text",
            source_class="news",
        )
        for i in range(n)
    ]
    return assign_evidence_ids(dedup_and_rank(raw, topic=TOPIC))


class _Down:
    name = "anthropic"
    configured = True

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, *_a: Any, **_k: Any) -> Any:
        self.calls += 1
        raise SynthesisVendorError("anthropic request failed: Client error '404 Not Found'")


class _Up:
    name = "openai"
    configured = True

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, topic: str, evidence: list[EvidenceRecord], *, recency_label: str) -> Any:
        self.calls += 1
        return DeterministicSynthesisProvider().synthesize(
            topic, evidence, recency_label=recency_label
        )


def test_a_vendor_error_asks_the_other_configured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    down, up = _Down(), _Up()
    monkeypatch.setattr(browser_activities, "_alternate_llm_providers", lambda _p, _s: [up])
    result, used, attempts = browser_activities._synthesize_with_fallback(
        down, TOPIC, _evidence(), recency_label="son 3 gün", settings=Settings(_env_file=None)
    )
    assert used is up and len(result.findings) >= 3
    assert down.calls == 1, "the same request to a vendor that answered 404 is not retried"
    assert attempts == [
        {
            "provider": "anthropic",
            "attempt": 1,
            "error_class": "vendor_error",
            "detail": "anthropic request failed: Client error '404 Not Found'",
        }
    ]


def test_with_no_other_provider_the_evidence_still_becomes_a_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    down = _Down()
    monkeypatch.setattr(browser_activities, "_alternate_llm_providers", lambda _p, _s: [])
    result, used, attempts = browser_activities._synthesize_with_fallback(
        down, TOPIC, _evidence(), recency_label="son 3 gün", settings=Settings(_env_file=None)
    )
    assert used.name == "deterministic" and len(result.findings) >= 3
    assert [a["error_class"] for a in attempts] == ["vendor_error"]


def test_every_configured_vendor_down_still_ends_on_the_deterministic_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _Down(), _Down()
    second.name = "openai"
    monkeypatch.setattr(browser_activities, "_alternate_llm_providers", lambda _p, _s: [second])
    _result, used, attempts = browser_activities._synthesize_with_fallback(
        first, TOPIC, _evidence(), recency_label="son 3 gün", settings=Settings(_env_file=None)
    )
    assert used.name == "deterministic"
    assert [a["provider"] for a in attempts] == ["anthropic", "openai"]


def test_the_alternates_are_the_other_configured_models_never_the_floor() -> None:
    settings = Settings(_env_file=None, anthropic_api_key="k-a", openai_api_key="k-o")
    names = [p.name for p in browser_activities._alternate_llm_providers(_Down(), settings)]
    assert names == ["openai"]
    none = Settings(
        _env_file=None, anthropic_api_key="", openai_api_key="", voice_openai_api_key=""
    )
    assert browser_activities._alternate_llm_providers(_Down(), none) == []


def test_the_shipped_anthropic_model_is_not_the_retired_one() -> None:
    assert Settings(_env_file=None).research_anthropic_model != "claude-3-5-haiku-20241022"
    assert Settings(_env_file=None).research_anthropic_model.startswith("claude-")
