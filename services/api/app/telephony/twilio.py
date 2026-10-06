"""Twilio Programmable Voice over its REST API (httpx; no SDK - two requests do not need one).

* ``POST /2010-04-01/Accounts/{AccountSid}/Calls.json`` with ``To``, ``From``, ``Twiml`` and
  ``Timeout`` creates the call; Twilio answers 201 with the call's ``sid`` and ``status``;
* ``GET .../Calls/{CallSid}.json`` reads its status.

HTTP basic auth with the Account SID and the auth token. Both are SECRETS (the Cloud Core's
env file, ``PAGENTOS_TELEPHONY_TWILIO_*``): they never appear in this object's repr, in an
error's message or in a log line - the request URL carries the Account SID, so an httpx error
is reported by its type name only, never by ``str(exc)``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

import httpx

from app.logging import get_logger
from app.telephony.provider import PlacedCall, TelephonyError

logger = get_logger("app.telephony.twilio")

API_ROOT: Final[str] = "https://api.twilio.com/2010-04-01"
#: Seconds Twilio lets the owner's phone ring before it is "no-answer".
RING_TIMEOUT_S: Final[int] = 30

_ACCOUNT_IN_URL = re.compile(r"(api\.twilio\.com/2010-04-01/Accounts/)[^/\s\"']+")


class _AccountSidRedactor(logging.Filter):
    """httpx logs every request's URL at INFO ("HTTP Request: POST https://api.twilio.com/
    2010-04-01/Accounts/AC.../Calls.json"), and Twilio's URL carries the Account SID. Found by
    test_the_settings_routes_say_connected_and_never_return_or_log_a_secret. The record is
    kept - the request is worth seeing - with the SID replaced."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _ACCOUNT_IN_URL.sub(r"\1***", str(arg)) if "api.twilio.com" in str(arg) else arg
                for arg in record.args
            )
        if isinstance(record.msg, str) and "api.twilio.com" in record.msg:
            record.msg = _ACCOUNT_IN_URL.sub(r"\1***", record.msg)
        return True


_REDACTOR = _AccountSidRedactor()
for _name in ("httpx", "httpcore"):
    if _REDACTOR not in logging.getLogger(_name).filters:
        logging.getLogger(_name).addFilter(_REDACTOR)


class TwilioTelephony:
    name = "twilio"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._transport = transport
        self._timeout_s = timeout_s

    def __repr__(self) -> str:
        return f"TwilioTelephony(configured={self.configured()})"

    def configured(self) -> bool:
        return bool(self._account_sid and self._auth_token)

    def _request(self, method: str, path: str, *, data: dict[str, Any] | None = None) -> Any:
        url = f"{API_ROOT}/Accounts/{self._account_sid}/{path}"
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=self._timeout_s,
                auth=(self._account_sid, self._auth_token),
            ) as client:
                response = client.request(method, url, data=data)
        except httpx.HTTPError as exc:
            raise TelephonyError(f"twilio_unreachable:{type(exc).__name__}") from None
        if response.status_code >= 400:
            code: Any = ""
            try:
                code = response.json().get("code", "")
            except ValueError:
                pass
            raise TelephonyError(f"twilio_http_{response.status_code}:{code}")
        try:
            return response.json()
        except ValueError:
            raise TelephonyError("twilio_bad_json") from None

    def place_call(self, *, to: str, from_: str, twiml: str) -> PlacedCall:
        body = self._request(
            "POST",
            "Calls.json",
            data={"To": to, "From": from_, "Twiml": twiml, "Timeout": str(RING_TIMEOUT_S)},
        )
        sid = str(body.get("sid", ""))
        if not sid:
            raise TelephonyError("twilio_no_call_sid")
        return PlacedCall(sid=sid, status=str(body.get("status", "")))

    def call_status(self, sid: str) -> str:
        return str(self._request("GET", f"Calls/{sid}.json").get("status", ""))


__all__ = ["API_ROOT", "RING_TIMEOUT_S", "TwilioTelephony"]
