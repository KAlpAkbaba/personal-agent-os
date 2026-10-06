"""Where the alarm rung meets the application: the two builders ``app.main`` calls.

``build_alarm_rung(settings, session_factory)`` is the rung, or ``None`` - never a rung that
can only say no - when either Pushover key or the tailnet link root is missing. Importance is
read from ONE place, ``app.telephony.policy`` (the same table that decides a phone call), and
the alarm's one word from that policy's ``ALERT_CATEGORIES``. ``build_receipt_loop`` is the
loop that asks Pushover what became of each alarm; it exists without keys too, so health
reports it "skipped", not missing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings
from app.ledger import vocabulary as v
from app.logging import get_logger
from app.notifications.models import NotificationRow
from app.telephony import policy
from app.urgent_alert import text
from app.urgent_alert.loop import UrgentAlertLoop
from app.urgent_alert.pushover import PushoverProvider
from app.urgent_alert.rung import AlarmRung
from app.urgent_alert.store_sql import SqlReceiptStore, write_ledger

logger = get_logger("app.urgent_alert.wiring")


def is_important(row: NotificationRow) -> bool:
    return policy.is_important_notification(row.kind)


def category_for(row: NotificationRow) -> str:
    return policy.alert_category(row.kind)


def link_for_base(base: str) -> Callable[[NotificationRow], str]:
    root = base.rstrip("/")
    return lambda row: f"{root}/notifications/{row.id}"


def _refused_to_ledger(session_factory: Any, clock: Callable[[], datetime]):  # noqa: ANN202
    def record(row: NotificationRow, reason: str) -> None:
        try:
            with session_factory() as db:
                write_ledger(
                    db,
                    event_type=v.EVENT_TYPE_ALERT_REFUSED,
                    notification_id=row.id,
                    summary="Önemli bildirimin telefon metni reddedildi; telefon çalmadı.",
                    at=clock(),
                    detail={"reason": reason},
                    status=v.STATUS_FAILED,
                )
        except Exception as exc:  # noqa: BLE001 - a broken ledger never stops the ladder
            logger.warning("urgent_alert_ledger_failed", error=type(exc).__name__)

    return record


def build_alarm_rung(
    settings: Settings,
    session_factory: Any,
    *,
    client: httpx.Client | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    link_for: Callable[[NotificationRow], str] | None = None,
) -> AlarmRung | None:
    app_token = settings.urgent_alert_pushover_app_token.get_secret_value()
    user_key = settings.urgent_alert_pushover_user_key.get_secret_value()
    if not app_token or not user_key:
        logger.info("urgent_alert_unavailable", reason="no_pushover_keys")
        return None
    base = settings.urgent_alert_link_base.strip().rstrip("/")
    if not base or text.link_refusal(f"{base}/notifications", root=base) is not None:
        logger.info("urgent_alert_unavailable", reason="no_tailnet_link_base")
        return None
    provider = PushoverProvider(app_token, user_key, client=client, clock=clock)
    return AlarmRung(
        provider,
        SqlReceiptStore(session_factory, clock=clock),
        link_for=link_for or link_for_base(base),
        is_important=is_important,
        category=category_for,
        link_root=base,
        on_refused=_refused_to_ledger(session_factory, clock),
    )


def build_receipt_loop(
    settings: Settings,
    session_factory: Any,
    alarm_rung: AlarmRung | None,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> UrgentAlertLoop:
    store = (
        alarm_rung.store
        if alarm_rung is not None and isinstance(alarm_rung.store, SqlReceiptStore)
        else SqlReceiptStore(session_factory, clock=clock)
    )
    return UrgentAlertLoop(
        store,
        rung=alarm_rung,
        interval_s=settings.urgent_alert_poll_interval_s,
        clock=clock,
    )


__all__ = [
    "build_alarm_rung",
    "build_receipt_loop",
    "category_for",
    "is_important",
    "link_for_base",
]
