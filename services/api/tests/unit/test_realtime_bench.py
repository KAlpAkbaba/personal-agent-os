"""M12 gate: the realtime latency harness produces a report offline against
the simulator, the simulator meets the targets, and the harness is PROVEN to
measure (a deliberately slow simulator fails the same check).

The JSON report is written to ``tmp_path`` (and to
``$PAGENTOS_REALTIME_BENCH_OUT`` when set) so the gate has an artifact.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.voice.realtime_bench import (
    EV_AUDIO_FRAME,
    EV_BARGE_IN_START,
    EV_END_OF_TURN,
    EV_FIRST_AUDIO,
    EV_MIC_SPEECH_START,
    EV_PLAYBACK_STOPPED,
    EV_TOOL_CALL,
    EV_TOOL_DONE,
    EV_UPLINK_FIRST_PACKET,
    METRICS,
    SOURCE_CLIENT,
    SOURCE_SIMULATOR,
    BenchTargets,
    TimingEvent,
    build_report,
    detect_gaps,
    events_from_client_reports,
    pair_metric,
    run_simulator_benchmark,
    summarize,
)
from app.voice.simulator import SimulatorTimings


def test_gate_simulator_meets_every_target_and_writes_the_report(tmp_path: Path) -> None:
    report = run_simulator_benchmark()
    data = report.to_dict()
    out = tmp_path / "realtime_bench_simulator.json"
    out.write_text(report.to_json(), encoding="utf-8")
    extra = os.environ.get("PAGENTOS_REALTIME_BENCH_OUT")
    if extra:
        Path(extra).parent.mkdir(parents=True, exist_ok=True)
        Path(extra).write_text(report.to_json(), encoding="utf-8")

    assert data["source"] == SOURCE_SIMULATOR
    assert data["is_acceptance_evidence"] is False
    for metric in METRICS:
        assert data["metrics"][metric]["n"] >= 1, metric
        assert data["target_check"][metric]["met"] is True, data["target_check"][metric]
    assert data["target_check"]["gaps"]["met"] is True
    assert data["false_barge_count"] == 0
    assert report.all_targets_met() is True
    assert data["all_targets_met"] is True
    # the two spec targets are recorded AS targets with their basis, not as claims
    assert data["targets"]["barge_in_to_stop_ms"]["target_ms"] == 150
    assert "ADR-0034" in data["targets"]["barge_in_to_stop_ms"]["basis"]
    assert data["targets"]["eot_to_first_audio_ms"]["target_ms"] == 700
    assert any("never claims" in n for n in data["notes"])
    # deterministic: the same run yields the same numbers
    again = run_simulator_benchmark().to_dict()
    assert again["metrics"] == data["metrics"]
    assert json.loads(out.read_text(encoding="utf-8"))["metrics"] == data["metrics"]


def test_harness_catches_a_slow_provider() -> None:
    slow = SimulatorTimings(barge_in_stop_delay_ms=400, first_audio_delay_ms=900)
    report = run_simulator_benchmark(timings=slow)
    checks = report.target_check()
    assert checks["barge_in_to_stop_ms"]["met"] is False
    assert checks["eot_to_first_audio_ms"]["met"] is False
    assert checks["mic_to_uplink_ms"]["met"] is True
    assert report.all_targets_met() is False


def test_harness_detects_audible_gaps_and_tool_silence() -> None:
    gappy = SimulatorTimings(response_gap_ms=500)
    report = run_simulator_benchmark(timings=gappy)
    assert any(g["kind"] == "audio_gap" and g["gap_ms"] >= 500 for g in report.gaps)
    assert report.all_targets_met() is False
    # tool silence: a tool that answers nothing for longer than the bound
    events = [
        TimingEvent(EV_TOOL_CALL, 1000, 1),
        TimingEvent(EV_TOOL_DONE, 6000, 1),
    ]
    gaps = detect_gaps(events, max_gap_ms=300, max_tool_silence_ms=3000)
    assert gaps == [{"kind": "tool_silence", "turn": 1, "from_ms": 1000, "to_ms": 6000,
                     "gap_ms": 5000}]
    # ...but a preamble within the bound and a completion within the bound is fine
    ok = [TimingEvent(EV_TOOL_CALL, 1000, 1), TimingEvent(EV_AUDIO_FRAME, 1300, 1),
          TimingEvent(EV_AUDIO_FRAME, 1900, 1), TimingEvent(EV_TOOL_DONE, 4000, 1)]
    assert detect_gaps(ok, max_gap_ms=300, max_tool_silence_ms=3000) == []


def test_harness_flags_a_hesitating_owner_who_was_cut_off() -> None:
    no_guard = SimulatorTimings(hesitation_guard_ms=0)
    report = run_simulator_benchmark(timings=no_guard)
    assert report.false_barge_count >= 1
    assert report.target_check()["false_barge"]["met"] is False


def test_pairing_reports_unmatched_starts() -> None:
    events = [
        TimingEvent(EV_BARGE_IN_START, 100, 1),
        TimingEvent(EV_PLAYBACK_STOPPED, 160, 1),
        TimingEvent(EV_BARGE_IN_START, 900, 2),  # playback never stopped
    ]
    samples = pair_metric(events, EV_BARGE_IN_START, EV_PLAYBACK_STOPPED)
    assert samples[0]["value_ms"] == 60 and samples[0]["unmatched"] is False
    assert samples[1]["unmatched"] is True
    report = build_report(events, source=SOURCE_CLIENT)
    check = report.target_check()["barge_in_to_stop_ms"]
    assert check["n"] == 1 and check["unmatched"] == 1 and check["met"] is False


def test_end_of_turn_answered_by_a_tool_call_is_not_unmatched() -> None:
    events = [TimingEvent(EV_END_OF_TURN, 100, 1), TimingEvent(EV_TOOL_CALL, 250, 1)]
    samples = pair_metric(events, EV_END_OF_TURN, EV_FIRST_AUDIO, cancel_kinds=(EV_TOOL_CALL,))
    assert samples == []


def test_client_reports_build_an_acceptance_flavoured_report() -> None:
    rows = [
        {"kind": EV_MIC_SPEECH_START, "t_ms": 0, "turn": 1},
        {"kind": EV_UPLINK_FIRST_PACKET, "t_ms": 35, "turn": 1},
        {"kind": EV_END_OF_TURN, "t_ms": 1200, "turn": 1},
        {"kind": EV_FIRST_AUDIO, "t_ms": 1750, "turn": 1},
        {"kind": "utterance", "t_ms": 1, "turn": 1},  # not a timing event; ignored
        {"kind": "bogus", "t_ms": 1},
    ]
    events = events_from_client_reports(rows)
    assert [e.kind for e in events] == [EV_MIC_SPEECH_START, EV_UPLINK_FIRST_PACKET,
                                        EV_END_OF_TURN, EV_FIRST_AUDIO]
    report = build_report(events, source=SOURCE_CLIENT, targets=BenchTargets())
    data = report.to_dict()
    assert data["is_acceptance_evidence"] is True
    assert data["metrics"]["mic_to_uplink_ms"]["p50"] == 35
    assert data["metrics"]["eot_to_first_audio_ms"]["p50"] == 550
    assert data["target_check"]["barge_in_to_stop_ms"]["met"] is None  # no samples yet
    assert report.all_targets_met(require_samples=True) is False
    assert report.all_targets_met(require_samples=False) is True


def test_summary_statistics() -> None:
    assert summarize([]) == {"n": 0, "min": None, "p50": None, "p95": None, "max": None,
                             "mean": None}
    s = summarize([50, 10, 30, 20, 40])
    assert s == {"n": 5, "min": 10, "p50": 30, "p95": 50, "max": 50, "mean": 30.0}


def test_targets_carry_a_basis_for_every_metric() -> None:
    t = BenchTargets().to_dict()
    for metric in METRICS:
        assert t[metric]["basis"]
    assert "max_tool_silence_ms" in t and "max_audio_gap_ms" in t
