"""Availability, measured or "no measurement" (docs/M18_4_SELF_EVOLUTION_SPEC.md §13).

Two honest sources and nothing else:

- the ledger's deployment and incident rows (``deployment.cloud_core.released`` /
  ``rolled_back``, ``incident.opened``) - how many release windows and incidents the last
  24 h / 7 d saw;
- this process's own start instant - how long the running version has been up.

What is NOT here, and is said so in the payload: a probe-based availability fraction. No
external prober records probes yet (the recovery supervisor polls health but keeps no
sample history the API can read), so ``availability`` is ``null`` with
``measurement: "none"`` - a target dressed as a result is exactly what this module exists
to refuse.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_RELEASED,
    EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_ROLLED_BACK,
    EVENT_TYPE_INCIDENT_OPENED,
)
from app.release.version import STARTED_AT

SLO_VERSION = 1
WINDOWS: dict[str, timedelta] = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}


def _count(rows: list[Any], event_type: str, since: datetime) -> int:
    total = 0
    for row in rows:
        at = row.occurred_at
        if at is not None and at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        if row.event_type == event_type and at is not None and at >= since:
            total += 1
    return total


def slo_report(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    rows = ledger_service.query(
        session,
        since=moment - WINDOWS["7d"],
        event_types=[
            EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_RELEASED,
            EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_ROLLED_BACK,
            EVENT_TYPE_INCIDENT_OPENED,
        ],
        limit=500,
    )
    windows: dict[str, dict[str, int]] = {}
    for name, span in WINDOWS.items():
        since = moment - span
        windows[name] = {
            "releases": _count(rows, EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_RELEASED, since),
            "rollbacks": _count(rows, EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_ROLLED_BACK, since),
            "incidents": _count(rows, EVENT_TYPE_INCIDENT_OPENED, since),
        }
    return {
        "slo_version": SLO_VERSION,
        "observed_at": moment.isoformat().replace("+00:00", "Z"),
        "process_uptime_s": max(0.0, (moment - STARTED_AT).total_seconds()),
        "windows": windows,
        # The number the owner would want, and the truth about it.
        "availability": None,
        "measurement": "none",
        "measurement_note": (
            "No probe history is recorded yet; the recovery supervisor polls health but keeps "
            "no samples the API can read. Release windows and incidents above are counted "
            "from the ledger; a fraction is not claimed."
        ),
    }


__all__ = ["SLO_VERSION", "slo_report"]
