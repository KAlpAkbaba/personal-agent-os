"""Calls him when something important happens: the alarm rung (Pushover priority=2).

Built in its own package and not yet on the ladder; the wiring card
(``team/plans/urgent-alert-rung-adr.md``) places it after toast and before push.
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
