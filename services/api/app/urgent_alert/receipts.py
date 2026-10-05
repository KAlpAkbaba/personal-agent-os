"""Did the owner see it? Open receipts, asked about until they are seen or the ringing ends.

``poll_open_receipts`` is pure: no timer, no Temporal - the wiring card calls it on a schedule
and turns its events into ledger lines (``alert.seen`` / ``alert.unseen``). A receipt is closed
the moment it produces an event, so a second pass never reports it again.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from app.urgent_alert.provider import AlarmProvider, AlarmReceipt

#: Pushover answers about a receipt for one week; past that an open record could never close.
RECEIPT_LIFETIME = timedelta(days=7)


@dataclass(frozen=True)
class OpenReceipt:
    notification_id: uuid.UUID
    receipt: AlarmReceipt


@dataclass(frozen=True)
class AlertEvent:
    kind: Literal["seen", "unseen"]
    notification_id: uuid.UUID
    receipt_id: str
    at: datetime


class ReceiptStore(Protocol):
    def open(self, notification_id: uuid.UUID, receipt: AlarmReceipt) -> None: ...

    def list_open(self) -> list[OpenReceipt]: ...

    def close(self, receipt_id: str, outcome: str, at: datetime) -> None: ...


class InMemoryReceiptStore:
    """This card's store; the durable table is the wiring card's."""

    def __init__(self) -> None:
        self._open: dict[str, OpenReceipt] = {}
        self.closed: dict[str, tuple[str, datetime]] = {}

    def open(self, notification_id: uuid.UUID, receipt: AlarmReceipt) -> None:
        self._open[receipt.receipt_id] = OpenReceipt(notification_id, receipt)

    def list_open(self) -> list[OpenReceipt]:
        return list(self._open.values())

    def close(self, receipt_id: str, outcome: str, at: datetime) -> None:
        if self._open.pop(receipt_id, None) is not None:
            self.closed[receipt_id] = (outcome, at)


def poll_open_receipts(
    store: ReceiptStore, provider: AlarmProvider, now: datetime
) -> list[AlertEvent]:
    events: list[AlertEvent] = []
    for item in store.list_open():
        receipt = item.receipt
        status = provider.poll(receipt.receipt_id)
        event: AlertEvent | None = None
        if status is not None and status.acknowledged_at is not None:
            event = AlertEvent(
                "seen", item.notification_id, receipt.receipt_id, status.acknowledged_at
            )
        elif status is not None and status.expired:
            at = status.expires_at or receipt.expires_at
            event = AlertEvent("unseen", item.notification_id, receipt.receipt_id, at)
        elif status is None and now > receipt.sent_at + RECEIPT_LIFETIME:
            event = AlertEvent(
                "unseen", item.notification_id, receipt.receipt_id, receipt.expires_at
            )
        if event is not None:
            store.close(receipt.receipt_id, event.kind, event.at)
            events.append(event)
    return events


__all__ = [
    "RECEIPT_LIFETIME",
    "AlertEvent",
    "InMemoryReceiptStore",
    "OpenReceipt",
    "ReceiptStore",
    "poll_open_receipts",
]
