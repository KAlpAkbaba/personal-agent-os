"""Realtime dialogue + barge-in state machine (VOICE_SPEC §2).

Realtime low-latency *audio* needs a provider + keys + a mic/AEC/VAD pipeline
(an owner action). What we can build and test deterministically without any of
that is the **control** state machine: when the owner starts speaking while the
assistant is talking, assistant speech must stop quickly (barge-in), and the
Turkish stop word "dur" always has top priority — it interrupts from any state,
including while a tool call is running and the assistant is only reporting short
progress.

States:

    IDLE            -> no one is speaking
    LISTENING       -> capturing owner speech (mic open, VAD active)
    ASSISTANT_SPEAKING -> assistant TTS is playing
    TOOL_RUNNING    -> a tool call is in flight; assistant may emit short progress
    INTERRUPTED     -> barge-in latched; assistant speech has been cut
    CLOSED          -> session ended

The machine records a monotonic event log so tests can assert *ordering* and
that barge-in latency (events between owner-speech-start and speech-stopped) is
bounded. It carries no audio bytes.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.voice.errors import VoiceError, VoiceErrorClass


class RealtimeState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    ASSISTANT_SPEAKING = "ASSISTANT_SPEAKING"
    TOOL_RUNNING = "TOOL_RUNNING"
    INTERRUPTED = "INTERRUPTED"
    CLOSED = "CLOSED"


# Turkish stop words that must interrupt from ANY active state with top priority.
STOP_WORDS = frozenset({"dur", "kes", "sus", "yeter", "tamam dur"})


@dataclass(slots=True)
class RealtimeEvent:
    seq: int
    kind: str
    state: RealtimeState
    detail: str = ""


@dataclass(slots=True)
class RealtimeSession:
    """Deterministic barge-in state machine. Also serves as the FakeRealtime
    session handle (push_audio / request_barge_in / close)."""

    provider: str = "fake-realtime"
    language: str = "tr-TR"
    state: RealtimeState = RealtimeState.IDLE
    events: list[RealtimeEvent] = field(default_factory=list)
    _seq: itertools.count[int] = field(default_factory=lambda: itertools.count(1), repr=False)
    barge_in_count: int = 0

    def _emit(self, kind: str, detail: str = "") -> RealtimeEvent:
        ev = RealtimeEvent(seq=next(self._seq), kind=kind, state=self.state, detail=detail)
        self.events.append(ev)
        return ev

    def _require_open(self) -> None:
        if self.state == RealtimeState.CLOSED:
            raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "session is closed",
                             provider=self.provider)

    # -------------------------------------------------------------- transitions

    def assistant_start_speaking(self, detail: str = "") -> None:
        self._require_open()
        self.state = RealtimeState.ASSISTANT_SPEAKING
        self._emit("assistant_speech_started", detail)

    def assistant_stop_speaking(self, detail: str = "") -> None:
        self._require_open()
        if self.state == RealtimeState.ASSISTANT_SPEAKING:
            self.state = RealtimeState.IDLE
        self._emit("assistant_speech_stopped", detail)

    def start_tool_call(self, detail: str = "") -> None:
        self._require_open()
        self.state = RealtimeState.TOOL_RUNNING
        self._emit("tool_call_started", detail)

    def assistant_progress(self, detail: str = "") -> None:
        """Short spoken progress *while a tool runs* (VOICE_SPEC §2). Does not
        leave TOOL_RUNNING, and is still interruptible by a stop word."""
        self._require_open()
        if self.state != RealtimeState.TOOL_RUNNING:
            raise VoiceError(VoiceErrorClass.VALIDATION_ERROR,
                             "progress reports are only valid during a tool call",
                             provider=self.provider)
        self._emit("assistant_progress", detail)

    def finish_tool_call(self, detail: str = "") -> None:
        self._require_open()
        if self.state == RealtimeState.TOOL_RUNNING:
            self.state = RealtimeState.IDLE
        self._emit("tool_call_finished", detail)

    def owner_speech_started(self, text: str = "") -> RealtimeEvent:
        """Owner starts talking. If the assistant is speaking, this is a barge-in
        and assistant speech is cut immediately. A stop word forces barge-in from
        any active state (including TOOL_RUNNING progress)."""
        self._require_open()
        word = text.strip().lower()
        is_stop = word in STOP_WORDS
        self._emit("owner_speech_started", text)

        if self.state == RealtimeState.ASSISTANT_SPEAKING or is_stop:
            self._barge_in(stop_word=is_stop, detail=text)
            return self.events[-1]

        self.state = RealtimeState.LISTENING
        return self._emit("listening", text)

    def _barge_in(self, *, stop_word: bool, detail: str = "") -> None:
        # Cut assistant speech FIRST (the latency-critical action), then latch.
        self._emit("assistant_speech_cut", "stop_word" if stop_word else "overlap")
        self.barge_in_count += 1
        self.state = RealtimeState.INTERRUPTED
        self._emit("barge_in", "dur" if stop_word else detail)
        # After the cut the mic stays open to hear the owner out.
        self.state = RealtimeState.LISTENING
        self._emit("listening", detail)

    def owner_speech_ended(self, detail: str = "") -> None:
        self._require_open()
        if self.state == RealtimeState.LISTENING:
            self.state = RealtimeState.IDLE
        self._emit("owner_speech_ended", detail)

    def network_lost(self, detail: str = "") -> None:
        """Temporary network loss fails gracefully; session context is retained
        (state is not destroyed) so it can be restored (VOICE_SPEC §2)."""
        self._require_open()
        self._emit("network_lost", detail)

    def network_restored(self, detail: str = "") -> None:
        self._require_open()
        self._emit("network_restored", detail)

    # ------------------------------------------------ FakeRealtime handle API

    def push_audio(self, chunk: bytes) -> None:
        self._require_open()
        self._emit("audio_pushed", f"{len(chunk)}B")

    def request_barge_in(self) -> None:
        self.owner_speech_started("dur")

    # M12: the extended RealtimeSessionHandle contract. The control FSM carries
    # no audio and no provider events, so these are recorded, never acted on;
    # the deterministic full-duplex behaviour lives in app/voice/simulator.py.

    def on_audio(self, sink: Callable[[bytes], None]) -> None:
        self._emit("audio_sink_attached")

    def on_event(self, sink: Callable[[Any], None]) -> None:
        self._emit("event_sink_attached")

    def submit_tool_result(self, call_id: str, result: dict[str, Any]) -> None:
        self._require_open()
        self._emit("tool_result_submitted", call_id)

    def close(self) -> None:
        self.state = RealtimeState.CLOSED
        self._emit("closed")

    # ----------------------------------------------------------------- queries

    def barge_in_latency_events(self) -> int | None:
        """Number of log events from the last owner-speech-start to the assistant
        speech being cut. Lower is better; None if no barge-in happened."""
        start = cut = None
        for ev in self.events:
            if ev.kind == "owner_speech_started":
                start = ev.seq
            if ev.kind == "assistant_speech_cut" and start is not None:
                cut = ev.seq
        if start is None or cut is None:
            return None
        return cut - start

    def event_kinds(self) -> list[str]:
        return [ev.kind for ev in self.events]


__all__ = ["STOP_WORDS", "RealtimeEvent", "RealtimeSession", "RealtimeState"]
