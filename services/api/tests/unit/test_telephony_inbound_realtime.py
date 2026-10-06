"""inbound-calls-bridge: the server-side OpenAI Realtime leg, against a fake OpenAI on 127.0.0.1.

The fake server is a real ``websockets`` server. It refuses (closes 1008 and the test goes RED)
unless the FIRST thing the leg sends is a ``session.update`` that:

* asks for ``audio/pcmu`` in AND out - the expected object is read from
  ``providers_openai_realtime._audio_format_object(AUDIO_FORMAT_G711_ULAW)``, never written here,
  so the literal "g711_ulaw" string the GA API rejects cannot turn this green;
* carries ``tools == []`` and ``tool_choice == "none"`` - the caller is not the owner;
* uses ``server_vad`` with ``interrupt_response`` on, and has input transcription switched on;
* carries the fixed secretary instructions.

Then it answers each ``input_audio_buffer.append`` with an audio delta and the transcript events,
all named by the constants of ``providers_openai_realtime`` (and the leg's own constant for the
output transcript, which that file does not have) - the two halves read each other.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from websockets.asyncio.server import serve

from app.telephony import inbound_realtime as rt
from app.voice import providers_openai_realtime as oai

API_KEY = "sk-test-" + "k" * 24
PCMU = oai._audio_format_object(oai.AUDIO_FORMAT_G711_ULAW)


def _session_problems(message: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if message.get("type") != oai.CMD_SESSION_UPDATE:
        return [f"first message was {message.get('type')!r}"]
    session = message.get("session") or {}
    audio = session.get("audio") or {}
    if (audio.get("input") or {}).get("format") != PCMU:
        problems.append("input format")
    if (audio.get("output") or {}).get("format") != PCMU:
        problems.append("output format")
    if "rate" in json.dumps(audio.get("input", {}).get("format", {})):
        problems.append("rate")
    if session.get("tools") != []:
        problems.append("tools")
    if session.get("tool_choice") != "none":
        problems.append("tool_choice")
    turn = (audio.get("input") or {}).get("turn_detection") or {}
    if turn.get("type") != "server_vad" or turn.get("interrupt_response") is not True:
        problems.append("turn_detection")
    if not (audio.get("input") or {}).get("transcription", {}).get("model"):
        problems.append("transcription")
    if session.get("instructions") != rt.SECRETARY_INSTRUCTIONS_TR:
        problems.append("instructions")
    return problems


class FakeOpenAI:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}
        self.path = ""
        self.problems: list[str] | None = None

    async def handler(self, ws) -> None:  # noqa: ANN001 - websockets connection
        self.headers = dict(ws.request.headers)
        self.path = ws.request.path
        first = json.loads(await ws.recv())
        self.received.append(first)
        self.problems = _session_problems(first)
        if self.problems:
            await ws.close(1008, "bad session")
            return
        await ws.send(json.dumps({"type": oai.EV_SESSION_UPDATED, "session": {}}))
        async for raw in ws:
            message = json.loads(raw)
            self.received.append(message)
            if message["type"] == rt.CMD_INPUT_AUDIO_APPEND:
                await ws.send(json.dumps({"type": oai.EV_SPEECH_STARTED}))
                await ws.send(
                    json.dumps(
                        {
                            "type": oai.EV_INPUT_TRANSCRIPT_COMPLETED,
                            "transcript": "Ben Ayşe, yarın arasın.",
                        }
                    )
                )
                await ws.send(
                    json.dumps({"type": oai.EV_OUTPUT_AUDIO_DELTA, "delta": message["audio"]})
                )
                await ws.send(
                    json.dumps(
                        {
                            "type": rt.EV_OUTPUT_AUDIO_TRANSCRIPT_DONE,
                            "transcript": "Tamam, ileteceğim.",
                        }
                    )
                )
                await ws.send(json.dumps({"type": oai.EV_RESPONSE_DONE, "response": {}}))
            elif message["type"] == oai.CMD_RESPONSE_CREATE:
                await ws.send(json.dumps({"type": oai.EV_ERROR, "error": {"code": "x"}}))


async def _run_leg(fake: FakeOpenAI, **leg_kwargs: Any) -> list[rt.LegEvent]:
    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        leg = rt.OpenAIRealtimeLeg(
            api_key=API_KEY,
            base_url=f"http://127.0.0.1:{port}/v1",
            model="gpt-realtime-test",
            voice="cedar",
            transcription_model="gpt-4o-transcribe",
            **leg_kwargs,
        )
        await leg.connect(rt.SECRETARY_INSTRUCTIONS_TR, oai.AUDIO_FORMAT_G711_ULAW)
        await leg.send_audio("//79fn5+")
        events: list[rt.LegEvent] = []

        async def collect() -> None:
            async for event in leg.events():
                events.append(event)
                if event.kind == rt.LEG_RESPONSE_DONE:
                    await leg.say(rt.FAREWELL_INSTRUCTIONS_TR)
                if event.kind == rt.LEG_ERROR:
                    return

        await asyncio.wait_for(collect(), timeout=10)
        await leg.close()
        return events


def test_the_leg_opens_a_toolless_g711_secretary_session_and_maps_events() -> None:
    fake = FakeOpenAI()
    events = asyncio.run(_run_leg(fake))
    assert fake.problems == [], fake.problems
    assert fake.path == "/v1/realtime?model=gpt-realtime-test"
    assert fake.headers.get("authorization") == f"Bearer {API_KEY}"
    # audio passes through untouched: no re-encoding in either direction
    append = fake.received[1]
    assert append == {"type": rt.CMD_INPUT_AUDIO_APPEND, "audio": "//79fn5+"}
    assert [(e.kind, e.data) for e in events] == [
        (rt.LEG_SPEECH_STARTED, ""),
        (rt.LEG_TRANSCRIPT_IN, "Ben Ayşe, yarın arasın."),
        (rt.LEG_AUDIO_DELTA, "//79fn5+"),
        (rt.LEG_TRANSCRIPT_OUT, "Tamam, ileteceğim."),
        (rt.LEG_RESPONSE_DONE, ""),
        (rt.LEG_ERROR, "x"),
    ]
    say = fake.received[2]
    assert say["type"] == oai.CMD_RESPONSE_CREATE
    assert say["response"]["instructions"] == rt.FAREWELL_INSTRUCTIONS_TR


def test_a_session_with_tools_is_refused_by_the_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake really checks: a session carrying a manifest is closed 1008 (events end)."""
    real = rt.build_session_update

    def with_tools(**kwargs: Any) -> dict[str, Any]:
        body = real(**kwargs)
        body["session"]["tools"] = [{"type": "function", "name": "read_mail"}]
        return body

    monkeypatch.setattr(rt, "build_session_update", with_tools)
    fake = FakeOpenAI()
    events = asyncio.run(_run_leg(fake))
    assert fake.problems == ["tools"]
    # the refused session yields no speech, only the closed socket as an error
    assert [e.kind for e in events] == [rt.LEG_ERROR]


def test_session_update_shape() -> None:
    body = rt.build_session_update(
        instructions=rt.SECRETARY_INSTRUCTIONS_TR,
        voice="cedar",
        transcription_model="gpt-4o-transcribe",
    )
    assert _session_problems(body) == []
    assert body["session"]["type"] == "realtime"
    assert body["session"]["audio"]["input"]["transcription"]["language"] == "tr"


def test_secretary_instructions_hold_the_security_rules() -> None:
    text = rt.SECRETARY_INSTRUCTIONS_TR
    for phrase in ("ileteceğim", "kim", "konu", "mesaj", "programını", "numaralarını"):
        assert phrase in text, phrase


def test_map_server_event_ignores_what_it_does_not_know() -> None:
    assert rt.map_server_event({"type": oai.EV_SESSION_UPDATED}) is None
    assert rt.map_server_event({"type": "rate_limits.updated"}) is None
    legacy = rt.map_server_event({"type": oai.EV_OUTPUT_AUDIO_DELTA_LEGACY, "delta": "AA=="})
    assert legacy == rt.LegEvent(rt.LEG_AUDIO_DELTA, "AA==")


def test_ws_url_from_base() -> None:
    assert (
        rt.realtime_ws_url("https://api.openai.com/v1/", "gpt-realtime-2.1")
        == "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1"
    )
