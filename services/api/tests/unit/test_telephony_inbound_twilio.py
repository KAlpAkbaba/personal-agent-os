"""inbound-calls-bridge: the pure Twilio half - the signature, the TwiML, the Media Streams frames.

Pinned here, each one RED under its own mutation:

* a webhook is Twilio's only when HMAC-SHA1(auth token, PUBLIC url + sorted fields) matches the
  ``X-Twilio-Signature`` header, compared with ``hmac.compare_digest``; a wrong token, a missing
  header, a changed field, a reordered-but-equal field list and the portless url are each judged
  the way Twilio's own validator (twilio-python ``RequestValidator``, MIT) would judge them;
* the answer TwiML says the KVKK notice in tr-TR BEFORE it connects the stream, the stream url is
  ``wss://<public host:port>/telephony/inbound/media`` and the bridge token rides a ``<Parameter>``;
* no TwiML this module writes ever contains ``<Record`` - Twilio call recording is never on;
* every Media Streams frame kind parses, and the frames we send back are Twilio's shape.

The expected signatures are computed HERE with hmac/hashlib/base64, never with the module's own
helper, and one of them is the worked example in Twilio's security documentation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import xml.etree.ElementTree as ET

import pytest

from app.telephony import inbound_twilio as tw

TOKEN = "f3c1e2a49b7d4e5f8a0b1c2d3e4f5a6b"
PUBLIC = "https://jarvis.tail1234.ts.net:8443"
VOICE_URL = PUBLIC + "/telephony/inbound/voice"
FIELDS = [
    ("CallSid", "CA" + "1" * 32),
    ("From", "+905321112233"),
    ("To", "+908501234567"),
    ("CallStatus", "ringing"),
    ("AccountSid", "AC" + "2" * 32),
]


def _sign(token: str, url: str, fields: list[tuple[str, str]]) -> str:
    """Twilio's algorithm, written out independently of the module under test."""
    grouped: dict[str, set[str]] = {}
    for name, value in fields:
        grouped.setdefault(name, set()).add(value)
    data = url + "".join(
        name + value for name in sorted(grouped) for value in sorted(grouped[name])
    )
    digest = hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


# ------------------------------------------------------------------ signature


def test_twilio_documentation_vector_is_accepted() -> None:
    """The worked example of twilio.com/docs/usage/security (auth token 12345): the docs print
    the signature ``GvWf1cFY/Q7PnoempGyD5oXAezc=`` for exactly these fields and this url."""
    fields = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+14158675310",
        "Digits": "1234",
        "From": "+14158675310",
        "To": "+18005551212",
    }
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    assert tw.validate_signature("12345", url, fields, "GvWf1cFY/Q7PnoempGyD5oXAezc=")
    assert not tw.validate_signature("12345", url, fields, "GvWf1cFY/Q7PnoempGyD5oXAezc")
    assert not tw.validate_signature("12345", url, {**fields, "Digits": "1235"}, "GvWf1cFY/Q7PnoempGyD5oXAezc=")


def test_a_correct_signature_is_accepted() -> None:
    assert tw.validate_signature(TOKEN, VOICE_URL, FIELDS, _sign(TOKEN, VOICE_URL, FIELDS))


def test_field_order_does_not_matter_but_every_field_does() -> None:
    header = _sign(TOKEN, VOICE_URL, FIELDS)
    assert tw.validate_signature(TOKEN, VOICE_URL, list(reversed(FIELDS)), header)
    changed = [(k, "+905009998877" if k == "From" else v) for k, v in FIELDS]
    assert not tw.validate_signature(TOKEN, VOICE_URL, changed, header)
    assert not tw.validate_signature(TOKEN, VOICE_URL, [*FIELDS, ("Digits", "1")], header)
    assert not tw.validate_signature(TOKEN, VOICE_URL, FIELDS[1:], header)


def test_a_repeated_form_key_is_signed_with_its_sorted_values() -> None:
    fields = [*FIELDS, ("StirVerstat", "TN-Validation-Passed-B"), ("StirVerstat", "A")]
    header = _sign(TOKEN, VOICE_URL, fields)
    assert tw.validate_signature(TOKEN, VOICE_URL, fields, header)
    assert tw.validate_signature(TOKEN, VOICE_URL, list(reversed(fields)), header)


@pytest.mark.parametrize(
    ("token", "url", "header"),
    [
        ("wrong" + TOKEN[5:], VOICE_URL, None),
        (TOKEN, VOICE_URL, ""),
        (TOKEN, "https://jarvis.tail1234.ts.net/telephony/inbound/voice", None),
        (TOKEN, "http://100.64.0.7:8001/telephony/inbound/voice", None),
        ("", VOICE_URL, None),
    ],
    ids=["wrong-token", "missing-header", "portless-url", "proxy-url", "no-auth-token"],
)
def test_refused_signatures(token: str, url: str, header: str | None) -> None:
    good = _sign(TOKEN, VOICE_URL, FIELDS)
    # The url Twilio signed is the configured PUBLIC one; validating against any other url
    # (portless, or the address the reverse proxy forwards to) must fail.
    signed = good if header is None else header
    if token == "":
        signed = _sign("", url, FIELDS)
    assert not tw.validate_signature(token, url, FIELDS, signed)


def test_the_comparison_is_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []
    real = hmac.compare_digest

    def spy(a, b):  # noqa: ANN001 - the stdlib signature
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(tw.hmac, "compare_digest", spy)
    good = _sign(TOKEN, VOICE_URL, FIELDS)
    assert tw.validate_signature(TOKEN, VOICE_URL, FIELDS, good)
    assert not tw.validate_signature(TOKEN, VOICE_URL, FIELDS, "x" + good[1:])
    assert len(seen) == 2


def test_port_variant_names_the_portless_form_only_for_a_port_url() -> None:
    assert tw.port_variant(VOICE_URL) == "https://jarvis.tail1234.ts.net/telephony/inbound/voice"
    assert tw.port_variant("https://example.com/telephony/inbound/voice") is None


# ------------------------------------------------------------------ TwiML


def test_answer_says_the_kvkk_notice_in_turkish_then_connects_the_stream() -> None:
    url = tw.stream_url(PUBLIC)
    assert url == "wss://jarvis.tail1234.ts.net:8443/telephony/inbound/media"
    xml = tw.twiml_answer(tw.KVKK_ANNOUNCEMENT_TR, url, "tok-123_abc")
    root = ET.fromstring(xml)
    assert root.tag == "Response"
    children = list(root)
    assert [c.tag for c in children] == ["Say", "Connect"]
    say, connect = children
    assert say.attrib["language"] == "tr-TR"
    assert say.text == tw.KVKK_ANNOUNCEMENT_TR
    stream = connect.find("Stream")
    assert stream is not None and stream.attrib["url"] == url
    param = stream.find("Parameter")
    assert param is not None
    assert param.attrib == {"name": "bridge_token", "value": "tok-123_abc"}
    assert "<Record" not in xml


def test_kvkk_notice_says_no_recording_and_names_no_owner() -> None:
    text = tw.KVKK_ANNOUNCEMENT_TR
    assert "ses kaydı olarak saklanmaz" in text
    assert "yazıya dökülür" in text
    assert "bu hattın" in text


def test_refuse_says_it_in_turkish_and_hangs_up() -> None:
    xml = tw.twiml_refuse(tw.REFUSE_TEXT_TR)
    root = ET.fromstring(xml)
    assert [c.tag for c in root] == ["Say", "Hangup"]
    assert root[0].attrib["language"] == "tr-TR"
    assert root[0].text == "Şu anda cevap veremiyorum, lütfen daha sonra arayın."
    assert "<Record" not in xml


def test_twiml_escapes_what_it_is_given() -> None:
    xml = tw.twiml_answer('a<b>&"c', "wss://h:8443/telephony/inbound/media", 'x"y')
    root = ET.fromstring(xml)
    assert root[0].text == 'a<b>&"c'
    assert root.find("Connect/Stream/Parameter").attrib["value"] == 'x"y'


def test_no_twiml_in_the_module_ever_records() -> None:
    import inspect

    assert "<Record" not in inspect.getsource(tw)


# ------------------------------------------------------------------ Media Streams frames


def test_parse_every_frame_kind() -> None:
    sid = "MZ" + "3" * 32
    connected = tw.parse_stream_event(
        json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"})
    )
    assert connected.kind == "connected"
    start = tw.parse_stream_event(
        {
            "event": "start",
            "sequenceNumber": "1",
            "streamSid": sid,
            "start": {
                "accountSid": "AC1",
                "streamSid": sid,
                "callSid": "CA9",
                "tracks": ["inbound"],
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                "customParameters": {"bridge_token": "tok"},
            },
        }
    )
    assert (start.kind, start.stream_sid, start.call_sid) == ("start", sid, "CA9")
    assert start.custom_parameters == {"bridge_token": "tok"}
    media = tw.parse_stream_event(
        {
            "event": "media",
            "streamSid": sid,
            "media": {"track": "inbound", "chunk": "2", "timestamp": "5", "payload": "//79"},
        }
    )
    assert (media.kind, media.payload) == ("media", "//79")
    mark = tw.parse_stream_event({"event": "mark", "streamSid": sid, "mark": {"name": "m1"}})
    assert (mark.kind, mark.mark_name) == ("mark", "m1")
    dtmf = tw.parse_stream_event(
        {"event": "dtmf", "streamSid": sid, "dtmf": {"track": "inbound_track", "digit": "5"}}
    )
    assert (dtmf.kind, dtmf.digit) == ("dtmf", "5")
    stop = tw.parse_stream_event(
        {"event": "stop", "streamSid": sid, "stop": {"accountSid": "AC1", "callSid": "CA9"}}
    )
    assert (stop.kind, stop.call_sid) == ("stop", "CA9")
    assert tw.parse_stream_event({"event": "surprise"}).kind == "unknown"
    with pytest.raises(ValueError):
        tw.parse_stream_event("not json")


def test_frames_we_send_are_twilio_shaped() -> None:
    assert json.loads(tw.media_frame("MZ1", "AAEC")) == {
        "event": "media",
        "streamSid": "MZ1",
        "media": {"payload": "AAEC"},
    }
    assert json.loads(tw.clear_frame("MZ1")) == {"event": "clear", "streamSid": "MZ1"}
    assert json.loads(tw.mark_frame("MZ1", "r1")) == {
        "event": "mark",
        "streamSid": "MZ1",
        "mark": {"name": "r1"},
    }


def test_twilio_inbound_fills_the_provider_protocol() -> None:
    provider = tw.TwilioInbound(TOKEN)
    assert isinstance(provider, tw.InboundTelephonyProvider)
    assert provider.configured()
    assert not tw.TwilioInbound("").configured()
    assert provider.validate_request(VOICE_URL, FIELDS, _sign(TOKEN, VOICE_URL, FIELDS))
    assert "TOKEN" not in repr(provider) and TOKEN not in repr(provider)
