"""The alarm provider contract: a message that rings the phone until the owner acknowledges it.

The constitution puts every third-party service behind an interface; Pushover is the first
implementation (``app.urgent_alert.pushover``), not the shape of this one. ``None``/``False``
means "could not send / could not read" - a provider never raises into the ladder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AlarmReceipt:
    """What the provider gave back for an accepted alarm: the handle to ask about it later."""

    receipt_id: str
    sent_at: datetime
    #: When the provider stops ringing on its own; past it, an unacknowledged alarm is unseen.
    expires_at: datetime


@dataclass(frozen=True)
class ReceiptStatus:
    """What the provider knows about one alarm. Who acknowledged it is deliberately absent:
    Pushover answers with the user key there, and a key has no business in an object."""

    acknowledged_at: datetime | None
    expired: bool
    last_delivered_at: datetime | None
    expires_at: datetime | None


@runtime_checkable
class AlarmProvider(Protocol):
    def configured(self) -> bool: ...

    def send(self, title: str, body: str, url: str) -> AlarmReceipt | None: ...

    def poll(self, receipt_id: str) -> ReceiptStatus | None: ...

    def cancel(self, receipt_id: str) -> bool: ...


__all__ = ["AlarmProvider", "AlarmReceipt", "ReceiptStatus"]
