"""``AlarmRung``: the ladder rung that rings the phone.

It speaks ``app.notifications.ladder.Rung`` exactly (``name``, ``available()``,
``deliver(row)``). ``deliver`` returning ``True`` means the provider accepted the alarm AND its
receipt was kept: an alarm whose receipt is lost could never be reported seen or unseen, so it
is cancelled and the ladder steps down instead. Only rows marked important ring; everything
else returns ``False`` without a request, and the ladder moves on as it does for any rung that
did not reach the owner. ``True`` is still not "he saw it" - that is ``alert.seen``, from the
receipt (``app.urgent_alert.loop``).
"""

from __future__ import annotations

from collections.abc import Callable

from app.logging import get_logger
from app.notifications.models import CHANNEL_ALARM, NotificationRow
from app.urgent_alert import text
from app.urgent_alert.provider import AlarmProvider
from app.urgent_alert.receipts import ReceiptStore

logger = get_logger("app.urgent_alert.rung")


def marked_important(row: NotificationRow) -> bool:
    """A row-level importance flag: ``data_json["important"] is True``. The wiring passes
    the telephony policy's predicate instead (``app.urgent_alert.wiring.is_important``)."""
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
        link_root: str | None = None,
        on_refused: Callable[[NotificationRow, str], None] | None = None,
    ) -> None:
        self._provider = provider
        self._store = store
        self._link_for = link_for
        self._is_important = is_important
        self._category = category
        self._link_root = link_root
        self._on_refused = on_refused

    @property
    def provider(self) -> AlarmProvider | None:
        return self._provider

    @property
    def store(self) -> ReceiptStore:
        return self._store

    def available(self) -> bool:
        return self._provider is not None and self._provider.configured()

    def rings_for(self, row: NotificationRow) -> bool:
        """Whether this rung would ring the phone for ``row`` - what the toast rung asks
        before it lets a toast nobody may have seen end the ladder."""
        return self.available() and self._is_important(row)

    def deliver(self, row: NotificationRow) -> bool:
        if self._provider is None or not self._is_important(row):
            return False
        try:
            title, body, url = text.compose(
                self._category(row), self._link_for(row), root=self._link_root
            )
        except text.TextRefused as exc:
            # The reason only: the refused text may be exactly what must not be logged.
            logger.error("alarm_text_refused", notification_id=str(row.id), reason=exc.reason)
            if self._on_refused is not None:
                self._on_refused(row, exc.reason)
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
