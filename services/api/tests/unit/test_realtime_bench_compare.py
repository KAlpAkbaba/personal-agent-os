"""gpt-live-provider (d20261004): two realtime providers side by side.

``compare_reports`` is pure: the same metric names for both reports, the provider and
model each report carries, and a per-minute cost estimate that is the vendor's published
per-minute price where there is one and ``None`` - never 0 - where there is not
(OpenAI Realtime is billed by tokens; a 0 would read as "free").
"""

from __future__ import annotations

from app.voice import realtime_bench as rb
from app.voice.providers_openai_live import USD_PER_MINUTE


def _events(eot_to_audio: int, barge_to_stop: int) -> list[rb.TimingEvent]:
    return [
        rb.TimingEvent(rb.EV_END_OF_TURN, 1000, 1),
        rb.TimingEvent(rb.EV_FIRST_AUDIO, 1000 + eot_to_audio, 1),
        rb.TimingEvent(rb.EV_BARGE_IN_START, 5000, 2),
        rb.TimingEvent(rb.EV_PLAYBACK_STOPPED, 5000 + barge_to_stop, 2),
        rb.TimingEvent(rb.EV_TOOL_CALL, 8000, 3),
        rb.TimingEvent(rb.EV_TOOL_DONE, 9500, 3),
        rb.TimingEvent(rb.EV_SPEECH_RESUMED, 9900, 3),
    ]


def _report(
    provider: str, model: str, eot: int, barge: int, false_barge: int
) -> rb.RealtimeBenchReport:
    return rb.build_report(
        _events(eot, barge),
        source=rb.SOURCE_CLIENT,
        false_barge_count=false_barge,
        provider=provider,
        model=model,
    )


def test_report_carries_provider_and_model_and_the_schema_moved() -> None:
    rep = _report("openai-live", "gpt-live-1", 400, 90, 0)
    d = rep.to_dict()
    assert d["provider"] == "openai-live"
    assert d["model"] == "gpt-live-1"
    assert rb.REPORT_SCHEMA_VERSION == "m12.2"
    assert d["schema_version"] == "m12.2"


def test_compare_reports_side_by_side_with_per_minute_cost() -> None:
    a = _report("openai-realtime", "gpt-realtime-2.1", 650, 140, 2)
    b = _report("openai-live", "gpt-live-1", 400, 90, 0)
    cmp = rb.compare_reports(a, b)
    assert cmp["kind"] == "realtime_latency_comparison"
    assert cmp["schema_version"] == rb.REPORT_SCHEMA_VERSION
    assert [s["provider"] for s in cmp["sides"]] == ["openai-realtime", "openai-live"]
    assert [s["model"] for s in cmp["sides"]] == ["gpt-realtime-2.1", "gpt-live-1"]
    rows = cmp["metrics"]
    # the same names on both sides, in the same order
    for name in (
        "eot_to_first_audio_ms",
        "barge_in_to_stop_ms",
        "tool_done_to_speech_ms",
        "false_barge_count",
    ):
        assert name in rows
        assert set(rows[name]) == {"a", "b"}
    assert rows["eot_to_first_audio_ms"]["a"]["p50"] == 650
    assert rows["eot_to_first_audio_ms"]["b"]["p50"] == 400
    assert rows["barge_in_to_stop_ms"]["b"]["p95"] == 90
    assert rows["tool_done_to_speech_ms"]["a"]["p50"] == 400
    assert rows["false_barge_count"] == {"a": 2, "b": 0}
    cost = cmp["cost_per_minute_usd"]
    assert cost["b"] == USD_PER_MINUTE == 0.05
    # token-billed: unknown per minute -> null, never 0
    assert cost["a"] is None
    assert cmp["cost_basis"]["a"] is not None


def test_unknown_provider_cost_is_null_not_zero() -> None:
    a = _report("simulator", "", 500, 100, 0)
    b = rb.build_report([], source=rb.SOURCE_CLIENT)
    cmp = rb.compare_reports(a, b)
    assert cmp["cost_per_minute_usd"] == {"a": None, "b": None}
    assert cmp["sides"][1]["provider"] is None
    assert cmp["metrics"]["eot_to_first_audio_ms"]["b"]["n"] == 0
