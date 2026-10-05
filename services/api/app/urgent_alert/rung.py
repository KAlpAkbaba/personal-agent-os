"""``AlarmRung``: the ladder rung that rings the phone - built here, wired by a later card.

It speaks ``app.notifications.ladder.Rung`` exactly (``name``, ``available()``,
``deliver(row)``). ``deliver`` returning ``True`` means the provider accepted the alarm AND its
receipt was kept: an alarm whose receipt is lost could never be reported seen or unseen, so it
is cancelled and the ladder steps down instead. Only rows marked important ring; everything
else returns ``False`` without a request, and the ladder moves on as it does for any rung that
did not reach the owner.
"""

from __future__ import annotations

from collections.abc import Callable

from app.logging import get_logger
from app.notifications.models import NotificationRow
from app.urgent_alert import text
from app.urgent_alert.provider import AlarmProvider
from app.urgent_alert.receipts import ReceiptStore

logger = get_logger("app.urgent_alert.rung")

CHANNEL_ALARM = "alarm"


def marked_important(row: NotificationRow) -> bool:
    """The one place importance is read from a row: ``data_json["important"] is True``.
    The wiring card fills that key from the single importance policy."""
    return (row.data_json or {}).get("important") is True


def category_of(row: NotificationRow) -> str:
    value = (row.data_json or {}).get("alert_category")
    return value if isinstance(value, str) else ""


class AlarmRung:
    name = CHANNEL_ALARM

    def __init__(
        self,
        provider: AlarmProvider | None,
        store: ReceiptStore,
        *,
        link_for: Callable[[NotificationRow], str],
        is_important: Callable[[NotificationRow], bool] = marked_important,
        category: Callable[[NotificationRow], str] = category_of,
    ) -> None:
        self._provider = provider
        self._store = store
        self._link_for = link_for
        self._is_important = is_important
        self._category = category

    def available(self) -> bool:
        return self._provider is not None and self._provider.configured()

    def deliver(self, row: NotificationRow) -> bool:
        if self._provider is None or not self._is_important(row):
            return False
        try:
            title, body, url = text.compose(self._category(row), self._link_for(row))
        except text.TextRefused as exc:
            # The reason only: the refused text may be exactly what must not be logged.
            logger.error("alarm_text_refused", notification_id=str(row.id), reason=exc.reason)
            return False
        receipt = self._provider.send(title, body, url)
        if receipt is None:
            logger.info("alarm_not_sent", notification_id=str(row.id))
            return False
        try:
            self._store.open(row.id, receipt)
        except Exception as exc:  # noqa: BLE001 - an unkept receipt is cancelled, not raised
            logger.warning(
                "alarm_receipt_not_kept",
                notification_id=str(row.id),
                error_class=type(exc).__name__,
            )
            self._provider.cancel(receipt.receipt_id)
            return False
        logger.info("alarm_ringing", notification_id=str(row.id))
        return True


__all__ = ["CHANNEL_ALARM", "AlarmRung", "category_of", "marked_important"]
