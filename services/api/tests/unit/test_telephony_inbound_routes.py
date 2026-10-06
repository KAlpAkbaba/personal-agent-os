"""inbound-calls-bridge: the webhook routes, the gates, and what a finished call leaves behind.

The router is mounted on its OWN FastAPI test app here - it is not part of ``create_app`` until
the binding card (ADR) lands, so ``test_identity_enforcement`` stays unchanged and green.

Pinned here, each one RED under its own mutation:

* an unsigned or wrongly signed webhook is a 403 with an EMPTY body; the signature is checked
  against the configured PUBLIC url (``https://...:8443``), not the url the app sees behind the
  reverse proxy (``http://testserver``);
* a signed call is answered with ``application/xml`` TwiML: the KVKK notice in tr-TR, the stream
  and its bridge token;
* off, a second concurrent call, and a full daily allowance are each refused with Turkish
  TwiML + Hangup, and no token is issued;
* end to end with a fake Twilio and a fake realtime leg: voice -> token -> media socket ->
  finalize -> ONE ledger row (``telephony.inbound_answered``) and ONE notification
  ("<number> aradı", the caller's own words); the status callback that follows adds nothing;
* finalize: transcript capped at 8000 characters with a ``truncated`` flag, notification body
  the first 300 characters of the caller's lines, ``InvalidVocabulary`` never swallowed, and the
  new notification kind never makes JARVIS phone the owner back.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.ledger import vocabulary as v
from app.telephony import inbound_bridge as br
from app.telephony import inbound_records as records
from app.telephony import inbound_routes as routes
from app.telephony import inbound_twilio as tw
from app.telephony import policy
from app.telephony.inbound_records import InboundRecorder
from app.telephony.inbound_settings import InboundSettings
from tests.unit.test_telephony_inbound_bridge import (
    CALLER,
    JARVIS_AUDIO,
    STREAM_SID,
    T0,
    Clock,
    FakeRealtimeLeg,
    conversation,
    ledger_rows,
    make_scope,
    media,
    notification_rows,
    start_frame,
)

AUTH = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
PUBLIC = "https://jarvis.tail1234.ts.net:8443"
CALL_A = "CA" + "a" * 32
CALL_B = "CA" + "b" * 32


def _sign(url: str, fields: dict[str, str]) -> str:
    data = url + "".join(k + fields[k] for k in sorted(fields))
    return base64.b64encode(hmac.new(AUTH.encode(), data.encode(), hashlib.sha1).digest()).decode()


def _voice_fields(call_sid: str = CALL_A, caller: str = CALLER) -> dict[str, str]:
    return {
        "CallSid": call_sid,
        "AccountSid": "AC" + "1" * 32,
        "From": caller,
        "To": "+908501234567",
        "CallStatus": "ringing",
        "Direction": "inbound",
    }


class World:
    def __init__(self, *, enabled: bool = True) -> None:
        self.engine, self.scope = make_scope()
        self.clock = Clock(T0)
        self.legs: list[FakeRealtimeLeg] = []
        self.settings = InboundSettings(
            enabled=enabled,
            public_base_url=PUBLIC,
            twilio_auth_token=AUTH,
            openai_api_key="sk-test",
        )
        self.line = br.InboundLine(
            settings=self.settings,
            provider=tw.TwilioInbound(AUTH),
            recorder=InboundRecorder(self.scope),
            leg_factory=self._leg,
            clock=self.clock,
            sleep=self._never,
        )
        app = FastAPI()
        app.include_router(routes.router)
        app.state.telephony_inbound = self.line
        self.client = TestClient(app)

    def _leg(self) -> FakeRealtimeLeg:
        leg = FakeRealtimeLeg(conversation)
        self.legs.append(leg)
        return leg

    async def _never(self, seconds: float) -> None:
        import asyncio

        await asyncio.Event().wait()

    def post(self, path: str, fields: dict[str, str], *, signature: str | None = "auto"):
        headers = {}
        if signature == "auto":
            headers["X-Twilio-Signature"] = _sign(PUBLIC + path, fields)
        elif signature is not None:
            headers["X-Twilio-Signature"] = signature
        return self.client.post(path, data=fields, headers=headers)

    def voice(self, call_sid: str = CALL_A, **kw: Any):
        return self.post(tw.VOICE_PATH, _voice_fields(call_sid), **kw)

    def close(self) -> None:
        self.client.close()
        self.engine.dispose()


@pytest.fixture()
def world():
    w = World()
    yield w
    w.close()


def _token(xml: str) -> str:
    param = ET.fromstring(xml).find("Connect/Stream/Parameter")
    assert param is not None, xml
    return param.attrib["value"]


def _is_refusal(response: Any) -> bool:
    root = ET.fromstring(response.text)
    return [c.tag for c in root] == ["Say", "Hangup"] and "cevap veremiyorum" in root[0].text


# ------------------------------------------------------------------ the signature gate


@pytest.mark.parametrize("signature", [None, "", "AAAA", "wrong-url"])
def test_unsigned_or_badly_signed_webhooks_are_403_without_a_body(world: World, signature) -> None:
    if signature == "wrong-url":
        # signed for the url the app sees behind the proxy, not the public one Twilio called
        signature = _sign("http://testserver" + tw.VOICE_PATH, _voice_fields())
    response = world.voice(signature=signature)
    assert response.status_code == 403
    assert len(response.content) == 0
    status = world.post(tw.STATUS_PATH, {"CallSid": CALL_A, "CallStatus": "completed"}, signature=signature)
    assert status.status_code == 403 and len(status.content) == 0
    assert world.line.tokens.pending() == 0


def test_a_changed_field_is_403(world: World) -> None:
    fields = _voice_fields()
    signature = _sign(PUBLIC + tw.VOICE_PATH, fields)
    fields["From"] = "+905009998877"
    response = world.post(tw.VOICE_PATH, fields, signature=signature)
    assert response.status_code == 403 and response.content == b""


def test_a_portless_signature_is_403_and_named_in_the_log(world: World) -> None:
    import structlog

    portless = "https://jarvis.tail1234.ts.net" + tw.VOICE_PATH
    with structlog.testing.capture_logs() as logs:
        response = world.voice(signature=_sign(portless, _voice_fields()))
    assert response.status_code == 403 and response.content == b""
    assert any(e["event"] == "telephony_inbound_signature_port_variant" for e in logs)
    assert AUTH not in repr(logs)


# ------------------------------------------------------------------ the answer and the gates


def test_a_signed_call_is_answered_with_the_kvkk_notice_and_the_stream(world: World) -> None:
    response = world.voice()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(response.text)
    assert root[0].tag == "Say" and root[0].attrib["language"] == "tr-TR"
    assert root[0].text == tw.KVKK_ANNOUNCEMENT_TR
    stream = root.find("Connect/Stream")
    assert stream is not None
    assert stream.attrib["url"] == "wss://jarvis.tail1234.ts.net:8443/telephony/inbound/media"
    assert len(_token(response.text)) >= 40
    assert "<Record" not in response.text
    # nothing is written while it rings: finalize writes the one row
    assert ledger_rows(world.scope) == []


def test_off_is_refused_and_opens_nothing() -> None:
    w = World(enabled=False)
    try:
        response = w.voice()
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/xml")
        assert _is_refusal(response)
        assert "<Connect" not in response.text and "<Record" not in response.text
        assert w.line.tokens.pending() == 0
    finally:
        w.close()


def test_a_second_concurrent_call_is_refused(world: World) -> None:
    first = world.voice(CALL_A)
    assert not _is_refusal(first)
    second = world.voice(CALL_B)
    assert _is_refusal(second)
    assert world.line.tokens.pending() == 1


def test_the_daily_allowance_is_counted_from_the_ledger(world: World) -> None:
    recorder = world.line.recorder
    for n in range(3):
        start = T0 - timedelta(hours=1) + timedelta(minutes=13 * n)
        assert recorder.finalize(
            f"CA{n:032d}", CALLER, start, start + timedelta(minutes=10), []
        )
    third_used = recorder.seconds_used_today(T0)
    assert third_used == 1800
    response = world.voice()
    assert _is_refusal(response)
    assert world.line.tokens.pending() == 0


def test_yesterday_does_not_count(world: World) -> None:
    yesterday = T0 - timedelta(days=1)
    world.line.recorder.finalize("CA" + "9" * 32, CALLER, yesterday, yesterday + timedelta(minutes=40), [])
    assert not _is_refusal(world.voice())


def test_what_is_left_of_the_day_bounds_the_call(world: World) -> None:
    start = T0 - timedelta(hours=1)
    world.line.recorder.finalize("CA" + "9" * 32, CALLER, start, start + timedelta(minutes=28), [])
    token = _token(world.voice().text)
    grant = world.line.tokens.redeem(token, CALL_A)
    assert grant is not None and grant.max_seconds == 120


# ------------------------------------------------------------------ the media socket


@pytest.mark.parametrize("case", ["missing", "wrong", "spent", "expired"])
def test_media_socket_refuses_a_bad_token_with_1008(world: World, case: str) -> None:
    token = _token(world.voice().text)
    if case == "spent":
        assert world.line.tokens.redeem(token, CALL_A) is not None
    if case == "expired":
        world.clock.now = T0 + timedelta(seconds=121)
    sent = {"missing": None, "wrong": "x" * 43}.get(case, token)
    with world.client.websocket_connect(tw.MEDIA_PATH) as ws:
        ws.send_json(start_frame(sent, call_sid=CALL_A))
        # A 'stop' right behind it: a bridge that wrongly accepted the token ends the call
        # (normal close, a leg opened) instead of leaving this receive waiting for ever.
        ws.send_json({"event": "stop", "streamSid": STREAM_SID, "stop": {"callSid": CALL_A}})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 1008
    assert world.legs == []


def test_end_to_end_one_call_one_row_one_notification(world: World) -> None:
    token = _token(world.voice().text)
    with world.client.websocket_connect(tw.MEDIA_PATH) as ws:
        ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        ws.send_json(start_frame(token, call_sid=CALL_A))
        ws.send_json(media("//79fn5+"))
        frames = [ws.receive_json() for _ in range(3)]
        assert [f["event"] for f in frames] == ["clear", "media", "mark"]
        assert frames[1]["media"]["payload"] == JARVIS_AUDIO
        assert frames[1]["streamSid"] == STREAM_SID
        ws.send_json({"event": "stop", "streamSid": STREAM_SID, "stop": {"callSid": CALL_A}})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert world.legs[0].audio == ["//79fn5+"]
    rows = ledger_rows(world.scope)
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == "telephony.inbound_answered"
    assert row.subsystem == v.SUBSYSTEM_TELEPHONY
    assert row.source == "telephony" and row.source_ref == f"inbound:{CALL_A}"
    assert row.detail_json["caller"] == CALLER
    assert "Arayan: Ben Ayşe Demir" in row.detail_json["transcript"]
    assert row.detail_json["truncated"] is False
    notes = notification_rows(world.scope)
    assert len(notes) == 1
    assert notes[0].kind == records.NOTIFICATION_KIND
    assert notes[0].title == f"{CALLER} aradı"
    assert notes[0].body == "Ben Ayşe Demir, toplantı yarına kaldı."
    # the status callback that follows the call adds nothing
    after = world.post(
        tw.STATUS_PATH,
        {"CallSid": CALL_A, "CallStatus": "completed", "CallDuration": "42"},
    )
    assert after.status_code == 204
    assert len(ledger_rows(world.scope)) == 1 and len(notification_rows(world.scope)) == 1
    # and the line is free again
    assert not _is_refusal(world.voice(CALL_B))


def test_a_caller_who_hangs_up_during_the_notice_is_still_a_missed_call(world: World) -> None:
    world.voice(CALL_A)
    response = world.post(
        tw.STATUS_PATH, {"CallSid": CALL_A, "CallStatus": "completed", "CallDuration": "6"}
    )
    assert response.status_code == 204
    (row,) = ledger_rows(world.scope)
    assert row.detail_json["duration_s"] == 6 and row.detail_json["transcript"] == ""
    (note,) = notification_rows(world.scope)
    assert note.title == f"{CALLER} aradı" and note.body == records.NO_MESSAGE_TR
    # the unredeemed token no longer holds the line
    assert not _is_refusal(world.voice(CALL_B))


def test_a_status_for_a_call_this_line_never_answered_writes_nothing(world: World) -> None:
    response = world.post(tw.STATUS_PATH, {"CallSid": CALL_B, "CallStatus": "no-answer"})
    assert response.status_code == 204
    assert ledger_rows(world.scope) == [] and notification_rows(world.scope) == []


# ------------------------------------------------------------------ finalize


def _lines(*pairs: tuple[str, str]) -> list[br.TranscriptLine]:
    return [br.TranscriptLine(speaker, text, T0) for speaker, text in pairs]


def test_finalize_is_once_per_call_sid() -> None:
    engine, scope = make_scope()
    recorder = InboundRecorder(scope)
    lines = _lines(("Arayan", "Merhaba"))
    assert recorder.finalize(CALL_A, CALLER, T0, T0 + timedelta(seconds=30), lines) is True
    assert recorder.finalize(CALL_A, CALLER, T0, T0 + timedelta(seconds=31), lines) is False
    assert len(ledger_rows(scope)) == 1 and len(notification_rows(scope)) == 1
    engine.dispose()


def test_finalize_caps_the_transcript_and_the_notification_body() -> None:
    engine, scope = make_scope()
    recorder = InboundRecorder(scope)
    long_words = "çok önemli bir mesaj " * 40
    lines = _lines(*[("Arayan", long_words), ("JARVIS", "Not aldım.")] * 20)
    recorder.finalize(CALL_A, CALLER, T0, T0 + timedelta(minutes=5), lines)
    (row,) = ledger_rows(scope)
    assert len(row.detail_json["transcript"]) == records.MAX_TRANSCRIPT_CHARS
    assert row.detail_json["truncated"] is True
    (note,) = notification_rows(scope)
    assert len(note.body) == records.NOTIFICATION_BODY_CHARS
    assert note.body == " ".join([long_words.strip()] * 20)[:300]
    assert "Not aldım" not in note.body
    engine.dispose()


def test_a_short_transcript_is_not_truncated_and_the_body_is_only_the_callers_words() -> None:
    engine, scope = make_scope()
    recorder = InboundRecorder(scope)
    lines = _lines(("JARVIS", "Buyurun."), ("Arayan", "Ben Can."), ("Arayan", "Akşam arasın."))
    recorder.finalize(CALL_A, "", T0, T0 + timedelta(seconds=40), lines)
    (row,) = ledger_rows(scope)
    assert row.detail_json["truncated"] is False
    assert row.detail_json["duration_s"] == 40
    assert re.search(r"JARVIS: Buyurun\.", row.detail_json["transcript"])
    (note,) = notification_rows(scope)
    assert note.title == "Bilinmeyen numara aradı"
    assert note.body == "Ben Can. Akşam arasın."
    engine.dispose()


@pytest.mark.parametrize("caller", ["", "anonymous", "+266696687", "Restricted"])
def test_unknown_numbers_are_named_as_unknown(caller: str) -> None:
    assert records.notification_title(caller) == "Bilinmeyen numara aradı"


def test_finalize_does_not_swallow_an_unknown_event_type(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, scope = make_scope()
    monkeypatch.setattr(records, "EVENT_TYPE", "telephony.not_in_the_vocabulary")
    with pytest.raises(v.InvalidVocabulary):
        InboundRecorder(scope).finalize(CALL_A, CALLER, T0, T0, [])
    assert notification_rows(scope) == []
    engine.dispose()


def test_the_event_type_is_in_the_closed_vocabulary() -> None:
    assert v.validate_event_type("telephony.inbound_answered") == records.EVENT_TYPE
    assert v.EVENT_TYPE_TELEPHONY_INBOUND_ANSWERED == "telephony.inbound_answered"


def test_an_inbound_call_notification_never_makes_jarvis_call_the_owner() -> None:
    assert policy.call_kind_for_notification(records.NOTIFICATION_KIND) is None


def test_day_start_is_istanbul_midnight() -> None:
    late = datetime(2026, 10, 6, 21, 30, tzinfo=UTC)  # 00:30 on the 7th in Istanbul
    assert records.day_start(late) == datetime(2026, 10, 6, 21, 0, tzinfo=UTC)
