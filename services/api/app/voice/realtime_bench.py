"""Realtime latency benchmark harness (M12 spec §8, ADR-0034 §5).

Five per-turn metrics, computed from timestamped events:

    mic_to_uplink_ms        mic_speech_start      -> uplink_first_packet
    eot_to_first_audio_ms   end_of_turn           -> first_audio
    barge_in_to_stop_ms     barge_in_start        -> playback_stopped
    tool_preamble_ms        tool_call             -> preamble_audio_start
    tool_done_to_speech_ms  tool_done             -> speech_resumed

plus gap detection (audible silence inside a response longer than a bound, and
silence during a long-running tool longer than a bound — spec §6 calls the
latter a defect).

Two sources feed the same computation:

- **simulator** (``run_simulator_benchmark``): the deterministic full-duplex
  simulator plays a scripted conversation offline; the numbers are gate
  evidence that the harness and the session logic work — never acceptance.
- **client** (``events_from_client_reports``): a real client posts its
  monotonic timestamps to ``POST /v1/voice/realtime/sessions/{id}/events`` and
  the report is built from them on the owner's real environment.

Targets are recorded in the report as TARGETS with their basis. They are
PersonalAgentOS's own numbers (ADR-0034), never a claim about a provider.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.voice.providers import (
    RT_RESPONSE_AUDIO,
    RT_RESPONSE_DONE,
    RT_SPEECH_STARTED,
    RT_SPEECH_STOPPED,
    RT_TOOL_CALL,
)
from app.voice.simulator import (
    SimulatedRealtimeProvider,
    SimulatedRealtimeSession,
    SimulatedTurn,
    SimulatorTimings,
    synth_frame,
)

REPORT_SCHEMA_VERSION = "m12.1"
SOURCE_SIMULATOR = "simulator"
SOURCE_CLIENT = "client"

# ------------------------------------------------------------ timing events

EV_MIC_SPEECH_START = "mic_speech_start"
EV_UPLINK_FIRST_PACKET = "uplink_first_packet"
EV_END_OF_TURN = "end_of_turn"
EV_FIRST_AUDIO = "first_audio"
EV_BARGE_IN_START = "barge_in_start"
EV_PLAYBACK_STOPPED = "playback_stopped"
EV_TOOL_CALL = "tool_call"
EV_PREAMBLE_AUDIO_START = "preamble_audio_start"
EV_TOOL_DONE = "tool_done"
EV_SPEECH_RESUMED = "speech_resumed"
EV_AUDIO_FRAME = "audio_frame"
EV_RESPONSE_DONE = "response_done"
EV_NETWORK_LOST = "network_lost"
EV_NETWORK_RESTORED = "network_restored"
#: The web client's own mark that playback of a response actually finished (payload:
#: response_id, basis — 1 = a real 'ended' event from the audio element, 0 = a timeout
#: fallback). No metric pair yet (ADR-0067 item 5): stored like every other timing
#: event so it is available once a metric needs it, never dropped at the door.
EV_AUDIO_DONE = "audio_done"

TIMING_EVENT_KINDS = (
    EV_MIC_SPEECH_START, EV_UPLINK_FIRST_PACKET, EV_END_OF_TURN, EV_FIRST_AUDIO,
    EV_BARGE_IN_START, EV_PLAYBACK_STOPPED, EV_TOOL_CALL, EV_PREAMBLE_AUDIO_START,
    EV_TOOL_DONE, EV_SPEECH_RESUMED, EV_AUDIO_FRAME, EV_RESPONSE_DONE,
    EV_NETWORK_LOST, EV_NETWORK_RESTORED, EV_AUDIO_DONE,
)  # fmt: skip

#: metric -> (start kind, end kind)
METRIC_PAIRS: dict[str, tuple[str, str]] = {
    "mic_to_uplink_ms": (EV_MIC_SPEECH_START, EV_UPLINK_FIRST_PACKET),
    "eot_to_first_audio_ms": (EV_END_OF_TURN, EV_FIRST_AUDIO),
    "barge_in_to_stop_ms": (EV_BARGE_IN_START, EV_PLAYBACK_STOPPED),
    "tool_preamble_ms": (EV_TOOL_CALL, EV_PREAMBLE_AUDIO_START),
    "tool_done_to_speech_ms": (EV_TOOL_DONE, EV_SPEECH_RESUMED),
}
#: metric -> kinds that legitimately end an open measurement WITHOUT a sample
#: (an end-of-turn answered by a tool call has no "first audio"; an owner who
#: speaks again before the answer is not a latency sample; a tool that
#: completes without a preamble is not an unmatched preamble).
METRIC_CANCELS: dict[str, tuple[str, ...]] = {
    "mic_to_uplink_ms": (),
    "eot_to_first_audio_ms": (EV_TOOL_CALL, EV_MIC_SPEECH_START, EV_NETWORK_LOST),
    "barge_in_to_stop_ms": (),
    "tool_preamble_ms": (EV_TOOL_DONE, EV_MIC_SPEECH_START),
    "tool_done_to_speech_ms": (EV_MIC_SPEECH_START, EV_NETWORK_LOST),
}
METRICS = tuple(METRIC_PAIRS)


@dataclass(frozen=True, slots=True)
class TimingEvent:
    kind: str
    t_ms: int
    turn: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "t_ms": self.t_ms,
            "turn": self.turn,
            "payload": dict(self.payload),
        }


# ------------------------------------------------------------------ targets


@dataclass(frozen=True, slots=True)
class BenchTargets:
    """Targets, in ms. The two from ADR-0034/spec §8 are marked as such; the
    rest are PersonalAgentOS provisional numbers (ADR-0036) so the harness has
    a bound for every metric it reports."""

    barge_in_to_stop_ms: int = 150
    eot_to_first_audio_ms: int = 700
    mic_to_uplink_ms: int = 120
    tool_preamble_ms: int = 1000
    tool_done_to_speech_ms: int = 1000
    max_audio_gap_ms: int = 300
    max_tool_silence_ms: int = 3000

    BASIS = {
        "barge_in_to_stop_ms": "ADR-0034 / M12 spec §8 (< ~150 ms where technically achievable)",
        "eot_to_first_audio_ms": "ADR-0034 / M12 spec §8 (short turns ~500-700 ms)",
        "mic_to_uplink_ms": "PersonalAgentOS provisional (ADR-0036)",
        "tool_preamble_ms": "PersonalAgentOS provisional (ADR-0036)",
        "tool_done_to_speech_ms": "PersonalAgentOS provisional (ADR-0036)",
        "max_audio_gap_ms": "PersonalAgentOS provisional (ADR-0036): no perceptible gap",
        "max_tool_silence_ms": "M12 spec §6: silence during a tool beyond a bound is a defect",
    }

    def for_metric(self, metric: str) -> int:
        return int(getattr(self, metric))

    def to_dict(self) -> dict[str, Any]:
        return {
            name: {"target_ms": int(getattr(self, name)), "basis": self.BASIS[name]}
            for name in self.BASIS
        }


# ------------------------------------------------------------------ compute


def _percentile(sorted_values: list[int], q: float) -> int:
    if not sorted_values:
        return 0
    idx = round((len(sorted_values) - 1) * q)
    return sorted_values[max(0, min(len(sorted_values) - 1, idx))]


def summarize(values: Iterable[int]) -> dict[str, Any]:
    vals = sorted(int(v) for v in values)
    if not vals:
        return {"n": 0, "min": None, "p50": None, "p95": None, "max": None, "mean": None}
    return {
        "n": len(vals),
        "min": vals[0],
        "p50": _percentile(vals, 0.5),
        "p95": _percentile(vals, 0.95),
        "max": vals[-1],
        "mean": round(sum(vals) / len(vals), 1),
    }


def pair_metric(
    events: list[TimingEvent],
    start_kind: str,
    end_kind: str,
    cancel_kinds: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Chronological pairing: each start is matched with the NEXT end; a start
    without an end before the next start is reported as unmatched (a defect
    indicator, e.g. barge-in requested but playback never stopped). A
    ``cancel_kinds`` event closes an open start silently (see METRIC_CANCELS)."""
    ordered = sorted(events, key=lambda e: (e.t_ms, e.kind != start_kind))
    samples: list[dict[str, Any]] = []
    open_start: TimingEvent | None = None
    for ev in ordered:
        if ev.kind == start_kind:
            if open_start is not None:
                samples.append(
                    {
                        "turn": open_start.turn,
                        "start_ms": open_start.t_ms,
                        "end_ms": None,
                        "value_ms": None,
                        "unmatched": True,
                    }
                )
            open_start = ev
        elif ev.kind == end_kind and open_start is not None:
            samples.append(
                {
                    "turn": open_start.turn,
                    "start_ms": open_start.t_ms,
                    "end_ms": ev.t_ms,
                    "value_ms": ev.t_ms - open_start.t_ms,
                    "unmatched": False,
                }
            )
            open_start = None
        elif ev.kind in cancel_kinds and open_start is not None:
            open_start = None
    if open_start is not None:
        samples.append(
            {
                "turn": open_start.turn,
                "start_ms": open_start.t_ms,
                "end_ms": None,
                "value_ms": None,
                "unmatched": True,
            }
        )
    return samples


def detect_gaps(
    events: list[TimingEvent], *, max_gap_ms: int, max_tool_silence_ms: int
) -> list[dict[str, Any]]:
    """Audible silence: consecutive audio frames further apart than
    ``max_gap_ms`` inside one response, and tool-call silence longer than
    ``max_tool_silence_ms`` before any preamble/resumed audio."""
    ordered = sorted(events, key=lambda e: e.t_ms)
    gaps: list[dict[str, Any]] = []
    last_frame: TimingEvent | None = None
    tool_open: TimingEvent | None = None
    tool_last_sound_ms = 0
    for ev in ordered:
        if ev.kind == EV_AUDIO_FRAME:
            if tool_open is not None:
                # During a tool the bound is the tool-silence bound (spec §6):
                # the preamble may end long before the tool completes.
                silent = ev.t_ms - tool_last_sound_ms
                if silent > max_tool_silence_ms:
                    gaps.append(
                        {
                            "kind": "tool_silence",
                            "turn": tool_open.turn,
                            "from_ms": tool_last_sound_ms,
                            "to_ms": ev.t_ms,
                            "gap_ms": silent,
                        }
                    )
                tool_last_sound_ms = ev.t_ms
            elif last_frame is not None and ev.turn == last_frame.turn:
                delta = ev.t_ms - last_frame.t_ms
                if delta > max_gap_ms:
                    gaps.append(
                        {
                            "kind": "audio_gap",
                            "turn": ev.turn,
                            "from_ms": last_frame.t_ms,
                            "to_ms": ev.t_ms,
                            "gap_ms": delta,
                        }
                    )
            last_frame = ev
        elif ev.kind in (EV_RESPONSE_DONE, EV_PLAYBACK_STOPPED, EV_END_OF_TURN):
            last_frame = None
        elif ev.kind == EV_TOOL_CALL:
            tool_open = ev
            tool_last_sound_ms = ev.t_ms
            last_frame = None
        elif ev.kind == EV_TOOL_DONE and tool_open is not None:
            silent = ev.t_ms - tool_last_sound_ms
            if silent > max_tool_silence_ms:
                gaps.append(
                    {
                        "kind": "tool_silence",
                        "turn": tool_open.turn,
                        "from_ms": tool_last_sound_ms,
                        "to_ms": ev.t_ms,
                        "gap_ms": silent,
                    }
                )
            tool_open = None
            last_frame = None
    return gaps


# ------------------------------------------------------------------- report


@dataclass(slots=True)
class RealtimeBenchReport:
    source: str
    generated_at: str
    targets: BenchTargets
    metrics: dict[str, dict[str, Any]]
    samples: dict[str, list[dict[str, Any]]]
    gaps: list[dict[str, Any]]
    false_barge_count: int
    event_count: int
    notes: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def target_check(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for metric in METRICS:
            summary = self.metrics[metric]
            target = self.targets.for_metric(metric)
            unmatched = sum(1 for s in self.samples[metric] if s["unmatched"])
            met: bool | None
            if summary["n"] == 0:
                met = None
            else:
                met = summary["p95"] <= target and unmatched == 0
            out[metric] = {
                "target_ms": target,
                "observed_p95_ms": summary["p95"],
                "observed_max_ms": summary["max"],
                "n": summary["n"],
                "unmatched": unmatched,
                "met": met,
            }
        out["gaps"] = {"target": "none", "observed": len(self.gaps), "met": not self.gaps}
        out["false_barge"] = {
            "target": 0,
            "observed": self.false_barge_count,
            "met": self.false_barge_count == 0,
        }
        return out

    def all_targets_met(self, *, require_samples: bool = True) -> bool:
        checks = self.target_check()
        for metric in METRICS:
            met = checks[metric]["met"]
            if met is None:
                if require_samples:
                    return False
                continue
            if not met:
                return False
        return bool(checks["gaps"]["met"] and checks["false_barge"]["met"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "realtime_latency",
            "schema_version": REPORT_SCHEMA_VERSION,
            "source": self.source,
            "generated_at": self.generated_at,
            "is_acceptance_evidence": self.source == SOURCE_CLIENT,
            "targets": self.targets.to_dict(),
            "metrics": self.metrics,
            "target_check": self.target_check(),
            "all_targets_met": self.all_targets_met(require_samples=False),
            "samples": self.samples,
            "gaps": self.gaps,
            "false_barge_count": self.false_barge_count,
            "event_count": self.event_count,
            "notes": self.notes,
            "context": self.context,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_report(
    events: Iterable[TimingEvent],
    *,
    source: str,
    targets: BenchTargets | None = None,
    false_barge_count: int = 0,
    context: dict[str, Any] | None = None,
) -> RealtimeBenchReport:
    targets = targets or BenchTargets()
    evs = list(events)
    samples = {
        m: pair_metric(evs, *METRIC_PAIRS[m], cancel_kinds=METRIC_CANCELS[m]) for m in METRICS
    }
    metrics = {
        m: summarize(s["value_ms"] for s in samples[m] if s["value_ms"] is not None)
        for m in METRICS
    }
    notes = [
        "Targets are PersonalAgentOS targets (ADR-0034/ADR-0036), never claims about a provider.",
        "source=simulator numbers are gate evidence only; acceptance is real-only "
        "(owner machine, microphone, network, Hetzner Cloud Core).",
    ]
    return RealtimeBenchReport(
        source=source,
        generated_at=_now(),
        targets=targets,
        metrics=metrics,
        samples=samples,
        gaps=detect_gaps(
            evs,
            max_gap_ms=targets.max_audio_gap_ms,
            max_tool_silence_ms=targets.max_tool_silence_ms,
        ),
        false_barge_count=false_barge_count,
        event_count=len(evs),
        notes=notes,
        context=dict(context or {}),
    )


# ------------------------------------------------------- simulator source


def events_from_simulator(session: SimulatedRealtimeSession) -> list[TimingEvent]:
    """Derive timing events from a simulated session: the simulated client's
    marks (mic start, barge-in request, tool done) + the provider's events."""
    out: list[TimingEvent] = []
    for mark in session.client_marks:
        turn = int(mark.payload.get("turn", 0))
        if mark.kind == "mic_speech_start":
            out.append(TimingEvent(EV_MIC_SPEECH_START, mark.at_ms, turn))
        elif mark.kind == "barge_in_start":
            out.append(TimingEvent(EV_BARGE_IN_START, mark.at_ms, turn))
        elif mark.kind == "tool_done":
            out.append(TimingEvent(EV_TOOL_DONE, mark.at_ms, turn))

    awaiting_first_audio = False
    tool_running = False
    for ev in session.events:
        turn = int(ev.payload.get("turn", 0))
        if ev.kind == RT_SPEECH_STARTED:
            out.append(TimingEvent(EV_UPLINK_FIRST_PACKET, ev.at_ms, turn))
        elif ev.kind == RT_SPEECH_STOPPED:
            out.append(TimingEvent(EV_END_OF_TURN, ev.at_ms, turn))
            awaiting_first_audio = True
        elif ev.kind == RT_TOOL_CALL:
            out.append(
                TimingEvent(EV_TOOL_CALL, ev.at_ms, turn, {"call_id": ev.payload.get("call_id")})
            )
            tool_running = True
            awaiting_first_audio = False  # the tool turn resumes via tool_done
        elif ev.kind == RT_RESPONSE_AUDIO:
            out.append(TimingEvent(EV_AUDIO_FRAME, ev.at_ms, turn))
            if ev.payload.get("preamble"):
                if tool_running:
                    out.append(TimingEvent(EV_PREAMBLE_AUDIO_START, ev.at_ms, turn))
                    tool_running = False  # only the first preamble frame counts
            elif awaiting_first_audio:
                out.append(TimingEvent(EV_FIRST_AUDIO, ev.at_ms, turn))
                awaiting_first_audio = False
        elif ev.kind == RT_RESPONSE_DONE:
            if ev.payload.get("cancelled"):
                out.append(TimingEvent(EV_PLAYBACK_STOPPED, ev.at_ms, turn))
            else:
                out.append(TimingEvent(EV_RESPONSE_DONE, ev.at_ms, turn))
    # tool_done -> speech resumed: the first non-preamble frame after each
    # final tool result the simulated client submitted.
    tool_done_times = sorted(m.at_ms for m in session.client_marks if m.kind == "tool_done")
    frames = [
        e for e in session.events if e.kind == RT_RESPONSE_AUDIO and not e.payload.get("preamble")
    ]
    for td in tool_done_times:
        nxt = next((f for f in frames if f.at_ms >= td), None)
        if nxt is not None:
            out.append(TimingEvent(EV_SPEECH_RESUMED, nxt.at_ms, int(nxt.payload.get("turn", 0))))
    # dedupe (kind, t_ms, turn)
    seen: set[tuple[str, int, int]] = set()
    unique: list[TimingEvent] = []
    for ev in out:
        key = (ev.kind, ev.t_ms, ev.turn)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ev)
    return unique


def default_script() -> list[SimulatedTurn]:
    """One scripted assistant turn per owner end-of-turn in
    :func:`run_simulator_benchmark` (an interrupted answer restarts as a new
    turn, so the barge-in turns consume two entries each)."""
    return [
        SimulatedTurn.reply("Merhaba, sizi dinliyorum."),  # turn 1
        SimulatedTurn.reply("Raporun ilk bölümünü özetliyorum: üç ana bulgu var."),  # turn 2
        SimulatedTurn.reply("Devam ediyorum."),  # turn 2, after the overlap barge-in
        SimulatedTurn.reply("İkinci bulgu maliyetle ilgili."),  # turn 3, stopped with "dur"
        SimulatedTurn.tool_call(
            "research.start", {"topic": "OpenAI, Anthropic, Google ve açık kaynak gelişmeleri"}
        ),
        SimulatedTurn.reply("Anladım, sadece OpenAI kısmına bakıyorum."),  # turn 5 (hesitation)
    ]


def _speak(session: SimulatedRealtimeSession, *, frames: int, filler_last: bool = False) -> None:
    for i in range(frames):
        session.push_audio(
            synth_frame(i, frame_ms=session.timings.frame_ms),
            filler=filler_last and i == frames - 1,
        )
        session.advance(session.timings.frame_ms)


def run_simulator_benchmark(
    *,
    timings: SimulatorTimings | None = None,
    targets: BenchTargets | None = None,
    preamble: str = "Bakıyorum. OpenAI, Anthropic, Google ve önemli açık kaynak "
    "gelişmelerini karşılaştıracağım.",
) -> RealtimeBenchReport:
    """Play a scripted Turkish conversation against the simulator and report.

    Turns: (1) a short question; (2) a question whose answer the owner
    interrupts by talking over it (overlap barge-in); (3) an explicit stop
    ("dur") mid-answer; (4) a long-running tool with a spoken preamble and a
    later completion; (5) a hesitating owner ("şey…") who must NOT be cut off.
    """
    provider = SimulatedRealtimeProvider(timings=timings, script_factory=default_script)
    session = provider.open_session()
    t = session.timings

    # turn 1: short question -> answer
    _speak(session, frames=10)
    session.run_until_idle()

    # turn 2: question, then overlap barge-in ~250 ms into the answer
    _speak(session, frames=10)
    session.advance(t.uplink_delay_ms + t.end_of_turn_delay_ms + t.first_audio_delay_ms + 250)
    assert session.responding, "simulator must be mid-answer for the overlap barge-in"
    _speak(session, frames=8)
    session.run_until_idle()

    # turn 3: question, then an explicit stop word mid-answer (client stops first)
    _speak(session, frames=10)
    session.advance(t.uplink_delay_ms + t.end_of_turn_delay_ms + t.first_audio_delay_ms + 200)
    assert session.responding
    session.request_barge_in()
    session.run_until_idle()

    # turn 4: long-running tool -> preamble at once -> completion later
    _speak(session, frames=12)
    session.advance(t.uplink_delay_ms + t.end_of_turn_delay_ms + t.tool_call_delay_ms + 1)
    call = session.pending_tool_call
    assert call is not None, "simulator must have emitted the scripted tool call"
    session.submit_tool_result(call["call_id"], {"status": "running", "preamble": preamble})
    session.advance(t.tool_preamble_delay_ms + t.preamble_duration_ms + 400)
    session.submit_tool_result(
        call["call_id"], {"status": "succeeded", "summary": "3 kaynak, 1 özet"}
    )
    session.run_until_idle()

    # turn 5: hesitation — end-of-turn must wait for the guard, not cut the owner
    speech_stopped_before = len(session.events_of(RT_SPEECH_STOPPED))
    _speak(session, frames=6, filler_last=True)
    session.advance(t.uplink_delay_ms + t.end_of_turn_delay_ms + 10)
    false_barge = len(session.events_of(RT_SPEECH_STOPPED)) - speech_stopped_before
    _speak(session, frames=4)  # the owner continues after the hesitation
    session.run_until_idle()
    session.close()

    events = events_from_simulator(session)
    return build_report(
        events,
        source=SOURCE_SIMULATOR,
        targets=targets,
        false_barge_count=false_barge,
        context={
            "provider": provider.name,
            "timings_ms": {
                f: getattr(t, f)
                for f in (
                    "uplink_delay_ms",
                    "end_of_turn_delay_ms",
                    "hesitation_guard_ms",
                    "first_audio_delay_ms",
                    "barge_in_stop_delay_ms",
                    "tool_call_delay_ms",
                    "tool_preamble_delay_ms",
                    "tool_done_to_speech_ms",
                    "response_gap_ms",
                )
            },
            "turns": session.turn,
            "audio_in_bytes": session.audio_in_bytes,
            "audio_out_bytes": session.audio_out_bytes,
            "fsm_barge_ins": session.fsm.barge_in_count,
        },
    )


# ---------------------------------------------------------- client source


def events_from_client_reports(rows: Iterable[dict[str, Any]]) -> list[TimingEvent]:
    """Rows as stored by the session service (kind, t_ms, turn, payload)."""
    out: list[TimingEvent] = []
    for row in rows:
        kind = str(row.get("kind", ""))
        if kind not in TIMING_EVENT_KINDS:
            continue
        out.append(
            TimingEvent(
                kind,
                int(row.get("t_ms", 0)),
                int(row.get("turn", 0) or 0),
                dict(row.get("payload") or {}),
            )
        )
    return out


__all__ = [
    "METRICS",
    "METRIC_PAIRS",
    "REPORT_SCHEMA_VERSION",
    "SOURCE_CLIENT",
    "SOURCE_SIMULATOR",
    "TIMING_EVENT_KINDS",
    "BenchTargets",
    "RealtimeBenchReport",
    "TimingEvent",
    "build_report",
    "default_script",
    "detect_gaps",
    "events_from_client_reports",
    "events_from_simulator",
    "pair_metric",
    "run_simulator_benchmark",
    "summarize",
]
