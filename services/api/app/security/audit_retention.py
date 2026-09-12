"""B07 req 679: the audit trail stops growing without bound - carefully.

The security audit ledger works. What it has never had is an end: 13,560 events by
2026-09-12 and nothing that would ever remove one. That is a slow problem rather than an
urgent one, which is exactly the kind that gets fixed badly, so the shape here is
conservative in three deliberate ways.

**Not every audit table is the same thing, and treating them alike would be the mistake.**

* ``audit_events`` - the broker's operational record of device traffic. High volume, low
  individual value, and the reason the number is thirteen thousand. Six months.
* ``session_events`` - authentication decisions: who authenticated, what was refused and
  why. Low volume, high individual value, and the thing you actually want when a question
  about access comes up months later. A year.
* ``activity_events`` - the **Activity Ledger**, and it is NOT swept at all. It is the
  evidence base the Self Explanation engine answers "son ne yaptın" from, and
  ``TruthKind.EVIDENCE`` never ages by design: what happened stays happened. Deleting it to
  save rows would be trading the product's memory for disk space. Named in
  :data:`NEVER_SWEPT` with the reason, so this is a decision and not an omission.

**Dry run first.** The sweep counts what it WOULD delete and deletes nothing until it is
told to, which is the roadmap's own rollback plan for this batch. A retention sweep is the
one kind of housekeeping whose bug is unrecoverable, and "it would have removed 9,000 rows"
is a sentence the owner should get to read before it is true.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.logging import get_logger

logger = get_logger("app.security.audit_retention")

#: table name -> how long a row is kept. See the module docstring for why they differ.
RETENTION: Final[dict[str, timedelta]] = {
    "audit_events": timedelta(days=180),
    "session_events": timedelta(days=365),
}

#: Audit tables that are deliberately never swept, and why. A retention policy that quietly
#: skipped a table would look identical to one that forgot it.
NEVER_SWEPT: Final[dict[str, str]] = {
    "activity_events": (
        "the Activity Ledger is the evidence base the owner's 'what have you been doing' "
        "answers are built from, and EVIDENCE truth never ages by design: what happened "
        "stays happened. Rows here are the product's memory, not its exhaust."
    ),
}


def _model_for(table: str) -> Any:
    from app.broker.models import AuditEvent
    from app.identity.models import SessionEvent

    return {"audit_events": AuditEvent, "session_events": SessionEvent}[table]


def sweep_audit_retention(
    db: Session, *, now: datetime | None = None, dry_run: bool = True
) -> dict[str, int]:
    """Count (and, when told to, remove) audit rows past their retention.

    Returns ``{table: rows}`` - what was deleted, or what would have been. One table's
    failure never stops the others: the same rule the RetentionSweeper applies to sweeps,
    applied here to tables, because a broker table that has grown a lock must not stop the
    identity table being tidied.
    """
    moment = now or datetime.now(UTC)
    counted: dict[str, int] = {}
    for table, keep_for in RETENTION.items():
        horizon = moment - keep_for
        model = _model_for(table)
        try:
            rows = int(
                db.execute(
                    select(func.count()).select_from(model).where(model.created_at < horizon)
                ).scalar_one()
            )
            if rows and not dry_run:
                db.execute(delete(model).where(model.created_at < horizon))
                db.commit()
            counted[table] = rows
        except Exception as exc:  # noqa: BLE001 - one table never stops the others
            db.rollback()
            logger.warning(
                "audit_retention_table_failed",
                table=table,
                error=f"{type(exc).__name__}: {exc}",
            )
            counted[table] = 0
    total = sum(counted.values())
    if total:
        logger.info(
            "audit_retention_swept",
            dry_run=dry_run,
            total=total,
            **{f"rows_{table}": count for table, count in counted.items()},
        )
    return counted


def health_check(*, dry_run: bool) -> dict[str, Any]:
    """What the retention policy IS, on the health surface. A policy nobody can read is a
    policy nobody can check."""
    return {
        "status": "ok",
        "required": False,
        "dry_run": dry_run,
        "retention_days": {table: int(keep.days) for table, keep in RETENTION.items()},
        "never_swept": sorted(NEVER_SWEPT),
    }


__all__ = ["NEVER_SWEPT", "RETENTION", "health_check", "sweep_audit_retention"]
