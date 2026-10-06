"""inbound-calls-bridge: the bridge that drives the Twilio leg and the realtime leg together.

Pinned here, each one RED under its own mutation:

* the media socket is authorised by the one-time ``bridge_token`` of the ``start`` frame: a
  missing, wrong, spent or expired token - or one issued for another CallSid - closes it 1008
  and NO realtime leg is ever opened;
* the caller's audio reaches the leg byte-for-byte, the leg's audio reaches Twilio
  byte-for-byte, and the caller starting to speak clears what JARVIS was saying;
* at the call's time limit the leg is told to say goodbye, and the call ends ten seconds later
  (a fake sleep: no wall clock);
* ``stop`` ends the call and finalize runs ONCE; a leg error closes both legs and the
  transcript is still written;
* not one byte of the audio reaches the ledger row, the notification or a log line.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger.models import ActivityEventRow
from app.notifications.models import NotificationRow
from app.telephony import inbound_bridge as br
from app.telephony import inbound_realtime as rt
from app.telephony.inbound_records import InboundRecorder
from app.telephony.inbound_settings import InboundSettings
from app.voice import providers_openai_realtime as oai

#: 14:00 in Istanbul - outside quiet hours.
T0 = datetime(2026, 10, 6, 11, 0, tzinfo=UTC)
CALL_SID = "CA" + "7" * 32
STREAM_SID = "MZ" + "8" * 32
CALLER = "+905321112233"
#: Distinctive base64 "audio" so a leak anywhere is findable by a plain substring search.
CALLER_AUDIO = "Q0FMTEVSQVVESU9GUkFNRQ=="
JARVIS_AUDIO = "SkFSVklTQVVESU9GUkFNRQ=="


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeTwilioSocket:
    """Twilio's side of the media WebSocket: frames in through a queue, frames out to a list."""

    def __init__(self) -> None:
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed: int | None = None
        self.on_send: Callable[[dict[str, Any]], None] | None = None

    def push(self, frame: dict[str, Any] | None) -> None:
        self.inbox.put_nowait(None if frame is None else json.dumps(frame))

    async def receive_text(self) -> str | None:
        if self.closed is not None:
            return None
        return await self.inbox.get()

    async def send_text(self, text: str) -> None:
        frame = json.loads(text)
        self.sent.append(frame)
        if self.on_send is not None:
            self.on_send(frame)

    async def close(self, code: int = 1000) -> None:
        if self.closed is None:
            self.closed = code


class FakeRealtimeLeg:
    """The OpenAI leg as the bridge sees it. ``script`` maps an audio payload to the events the
    'model' answers it with."""

    def __init__(self, script: Callable[[str], list[rt.LegEvent]] | None = None) -> None:
        self.connected: tuple[str, str] | None = None
        self.audio: list[str] = []
        self.said: list[str] = []
        self.closed = False
        self._queue: asyncio.Queue[rt.LegEvent | None] = asyncio.Queue()
        self._script = script

    def emit(self, event: rt.LegEvent) -> None:
        self._queue.put_nowait(event)

    async def connect(self, instructions: str, audio_format: str) -> None:
        self.connected = (instructions, audio_format)

    async def send_audio(self, payload_b64: str) -> None:
        self.audio.append(payload_b64)
        if self._script is not None:
            for event in self._script(payload_b64):
                self.emit(event)

    async def say(self, instructions: str) -> None:
        self.said.append(instructions)

    async def events(self) -> AsyncIterator[rt.LegEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        self.closed = True
        self._queue.put_nowait(None)


def conversation(payload: str) -> list[rt.LegEvent]:
    return [
        rt.LegEvent(rt.LEG_SPEECH_STARTED),
        rt.LegEvent(rt.LEG_TRANSCRIPT_IN, "Ben Ayşe Demir, toplantı yarına kaldı."),
        rt.LegEvent(rt.LEG_AUDIO_DELTA, JARVIS_AUDIO),
        rt.LegEvent(rt.LEG_TRANSCRIPT_OUT, "Anladım, sahibe ileteceğim."),
        rt.LegEvent(rt.LEG_RESPONSE_DONE),
    ]


def make_scope() -> tuple[Any, Callable[[], contextlib.AbstractContextManager[Session]]]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(engine)
    NotificationRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        with factory() as db:
            yield db

    return engine, scope


def ledger_rows(scope: Any) -> list[ActivityEventRow]:
    with scope() as db:
        return list(db.execute(select(ActivityEventRow)).scalars().all())


def notification_rows(scope: Any) -> list[NotificationRow]:
    with scope() as db:
        return list(db.execute(select(NotificationRow)).scalars().all())


def start_frame(token: str | None, call_sid: str = CALL_SID) -> dict[str, Any]:
    params = {} if token is None else {"bridge_token": token}
    return {
        "event": "start",
        "sequenceNumber": "1",
        "streamSid": STREAM_SID,
        "start": {
            "accountSid": "AC1",
            "streamSid": STREAM_SID,
            "callSid": call_sid,
            "tracks": ["inbound"],
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            "customParameters": params,
        },
    }


def media(payload: str) -> dict[str, Any]:
    return {"event": "media", "streamSid": STREAM_SID, "media": {"payload": payload}}


STOP = {"event": "stop", "streamSid": STREAM_SID, "stop": {"callSid": CALL_SID}}


class Harness:
    def __init__(self, *, sleep: Any = None, script: Any = conversation) -> None:
        self.clock = Clock(T0)
        self.settings = InboundSettings(enabled=True)
        self.tokens = br.BridgeTokenStore(ttl_s=self.settings.bridge_token_ttl_s, clock=self.clock)
        self.socket = FakeTwilioSocket()
        self.legs: list[FakeRealtimeLeg] = []
        self.records: list[br.CallRecord] = []
        self._script = script
        self.sleeps: list[float] = []
        self._sleep = sleep

    def leg_factory(self) -> FakeRealtimeLeg:
        leg = FakeRealtimeLeg(self._script)
        self.legs.append(leg)
        return leg

    async def never(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        await asyncio.Event().wait()

    def bridge(
        self, finalize: Callable[[br.CallRecord], Any] | None = None
    ) -> br.InboundCallBridge:
        return br.InboundCallBridge(
            twilio=self.socket,
            leg_factory=self.leg_factory,
            tokens=self.tokens,
            settings=self.settings,
            finalize=finalize or self.records.append,
            clock=self.clock,
            sleep=self._sleep or self.never,
        )


def run(coro: Any) -> Any:
    return asyncio.run(asyncio.wait_for(coro, timeout=10))


# ------------------------------------------------------------------ the bridge token


@pytest.mark.parametrize("case", ["missing", "wrong", "spent", "expired", "other-call", "no-start"])
def test_a_bad_bridge_token_closes_1008_and_opens_no_leg(case: str) -> None:
    async def scenario() -> Harness:
        h = Harness()
        token = h.tokens.issue(CALL_SID, CALLER, max_seconds=300)
        h.socket.push({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        if case == "missing":
            h.socket.push(start_frame(None))
        elif case == "wrong":
            h.socket.push(start_frame(token[:-2] + "zz"))
        elif case == "spent":
            assert h.tokens.redeem(token, CALL_SID) is not None
            h.socket.push(start_frame(token))
        elif case == "expired":
            h.clock.now = T0 + timedelta(seconds=121)
            h.socket.push(start_frame(token))
        elif case == "other-call":
            h.socket.push(start_frame(token, call_sid="CA" + "0" * 32))
        else:
            h.socket.push(media(CALLER_AUDIO))
        result = await h.bridge().run()
        assert result is None
        return h

    h = run(scenario())
    assert h.socket.closed == 1008
    assert h.legs == []
    assert h.records == []
    assert h.socket.sent == []


def test_a_token_is_good_once_and_for_two_minutes() -> None:
    clock = Clock(T0)
    store = br.BridgeTokenStore(ttl_s=120, clock=clock)
    token = store.issue(CALL_SID, CALLER, max_seconds=300)
    assert len(token) >= 40
    assert store.pending() == 1
    clock.now = T0 + timedelta(seconds=119)
    grant = store.redeem(token, CALL_SID)
    assert grant is not None and (grant.call_sid, grant.caller) == (CALL_SID, CALLER)
    assert store.redeem(token, CALL_SID) is None
    assert store.pending() == 0
    late = store.issue(CALL_SID, CALLER, max_seconds=300)
    clock.now = T0 + timedelta(seconds=119 + 121)
    assert store.pending() == 0
    assert store.redeem(late, CALL_SID) is None


# ------------------------------------------------------------------ the call


def test_audio_passes_through_both_ways_and_speech_clears_jarvis() -> None:
    async def scenario() -> Harness:
        h = Harness()
        token = h.tokens.issue(CALL_SID, CALLER, max_seconds=300)
        done = asyncio.Event()

        def on_send(frame: dict[str, Any]) -> None:
            if frame["event"] == "mark":
                done.set()

        h.socket.on_send = on_send
        h.socket.push({"event": "connected"})
        h.socket.push(start_frame(token))
        h.socket.push(media(CALLER_AUDIO))
        task = asyncio.create_task(h.bridge().run())
        await done.wait()
        h.socket.push(STOP)
        await task
        return h

    h = run(scenario())
    (leg,) = h.legs
    assert leg.connected == (rt.SECRETARY_INSTRUCTIONS_TR, oai.AUDIO_FORMAT_G711_ULAW)
    assert leg.audio == [CALLER_AUDIO]
    kinds = [f["event"] for f in h.socket.sent]
    assert kinds == ["clear", "media", "mark"]
    assert h.socket.sent[0] == {"event": "clear", "streamSid": STREAM_SID}
    assert h.socket.sent[1] == {
        "event": "media",
        "streamSid": STREAM_SID,
        "media": {"payload": JARVIS_AUDIO},
    }
    assert leg.closed and h.socket.closed == 1000
    (record,) = h.records
    assert record.call_sid == CALL_SID and record.caller == CALLER
    assert [(line.speaker, line.text) for line in record.transcript] == [
        ("Arayan", "Ben Ayşe Demir, toplantı yarına kaldı."),
        ("JARVIS", "Anladım, sahibe ileteceğim."),
    ]
    assert (record.frames_in, record.frames_out) == (1, 1)
    assert record.ended_by == "stop"


def test_time_limit_says_goodbye_then_hangs_up_ten_seconds_later() -> None:
    async def scenario() -> Harness:
        h = Harness(script=None)

        async def instant(seconds: float) -> None:
            h.sleeps.append(seconds)
            h.clock.now += timedelta(seconds=seconds)

        h._sleep = instant
        token = h.tokens.issue(CALL_SID, CALLER, max_seconds=300)
        h.socket.push(start_frame(token))
        await h.bridge().run()
        return h

    h = run(scenario())
    (leg,) = h.legs
    assert h.sleeps == [300, 10]
    assert leg.said == [rt.FAREWELL_INSTRUCTIONS_TR]
    assert leg.closed and h.socket.closed == 1000
    (record,) = h.records
    assert record.ended_by == "time_limit"
    assert record.ended - record.started == timedelta(seconds=310)


def test_the_grant_shortens_the_time_limit() -> None:
    async def scenario() -> Harness:
        h = Harness(script=None)

        async def instant(seconds: float) -> None:
            h.sleeps.append(seconds)

        h._sleep = instant
        token = h.tokens.issue(CALL_SID, CALLER, max_seconds=90)
        h.socket.push(start_frame(token))
        await h.bridge().run()
        return h

    assert run(scenario()).sleeps == [90, 10]


def test_a_leg_error_closes_both_legs_and_still_writes_the_transcript() -> None:
    async def scenario() -> Harness:
        h = Harness(script=None)
        token = h.tokens.issue(CALL_SID, CALLER, max_seconds=300)
        h.socket.push(start_frame(token))
        task = asyncio.create_task(h.bridge().run())
        while not h.legs or h.legs[0].connected is None:
            await asyncio.sleep(0)
        h.legs[0].emit(rt.LegEvent(rt.LEG_TRANSCRIPT_IN, "Ben Mehmet."))
        h.legs[0].emit(rt.LegEvent(rt.LEG_ERROR, "server_error"))
        await task
        return h

    h = run(scenario())
    assert h.legs[0].closed
    assert h.socket.closed == 1000
    (record,) = h.records
    assert record.ended_by == "error"
    assert [line.text for line in record.transcript] == ["Ben Mehmet."]


def test_a_leg_that_cannot_connect_still_ends_and_writes() -> None:
    class Broken(FakeRealtimeLeg):
        async def connect(self, instructions: str, audio_format: str) -> None:
            raise OSError("no route")

    async def scenario() -> Harness:
        h = Harness()
        h.leg_factory = lambda: h.legs.append(Broken()) or h.legs[-1]  # type: ignore[method-assign]
        token = h.tokens.issue(CALL_SID, CALLER, max_seconds=300)
        h.socket.push(start_frame(token))
        await h.bridge().run()
        return h

    h = run(scenario())
    assert h.legs[0].closed and h.socket.closed == 1000
    assert [r.ended_by for r in h.records] == ["error"]


def test_the_caller_hanging_up_ends_the_call() -> None:
    async def scenario() -> Harness:
        h = Harness(script=None)
        token = h.tokens.issue(CALL_SID, CALLER, max_seconds=300)
        h.socket.push(start_frame(token))
        h.socket.push(None)
        await h.bridge().run()
        return h

    h = run(scenario())
    assert [r.ended_by for r in h.records] == ["hangup"]
    assert h.legs[0].closed


# ------------------------------------------------------------------ nothing of the audio is kept


def test_no_audio_byte_reaches_the_ledger_the_notification_or_a_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine, scope = make_scope()
    recorder = InboundRecorder(scope)
    caplog.set_level(logging.DEBUG)

    async def scenario() -> Harness:
        h = Harness()
        token = h.tokens.issue(CALL_SID, CALLER, max_seconds=300)
        done = asyncio.Event()
        h.socket.on_send = lambda f: done.set() if f["event"] == "mark" else None
        h.socket.push(start_frame(token))
        h.socket.push(media(CALLER_AUDIO))
        task = asyncio.create_task(h.bridge(finalize=recorder.finalize_record).run())
        await done.wait()
        h.socket.push(STOP)
        await task
        return h

    with structlog.testing.capture_logs() as structured:
        run(scenario())
    rows = ledger_rows(scope)
    notes = notification_rows(scope)
    assert len(rows) == 1 and len(notes) == 1
    everything = " ".join(
        [caplog.text, repr(structured)]
        + [repr(r.detail_json) + r.factual_summary for r in rows]
        + [n.title + n.body + repr(n.data_json) for n in notes]
    )
    assert structured, "nothing was logged - the scan would pass vacuously"
    assert "toplantı yarına kaldı" in everything, "the transcript must be there"
    assert CALLER_AUDIO not in everything
    assert JARVIS_AUDIO not in everything
    detail = rows[0].detail_json
    assert detail["frames_in"] == 1 and detail["frames_out"] == 1
    engine.dispose()
