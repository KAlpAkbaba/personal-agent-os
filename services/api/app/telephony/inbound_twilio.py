"""Inbound calls, Twilio's half: the webhook signature, the TwiML, the Media Streams frames.

Pure functions, no I/O. The signature is Twilio's documented algorithm (the one twilio-python's
MIT ``RequestValidator`` implements), in the standard library: HMAC-SHA1 keyed with the auth
token over the PUBLIC url Twilio called followed by every POST field, name then value, names in
alphabetical order (a repeated name: its values sorted), base64. Compared with
``hmac.compare_digest``. The url is the configured public one - ``https://<node>.ts.net:8443/...``
with the port written - never the ``http://<bind ip>:8001`` the app sees behind the edge proxy.

The answer says the KVKK notice with Twilio's ``<Say language="tr-TR">`` and then connects a
bidirectional Media Stream; the one-time bridge token rides a ``<Parameter>`` because a stream
url takes no query string. No TwiML here records: Twilio call recording is never switched on.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import urllib.parse
from collections.abc import Iterable, Mapping
from typing import Any, Final, Protocol, runtime_checkable
from xml.sax.saxutils import escape, quoteattr

#: The notice every caller hears before the line listens (ADR; the owner may change it).
KVKK_ANNOUNCEMENT_TR: Final[str] = (
    "Merhaba, ben bu hattın dijital asistanıyım. Bu görüşme ses kaydı olarak saklanmaz; "
    "söyledikleriniz yazıya dökülür ve hat sahibine iletilir. Devam ederek bunu kabul etmiş "
    "olursunuz. Buyurun, sizi dinliyorum."
)
REFUSE_TEXT_TR: Final[str] = "Şu anda cevap veremiyorum, lütfen daha sonra arayın."

SIGNATURE_HEADER: Final[str] = "X-Twilio-Signature"
VOICE_PATH: Final[str] = "/telephony/inbound/voice"
STATUS_PATH: Final[str] = "/telephony/inbound/status"
MEDIA_PATH: Final[str] = "/telephony/inbound/media"
BRIDGE_TOKEN_PARAM: Final[str] = "bridge_token"

STREAM_EVENT_KINDS: Final[tuple[str, ...]] = ("connected", "start", "media", "stop", "mark", "dtmf")

Fields = Mapping[str, str] | Iterable[tuple[str, str]]


# ------------------------------------------------------------------ signature


def _pairs(fields: Fields) -> list[tuple[str, str]]:
    if isinstance(fields, Mapping):
        return [(str(k), str(val)) for k, val in fields.items()]
    return [(str(k), str(val)) for k, val in fields]


def signature_base(public_url: str, fields: Fields) -> str:
    grouped: dict[str, set[str]] = {}
    for name, value in _pairs(fields):
        grouped.setdefault(name, set()).add(value)
    return public_url + "".join(
        name + value for name in sorted(grouped) for value in sorted(grouped[name])
    )


def compute_signature(auth_token: str, public_url: str, fields: Fields) -> str:
    digest = hmac.new(
        auth_token.encode("utf-8"), signature_base(public_url, fields).encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def validate_signature(auth_token: str, public_url: str, fields: Fields, header: str | None) -> bool:
    """Whether ``header`` is Twilio's signature of this request. No token or no header: never."""
    if not auth_token or not header:
        return False
    expected = compute_signature(auth_token, public_url, fields)
    return hmac.compare_digest(expected.encode("ascii"), header.encode("ascii", "replace"))


def port_variant(url: str) -> str | None:
    """The same url without its explicit port, or ``None`` when it names none. Only for a log
    line: Twilio's own validator also accepts this form, the card's rule does not."""
    parts = urllib.parse.urlsplit(url)
    if parts.port is None or parts.hostname is None:
        return None
    return urllib.parse.urlunsplit(parts._replace(netloc=parts.hostname))


# ------------------------------------------------------------------ TwiML


def stream_url(public_base_url: str) -> str:
    base = public_base_url.rstrip("/")
    base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    return base + MEDIA_PATH


def twiml_answer(kvkk_text: str, stream_url: str, bridge_token: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say language="tr-TR">{escape(kvkk_text)}</Say>'
        f"<Connect><Stream url={quoteattr(stream_url)}>"
        f"<Parameter name={quoteattr(BRIDGE_TOKEN_PARAM)} value={quoteattr(bridge_token)}/>"
        "</Stream></Connect></Response>"
    )


def twiml_refuse(text: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say language="tr-TR">{escape(text)}</Say><Hangup/></Response>'
    )


# ------------------------------------------------------------------ Media Streams frames


@dataclasses.dataclass(frozen=True, slots=True)
class StreamEvent:
    #: connected | start | media | stop | mark | dtmf | unknown
    kind: str
    stream_sid: str = ""
    call_sid: str = ""
    payload: str = ""
    custom_parameters: dict[str, str] = dataclasses.field(default_factory=dict)
    mark_name: str = ""
    digit: str = ""


def parse_stream_event(raw: str | bytes | Mapping[str, Any]) -> StreamEvent:
    """One Twilio Media Streams message. Not JSON (or not an object): ``ValueError``."""
    if isinstance(raw, Mapping):
        message: Any = raw
    else:
        message = json.loads(raw)
    if not isinstance(message, Mapping):
        raise ValueError("a Media Streams message is a JSON object")
    kind = str(message.get("event") or "")
    if kind not in STREAM_EVENT_KINDS:
        return StreamEvent(kind="unknown")
    stream_sid = str(message.get("streamSid") or "")
    body = message.get(kind)
    body = body if isinstance(body, Mapping) else {}
    if kind == "start":
        params = body.get("customParameters")
        return StreamEvent(
            kind=kind,
            stream_sid=stream_sid or str(body.get("streamSid") or ""),
            call_sid=str(body.get("callSid") or ""),
            custom_parameters={str(k): str(val) for k, val in (params or {}).items()}
            if isinstance(params, Mapping)
            else {},
        )
    if kind == "media":
        return StreamEvent(kind=kind, stream_sid=stream_sid, payload=str(body.get("payload") or ""))
    if kind == "mark":
        return StreamEvent(kind=kind, stream_sid=stream_sid, mark_name=str(body.get("name") or ""))
    if kind == "dtmf":
        return StreamEvent(kind=kind, stream_sid=stream_sid, digit=str(body.get("digit") or ""))
    if kind == "stop":
        return StreamEvent(kind=kind, stream_sid=stream_sid, call_sid=str(body.get("callSid") or ""))
    return StreamEvent(kind=kind, stream_sid=stream_sid)


def media_frame(stream_sid: str, payload_b64: str) -> str:
    return json.dumps({"event": "media", "streamSid": stream_sid, "media": {"payload": payload_b64}})


def mark_frame(stream_sid: str, name: str) -> str:
    return json.dumps({"event": "mark", "streamSid": stream_sid, "mark": {"name": name}})


def clear_frame(stream_sid: str) -> str:
    return json.dumps({"event": "clear", "streamSid": stream_sid})


# ------------------------------------------------------------------ the provider seam


@runtime_checkable
class InboundTelephonyProvider(Protocol):
    """What the inbound line needs from a telephony provider (Telnyx later fills the same)."""

    name: str

    def configured(self) -> bool: ...

    def validate_request(self, public_url: str, fields: Fields, header: str | None) -> bool: ...

    def answer(self, kvkk_text: str, stream_url: str, bridge_token: str) -> str: ...

    def refuse(self, text: str) -> str: ...

    def parse_stream_event(self, raw: str | bytes | Mapping[str, Any]) -> StreamEvent: ...

    def media_frame(self, stream_sid: str, payload_b64: str) -> str: ...

    def mark_frame(self, stream_sid: str, name: str) -> str: ...

    def clear_frame(self, stream_sid: str) -> str: ...


class TwilioInbound:
    name = "twilio"

    def __init__(self, auth_token: str) -> None:
        self._auth_token = auth_token

    def __repr__(self) -> str:
        return f"TwilioInbound(configured={self.configured()})"

    def configured(self) -> bool:
        return bool(self._auth_token)

    def validate_request(self, public_url: str, fields: Fields, header: str | None) -> bool:
        return validate_signature(self._auth_token, public_url, fields, header)

    def answer(self, kvkk_text: str, stream_url: str, bridge_token: str) -> str:
        return twiml_answer(kvkk_text, stream_url, bridge_token)

    def refuse(self, text: str) -> str:
        return twiml_refuse(text)

    def parse_stream_event(self, raw: str | bytes | Mapping[str, Any]) -> StreamEvent:
        return parse_stream_event(raw)

    def media_frame(self, stream_sid: str, payload_b64: str) -> str:
        return media_frame(stream_sid, payload_b64)

    def mark_frame(self, stream_sid: str, name: str) -> str:
        return mark_frame(stream_sid, name)

    def clear_frame(self, stream_sid: str) -> str:
        return clear_frame(stream_sid)


__all__ = [
    "BRIDGE_TOKEN_PARAM",
    "KVKK_ANNOUNCEMENT_TR",
    "MEDIA_PATH",
    "REFUSE_TEXT_TR",
    "SIGNATURE_HEADER",
    "STATUS_PATH",
    "VOICE_PATH",
    "InboundTelephonyProvider",
    "StreamEvent",
    "TwilioInbound",
    "clear_frame",
    "compute_signature",
    "mark_frame",
    "media_frame",
    "parse_stream_event",
    "port_variant",
    "signature_base",
    "stream_url",
    "twiml_answer",
    "twiml_refuse",
    "validate_signature",
]
