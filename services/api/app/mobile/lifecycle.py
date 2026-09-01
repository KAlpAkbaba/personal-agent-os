"""Mobile client lifecycle state machine (M9 acceptance: mic/realtime voice
works under normal mobile lifecycle).

The thing a native mobile client gets wrong is not the happy path; it is what
happens to an *in-flight* realtime voice session and to *narration playback*
when the OS moves the app between foreground, background, a phone call and
termination. That is control-plane behaviour, it needs no audio hardware, and
it is therefore exactly the part that can be specified and tested here on a
machine with no device.

Reuse, not duplication: the barge-in machine already exists
(`app/voice/realtime.py`, VOICE_SPEC §2) and is not re-implemented. A
`RealtimeSession` is *held* by the lifecycle, the barge-in transitions are
forwarded to it unchanged, and this module adds only the transitions the OS
imposes on top.

The rules, and why each one is what it is:

- **background: narration keeps playing, the mic is released.** Background
  audio playback is the entire reason to want a native client (VOICE_SPEC §3
  "resume across PC/mobile/web"); a background mic capture is not something
  either mobile OS grants a general-purpose app, so an in-flight realtime
  session is *closed*, not pretended-suspended. Returning to the foreground
  therefore needs a new realtime session — flagged, never silently reopened,
  because reopening a microphone without the owner asking is not a decision
  software makes for itself.
- **interruption (a phone call): narration pauses, the mic is released.** The
  OS takes the audio session away. Narration is auto-paused and auto-resumed on
  `resume()`, because the owner did not ask for the interruption and the
  playback position is exactly what the M4 cursor exists to protect.
- **every transition that could lose state checkpoints the narration cursor**
  to the cloud, so the owner can pick the report up on another device
  (MASTER_SPEC §B cross-device continuity). The checkpoints are recorded here
  and the caller flushes them via `PATCH /v1/narration/sessions/{id}/cursor`.
- **session revocation is terminal and does not checkpoint.** A revoked
  session cannot write to the cloud; pretending otherwise would fabricate a
  successful save. The client re-authenticates and re-reads the last cursor the
  cloud actually has.

No audio, no timers, no I/O: a deterministic machine plus an ordered event log.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.mobile.errors import MobileError, MobileErrorClass
from app.voice.realtime import RealtimeSession, RealtimeState


class AppState(StrEnum):
    """Where the OS has put the app."""

    FOREGROUND = "FOREGROUND"
    BACKGROUND = "BACKGROUND"
    #: A call, an alarm, or anything else that seizes the audio session.
    INTERRUPTED = "INTERRUPTED"
    TERMINATED = "TERMINATED"


class Playback(StrEnum):
    """Narration playback, independent of the app state."""

    STOPPED = "STOPPED"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"


class MicState(StrEnum):
    """Whether a realtime voice session holds the microphone."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"


#: Which app states may hold an open microphone. Deliberately only one.
MIC_ALLOWED_STATES = frozenset({AppState.FOREGROUND})

#: App states in which narration audio may keep playing.
PLAYBACK_ALLOWED_STATES = frozenset({AppState.FOREGROUND, AppState.BACKGROUND})


@dataclass(slots=True)
class LifecycleEvent:
    seq: int
    kind: str
    app_state: AppState
    playback: Playback
    mic: MicState
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "app_state": str(self.app_state),
            "playback": str(self.playback),
            "mic": str(self.mic),
            "detail": self.detail,
        }


@dataclass(slots=True)
class CursorCheckpoint:
    """A narration cursor the client must flush to the cloud, and why."""

    seq: int
    reason: str
    cursor: dict[str, Any]


@dataclass(slots=True)
class MobileLifecycle:
    """Deterministic OS-lifecycle machine wrapped around the barge-in machine."""

    app_state: AppState = AppState.FOREGROUND
    playback: Playback = Playback.STOPPED
    realtime: RealtimeSession | None = None
    narration_session_id: str | None = None
    cursor: dict[str, Any] = field(default_factory=dict)
    events: list[LifecycleEvent] = field(default_factory=list)
    checkpoints: list[CursorCheckpoint] = field(default_factory=list)
    #: Set when a realtime session was torn down by the OS rather than by the
    #: owner. The client shows an affordance; it never reopens the mic itself.
    realtime_resume_required: bool = False
    #: Set when the session was revoked: nothing works until re-authentication.
    requires_reauth: bool = False
    #: True while playback was paused by the machine (not by the owner), so
    #: `resume()` knows it is allowed to start it again.
    _auto_paused: bool = False
    #: Where `resume()` should return to after an interruption.
    _resume_to: AppState = AppState.FOREGROUND
    _seq: itertools.count[int] = field(
        default_factory=lambda: itertools.count(1), repr=False
    )

    # ------------------------------------------------------------------ helpers

    @property
    def mic(self) -> MicState:
        if self.realtime is None or self.realtime.state == RealtimeState.CLOSED:
            return MicState.CLOSED
        return MicState.OPEN

    def _emit(self, kind: str, detail: str = "") -> LifecycleEvent:
        event = LifecycleEvent(
            seq=next(self._seq),
            kind=kind,
            app_state=self.app_state,
            playback=self.playback,
            mic=self.mic,
            detail=detail,
        )
        self.events.append(event)
        return event

    def _refuse(self, message: str, **details: Any) -> MobileError:
        return MobileError(
            MobileErrorClass.VALIDATION_ERROR,
            message,
            details={"app_state": str(self.app_state), **details},
        )

    def _require_live(self) -> None:
        if self.app_state == AppState.TERMINATED:
            raise self._refuse("the client is terminated")
        if self.requires_reauth:
            raise MobileError(
                MobileErrorClass.SESSION_REVOKED,
                "the owner session was revoked; re-authenticate before continuing",
            )

    def _release_mic(self, reason: str) -> None:
        """Close an in-flight realtime session. Idempotent."""
        if self.realtime is not None and self.realtime.state != RealtimeState.CLOSED:
            self.realtime.close()
            self.realtime_resume_required = True
            self._emit("mic_released", reason)

    def _checkpoint(self, reason: str) -> None:
        """Record the cursor the client must flush to the cloud."""
        if not self.cursor:
            return
        checkpoint = CursorCheckpoint(
            seq=next(self._seq), reason=reason, cursor=dict(self.cursor)
        )
        self.checkpoints.append(checkpoint)
        self._emit("cursor_checkpointed", reason)

    # ---------------------------------------------------------------- narration

    def start_narration(
        self, *, session_id: str, cursor: dict[str, Any] | None = None
    ) -> None:
        """Begin (or resume) playback of a cloud narration session."""
        self._require_live()
        if self.app_state not in PLAYBACK_ALLOWED_STATES:
            raise self._refuse("narration cannot start while the app is interrupted")
        self.narration_session_id = session_id
        if cursor is not None:
            self.cursor = dict(cursor)
        self.playback = Playback.PLAYING
        self._auto_paused = False
        self._emit("narration_started", session_id)

    def advance_narration(self, cursor: dict[str, Any]) -> None:
        """Playback moved. The cursor is the cloud's, not a local byte offset."""
        self._require_live()
        if self.playback != Playback.PLAYING:
            raise self._refuse("narration is not playing")
        self.cursor = dict(cursor)
        self._emit("narration_advanced", str(cursor.get("sentence_index", "")))

    def pause_narration(self, *, reason: str = "owner", auto: bool = False) -> None:
        self._require_live()
        if self.playback != Playback.PLAYING:
            return
        self.playback = Playback.PAUSED
        self._auto_paused = auto
        self._emit("narration_paused", reason)
        self._checkpoint(f"paused:{reason}")

    def stop_narration(self, *, reason: str = "owner") -> None:
        self._require_live()
        if self.playback == Playback.STOPPED:
            return
        self.playback = Playback.STOPPED
        self._auto_paused = False
        self._emit("narration_stopped", reason)
        self._checkpoint(f"stopped:{reason}")

    # ----------------------------------------------------------------- realtime

    def open_realtime(self, *, language: str = "tr-TR") -> RealtimeSession:
        """Open a realtime voice session (microphone). Foreground only."""
        self._require_live()
        if self.app_state not in MIC_ALLOWED_STATES:
            raise self._refuse(
                "the microphone is only available in the foreground",
                mic_allowed_states=[str(s) for s in sorted(MIC_ALLOWED_STATES)],
            )
        if self.mic == MicState.OPEN:
            raise self._refuse("a realtime session is already open")
        self.realtime = RealtimeSession(language=language)
        self.realtime_resume_required = False
        self._emit("mic_opened", language)
        return self.realtime

    def close_realtime(self, *, reason: str = "owner") -> None:
        """Owner-initiated close: no resume affordance is raised."""
        self._require_live()
        if self.mic == MicState.CLOSED:
            return
        assert self.realtime is not None
        self.realtime.close()
        self.realtime_resume_required = False
        self._emit("mic_closed", reason)

    def _require_mic(self) -> RealtimeSession:
        if self.mic == MicState.CLOSED or self.realtime is None:
            raise self._refuse("no realtime session is open")
        return self.realtime

    def owner_speech_started(self, text: str = "") -> None:
        """Forwarded to the barge-in machine, plus the one mobile-specific rule:
        the owner speaking is also a barge-in over *narration*, because it is
        the same speaker the report is coming out of."""
        self._require_live()
        session = self._require_mic()
        session.owner_speech_started(text)
        if self.playback == Playback.PLAYING:
            # Not an auto-pause: the owner interrupted deliberately, so resuming
            # is an owner decision ("devam"), not something the OS triggers.
            self.pause_narration(reason="barge_in", auto=False)
        self._emit("owner_speech_started", text)

    def owner_speech_ended(self, detail: str = "") -> None:
        self._require_live()
        self._require_mic().owner_speech_ended(detail)
        self._emit("owner_speech_ended", detail)

    def assistant_start_speaking(self, detail: str = "") -> None:
        self._require_live()
        self._require_mic().assistant_start_speaking(detail)
        self._emit("assistant_speech_started", detail)

    def network_lost(self, detail: str = "") -> None:
        """Transient loss. Realtime keeps its context (VOICE_SPEC §2); narration
        pauses because the next audio chunk cannot be fetched."""
        self._require_live()
        if self.realtime is not None and self.realtime.state != RealtimeState.CLOSED:
            self.realtime.network_lost(detail)
        if self.playback == Playback.PLAYING:
            self.pause_narration(reason="network_lost", auto=True)
        self._emit("network_lost", detail)

    def network_restored(self, detail: str = "") -> None:
        self._require_live()
        if self.realtime is not None and self.realtime.state != RealtimeState.CLOSED:
            self.realtime.network_restored(detail)
        if self.playback == Playback.PAUSED and self._auto_paused:
            self.playback = Playback.PLAYING
            self._auto_paused = False
            self._emit("narration_resumed", "network_restored")
        self._emit("network_restored", detail)

    # ------------------------------------------------------------- OS lifecycle

    def to_background(self, *, reason: str = "home_button") -> None:
        """FOREGROUND -> BACKGROUND. Mic released, narration keeps playing."""
        self._require_live()
        if self.app_state == AppState.BACKGROUND:
            return
        if self.app_state != AppState.FOREGROUND:
            raise self._refuse("only a foreground app can be backgrounded")
        self._release_mic("backgrounded")
        self.app_state = AppState.BACKGROUND
        self._emit("backgrounded", reason)
        # Backgrounded apps get killed without warning; save the position now.
        self._checkpoint("backgrounded")

    def to_foreground(self, *, reason: str = "owner_opened") -> None:
        """BACKGROUND | INTERRUPTED -> FOREGROUND."""
        self._require_live()
        if self.app_state == AppState.FOREGROUND:
            return
        if self.app_state == AppState.INTERRUPTED:
            self.resume(reason=reason, to=AppState.FOREGROUND)
            return
        self.app_state = AppState.FOREGROUND
        self._emit("foregrounded", reason)

    def interrupt(self, *, reason: str = "phone_call") -> None:
        """FOREGROUND | BACKGROUND -> INTERRUPTED. The OS took the audio session."""
        self._require_live()
        if self.app_state == AppState.INTERRUPTED:
            return
        self._resume_to = self.app_state
        self._release_mic(f"interrupted:{reason}")
        if self.playback == Playback.PLAYING:
            self.pause_narration(reason=reason, auto=True)
        self.app_state = AppState.INTERRUPTED
        self._emit("interrupted", reason)

    def resume(
        self, *, reason: str = "call_ended", to: AppState | None = None
    ) -> None:
        """INTERRUPTED -> back where we were (or an explicit target)."""
        self._require_live()
        if self.app_state != AppState.INTERRUPTED:
            raise self._refuse("resume is only valid from INTERRUPTED")
        target = to or self._resume_to
        if target not in PLAYBACK_ALLOWED_STATES:  # pragma: no cover - defensive
            target = AppState.FOREGROUND
        self.app_state = target
        self._emit("resumed", reason)
        if self.playback == Playback.PAUSED and self._auto_paused:
            self.playback = Playback.PLAYING
            self._auto_paused = False
            self._emit("narration_resumed", reason)
        # The mic is NOT reopened: `realtime_resume_required` is the affordance.

    def terminate(self, *, reason: str = "os_killed") -> None:
        """Any state -> TERMINATED. Last chance to save the cursor."""
        if self.app_state == AppState.TERMINATED:
            return
        self._release_mic(f"terminated:{reason}")
        self.playback = Playback.STOPPED
        self._auto_paused = False
        was_revoked = self.requires_reauth
        self.app_state = AppState.TERMINATED
        self._emit("terminated", reason)
        if not was_revoked:
            self._checkpoint(f"terminated:{reason}")

    def session_revoked(self, *, reason: str = "session_revoked") -> None:
        """The API answered 401: the owner session is gone.

        Terminal and deliberately checkpoint-free — a revoked session cannot
        write to the cloud, and recording a checkpoint the client can never
        flush would be a lie about where the owner left off.
        """
        self.requires_reauth = True
        self._emit("session_revoked", reason)
        self.terminate(reason=reason)

    # ----------------------------------------------------------------- queries

    def event_kinds(self) -> list[str]:
        return [event.kind for event in self.events]

    def checkpoint_reasons(self) -> list[str]:
        return [checkpoint.reason for checkpoint in self.checkpoints]

    def last_checkpoint(self) -> CursorCheckpoint | None:
        return self.checkpoints[-1] if self.checkpoints else None

    def snapshot(self) -> dict[str, Any]:
        return {
            "app_state": str(self.app_state),
            "playback": str(self.playback),
            "mic": str(self.mic),
            "realtime_state": str(self.realtime.state) if self.realtime else None,
            "narration_session_id": self.narration_session_id,
            "cursor": dict(self.cursor),
            "realtime_resume_required": self.realtime_resume_required,
            "requires_reauth": self.requires_reauth,
            "checkpoints": len(self.checkpoints),
        }


__all__ = [
    "MIC_ALLOWED_STATES",
    "PLAYBACK_ALLOWED_STATES",
    "AppState",
    "CursorCheckpoint",
    "LifecycleEvent",
    "MicState",
    "MobileLifecycle",
    "Playback",
]
