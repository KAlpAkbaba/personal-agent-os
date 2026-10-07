"""``SqlReceiptStore``: the ``ReceiptStore`` protocol on the ``urgent_alert_receipts`` table.

Every write commits its own short transaction from ``session_factory`` (the ladder's rung and
the receipt loop both run off a request). The receipt row is committed BEFORE its ledger line
(``alert.sent`` / ``alert.seen`` / ``alert.unseen``): a ledger fault may cost a line, never the
one handle that can stop the ringing. Ledger lines carry ``source_ref = notification:<id>``
and the receipt id's first six characters, never the provider's answer (it names the user
key).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ledger import service as ledger
from app.ledger import vocabulary as v
from app.notifications.models import NotificationRow
from app.urgent_alert.models import (
    OUTCOME_SEEN,
    OUTCOME_UNSEEN,
    SOURCE_PUSHOVER,
    UrgentAlertReceiptRow,
)
from app.urgent_alert.provider import AlarmReceipt
from app.urgent_alert.receipts import OpenReceipt


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def write_ledger(
    db: Session,
    *,
    event_type: str,
    notification_id: uuid.UUID,
    summary: str,
    at: datetime,
    detail: dict[str, Any],
    status: str = v.STATUS_COMPLETED,
) -> None:
    """One ``urgent_alert`` ledger line (``ledger.record`` commits it)."""
    ledger.record(
        db,
        ledger.ActivityEvent(
            event_type=event_type,
            subsystem=v.SUBSYSTEM_URGENT_ALERT,
            action=event_type,
            factual_summary=summary,
            source=event_type,
            source_ref=f"notification:{notification_id}",
            status=status,
            occurred_at=at,
            module="app.urgent_alert",
            detail_json=detail,
        ),
    )


@dataclass(frozen=True)
class ReadReceipt:
    """An open receipt whose notification the owner has already read somewhere."""

    item: OpenReceipt
    read_at: datetime


class SqlReceiptStore:
    def __init__(
        self,
        session_factory: Any,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    @property
    def session_factory(self) -> Any:
        return self._session_factory

    def open(self, notification_id: uuid.UUID, receipt: AlarmReceipt) -> None:
        with self._session_factory() as db:
            db.add(
                UrgentAlertReceiptRow(
                    notification_id=notification_id,
                    receipt_id=receipt.receipt_id,
                    sent_at=receipt.sent_at,
                    expires_at=receipt.expires_at,
                    source=SOURCE_PUSHOVER,
                )
            )
            # The receipt first, on its own: a ledger fault must never lose the one handle
            # that can stop the ringing.
            db.commit()
            write_ledger(
                db,
                event_type=v.EVENT_TYPE_ALERT_SENT,
                notification_id=notification_id,
                summary="Önemli bildirim için telefon çalıyor (Pushover).",
                at=receipt.sent_at,
                detail={
                    "receipt": receipt.receipt_id[:6],
                    "expires_at": receipt.expires_at.isoformat(),
                },
                status=v.STATUS_STARTED,
            )

    def _open_rows(self, db: Session) -> list[UrgentAlertReceiptRow]:
        return list(
            db.execute(
                select(UrgentAlertReceiptRow)
                .where(UrgentAlertReceiptRow.closed_at.is_(None))
                .order_by(UrgentAlertReceiptRow.sent_at.asc())
            )
            .scalars()
            .all()
        )

    @staticmethod
    def _item(row: UrgentAlertReceiptRow) -> OpenReceipt:
        return OpenReceipt(
            row.notification_id,
            AlarmReceipt(
                receipt_id=row.receipt_id,
                sent_at=_aware(row.sent_at),  # type: ignore[arg-type]
                expires_at=_aware(row.expires_at),  # type: ignore[arg-type]
            ),
        )

    def list_open(self) -> list[OpenReceipt]:
        with self._session_factory() as db:
            return [self._item(row) for row in self._open_rows(db)]

    def count_open(self) -> int:
        with self._session_factory() as db:
            return int(
                db.execute(
                    select(func.count())
                    .select_from(UrgentAlertReceiptRow)
                    .where(UrgentAlertReceiptRow.closed_at.is_(None))
                ).scalar_one()
            )

    def read_elsewhere(self) -> list[ReadReceipt]:
        """Open receipts whose notification is already read (``read_at``): the web inbox,
        or a toast - the phone has nothing left to tell him."""
        with self._session_factory() as db:
            rows = db.execute(
                select(UrgentAlertReceiptRow, NotificationRow.read_at)
                .join(NotificationRow, NotificationRow.id == UrgentAlertReceiptRow.notification_id)
                .where(
                    UrgentAlertReceiptRow.closed_at.is_(None),
                    NotificationRow.read_at.is_not(None),
                )
            ).all()
            return [ReadReceipt(self._item(row), _aware(read_at)) for row, read_at in rows]  # type: ignore[arg-type]

    def close(
        self,
        receipt_id: str,
        outcome: str,
        at: datetime,
        *,
        source: str = SOURCE_PUSHOVER,
    ) -> None:
        """Close one receipt and write its ledger line: ``seen`` and ``cancelled`` (read
        elsewhere) are ``alert.seen``, ``unseen`` is ``alert.unseen``. A receipt already
        closed is left as it is (two passes never report one twice)."""
        event_type = (
            v.EVENT_TYPE_ALERT_UNSEEN if outcome == OUTCOME_UNSEEN else v.EVENT_TYPE_ALERT_SEEN
        )
        with self._session_factory() as db:
            row = db.execute(
                select(UrgentAlertReceiptRow).where(
                    UrgentAlertReceiptRow.receipt_id == receipt_id,
                    UrgentAlertReceiptRow.closed_at.is_(None),
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.closed_at = self._clock()
            row.outcome = outcome
            row.source = source
            if outcome == OUTCOME_SEEN or source != SOURCE_PUSHOVER:
                row.seen_at = at
            notification_id = row.notification_id
            db.commit()
            write_ledger(
                db,
                event_type=event_type,
                notification_id=notification_id,
                summary=_SUMMARIES[event_type],
                at=at,
                detail={"receipt": receipt_id[:6], "outcome": outcome, "source": source},
            )

    def last_closed(self) -> tuple[str, datetime | None] | None:
        """The newest closed receipt's outcome and when he saw it (``None`` if never)."""
        with self._session_factory() as db:
            row = db.execute(
                select(UrgentAlertReceiptRow)
                .where(UrgentAlertReceiptRow.closed_at.is_not(None))
                .order_by(UrgentAlertReceiptRow.closed_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None or row.outcome is None:
                return None
            return row.outcome, _aware(row.seen_at)


_SUMMARIES: dict[str, str] = {
    v.EVENT_TYPE_ALERT_SEEN: "Sahip önemli bildirimi gördü.",
    v.EVENT_TYPE_ALERT_UNSEEN: "Önemli bildirim görülmeden çalma süresi doldu.",
}


__all__ = ["ReadReceipt", "SqlReceiptStore", "write_ledger"]
