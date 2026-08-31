"""Unit tests: benchmark harness compares >= 2 providers and emits the required
report fields (STT + TTS), deterministically and offline."""

import pytest

from app.voice.benchmark import (
    REPORT_SCHEMA_VERSION,
    char_error_rate,
    report_to_markdown,
    run_stt_benchmark,
    run_tts_benchmark,
    word_error_rate,
)
from app.voice.registry import benchmark_stt_candidates, benchmark_tts_candidates


def test_wer_cer_basic() -> None:
    assert word_error_rate("a b c", "a b c") == 0.0
    assert word_error_rate("a b c", "a b") == pytest.approx(1 / 3)
    assert char_error_rate("abc", "abc") == 0.0
    assert char_error_rate("abcd", "abxd") == pytest.approx(0.25)


def test_stt_benchmark_compares_two_and_has_fields() -> None:
    report = run_stt_benchmark(benchmark_stt_candidates())
    d = report.to_dict()
    assert d["kind"] == "stt"
    assert d["schema_version"] == REPORT_SCHEMA_VERSION
    assert d["compares_provider_count"] >= 2
    assert len(set(d["providers"])) >= 2
    # per-case + per-provider metrics present
    assert d["cases"] and "results" in d["cases"][0]
    for _name, metrics in d["per_provider"].items():
        assert "mean_wer" in metrics and "mean_cer" in metrics
        assert "mean_latency_ms" in metrics
        assert "capabilities" in metrics
    # The lossy provider must have a strictly higher WER than the accurate one.
    per = d["per_provider"]
    assert per["fake-stt-lossy"]["mean_wer"] > per["fake-stt-accurate"]["mean_wer"]


def test_tts_benchmark_compares_two_and_has_fields() -> None:
    report = run_tts_benchmark(benchmark_tts_candidates())
    d = report.to_dict()
    assert d["kind"] == "tts"
    assert d["compares_provider_count"] >= 2
    for _name, metrics in d["per_provider"].items():
        assert "mean_latency_ms" in metrics
        assert "mean_audio_ms" in metrics
        assert "capabilities" in metrics
    # audio length was measured and is positive
    assert d["cases"][0]["results"][d["providers"][0]]["audio_ms"] > 0


def test_benchmark_requires_two_distinct_providers() -> None:
    from app.voice.providers import FakeTTSProvider

    with pytest.raises(ValueError):
        run_tts_benchmark([FakeTTSProvider("same"), FakeTTSProvider("same")])


def test_markdown_summary_mentions_provider_count() -> None:
    report = run_stt_benchmark(benchmark_stt_candidates())
    md = report_to_markdown(report)
    assert md.startswith("# Voice STT Benchmark")
    assert "Providers compared:" in md
    assert "| Provider |" in md


def test_benchmark_is_deterministic() -> None:
    a = run_stt_benchmark(benchmark_stt_candidates()).to_dict()["per_provider"]
    b = run_stt_benchmark(benchmark_stt_candidates()).to_dict()["per_provider"]
    assert {k: v["mean_wer"] for k, v in a.items()} == {k: v["mean_wer"] for k, v in b.items()}
