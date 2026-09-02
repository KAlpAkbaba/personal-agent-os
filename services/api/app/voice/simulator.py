"""Deterministic full-duplex realtime SIMULATOR provider (M12 spec §8, gap analysis).

The M4 ``FakeRealtimeProvider`` drives only the control FSM. This provider adds
what the harness and the session service need to be proven offline: a virtual
session clock, synthetic audio frames, structured provider events with
millisecond timestamps, server-side end-of-turn with a Turkish hesitation
guard, barge-in that cancels an in-flight response, tool calls with a spoken
preamble, and network loss/restore — all with **controllable timings** so a
test can make the simulator fast (targets met) or slow (targets missed) and
thereby prove the benchmark harness measures rather than assumes.

No wall clock, no threads, no network. Time advances only through
``advance(ms)`` / ``run_until_idle()``; scheduled work is a priority queue keyed
by (due_ms, seq), so ordering is total and reproducible.

The M4 control FSM (``app.voice.realtime.RealtimeSession``) is embedded and
driven by every transition, so its ordering semantics ("cut speech first, then
latch") and ``STOP_WORDS`` priority are reused, not re-implemented.
"""

from __future__ import annotations

import heapq
import itertools
import math
import secrets
import struct
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from typing import Any

from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import (
    END_OF_TURN_SEMANTIC,
    INTERRUPT_LATENCY_FAST,
    RT_ERROR,
    RT_NETWORK_LOST,
    RT_NETWORK_RESTORED,
    RT_RESPONSE_AUDIO,
    RT_RESPONSE_DONE,
    RT_RESPONSE_STARTED,
    RT_SPEECH_STARTED,
    RT_SPEECH_STOPPED,
    RT_TOOL_CALL,
    TRANSPORT_SIMULATED,
    AudioSink,
    EphemeralCredential,
    EventSink,
    ProviderCapabilities,
    RealtimeSessionEvent,
)
from app.voice.realtime import RealtimeSession, RealtimeState

SIMULATOR_PROVIDER_NAME = "simulator"

_SAMPLE_RATE = 16000


@dataclass(frozen=True, slots=True)
class SimulatorTimings:
    """Every latency the simulator exhibits, in milliseconds, all controllable.

    The defaults describe a *good* provider (they meet the spec §8 targets);
    tests construct slow variants to prove the harness catches misses.
    """

    #: mic frame pushed -> provider acknowledges (speech_started for the 1st frame)
    uplink_delay_ms: int = 20
    #: trailing silence after the last owner frame before end-of-turn fires
    end_of_turn_delay_ms: int = 180
    #: extra trailing silence when the last frame was a hesitation filler
    hesitation_guard_ms: int = 450
    #: end-of-turn -> first response audio frame
    first_audio_delay_ms: int = 320
    #: barge-in detected -> in-flight response cancelled (playback stops)
    barge_in_stop_delay_ms: int = 60
    #: end-of-turn -> the provider emits a tool_call event
    tool_call_delay_ms: int = 150
    #: preamble submitted -> preamble audio starts
    tool_preamble_delay_ms: int = 200
    #: final tool result submitted -> first resumed response audio frame
    tool_done_to_speech_ms: int = 300
    #: output audio frame period
    frame_ms: int = 20
    #: length of a scripted reply
    response_duration_ms: int = 1200
    #: length of a spoken preamble
    preamble_duration_ms: int = 600
    #: an artificial mid-response gap (0 = none); used to prove gap detection
    response_gap_ms: int = 0

    def __post_init__(self) -> None:
        for f in fields(self):
            if int(getattr(self, f.name)) < 0:
                raise ValueError(f"{f.name} must be >= 0")
        if self.frame_ms <= 0:
            raise ValueError("frame_ms must be > 0")


@dataclass(frozen=True, slots=True)
class SimulatedTurn:
    """What the simulated assistant does when the owner's turn ends."""

    kind: str = "reply"  # "reply" | "tool_call"
    text: str = "Tamam, anladım."
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def reply(cls, text: str = "Tamam, anladım.") -> SimulatedTurn:
        return cls(kind="reply", text=text)

    @classmethod
    def tool_call(cls, name: str, arguments: dict[str, Any] | None = None) -> SimulatedTurn:
        return cls(kind="tool_call", tool_name=name, arguments=dict(arguments or {}))


@dataclass(frozen=True, slots=True)
class ClientMark:
    """A client-side timestamp the simulated client records (the harness needs
    both sides: what the client did and what the provider emitted)."""

    kind: str
    at_ms: int
    payload: dict[str, Any] = field(default_factory=dict)


def synth_frame(seq: int, *, frame_ms: int, sample_rate: int = _SAMPLE_RATE) -> bytes:
    """Deterministic PCM16 mono frame: a quiet 440 Hz tone phase-continuous in ``seq``."""
    n = sample_rate * frame_ms // 1000
    base = seq * n
    amp = 3000
    return b"".join(
        struct.pack("<h", int(amp * math.sin(2 * math.pi * 440 * (base + i) / sample_rate)))
        for i in range(n)
    )


class SimulatedRealtimeSession:
    """One deterministic full-duplex media session (see module docstring)."""

    def __init__(
        self,
        *,
        provider: str = SIMULATOR_PROVIDER_NAME,
        language: str = "tr-TR",
        timings: SimulatorTimings | None = None,
        script: Iterable[SimulatedTurn] = (),
    ) -> None:
        self.provider = provider
        self.language = language
        self.timings = timings or SimulatorTimings()
        self.script: deque[SimulatedTurn] = deque(script)
        self.fsm = RealtimeSession(provider=provider, language=language)
        self.clock_ms = 0
        self.events: list[RealtimeSessionEvent] = []
        self.client_marks: list[ClientMark] = []
        self.audio_out_bytes = 0
        self.audio_in_bytes = 0
        self.turn = 0
        self._queue: list[tuple[int, int, Callable[[], None]]] = []
        self._seq = itertools.count(1)
        self._audio_sinks: list[AudioSink] = []
        self._event_sinks: list[EventSink] = []
        self._closed = False
        # owner side
        self._owner_speaking = False
        self._last_owner_frame_ms: int | None = None
        self._last_frame_was_filler = False
        # assistant side
        self._response_gen = 0  # bumping it cancels every scheduled frame of the response
        self._responding = False
        self._preamble_playing = False
        # tools
        self._pending_tool: dict[str, Any] | None = None
        self._tool_seq = itertools.count(1)
        self._network_down = False

    # ------------------------------------------------------------ plumbing

    def _emit(self, kind: str, **payload: Any) -> RealtimeSessionEvent:
        ev = RealtimeSessionEvent(kind=kind, at_ms=self.clock_ms, payload=payload)
        self.events.append(ev)
        for sink in self._event_sinks:
            sink(ev)
        return ev

    def _mark(self, kind: str, **payload: Any) -> None:
        self.client_marks.append(ClientMark(kind=kind, at_ms=self.clock_ms, payload=payload))

    def _schedule(self, delay_ms: int, fn: Callable[[], None]) -> None:
        heapq.heappush(self._queue, (self.clock_ms + max(0, delay_ms), next(self._seq), fn))

    def _require_open(self) -> None:
        if self._closed:
            raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "session is closed",
                             provider=self.provider)

    # ----------------------------------------------------- virtual clock

    def advance(self, ms: int) -> None:
        """Advance the session clock by ``ms``, running everything that is due."""
        if ms < 0:
            raise ValueError("cannot advance backwards")
        target = self.clock_ms + ms
        while self._queue and self._queue[0][0] <= target:
            due, _, fn = heapq.heappop(self._queue)
            self.clock_ms = due
            fn()
        self.clock_ms = target

    def run_until_idle(self, *, max_ms: int = 60_000) -> None:
        """Run scheduled work until nothing is pending (or ``max_ms`` elapse)."""
        deadline = self.clock_ms + max_ms
        while self._queue and self._queue[0][0] <= deadline:
            due, _, fn = heapq.heappop(self._queue)
            self.clock_ms = due
            fn()

    @property
    def pending(self) -> int:
        return len(self._queue)

    @property
    def state(self) -> RealtimeState:
        return self.fsm.state

    @property
    def responding(self) -> bool:
        return self._responding

    # ------------------------------------------------ handle: audio in

    def push_audio(self, chunk: bytes, *, filler: bool = False) -> None:
        """Owner microphone frame. ``filler=True`` marks a hesitation ("şey…",
        "yani…", a trailing vowel) so the semantic end-of-turn waits longer."""
        self._require_open()
        if not chunk:
            raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "empty audio chunk",
                             provider=self.provider)
        self.audio_in_bytes += len(chunk)
        self._last_frame_was_filler = filler
        self._last_owner_frame_ms = self.clock_ms
        if not self._owner_speaking:
            self._owner_speaking = True
            self.turn += 1
            self._mark("mic_speech_start", turn=self.turn)
            overlapped = self._responding or self._preamble_playing
            if overlapped:
                self._mark("barge_in_start", turn=self.turn)
            self._schedule(self.timings.uplink_delay_ms,
                           lambda t=self.turn, o=overlapped: self._provider_speech_started(t, o))
        expected = self._last_owner_frame_ms
        guard = self.timings.hesitation_guard_ms if filler else 0
        self._schedule(
            self.timings.uplink_delay_ms + self.timings.end_of_turn_delay_ms + guard,
            lambda e=expected: self._maybe_end_of_turn(e),
        )

    def _provider_speech_started(self, turn: int, overlapped: bool) -> None:
        if self._network_down or self._closed:
            return
        self._emit(RT_SPEECH_STARTED, turn=turn, overlap=overlapped)
        # M4 FSM ordering: if the assistant is speaking this IS a barge-in and
        # speech is cut first; the provider-side cancellation follows after
        # barge_in_stop_delay_ms (the measured "barge-in -> stop" latency).
        self.fsm.owner_speech_started("(audio)")
        if overlapped:
            self._schedule(self.timings.barge_in_stop_delay_ms, self._cancel_response)

    def _maybe_end_of_turn(self, expected_last_frame_ms: int) -> None:
        if (
            self._closed
            or not self._owner_speaking
            or self._last_owner_frame_ms != expected_last_frame_ms
        ):
            return  # a newer frame arrived; that frame scheduled its own check
        self._owner_speaking = False
        self._emit(RT_SPEECH_STOPPED, turn=self.turn,
                   after_filler=self._last_frame_was_filler)
        self.fsm.owner_speech_ended()
        self._begin_assistant_turn(self.turn)

    # -------------------------------------------------- assistant side

    def _begin_assistant_turn(self, turn: int) -> None:
        turn_spec = self.script.popleft() if self.script else SimulatedTurn.reply()
        if turn_spec.kind == "tool_call":
            self._schedule(self.timings.tool_call_delay_ms,
                           lambda: self._emit_tool_call(turn, turn_spec))
            return
        self._schedule(self.timings.first_audio_delay_ms,
                       lambda: self._start_response(turn, turn_spec.text,
                                                    self.timings.response_duration_ms))

    def _start_response(self, turn: int, text: str, duration_ms: int, *,
                        preamble: bool = False) -> None:
        if self._closed or self._network_down or self._owner_speaking:
            # A provider does not start talking over the owner; the owner's
            # next end-of-turn begins a fresh assistant turn instead.
            return
        self._response_gen += 1
        gen = self._response_gen
        if preamble:
            self._preamble_playing = True
            # Spoken progress WHILE the tool runs (M4 FSM: stays TOOL_RUNNING,
            # still interruptible by a stop word).
            if self.fsm.state == RealtimeState.TOOL_RUNNING:
                self.fsm.assistant_progress("(preamble)")
        else:
            self._responding = True
            self._emit(RT_RESPONSE_STARTED, turn=turn, chars=len(text))
            self.fsm.assistant_start_speaking(text)
        frames = max(1, duration_ms // self.timings.frame_ms)
        gap_after = frames // 2 if self.timings.response_gap_ms else None
        for i in range(frames):
            delay = i * self.timings.frame_ms
            if gap_after is not None and i >= gap_after:
                delay += self.timings.response_gap_ms
            self._schedule(delay, lambda g=gen, i=i, t=turn, p=preamble: self._frame(g, i, t, p))
        total = (frames - 1) * self.timings.frame_ms + (self.timings.response_gap_ms or 0)
        self._schedule(total + self.timings.frame_ms,
                       lambda g=gen, t=turn, p=preamble: self._finish_response(g, t, p))

    def _frame(self, gen: int, index: int, turn: int, preamble: bool) -> None:
        if gen != self._response_gen or self._closed:
            return  # cancelled by barge-in
        data = synth_frame(index, frame_ms=self.timings.frame_ms)
        self.audio_out_bytes += len(data)
        for sink in self._audio_sinks:
            sink(data)
        self._emit(RT_RESPONSE_AUDIO, turn=turn, bytes=len(data), index=index, preamble=preamble)

    def _finish_response(self, gen: int, turn: int, preamble: bool) -> None:
        if gen != self._response_gen or self._closed:
            return
        if preamble:
            self._preamble_playing = False
            self._emit(RT_RESPONSE_DONE, turn=turn, cancelled=False, preamble=True)
            return
        self._responding = False
        self.fsm.assistant_stop_speaking()
        self._emit(RT_RESPONSE_DONE, turn=turn, cancelled=False)

    def _cancel_response(self) -> None:
        if self._closed or not (self._responding or self._preamble_playing):
            return
        self._response_gen += 1  # drops every scheduled frame of the old response
        was_preamble = self._preamble_playing
        self._responding = False
        self._preamble_playing = False
        self._emit(RT_RESPONSE_DONE, turn=self.turn, cancelled=True, preamble=was_preamble)

    # --------------------------------------------------------- tools

    def _emit_tool_call(self, turn: int, spec: SimulatedTurn) -> None:
        if self._closed or self._network_down:
            return
        call_id = f"call_{next(self._tool_seq):04d}"
        self._pending_tool = {"call_id": call_id, "name": spec.tool_name, "turn": turn}
        self.fsm.start_tool_call(spec.tool_name)
        self._emit(RT_TOOL_CALL, turn=turn, call_id=call_id, name=spec.tool_name,
                   arguments=dict(spec.arguments))

    def submit_tool_result(self, call_id: str, result: dict[str, Any]) -> None:
        """Client hands the provider a tool output. ``{"status": "running",
        "preamble": "..."}`` makes the assistant speak the preamble while the
        tool keeps running; anything else is the final result and the
        assistant resumes speaking after ``tool_done_to_speech_ms``."""
        self._require_open()
        pending = self._pending_tool
        if pending is None or pending["call_id"] != call_id:
            raise VoiceError(VoiceErrorClass.VALIDATION_ERROR,
                             f"no pending tool call {call_id!r}", provider=self.provider)
        turn = pending["turn"]
        if result.get("status") == "running":
            preamble = str(result.get("preamble") or "")
            self._mark("tool_running_submitted", turn=turn, call_id=call_id,
                       preamble_chars=len(preamble))
            if preamble:
                self._schedule(self.timings.tool_preamble_delay_ms,
                               lambda: self._start_response(
                                   turn, preamble, self.timings.preamble_duration_ms,
                                   preamble=True))
            return
        self._pending_tool = None
        self._mark("tool_done", turn=turn, call_id=call_id)
        self.fsm.finish_tool_call(call_id)
        if self._preamble_playing:
            self._response_gen += 1
            self._preamble_playing = False
        self._schedule(self.timings.tool_done_to_speech_ms,
                       lambda: self._start_response(turn, "Sonuç hazır.",
                                                    self.timings.response_duration_ms))

    @property
    def pending_tool_call(self) -> dict[str, Any] | None:
        return dict(self._pending_tool) if self._pending_tool else None

    # --------------------------------------------------- handle: misc

    def on_audio(self, sink: AudioSink) -> None:
        self._audio_sinks.append(sink)

    def on_event(self, sink: EventSink) -> None:
        self._event_sinks.append(sink)

    def request_barge_in(self) -> None:
        """Explicit client-initiated cancel (a stop word, or a UI stop). The
        client stops LOCAL playback first; this cancels the provider side."""
        self._require_open()
        self._mark("barge_in_start", turn=self.turn, explicit=True)
        self.fsm.request_barge_in()
        if self._responding or self._preamble_playing:
            self._schedule(self.timings.barge_in_stop_delay_ms, self._cancel_response)

    def network_lost(self) -> None:
        self._require_open()
        self._network_down = True
        self.fsm.network_lost()
        self._emit(RT_NETWORK_LOST)
        if self._responding or self._preamble_playing:
            self._response_gen += 1
            self._responding = False
            self._preamble_playing = False

    def network_restored(self) -> None:
        self._require_open()
        self._network_down = False
        self.fsm.network_restored()
        self._emit(RT_NETWORK_RESTORED)

    def fail(self, message: str) -> None:
        """Inject a provider error event (tests of the error path)."""
        self._require_open()
        self._emit(RT_ERROR, message=message)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.clear()
        self.fsm.close()

    @property
    def closed(self) -> bool:
        return self._closed

    # ------------------------------------------------------- queries

    def event_kinds(self) -> list[str]:
        return [e.kind for e in self.events]

    def events_of(self, kind: str) -> list[RealtimeSessionEvent]:
        return [e for e in self.events if e.kind == kind]


class SimulatedRealtimeProvider:
    """The ``ConversationRealtime``-capable offline provider used by the gate."""

    name = SIMULATOR_PROVIDER_NAME

    def __init__(
        self,
        *,
        timings: SimulatorTimings | None = None,
        script_factory: Callable[[], Iterable[SimulatedTurn]] | None = None,
        credential_ttl_s: int = 600,
    ) -> None:
        self.timings = timings or SimulatorTimings()
        self._script_factory = script_factory
        self.credential_ttl_s = credential_ttl_s
        self.minted: list[str] = []  # session refs only; never the secret

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name, kind="realtime", languages=("tr-TR", "en-US"), streaming=True,
            long_form_stability="n/a", pronunciation_dict=False, voice_selection=True,
            speed_control=True,
            cost_metadata={"unit": "audio_minutes", "usd_per_min": 0.0, "note": "simulator"},
            output_formats=("pcm16",), latency_class="realtime", requires_api_key=False,
            speech_to_speech=True, full_duplex=True, barge_in=True,
            end_of_turn=END_OF_TURN_SEMANTIC, tool_calling=True,
            transports=(TRANSPORT_SIMULATED,), ephemeral_credentials=True,
            input_formats=("pcm16",), interrupt_latency_class=INTERRUPT_LATENCY_FAST,
        )

    def open_session(self, *, language: str = "tr-TR") -> SimulatedRealtimeSession:
        script = self._script_factory() if self._script_factory else ()
        return SimulatedRealtimeSession(provider=self.name, language=language,
                                        timings=self.timings, script=script)

    def mint_credential(
        self, *, session_id: str, ttl_s: int | None = None, transport: str = TRANSPORT_SIMULATED
    ) -> EphemeralCredential:
        """A random, single-session secret. Nothing from Settings is involved —
        the simulator has no vendor key, which is exactly the property the
        session-service tests assert (the vendor key is never a client value)."""
        ttl = ttl_s if ttl_s is not None else self.credential_ttl_s
        ref = f"sim:{session_id}"
        self.minted.append(ref)
        return EphemeralCredential(
            provider=self.name,
            secret="sim_" + secrets.token_urlsafe(24),
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
            transport=transport,
            session_ref=ref,
        )


__all__ = [
    "SIMULATOR_PROVIDER_NAME",
    "ClientMark",
    "SimulatedRealtimeProvider",
    "SimulatedRealtimeSession",
    "SimulatedTurn",
    "SimulatorTimings",
    "synth_frame",
]
