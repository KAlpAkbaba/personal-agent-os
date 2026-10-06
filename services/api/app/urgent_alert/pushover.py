"""Pushover emergency priority (``priority=2``) over plain ``httpx`` - three endpoints, no SDK.

priority=2 rings through the iPhone's silent switch and Do Not Disturb (iOS Critical Alert)
and repeats every ``retry`` seconds until the owner taps "acknowledge" or ``expire`` passes;
the receipt says which happened. Limits (pushover.net/api, read 2026-10-05): retry >= 30 s,
expire <= 10800 s.

**Keys never reach a log.** The receipt query carries the app token in its URL (Pushover
offers no other way) and httpx logs every request URL at INFO, so a filter masks ``token=``
on the httpx/httpcore loggers. Error bodies are not logged, only their status code; the
receipt's ``acknowledged_by`` (the user key itself) is never read.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.logging import get_logger
from app.urgent_alert.provider import AlarmReceipt, ReceiptStatus

logger = get_logger("app.urgent_alert.pushover")

BASE_URL = "https://api.pushover.net/1"
PRIORITY_EMERGENCY = 2
MIN_RETRY_S = 30
MAX_EXPIRE_S = 10800

#: Pushover receipts are 30 alphanumerics; anything else is refused before it becomes a path.
_RECEIPT_ID = re.compile(r"^[A-Za-z0-9]{1,64}$")
_TOKEN_IN_URL = re.compile(r"(token=)[^&\s\"']+")
_HTTP_LOGGERS = ("httpx", "httpcore", "httpcore.connection", "httpcore.http11", "httpcore.http2")


class _MaskTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        masked = _TOKEN_IN_URL.sub(r"\1***", message)
        if masked != message:
            record.msg, record.args = masked, ()
        return True


_MASK = _MaskTokenFilter()


def _install_mask() -> None:
    for name in _HTTP_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(f, _MaskTokenFilter) for f in target.filters):
            target.addFilter(_MASK)


# At import, beside the Twilio SID filter (app.telephony.twilio): installed before any
# provider exists, so no request can be logged ahead of it.
_install_mask()


def _reveal(value: Any) -> str:
    """Accepts a plain string or a pydantic ``SecretStr`` (what settings will hand over)."""
    getter = getattr(value, "get_secret_value", None)
    return (getter() if callable(getter) else value) or ""


def _at(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), UTC) if value else None


class PushoverProvider:
    def __init__(
        self,
        app_token: Any,
        user_key: Any,
        *,
        client: httpx.Client | None = None,
        retry_s: int = 60,
        expire_s: int = MAX_EXPIRE_S,
        timeout_s: float = 10.0,
        base_url: str = BASE_URL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        # A wrong configuration is loud here; only network/HTTP faults are quiet below.
        if retry_s < MIN_RETRY_S:
            raise ValueError(f"retry_s must be >= {MIN_RETRY_S}")
        if expire_s > MAX_EXPIRE_S or expire_s < retry_s:
            raise ValueError(f"expire_s must be between retry_s and {MAX_EXPIRE_S}")
        self._app_token = _reveal(app_token)
        self._user_key = _reveal(user_key)
        self._client = client or httpx.Client(timeout=timeout_s)
        self._retry_s = retry_s
        self._expire_s = expire_s
        self._base = base_url.rstrip("/")
        self._clock = clock
        _install_mask()

    def __repr__(self) -> str:
        return f"PushoverProvider(configured={self.configured()})"

    def configured(self) -> bool:
        return bool(self._app_token) and bool(self._user_key)

    def _call(self, op: str, method: str, path: str, **kwargs: Any) -> dict[str, Any] | None:
        try:
            response = self._client.request(method, f"{self._base}/{path}", **kwargs)
        except httpx.HTTPError as exc:
            logger.info("pushover_unreachable", op=op, error_class=type(exc).__name__)
            return None
        if response.status_code != 200:
            # 4xx: input refused (never retried as-is), 429: monthly quota, 5xx: transient.
            logger.info("pushover_refused", op=op, status_code=response.status_code)
            return None
        try:
            payload = response.json()
        except ValueError:
            logger.info("pushover_bad_reply", op=op)
            return None
        if not isinstance(payload, dict) or payload.get("status") != 1:
            logger.info("pushover_refused", op=op, status_code=response.status_code)
            return None
        return payload

    def send(self, title: str, body: str, url: str) -> AlarmReceipt | None:
        if not self.configured():
            return None
        sent_at = self._clock()
        payload = self._call(
            "send",
            "POST",
            "messages.json",
            data={
                "token": self._app_token,
                "user": self._user_key,
                "title": title,
                "message": body,
                "url": url,
                "priority": str(PRIORITY_EMERGENCY),
                "retry": str(self._retry_s),
                "expire": str(self._expire_s),
            },
        )
        receipt = (payload or {}).get("receipt")
        if not isinstance(receipt, str) or not _RECEIPT_ID.match(receipt):
            return None
        logger.info("pushover_accepted", receipt=receipt[:6])
        return AlarmReceipt(
            receipt_id=receipt,
            sent_at=sent_at,
            expires_at=sent_at + timedelta(seconds=self._expire_s),
        )

    def poll(self, receipt_id: str) -> ReceiptStatus | None:
        if not self.configured() or not _RECEIPT_ID.match(receipt_id):
            return None
        payload = self._call(
            "poll", "GET", f"receipts/{receipt_id}.json", params={"token": self._app_token}
        )
        if payload is None:
            return None
        try:
            return ReceiptStatus(
                acknowledged_at=_at(payload.get("acknowledged_at"))
                if payload.get("acknowledged") == 1
                else None,
                expired=payload.get("expired") == 1,
                last_delivered_at=_at(payload.get("last_delivered_at")),
                expires_at=_at(payload.get("expires_at")),
            )
        except (TypeError, ValueError, OverflowError, OSError):
            logger.info("pushover_bad_reply", op="poll")
            return None

    def cancel(self, receipt_id: str) -> bool:
        if not self.configured() or not _RECEIPT_ID.match(receipt_id):
            return False
        payload = self._call(
            "cancel", "POST", f"receipts/{receipt_id}/cancel.json", data={"token": self._app_token}
        )
        return payload is not None


__all__ = ["BASE_URL", "PRIORITY_EMERGENCY", "PushoverProvider"]
