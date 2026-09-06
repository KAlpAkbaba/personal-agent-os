"""The wake sequence (M18.3 spec §3.5): every physical step, every receipt.

One class, :class:`WakeSequence`, driven by two injected ports — a
``app.routines.dispatch.DeviceActionPort`` and a TTS provider — so the whole sequence is
unit-testable with no device, no browser, no audio and no network. That is not a testing
convenience: it is the same boundary ``app.routines.dispatch`` already draws, and it is why
this file can be read as a list of promises rather than a list of side effects.

The promises, in order (spec §1.5, §3.5):

1. **Disarm first.** The device's own armed fallback is disarmed before anything else, so a
   cloud-driven ring and a local fallback ring can never both happen. If the device is
   unreachable the disarm fails — and the local fallback ringing is then exactly what the
   owner wants, which is why a failed disarm never stops the sequence.
2. **The display is optional.** ``desktop.display_wake`` is best effort. A dark screen is
   not a reason for a silent alarm, so its failure is RECORDED and the sequence continues.
3. **Two audio paths, in order, and the fallback is not a lie.** The owner's named media
   through the browser worker's dedicated ``alarm`` profile; if that is not possible for
   ANY reason — no capability, no url, a challenge, autoplay blocked, no media element —
   the device's own tone with the same ramp, and the media failure is a receipt of its own
   with the real reason. Only if BOTH fail is the alarm ``FAILED``, and then it says so.
4. **Nothing here needs a voice session, a microphone, the camera or presence.** The
   greeting is a WAV the companion fetches and plays. ``tests/unit/test_alarms_structure.py``
   asserts the whole package never imports ``RealtimeSessionRow``.
5. **Every device call is a receipt with a ledger row.** :data:`RECEIPT_BY_DEVICE_CALL` is
   the enumeration, and a structural test asserts every capability this module dispatches
   appears in it — so a step added later without a receipt fails the suite rather than
   quietly becoming a hidden action.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy.orm import Session

from app.actions.receipt import (
    ERROR_CAPABILITY_MISSING,
    EXECUTION_EXECUTED,
    EXECUTION_FAILED,
    EXECUTION_NOOP,
    EXECUTION_REFUSED,
    TERMINAL_ALREADY,
    TERMINAL_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.alarms import speech as alarm_speech
from app.alarms.audio_store import AudioStore, AudioTooLarge, get_audio_store
from app.alarms.greeting_audio import (
    GREETING_LEVEL,
    GREETING_MAX_SECONDS,
    GreetingAudio,
    GreetingSynthesisFailed,
    TTSProviderLike,
    normalize_greeting,
    synthesize_greeting,
)
from app.alarms.models import (
    PLAYED_KIND_TONE_FALLBACK,
    PLAYED_KIND_YOUTUBE,
    STATE_DISPLAY_WAKING,
    STATE_FAILED,
    STATE_FIRING,
    STATE_GREETING,
    STATE_MEDIA_STARTING,
    STATE_PLAYING,
    WakeAlarm,
)
from app.ledger.vocabulary import SUBSYSTEM_AMBIENT, SUBSYSTEM_ROUTINE
from app.logging import get_logger
from app.routines.dispatch import (
    CAPABILITY_BROWSER_MEDIA_PLAY,
    CAPABILITY_BROWSER_MEDIA_STATUS,
    CAPABILITY_BROWSER_MEDIA_STOP,
    CAPABILITY_BROWSER_MEDIA_VOLUME,
    CAPABILITY_BROWSER_SESSION_OPEN,
    CAPABILITY_DESKTOP_ALARM_ARM,
    CAPABILITY_DESKTOP_ALARM_DISARM,
    CAPABILITY_DESKTOP_ALARM_START,
    CAPABILITY_DESKTOP_ALARM_STOP,
    CAPABILITY_DESKTOP_DISPLAY_OFF,
    CAPABILITY_DESKTOP_DISPLAY_WAKE,
    CAPABILITY_DESKTOP_PLAY_AUDIO,
    DeviceActionPort,
    DeviceRunResult,
)

logger = get_logger("app.alarms.sequence")

# ------------------------------------------------------------- the receipt enumeration

#: Device capability -> the receipt capability its outcome is recorded under (spec §3.5,
#: docs/M18_ACTION_CONTRACT.md §5.5). Every physical action this package dispatches appears
#: here, and ``tests/unit/test_alarms_structure.py`` asserts the two sets agree: a step
#: added without a receipt is a test failure, not a hidden action.
RECEIPT_BY_DEVICE_CALL: Final[dict[str, str]] = {
    CAPABILITY_DESKTOP_ALARM_ARM: "alarm.arm",
    CAPABILITY_DESKTOP_ALARM_DISARM: "alarm.disarm",
    CAPABILITY_DESKTOP_ALARM_START: "alarm.start",
    CAPABILITY_DESKTOP_ALARM_STOP: "alarm.stop",
    CAPABILITY_DESKTOP_DISPLAY_WAKE: "display.wake",
    CAPABILITY_DESKTOP_DISPLAY_OFF: "display.off",
    CAPABILITY_DESKTOP_PLAY_AUDIO: "greeting.play",
    CAPABILITY_BROWSER_SESSION_OPEN: "media.play",
    CAPABILITY_BROWSER_MEDIA_PLAY: "media.play",
    CAPABILITY_BROWSER_MEDIA_VOLUME: "media.volume",
    CAPABILITY_BROWSER_MEDIA_STATUS: "media.status",
    CAPABILITY_BROWSER_MEDIA_STOP: "media.stop",
}

#: The subsystem each receipt's ledger row is filed under. Display power is AMBIENT (the
#: policy that owns it); everything an alarm does is ROUTINE (the alarm IS a routine's
#: action). One mapping, so a reader asking "show me everything the display did" gets it
#: from one subsystem filter regardless of who asked for it.
_SUBSYSTEM_BY_RECEIPT: Final[dict[str, str]] = {
    "display.wake": SUBSYSTEM_AMBIENT,
    "display.off": SUBSYSTEM_AMBIENT,
}

#: Timeouts, seconds. A wake sequence runs while the owner is asleep; a step that hangs is
#: worse than a step that fails, because the NEXT step is the fallback that actually rings.
TIMEOUT_DISARM: Final = 10.0
TIMEOUT_DISPLAY: Final = 15.0
TIMEOUT_SESSION_OPEN: Final = 60.0
TIMEOUT_MEDIA: Final = 45.0
TIMEOUT_ALARM_START: Final = 15.0
TIMEOUT_PLAY_AUDIO: Final = 40.0

#: Spec §3.5 step 3: how long ``browser.media_play`` watches ``currentTime`` advance.
MEDIA_VERIFY_SECONDS: Final = 3
#: Spec §3.5 step 3: the greeting waits for the ramp plus a breath.
GREETING_DELAY_AFTER_RAMP_S: Final = 2

DEFAULT_VOLUME_POLICY: Final[dict[str, Any]] = {"start": 0.15, "end": 0.6, "ramp_seconds": 20}
DEFAULT_GREETING_POLICY: Final[dict[str, Any]] = {
    "enabled": True,
    "text": None,
    "duck_level": 0.15,
}
DEFAULT_DISPLAY_WAKE_POLICY: Final[dict[str, Any]] = {"enabled": True}

#: The dedicated persistent browser profile the alarm's media lives in (spec §4): never the
#: research profile, never the owner's own Chrome.
ALARM_BROWSER_PROFILE: Final = "alarm"
ALARM_BROWSER_SESSION_KIND: Final = "media"


def media_session_id(alarm_id: uuid.UUID | str) -> str:
    return f"alarm-{alarm_id}"


# ------------------------------------------------------------------------- step results


@dataclass(slots=True)
class StepOutcome:
    """One physical step: what was asked, what came back, and the receipt written for it."""

    capability: str
    receipt_capability: str
    ok: bool
    receipt: ActionReceipt
    result: dict[str, Any] = field(default_factory=dict)
    error_class: str | None = None
    reason: str = ""


@dataclass(slots=True)
class FireResult:
    """The outcome of one whole ``dispatch`` (spec §3.5 steps 1-3)."""

    state: str
    media_kind: str | None
    steps: list[StepOutcome] = field(default_factory=list)
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state != STATE_FAILED

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "media_kind": self.media_kind,
            "reason": self.reason,
            "steps": [
                {
                    "capability": s.capability,
                    "receipt_capability": s.receipt_capability,
                    "ok": s.ok,
                    "error_class": s.error_class,
                    "reason": s.reason,
                }
                for s in self.steps
            ],
        }


def _terminal_for(result: DeviceRunResult, *, observed_ok: bool | None = None) -> str:
    """Read-back first (docs/M18_ACTION_CONTRACT.md §1).

    ``observed_ok`` is what the DEVICE reported about the resulting state, when the command
    returns one. A command that succeeded at the transport but whose read-back does not
    confirm the state is ``unverified``, never ``verified`` — the same rule the eye already
    follows, applied to a display and a speaker.
    """
    if not result.ok:
        return TERMINAL_FAILED
    if observed_ok is None:
        return TERMINAL_UNVERIFIED
    return TERMINAL_VERIFIED if observed_ok else TERMINAL_UNVERIFIED


def _error_class_for(result: DeviceRunResult) -> str | None:
    if result.ok:
        return None
    return result.error_class or ERROR_CAPABILITY_MISSING


class WakeSequence:
    """Runs the physical steps of a wake alarm and writes a receipt for each.

    ``device_action`` is the ONE way this class touches the world. ``tts`` is only reached
    for the greeting, and only when the alarm's greeting policy asks for one; a caller with
    no TTS provider configured gets a recorded greeting failure and an alarm that keeps
    playing, never an exception that stops the ring.
    """

    def __init__(
        self,
        *,
        device_action: DeviceActionPort,
        tts: TTSProviderLike | None = None,
        audio_store: AudioStore | None = None,
        broker_audio_origin: str = "",
    ) -> None:
        self._device = device_action
        self._tts = tts
        self._audio_store = audio_store or get_audio_store()
        self._broker_audio_origin = broker_audio_origin.rstrip("/")
        self._greeting_cache: dict[str, GreetingAudio] = {}

    # ------------------------------------------------------------------ receipts

    def _run_step(
        self,
        db: Session,
        alarm: WakeAlarm,
        *,
        capability: str,
        payload: dict[str, Any],
        requested_state: str,
        idempotency_key: str,
        timeout_s: float,
        speech: str = "",
        observed_key: str | None = None,
        observed_expect: Any = True,
        now: datetime | None = None,
    ) -> StepOutcome:
        """Dispatch ONE device command and write its receipt.

        ``observed_key``/``observed_expect`` name the field in the device's own result that
        constitutes the read-back (``{"display_wake_requested": true}``, ``{"armed": true}``,
        ...). When the device returns no such field the receipt is ``unverified`` rather
        than ``verified``: this system does not call a physical change verified because a
        transport call returned 200.
        """
        started = now or datetime.now(UTC)
        receipt_capability = RECEIPT_BY_DEVICE_CALL[capability]
        result = self._device.run(
            capability=capability,
            payload=payload,
            idempotency_key=idempotency_key,
            timeout_s=timeout_s,
        )
        observed_ok: bool | None = None
        if observed_key is not None and result.ok:
            observed_ok = result.result.get(observed_key) == observed_expect
        terminal = _terminal_for(result, observed_ok=observed_ok)
        error_class = _error_class_for(result)
        refused = str(result.result.get("refused") or "") if result.ok else ""
        if refused:
            # The device did the honest thing and said no (spec §5.1): a SUCCESSFUL command
            # carrying a refusal is `refused`, not `failed` — the difference is what the
            # owner is told and whether anything retries.
            execution = EXECUTION_REFUSED
            terminal = TERMINAL_FAILED
            error_class = refused
        elif not result.ok:
            execution = EXECUTION_FAILED
        elif terminal == TERMINAL_ALREADY:
            execution = EXECUTION_NOOP
        else:
            execution = EXECUTION_EXECUTED

        receipt = ActionReceipt(
            action_id=idempotency_key,
            capability=receipt_capability,
            requested_state=requested_state,
            execution_status=execution,
            terminal_status=terminal,
            observed_after={
                "server": {
                    "alarm_id": str(alarm.id),
                    "alarm_state": alarm.state,
                    "device_capability": capability,
                },
                # Everything below came FROM the device: its own read-back, verbatim.
                "local": dict(result.result),
            },
            evidence_refs=[
                {"kind": "wake_alarm", "ref": str(alarm.id)},
                {"kind": "device_capability", "ref": capability},
            ],
            error_class=error_class,
            speech=speech,
            started_at=started,
            completed_at=datetime.now(UTC),
            observed_at=datetime.now(UTC),
        )
        record_receipt(
            db, receipt, _SUBSYSTEM_BY_RECEIPT.get(receipt_capability, SUBSYSTEM_ROUTINE)
        )
        return StepOutcome(
            capability=capability,
            receipt_capability=receipt_capability,
            ok=result.ok and not refused and terminal != TERMINAL_FAILED,
            receipt=receipt,
            result=dict(result.result),
            error_class=error_class,
            reason=refused or (result.message if not result.ok else ""),
        )

    # ------------------------------------------------------------------ arming

    def arm(self, db: Session, alarm: WakeAlarm, *, now: datetime | None = None) -> StepOutcome:
        """``desktop.alarm_arm`` (spec §3.4): the device rings this alarm on its own if the
        cloud never reaches it. ARMED only when the device acknowledged."""
        volume = _volume_policy(alarm)
        return self._run_step(
            db,
            alarm,
            capability=CAPABILITY_DESKTOP_ALARM_ARM,
            payload={
                "alarm_id": str(alarm.id),
                "fire_at": _aware(alarm.scheduled_for)
                .astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "grace_s": 45,
                "fallback": {
                    "label": alarm.label or "",
                    "wake_volume": volume,
                    "max_duration_s": alarm.max_play_seconds,
                },
            },
            requested_state="armed",
            idempotency_key=f"alarm-arm:{alarm.id}:{int(_aware(alarm.scheduled_for).timestamp())}",
            timeout_s=TIMEOUT_DISARM,
            observed_key="armed",
            now=now,
        )

    def disarm(
        self, db: Session, alarm: WakeAlarm, *, reason: str, now: datetime | None = None
    ) -> StepOutcome:
        """``desktop.alarm_disarm``. Idempotent by contract; a failure is recorded and never
        stops anything — an unreachable device that still rings its own fallback is the
        behaviour this milestone wants, not a fault to abort on."""
        return self._run_step(
            db,
            alarm,
            capability=CAPABILITY_DESKTOP_ALARM_DISARM,
            payload={"alarm_id": str(alarm.id), "reason": reason},
            requested_state="disarmed",
            idempotency_key=f"alarm-disarm:{alarm.id}:{reason}",
            timeout_s=TIMEOUT_DISARM,
            observed_key="disarmed",
            now=now,
        )

    # ------------------------------------------------------------------ the fire path

    def fire(
        self,
        db: Session,
        alarm: WakeAlarm,
        *,
        firing_id: uuid.UUID | None = None,
        now: datetime | None = None,
        transition: Any = None,
    ) -> FireResult:
        """Steps 1-3 of spec §3.5. ``transition`` is the caller's state writer
        (``app.alarms.service.transition``), passed in so this module never owns the
        aggregate's lifecycle bookkeeping — it owns the physical steps."""
        moment = now or datetime.now(UTC)
        steps: list[StepOutcome] = []

        def _to(state: str, **detail: Any) -> None:
            if transition is not None:
                transition(db, alarm, state, now=moment, **detail)

        _to(STATE_FIRING, firing_id=str(firing_id) if firing_id else None)

        # 1. Disarm the device's own fallback FIRST (spec §3.5 step 1).
        steps.append(self.disarm(db, alarm, reason="cloud_firing", now=moment))

        # 2. Wake the display — best effort, never a gate on the audio (spec §1.5).
        if _display_wake_policy(alarm).get("enabled", True):
            _to(STATE_DISPLAY_WAKING)
            steps.append(
                self._run_step(
                    db,
                    alarm,
                    capability=CAPABILITY_DESKTOP_DISPLAY_WAKE,
                    payload={"reason": "wake_alarm"},
                    requested_state="on",
                    idempotency_key=f"alarm-display-wake:{alarm.id}:{firing_id or 'once'}",
                    timeout_s=TIMEOUT_DISPLAY,
                    speech="",
                    observed_key="display_wake_requested",
                    now=moment,
                )
            )

        # 3. Audio. The owner's media first, the device's own tone as the fallback.
        _to(STATE_MEDIA_STARTING)
        media_kind: str | None = None
        media_steps, media_ok = self._start_media(db, alarm, firing_id=firing_id, now=moment)
        steps.extend(media_steps)
        if media_ok:
            media_kind = PLAYED_KIND_YOUTUBE
        else:
            tone_step = self._start_tone(db, alarm, firing_id=firing_id, now=moment)
            steps.append(tone_step)
            if tone_step.ok:
                media_kind = PLAYED_KIND_TONE_FALLBACK

        if media_kind is None:
            # Both audio paths failed. This is the one outcome the owner must hear about
            # as an error rather than as silence (spec §3.5 step 3's last line).
            reason = "; ".join(s.reason or s.error_class or "" for s in steps if not s.ok)
            _to(STATE_FAILED, reason=reason[:200] or "no_audio_path")
            return FireResult(state=STATE_FAILED, media_kind=None, steps=steps, reason=reason)

        ramp_seconds = int(_volume_policy(alarm).get("ramp_seconds", 20))
        alarm.media_kind = media_kind
        alarm.playing_since = moment
        alarm.greeting_due_at = moment + timedelta(
            seconds=ramp_seconds + GREETING_DELAY_AFTER_RAMP_S
        )
        _to(STATE_PLAYING, media_kind=media_kind)
        return FireResult(state=STATE_PLAYING, media_kind=media_kind, steps=steps)

    def _start_media(
        self,
        db: Session,
        alarm: WakeAlarm,
        *,
        firing_id: uuid.UUID | None,
        now: datetime,
    ) -> tuple[list[StepOutcome], bool]:
        """The browser-worker path (spec §3.5 step 3, §4).

        Returns ``([], False)`` with NO receipt when the alarm never asked for media — an
        alarm the owner set with the tone has no media step to fail, and inventing a failed
        receipt for a thing nobody asked for would be its own kind of lie.
        """
        media = alarm.resolved_media_identity or {}
        url = media.get("url") if isinstance(media, dict) else None
        if media.get("kind") != PLAYED_KIND_YOUTUBE or not isinstance(url, str) or not url:
            return [], False

        steps: list[StepOutcome] = []
        session_id = media_session_id(alarm.id)
        volume = _volume_policy(alarm)
        open_step = self._run_step(
            db,
            alarm,
            capability=CAPABILITY_BROWSER_SESSION_OPEN,
            payload={
                "session_id": session_id,
                "profile": ALARM_BROWSER_PROFILE,
                "session_kind": ALARM_BROWSER_SESSION_KIND,
                "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True},
                "channel": "chrome",
            },
            requested_state="open",
            idempotency_key=f"alarm-media-open:{alarm.id}:{firing_id or 'once'}",
            timeout_s=TIMEOUT_SESSION_OPEN,
            now=now,
        )
        steps.append(open_step)
        if not open_step.ok:
            return steps, False
        alarm.media_session_id = session_id

        play_step = self._run_step(
            db,
            alarm,
            capability=CAPABILITY_BROWSER_MEDIA_PLAY,
            payload={
                "session_id": session_id,
                "url": url,
                "volume": float(volume["start"]),
                "verify_seconds": MEDIA_VERIFY_SECONDS,
            },
            requested_state="playing",
            idempotency_key=f"alarm-media-play:{alarm.id}:{firing_id or 'once'}",
            timeout_s=TIMEOUT_MEDIA,
            observed_key="verified",
            now=now,
        )
        steps.append(play_step)
        # `verified` is the browser's own read-back that currentTime actually advanced —
        # NOT that play() was called. An unverified play is a failed play here, and the
        # tone takes over: a silent tab is indistinguishable from a broken alarm to a
        # sleeping owner.
        if not play_step.ok or play_step.result.get("verified") is not True:
            return steps, False

        steps.append(
            self._run_step(
                db,
                alarm,
                capability=CAPABILITY_BROWSER_MEDIA_VOLUME,
                payload={
                    "session_id": session_id,
                    "level": float(volume["end"]),
                    "ramp_seconds": int(volume["ramp_seconds"]),
                },
                requested_state="ramping",
                idempotency_key=f"alarm-media-ramp:{alarm.id}:{firing_id or 'once'}",
                timeout_s=TIMEOUT_MEDIA,
                observed_key="applied",
                now=now,
            )
        )
        # The ramp is a refinement of a ring that is already audible: if it fails, the media
        # is still playing at the start volume, which is a quieter alarm rather than none.
        return steps, True

    def _start_tone(
        self,
        db: Session,
        alarm: WakeAlarm,
        *,
        firing_id: uuid.UUID | None,
        now: datetime,
    ) -> StepOutcome:
        """``desktop.alarm_start`` — the device's own ramping tone (spec §3.5 step 3)."""
        volume = _volume_policy(alarm)
        payload: dict[str, Any] = {
            "alarm_id": str(alarm.id),
            "wake_volume": volume,
            "max_duration_s": alarm.max_play_seconds,
        }
        if alarm.label:
            payload["label"] = alarm.label
        return self._run_step(
            db,
            alarm,
            capability=CAPABILITY_DESKTOP_ALARM_START,
            payload=payload,
            requested_state="ringing",
            idempotency_key=f"alarm-tone:{alarm.id}:{firing_id or 'once'}",
            timeout_s=TIMEOUT_ALARM_START,
            observed_key="started",
            now=now,
        )

    # ------------------------------------------------------------------ the greeting

    def speak_greeting(
        self,
        db: Session,
        alarm: WakeAlarm,
        *,
        local_now: datetime,
        now: datetime | None = None,
        transition: Any = None,
    ) -> list[StepOutcome]:
        """Step 4 of spec §3.5, on a LATER tick: duck, speak, restore.

        ``local_now`` is the moment in the OWNER's timezone — the greeting says a clock
        time, and this module never guesses a zone (``app.alarms.speech``'s own rule).
        """
        moment = now or datetime.now(UTC)
        policy = _greeting_policy(alarm)
        steps: list[StepOutcome] = []
        if transition is not None:
            transition(db, alarm, STATE_GREETING, now=moment)

        text = policy.get("text") or alarm_speech.greeting_text(local_now, is_test=alarm.is_test)
        if policy.get("text"):
            text = normalize_greeting(str(text), db)

        audio: GreetingAudio | None = None
        failure = ""
        if self._tts is None:
            failure = "no_tts_provider"
        else:
            try:
                audio = synthesize_greeting(text, provider=self._tts, cache=self._greeting_cache)
            except GreetingSynthesisFailed as exc:
                failure = f"synthesis_failed:{exc}"

        handle = None
        if audio is not None:
            try:
                handle = self._audio_store.put(audio.audio, now=moment)
            except AudioTooLarge as exc:
                failure = f"audio_too_large:{exc}"

        ducked = self._duck(db, alarm, policy, firing_key="greeting", now=moment)
        if ducked is not None:
            steps.append(ducked)

        if handle is None:
            logger.warning(
                "alarm_greeting_not_spoken", alarm_id=str(alarm.id), reason=failure or "unknown"
            )
            alarm.detail_json = {
                **(alarm.detail_json or {}),
                "greeting_failure": failure or "unknown",
            }
        else:
            steps.append(
                self._run_step(
                    db,
                    alarm,
                    capability=CAPABILITY_DESKTOP_PLAY_AUDIO,
                    payload={
                        "audio_id": f"greeting-{alarm.id}",
                        "audio": {
                            "url": f"{self._broker_audio_origin}{handle.path()}",
                            "sha256": handle.sha256,
                            "bytes": handle.size_bytes,
                            "format": "wav",
                        },
                        "level": GREETING_LEVEL,
                        "max_seconds": GREETING_MAX_SECONDS,
                    },
                    requested_state="spoken",
                    idempotency_key=f"alarm-greeting:{alarm.id}:{alarm.snooze_count}",
                    timeout_s=TIMEOUT_PLAY_AUDIO,
                    observed_key="played",
                    now=moment,
                )
            )
            alarm.greeted_at = moment

        restored = self._restore_volume(db, alarm, firing_key="greeting", now=moment)
        if restored is not None:
            steps.append(restored)
        # The greeting is no longer DUE, whether or not it was spoken. Only ``greeted_at``
        # says it happened; clearing the due time is what stops a failed synthesis being
        # retried on every tick for the rest of the alarm — which would both spam the
        # provider and keep the alarm from ever completing.
        alarm.greeting_due_at = None
        if transition is not None:
            transition(db, alarm, STATE_PLAYING, now=moment, after="greeting")
        return steps

    def _duck(
        self,
        db: Session,
        alarm: WakeAlarm,
        policy: dict[str, Any],
        *,
        firing_key: str,
        now: datetime,
    ) -> StepOutcome | None:
        """Lower the music under the greeting. Only possible on the media path — the tone
        has no per-stream volume the cloud can address, so the greeting simply plays over
        it at its own level (spec §3.5 step 4's parenthesis)."""
        if alarm.media_kind != PLAYED_KIND_YOUTUBE or not alarm.media_session_id:
            return None
        return self._run_step(
            db,
            alarm,
            capability=CAPABILITY_BROWSER_MEDIA_VOLUME,
            payload={
                "session_id": alarm.media_session_id,
                "level": float(policy.get("duck_level", 0.15)),
                "ramp_seconds": 1,
            },
            requested_state="ducked",
            idempotency_key=f"alarm-duck:{alarm.id}:{firing_key}",
            timeout_s=TIMEOUT_MEDIA,
            observed_key="applied",
            now=now,
        )

    def _restore_volume(
        self, db: Session, alarm: WakeAlarm, *, firing_key: str, now: datetime
    ) -> StepOutcome | None:
        if alarm.media_kind != PLAYED_KIND_YOUTUBE or not alarm.media_session_id:
            return None
        return self._run_step(
            db,
            alarm,
            capability=CAPABILITY_BROWSER_MEDIA_VOLUME,
            payload={
                "session_id": alarm.media_session_id,
                "level": float(_volume_policy(alarm)["end"]),
                "ramp_seconds": 1,
            },
            requested_state="restored",
            idempotency_key=f"alarm-restore:{alarm.id}:{firing_key}",
            timeout_s=TIMEOUT_MEDIA,
            observed_key="applied",
            now=now,
        )

    # ------------------------------------------------------------------ stopping

    def stop_playback(
        self, db: Session, alarm: WakeAlarm, *, reason: str, now: datetime | None = None
    ) -> list[StepOutcome]:
        """Stop whatever is actually making noise, and only that.

        Both paths are attempted when the alarm's own record is ambiguous (it can be, after
        a restart mid-ring), because a stop that leaves the owner's alarm ringing is the
        worst outcome here and a redundant stop of something already silent is free.
        """
        moment = now or datetime.now(UTC)
        steps: list[StepOutcome] = []
        if alarm.media_session_id:
            steps.append(
                self._run_step(
                    db,
                    alarm,
                    capability=CAPABILITY_BROWSER_MEDIA_STOP,
                    payload={"session_id": alarm.media_session_id},
                    requested_state="stopped",
                    idempotency_key=f"alarm-media-stop:{alarm.id}:{reason}",
                    timeout_s=TIMEOUT_MEDIA,
                    observed_key="stopped",
                    now=moment,
                )
            )
        if alarm.media_kind != PLAYED_KIND_YOUTUBE or alarm.media_session_id is None:
            steps.append(
                self._run_step(
                    db,
                    alarm,
                    capability=CAPABILITY_DESKTOP_ALARM_STOP,
                    payload={"alarm_id": str(alarm.id)},
                    requested_state="stopped",
                    idempotency_key=f"alarm-tone-stop:{alarm.id}:{reason}",
                    timeout_s=TIMEOUT_ALARM_START,
                    observed_key="stopped",
                    now=moment,
                )
            )
        alarm.media_session_id = None
        return steps

    def media_status(
        self, db: Session, alarm: WakeAlarm, *, now: datetime | None = None
    ) -> StepOutcome | None:
        """``browser.media_status`` — used by the tick to notice a track that ENDED on its
        own, so an alarm completes because the music finished rather than only on a timer."""
        if alarm.media_kind != PLAYED_KIND_YOUTUBE or not alarm.media_session_id:
            return None
        moment = now or datetime.now(UTC)
        return self._run_step(
            db,
            alarm,
            capability=CAPABILITY_BROWSER_MEDIA_STATUS,
            payload={"session_id": alarm.media_session_id},
            requested_state="observed",
            idempotency_key=f"alarm-media-status:{alarm.id}:{int(moment.timestamp())}",
            timeout_s=TIMEOUT_MEDIA,
            observed_key="present",
            now=moment,
        )

    # ------------------------------------------------------------------ display power

    def display_off(
        self,
        db: Session,
        *,
        reason: str,
        alarm: WakeAlarm | None = None,
        holdoff_s: int = 120,
        action_id: str | None = None,
        now: datetime | None = None,
    ) -> StepOutcome:
        """``desktop.display_off`` with the receipt of spec §6.

        The one machine-state capability in this milestone, and the only one. Nothing here
        (or anywhere in this package) locks, sleeps, hibernates, logs off or shuts down —
        ``tests/unit/test_alarms_structure.py`` reads this source and asserts none of those
        API names appears.
        """
        moment = now or datetime.now(UTC)
        placeholder = alarm or _display_only_alarm()
        key = action_id or f"display-off:{reason}:{int(moment.timestamp())}"
        step = self._run_step(
            db,
            placeholder,
            capability=CAPABILITY_DESKTOP_DISPLAY_OFF,
            payload={"reason": reason, "holdoff_s": holdoff_s},
            requested_state="off",
            idempotency_key=key,
            timeout_s=TIMEOUT_DISPLAY,
            observed_key="display_off",
            now=moment,
        )
        return step

    def display_wake(
        self,
        db: Session,
        *,
        reason: str,
        alarm: WakeAlarm | None = None,
        action_id: str | None = None,
        now: datetime | None = None,
    ) -> StepOutcome:
        moment = now or datetime.now(UTC)
        placeholder = alarm or _display_only_alarm()
        key = action_id or f"display-wake:{reason}:{int(moment.timestamp())}"
        return self._run_step(
            db,
            placeholder,
            capability=CAPABILITY_DESKTOP_DISPLAY_WAKE,
            payload={"reason": reason},
            requested_state="on",
            idempotency_key=key,
            timeout_s=TIMEOUT_DISPLAY,
            observed_key="display_wake_requested",
            now=moment,
        )


# ------------------------------------------------------------------------- helpers


def _aware(dt: datetime) -> datetime:
    """A stored timestamp, made comparable — see ``app.alarms.service._aware`` for why
    (SQLite returns naive datetimes for a ``DateTime(timezone=True)`` column)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _display_only_alarm() -> WakeAlarm:
    """A detached, never-persisted ``WakeAlarm`` used only to carry identity into a
    display receipt issued by the ambient policy rather than by an alarm.

    A separate receipt-builder for the two callers would be the alternative, and it would
    be two things to keep in step. This object is never added to a session; the receipt
    reads only ``id`` and ``state`` from it.
    """
    row = WakeAlarm()
    row.id = uuid.uuid4()
    row.state = "SCHEDULED"
    return row


def _volume_policy(alarm: WakeAlarm) -> dict[str, Any]:
    policy = dict(DEFAULT_VOLUME_POLICY)
    policy.update({k: v for k, v in (alarm.volume_policy or {}).items() if v is not None})
    return policy


def _greeting_policy(alarm: WakeAlarm) -> dict[str, Any]:
    policy = dict(DEFAULT_GREETING_POLICY)
    policy.update(alarm.greeting_policy or {})
    return policy


def _display_wake_policy(alarm: WakeAlarm) -> dict[str, Any]:
    policy = dict(DEFAULT_DISPLAY_WAKE_POLICY)
    policy.update(alarm.display_wake_policy or {})
    return policy


__all__ = [
    "ALARM_BROWSER_PROFILE",
    "ALARM_BROWSER_SESSION_KIND",
    "DEFAULT_DISPLAY_WAKE_POLICY",
    "DEFAULT_GREETING_POLICY",
    "DEFAULT_VOLUME_POLICY",
    "GREETING_DELAY_AFTER_RAMP_S",
    "MEDIA_VERIFY_SECONDS",
    "RECEIPT_BY_DEVICE_CALL",
    "FireResult",
    "StepOutcome",
    "WakeSequence",
    "media_session_id",
]
