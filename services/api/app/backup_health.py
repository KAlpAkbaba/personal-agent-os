"""B08 req 646/648/649/650: the safety net answers for itself.

The backup runs nightly and writes ``LAST_BACKUP.json``. The restore drill runs weekly and
writes a report with its own timings and a verdict. Both have been running and neither was
ever read by anything: no health check, no notification, no surface. A backup nobody checks
is a backup you find out about on the day you need it, which is the one day it is too late.

**RPO and RTO are not estimates here, they are these two files.** The recovery point
objective is the age of the last good backup - how much work the owner would lose right now.
The recovery time objective is how long the last drill actually took to restore and verify.
Both are published rather than documented: a number in OPERATIONS.md is a claim, and a number
read off the last real run is a measurement.

**Not visible is not broken.** The API runs in a container and the backup root lives on the
host. Where it is not mounted the check says ``skipped`` with the reason, because reporting a
failure the process cannot actually observe teaches the owner to ignore this check.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from app.logging import get_logger

logger = get_logger("app.backup_health")

#: The nightly backup plus enough margin for a slow one and a late start. Past this, the
#: owner's recovery point is further back than the schedule promises, and that is degraded:
#: the product's promise about data loss is no longer being kept.
STALE_BACKUP_AFTER: Final[timedelta] = timedelta(hours=36)

#: The weekly drill, plus a week. A restore that has not been proven this month is a restore
#: nobody has proven - PROVEN_REAL requires a restore, and it ages.
STALE_DRILL_AFTER: Final[timedelta] = timedelta(days=14)

REASON_NOT_VISIBLE: Final[str] = "backup_root_not_visible"
REASON_NO_RECORD: Final[str] = "no_backup_record"
REASON_UNREADABLE: Final[str] = "backup_record_unreadable"
REASON_STALE: Final[str] = "backup_stale"
REASON_UNIT_FAILED: Final[str] = "scheduled_unit_failed"
REASON_OFFHOST_FAILED: Final[str] = "offhost_copy_failed"
#: B09 req 644: there is no second copy at all. Not an error in the backup - the backup
#: worked - but the repository is sitting on the disk it protects, so losing the host loses
#: both. Reported rather than silent, because "no off-host copy" was the actual production
#: state and nothing said so.
REASON_OFFHOST_ABSENT: Final[str] = "no_offhost_copy"


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _latest_drill(root: Path) -> dict[str, Any] | None:
    """The newest drill report, by filename - they are stamped, so the name sorts."""
    drills = root / "drills"
    if not drills.is_dir():
        return None
    reports = sorted(p for p in drills.glob("*.json") if p.is_file())
    for report in reversed(reports):
        try:
            loaded = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(loaded, dict):
            loaded["_report"] = report.name
            return loaded
    return None


def _failures(root: Path) -> list[dict[str, Any]]:
    """Markers left by ``pagentos-failure-marker@.service`` (B08 req 647).

    A scheduled unit that fails writes one here. A unit that succeeds removes its own, so
    what is left is what is currently broken - not a history, which would make this check
    complain for ever about one bad night.
    """
    directory = root / "failures"
    if not directory.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for marker in sorted(directory.glob("*.json")):
        try:
            loaded = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # An unreadable marker still means "this unit failed"; the name says which.
            loaded = {"unit": marker.stem, "unreadable": True}
        if isinstance(loaded, dict):
            out.append(loaded)
    return out


def backup_health(backup_root: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    """The state of the safety net, read off the last real run of each half."""
    moment = now or datetime.now(UTC)
    root = Path(backup_root)
    if not root.is_dir():
        return {
            "status": "skipped",
            "reason": REASON_NOT_VISIBLE,
            "backup_root": str(root),
            # Skipped, not failed: this process cannot see the host's backup directory, and
            # a check that cries wolf about what it cannot observe gets ignored.
            "required": False,
        }

    record_path = root / "LAST_BACKUP.json"
    if not record_path.is_file():
        return {"status": "fail", "reason": REASON_NO_RECORD, "backup_root": str(root)}
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("backup_record_unreadable", error=f"{type(exc).__name__}: {exc}")
        return {"status": "fail", "reason": REASON_UNREADABLE, "backup_root": str(root)}

    finished = _parse_iso(record.get("finished_at"))
    age = None if finished is None else moment - finished
    drill = _latest_drill(root) or {}
    drill_at = _parse_iso(drill.get("finished_at"))
    drill_age = None if drill_at is None else moment - drill_at
    drill_seconds = (drill.get("seconds") or {}).get("total")

    failures = _failures(root)
    reasons: list[str] = []
    #: Things worth seeing that are not this backup failing. Kept apart so a gap in the
    #: disaster-recovery posture never reads as "last night's backup broke".
    advisories: list[str] = []
    if failures:
        reasons.append(REASON_UNIT_FAILED)
    if age is None or age > STALE_BACKUP_AFTER:
        reasons.append(REASON_STALE)
    offhost = record.get("offhost")
    if offhost in (None, "", "not configured") or str(offhost).startswith("configured without"):
        # Advisory rather than a failure: the nightly backup did everything asked of it.
        # What is missing is a decision nobody has made yet (an off-host target), and
        # reporting it as a broken backup would be blaming the wrong thing.
        advisories.append(REASON_OFFHOST_ABSENT)
    if offhost == "failed":
        # The local snapshot is good; the copy that survives losing this host is not. That
        # is precisely the failure the off-host copy exists for, so it is not a footnote.
        reasons.append(REASON_OFFHOST_FAILED)

    return {
        "status": "fail" if reasons else "ok",
        "reasons": reasons,
        "advisories": advisories,
        "backup_root": str(root),
        "snapshot": record.get("snapshot"),
        "kind": record.get("kind"),
        "finished_at": record.get("finished_at"),
        "offhost": record.get("offhost"),
        # req 649: the recovery POINT - how much the owner would lose if the host died now.
        "rpo_hours": None if age is None else round(age.total_seconds() / 3600, 2),
        # req 650: the recovery TIME - what the last drill actually took, not an estimate.
        "rto_seconds": drill_seconds,
        "last_drill_at": drill.get("finished_at"),
        "last_drill_verdict": drill.get("verdict"),
        "last_drill_report": drill.get("_report"),
        "drill_stale": drill_age is None or drill_age > STALE_DRILL_AFTER,
        # req 647: which scheduled units are currently in a failed state, from the markers
        # their OnFailure= wrote. A unit that has since succeeded removed its own.
        "failed_units": [str(f.get("unit") or "?") for f in failures],
    }


__all__ = [
    "REASON_NOT_VISIBLE",
    "REASON_NO_RECORD",
    "REASON_OFFHOST_ABSENT",
    "REASON_OFFHOST_FAILED",
    "REASON_STALE",
    "REASON_UNIT_FAILED",
    "REASON_UNREADABLE",
    "STALE_BACKUP_AFTER",
    "STALE_DRILL_AFTER",
    "backup_health",
]
