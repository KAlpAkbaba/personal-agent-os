"""The inbound call bridge: Twilio's media socket on one side, the realtime leg on the other.

The media socket is not signed by us; its authority is the ``bridge_token`` the voice webhook
issued (256 bits, two minutes, one use, bound to the CallSid) and Twilio hands back in the
``start`` frame's ``customParameters``. A missing, wrong, spent, expired or other-call token
closes the socket 1008 before any realtime leg is opened.

Then the bridge drives both legs until one ends:

* Twilio ``media`` -> ``leg.send_audio`` (the base64 payload as it came);
* leg ``audio_delta`` -> a Twilio ``media`` frame (as it came), a ``mark`` after each response;
* leg ``speech_started`` -> Twilio ``clear``: the caller talks, JARVIS stops;
* both transcripts -> transcript lines ("Arayan" / "JARVIS", time);
* the time limit (5 minutes, less when the day's allowance is nearly spent) -> the leg says
  goodbye, ten seconds later both legs close;
* ``stop``, a hang-up, a leg error or a leg that cannot connect -> both legs close.

Whatever ended it, the call is finalized once (``InboundRecorder``) before Twilio's socket is
closed. No byte of audio is kept: only counters (frames each way) and seconds.

State is in this process (the API runs one uvicorn worker): the token store, the active calls.
A release swaps the container, so a call in progress at that moment drops (ADR).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from app.logging import get_logger
from app.telephony import inbound_realtime as rt
from app.telephony.inbound_records import (
    SPEAKER_CALLER,
    SPEAKER_JARVIS,
    InboundRecorder,
)
from app.telephony.inbound_settings import InboundSettings
from app.telephony.inbound_twilio import (
    BRIDGE_TOKEN_PARAM,
    InboundTelephonyProvider,
    TwilioInbound,
)
from app.voice.providers_openai_realtime import AUDIO_FORMAT_G711_ULAW

logger = get_logger("app.telephony.inbound_bridge")

CLOSE_POLICY_VIOLATION: Final[int] = 1008
CLOSE_NORMAL: Final[int] = 1000
#: Twilio sends ``connected`` and ``start`` at once; a socket silent this long is not a call.
START_TIMEOUT_S: Final[float] = 15.0
#: How long a CallSid the line answered is remembered for its status callback.
KNOWN_CALL_TTL: Final[timedelta] = timedelta(hours=1)

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[Any]]


class TwilioSocket(Protocol):
    async def receive_text(self) -> str | None:
        """The next text frame, ``None`` once the socket is gone."""

    async def send_text(self, text: str) -> None: ...

    async def close(self, code: int = CLOSE_NORMAL) -> None: ...


@dataclasses.dataclass(frozen=True, slots=True)
class BridgeGrant:
    call_sid: str
    caller: str
    issued_at: datetime
    max_seconds: int


@dataclasses.dataclass(frozen=True, slots=True)
class TranscriptLine:
    speaker: str
    text: str
    at: datetime


@dataclasses.dataclass(frozen=True, slots=True)
class CallRecord:
    call_sid: str
    caller: str
    started: datetime
    ended: datetime
    transcript: tuple[TranscriptLine, ...]
    frames_in: int
    frames_out: int
    #: stop | hangup | time_limit | error | leg_closed
    ended_by: str


class BridgeTokenStore:
    """One-time, short-lived media-socket tokens, in memory."""

    def __init__(self, *, ttl_s: int, clock: Clock) -> None:
        self._ttl = timedelta(seconds=ttl_s)
        self._clock = clock
        self._grants: dict[str, BridgeGrant] = {}

    def _expire(self, now: datetime) -> None:
        for token, grant in list(self._grants.items()):
            if now - grant.issued_at > self._ttl:
                del self._grants[token]

    def issue(self, call_sid: str, caller: str, *, max_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        self._grants[token] = BridgeGrant(call_sid, caller, self._clock(), max_seconds)
        return token

    def redeem(self, token: str | None, call_sid: str) -> BridgeGrant | None:
        now = self._clock()
        self._expire(now)
        if not token:
            return None
        grant = self._grants.get(token)
        if grant is None or not secrets.compare_digest(grant.call_sid, call_sid or ""):
            return None
        del self._grants[token]
        return grant

    def revoke_call(self, call_sid: str) -> None:
        for token, grant in list(self._grants.items()):
            if grant.call_sid == call_sid:
                del self._grants[token]

    def pending(self) -> int:
        self._expire(self._clock())
        return len(self._grants)


class InboundCallBridge:
    def __init__(
        self,
        *,
        twilio: TwilioSocket,
        leg_factory: Callable[[], rt.RealtimeLeg],
        tokens: BridgeTokenStore,
        settings: InboundSettings,
        finalize: Callable[[CallRecord], Any],
        clock: Clock,
        sleep: Sleep = asyncio.sleep,
        provider: InboundTelephonyProvider | None = None,
        on_start: Callable[[BridgeGrant], None] | None = None,
        on_end: Callable[[BridgeGrant], None] | None = None,
    ) -> None:
        self._twilio = twilio
        self._leg_factory = leg_factory
        self._tokens = tokens
        self._settings = settings
        self._finalize = finalize
        self._clock = clock
        self._sleep = sleep
        self._provider: InboundTelephonyProvider = provider or TwilioInbound("")
        self._on_start = on_start
        self._on_end = on_end
        self._stream_sid = ""
        self._transcript: list[TranscriptLine] = []
        self._frames_in = 0
        self._frames_out = 0
        self._responses = 0

    async def _await_start(self) -> tuple[BridgeGrant, str] | None:
        while True:
            raw = await self._twilio.receive_text()
            if raw is None:
                return None
            try:
                event = self._provider.parse_stream_event(raw)
            except ValueError:
                return None
            if event.kind == "connected":
                continue
            if event.kind != "start":
                return None
            token = event.custom_parameters.get(BRIDGE_TOKEN_PARAM)
            grant = self._tokens.redeem(token, event.call_sid)
            return (grant, event.stream_sid) if grant is not None else None

    async def run(self) -> CallRecord | None:
        try:
            async with asyncio.timeout(START_TIMEOUT_S):
                started = await self._await_start()
        except TimeoutError:
            started = None
        if started is None:
            logger.warning("telephony_inbound_media_refused")
            await self._twilio.close(CLOSE_POLICY_VIOLATION)
            return None
        grant, self._stream_sid = started
        if self._on_start is not None:
            self._on_start(grant)
        begun = self._clock()
        ended_by = "error"
        leg: rt.RealtimeLeg | None = None
        try:
            leg = self._leg_factory()
            await leg.connect(rt.SECRETARY_INSTRUCTIONS_TR, AUDIO_FORMAT_G711_ULAW)
            ended_by = await self._drive(leg, grant)
        except Exception as exc:  # noqa: BLE001 - any failure ends the call, the record is still written
            logger.warning("telephony_inbound_bridge_failed", error=type(exc).__name__)
            ended_by = "error"
        finally:
            if leg is not None:
                with contextlib.suppress(Exception):
                    await leg.close()
            record = CallRecord(
                call_sid=grant.call_sid,
                caller=grant.caller,
                started=begun,
                ended=self._clock(),
                transcript=tuple(self._transcript),
                frames_in=self._frames_in,
                frames_out=self._frames_out,
                ended_by=ended_by,
            )
            try:
                await asyncio.to_thread(self._finalize, record)
            except Exception as exc:  # noqa: BLE001 - logged; the sockets still close
                logger.error("telephony_inbound_finalize_failed", error=type(exc).__name__)
            with contextlib.suppress(Exception):
                await self._twilio.close(CLOSE_NORMAL)
            if self._on_end is not None:
                self._on_end(grant)
            logger.info(
                "telephony_inbound_call_ended",
                call_sid=grant.call_sid,
                ended_by=ended_by,
                seconds=int((record.ended - record.started).total_seconds()),
                frames_in=self._frames_in,
                frames_out=self._frames_out,
            )
        return record

    async def _drive(self, leg: rt.RealtimeLeg, grant: BridgeGrant) -> str:
        tasks = [
            asyncio.create_task(self._from_twilio(leg)),
            asyncio.create_task(self._from_leg(leg)),
            asyncio.create_task(self._time_limit(leg, grant.max_seconds)),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        first = next(iter(done))
        if first.cancelled() or first.exception() is not None:
            if not first.cancelled():
                logger.warning(
                    "telephony_inbound_leg_failed", error=type(first.exception()).__name__
                )
            return "error"
        return str(first.result())

    async def _from_twilio(self, leg: rt.RealtimeLeg) -> str:
        while True:
            raw = await self._twilio.receive_text()
            if raw is None:
                return "hangup"
            try:
                event = self._provider.parse_stream_event(raw)
            except ValueError:
                continue
            if event.kind == "media":
                if event.payload:
                    self._frames_in += 1
                    await leg.send_audio(event.payload)
            elif event.kind == "stop":
                return "stop"

    async def _from_leg(self, leg: rt.RealtimeLeg) -> str:
        async for event in leg.events():
            if event.kind == rt.LEG_AUDIO_DELTA:
                self._frames_out += 1
                await self._twilio.send_text(self._provider.media_frame(self._stream_sid, event.data))
            elif event.kind == rt.LEG_SPEECH_STARTED:
                await self._twilio.send_text(self._provider.clear_frame(self._stream_sid))
            elif event.kind == rt.LEG_TRANSCRIPT_IN:
                self._transcript.append(TranscriptLine(SPEAKER_CALLER, event.data, self._clock()))
            elif event.kind == rt.LEG_TRANSCRIPT_OUT:
                self._transcript.append(TranscriptLine(SPEAKER_JARVIS, event.data, self._clock()))
            elif event.kind == rt.LEG_RESPONSE_DONE:
                self._responses += 1
                await self._twilio.send_text(
                    self._provider.mark_frame(self._stream_sid, f"response-{self._responses}")
                )
            elif event.kind == rt.LEG_ERROR:
                logger.warning("telephony_inbound_leg_error", code=event.data[:64])
                return "error"
        return "leg_closed"

    async def _time_limit(self, leg: rt.RealtimeLeg, max_seconds: int) -> str:
        await self._sleep(max_seconds)
        await leg.say(rt.FAREWELL_INSTRUCTIONS_TR)
        await self._sleep(self._settings.farewell_grace_s)
        return "time_limit"


# ---------------------------------------------------------------------- the line


@dataclasses.dataclass(frozen=True, slots=True)
class Admission:
    token: str | None
    #: answer | disabled | not_configured | busy | daily_cap
    reason: str
    max_seconds: int = 0


@dataclasses.dataclass(slots=True)
class _Known:
    caller: str
    issued_at: datetime


class InboundLine:
    """Everything the inbound routes share: the gates, the tokens, the active call."""

    def __init__(
        self,
        *,
        settings: InboundSettings,
        provider: InboundTelephonyProvider,
        recorder: InboundRecorder,
        leg_factory: Callable[[], rt.RealtimeLeg],
        clock: Clock = lambda: datetime.now(UTC),
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.recorder = recorder
        self._leg_factory = leg_factory
        self._clock = clock
        self._sleep = sleep
        self.tokens = BridgeTokenStore(ttl_s=settings.bridge_token_ttl_s, clock=clock)
        self.active: set[str] = set()
        self._known: dict[str, _Known] = {}

    def now(self) -> datetime:
        return self._clock()

    def admit(self, call_sid: str, caller: str, *, used_seconds: int) -> Admission:
        """Answer or refuse a ringing call. Synchronous and in the event loop: the check and the
        token it issues cannot interleave with another webhook's."""
        settings = self.settings
        now = self._clock()
        for sid, known in list(self._known.items()):
            if now - known.issued_at > KNOWN_CALL_TTL:
                del self._known[sid]
        if not settings.enabled:
            return Admission(None, "disabled")
        if not settings.configured or not self.provider.configured():
            return Admission(None, "not_configured")
        if len(self.active) + self.tokens.pending() >= settings.max_concurrent:
            return Admission(None, "busy")
        left = settings.daily_minutes_cap * 60 - used_seconds
        if left < settings.min_call_seconds:
            return Admission(None, "daily_cap")
        max_seconds = min(settings.max_call_seconds, left)
        token = self.tokens.issue(call_sid, caller, max_seconds=max_seconds)
        self._known[call_sid] = _Known(caller, now)
        return Admission(token, "answer", max_seconds)

    def bridge(self, socket: TwilioSocket) -> InboundCallBridge:
        return InboundCallBridge(
            twilio=socket,
            leg_factory=self._leg_factory,
            tokens=self.tokens,
            settings=self.settings,
            finalize=self.recorder.finalize_record,
            clock=self._clock,
            sleep=self._sleep,
            provider=self.provider,
            on_start=lambda grant: self.active.add(grant.call_sid),
            on_end=lambda grant: self.active.discard(grant.call_sid),
        )

    async def status_callback(self, call_sid: str, duration_s: int | None) -> bool:
        """Twilio says the call is over. A call the bridge is still holding is the bridge's to
        finalize; a call this line answered whose stream never started (the caller hung up
        during the notice) is finalized here as a call without a message; a CallSid this line
        never answered (refused, or before a restart) writes nothing. Idempotent.

        The in-memory part runs in the event loop (no interleaving with ``admit``); only the
        database write goes to a thread."""
        if call_sid in self.active:
            return False
        known = self._known.get(call_sid)
        if known is None:
            return False
        self.tokens.revoke_call(call_sid)
        ended = (
            known.issued_at + timedelta(seconds=duration_s)
            if duration_s is not None
            else self._clock()
        )
        return await asyncio.to_thread(
            self.recorder.finalize,
            call_sid,
            known.caller,
            known.issued_at,
            ended,
            [],
            ended_by="status_callback",
        )


__all__ = [
    "Admission",
    "BridgeGrant",
    "BridgeTokenStore",
    "CallRecord",
    "InboundCallBridge",
    "InboundLine",
    "TranscriptLine",
    "TwilioSocket",
]
