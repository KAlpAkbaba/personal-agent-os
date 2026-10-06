"""Calls him when something important happens: the alarm rung (Pushover priority=2).

On the ladder after toast and before sound/push (``app.notifications.ladder``); the wiring
(``app.urgent_alert.wiring``) builds the rung, its receipt table and the receipt loop.
"""

from app.urgent_alert.provider import AlarmProvider, AlarmReceipt, ReceiptStatus
from app.urgent_alert.pushover import PushoverProvider
from app.urgent_alert.receipts import (
    AlertEvent,
    InMemoryReceiptStore,
    ReceiptStore,
    poll_open_receipts,
)
from app.urgent_alert.rung import CHANNEL_ALARM, AlarmRung, marked_important

__all__ = [
    "CHANNEL_ALARM",
    "AlarmProvider",
    "AlarmReceipt",
    "AlarmRung",
    "AlertEvent",
    "InMemoryReceiptStore",
    "PushoverProvider",
    "ReceiptStatus",
    "ReceiptStore",
    "marked_important",
    "poll_open_receipts",
]
