"""B08 req 646/648/649/650: the backup and the drill are read, and say what they measured.

Both files have been written for weeks. The nightly backup records its snapshot, its timings
and whether the off-host copy succeeded; the weekly drill records how long a real restore
took and whether it passed. Nothing read either of them - no health check, no notification,
no surface anywhere. A backup nobody checks is one you find out about on the day you need it.

RPO and RTO come out of the same two files rather than out of a document: the recovery point
is the age of the last good backup, and the recovery time is what the last drill actually
took. A number in OPERATIONS.md is a claim; a number off the last real run is a measurement.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.backup_health import (
    REASON_NO_RECORD,
    REASON_NOT_VISIBLE,
    REASON_OFFHOST_ABSENT,
    REASON_OFFHOST_FAILED,
    REASON_STALE,
    REASON_UNIT_FAILED,
    REASON_UNREADABLE,
    STALE_BACKUP_AFTER,
    backup_health,
)

NOW = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def backup_root(tmp_path):
    return tmp_path / "pagentos-backup"


def _record(root, *, finished: datetime, offhost: str = "ok", snapshot: str = "abc123") -> None:
    root.mkdir(parents=True, exist_ok=True)
    # The exact shape backup-cloud-core.sh writes, so this test breaks if the script's
    # record changes - two readers of one file, and only one of them is bash.
    (root / "LAST_BACKUP.json").write_text(
        json.dumps(
            {
                "snapshot": snapshot,
                "kind": "nightly",
                "label": "cloud-core",
                "started_at": _iso(finished - timedelta(minutes=4)),
                "finished_at": _iso(finished),
                "seconds": 240,
                "release": "abc1234",
                "alembic": "0042_bounded_delivery",
                "offhost": offhost,
                "buckets": 3,
                "objects": 812,
            }
        ),
        encoding="utf-8",
    )


def _drill(root, *, finished: datetime, verdict: str = "passed", total: int = 214) -> None:
    drills = root / "drills"
    drills.mkdir(parents=True, exist_ok=True)
    stamp = finished.strftime("%Y%m%dT%H%M%SZ")
    (drills / f"{stamp}-drill.json").write_text(
        json.dumps(
            {
                "mode": "drill",
                "snapshot": "abc123",
                "finished_at": _iso(finished),
                "seconds": {
                    "restore": 90,
                    "verify_files": 12,
                    "databases": 60,
                    "objects": 52,
                    "total": total,
                },
                "databases": {"pagentos": 41},
                "objects": 812,
                "verdict": verdict,
            }
        ),
        encoding="utf-8",
    )


def test_a_missing_backup_root_is_skipped_not_failed(backup_root) -> None:
    """The API runs in a container and this lives on the host. A check that cries wolf about
    what it cannot observe is a check the owner learns to ignore."""
    health = backup_health(backup_root, now=NOW)

    assert health["status"] == "skipped"
    assert health["reason"] == REASON_NOT_VISIBLE
    assert health["required"] is False


def test_a_visible_root_with_no_record_at_all_is_a_failure(backup_root) -> None:
    """Visible and empty is different from not visible: something mounted the directory and
    no backup has ever landed in it."""
    backup_root.mkdir(parents=True)

    health = backup_health(backup_root, now=NOW)

    assert health["status"] == "fail"
    assert health["reason"] == REASON_NO_RECORD


def test_last_nights_backup_reports_ok_and_says_how_old_it_is(backup_root) -> None:
    _record(backup_root, finished=NOW - timedelta(hours=5))
    _drill(backup_root, finished=NOW - timedelta(days=3))

    health = backup_health(backup_root, now=NOW)

    assert health["status"] == "ok"
    assert health["rpo_hours"] == 5.0
    assert health["snapshot"] == "abc123"


def test_a_backup_older_than_the_schedule_promises_is_degraded(backup_root) -> None:
    """req 646's own acceptance: past the threshold the product's promise about how much
    work the owner could lose is no longer being kept, and health has to say so."""
    _record(backup_root, finished=NOW - STALE_BACKUP_AFTER - timedelta(hours=1))

    health = backup_health(backup_root, now=NOW)

    assert health["status"] == "fail"
    assert REASON_STALE in health["reasons"]


def test_a_failed_offhost_copy_is_not_a_footnote(backup_root) -> None:
    """The local snapshot is fine and the copy that survives losing this host is not - which
    is precisely the failure the off-host copy exists for."""
    _record(backup_root, finished=NOW - timedelta(hours=2), offhost="failed")

    health = backup_health(backup_root, now=NOW)

    assert health["status"] == "fail"
    assert REASON_OFFHOST_FAILED in health["reasons"]


def test_the_recovery_time_is_what_the_drill_measured(backup_root) -> None:
    """req 650. Not an estimate and not a document: the seconds a real restore took."""
    _record(backup_root, finished=NOW - timedelta(hours=2))
    _drill(backup_root, finished=NOW - timedelta(days=2), total=214)

    health = backup_health(backup_root, now=NOW)

    assert health["rto_seconds"] == 214
    assert health["last_drill_verdict"] == "passed"
    assert health["drill_stale"] is False


def test_a_restore_nobody_has_proven_recently_is_flagged(backup_root) -> None:
    """A backup is not PROVEN_REAL without a restore, and the proof ages."""
    _record(backup_root, finished=NOW - timedelta(hours=2))
    _drill(backup_root, finished=NOW - timedelta(days=40))

    assert backup_health(backup_root, now=NOW)["drill_stale"] is True


def test_no_drill_at_all_reads_as_stale_rather_than_as_fine(backup_root) -> None:
    """Absent evidence is not evidence of a working restore."""
    _record(backup_root, finished=NOW - timedelta(hours=2))

    health = backup_health(backup_root, now=NOW)

    assert health["drill_stale"] is True
    assert health["rto_seconds"] is None


def test_the_newest_drill_is_the_one_reported(backup_root) -> None:
    _record(backup_root, finished=NOW - timedelta(hours=2))
    _drill(backup_root, finished=NOW - timedelta(days=9), total=999)
    _drill(backup_root, finished=NOW - timedelta(days=2), total=214)

    assert backup_health(backup_root, now=NOW)["rto_seconds"] == 214


def test_an_unreadable_record_fails_rather_than_being_treated_as_absent(backup_root) -> None:
    """Corrupt is not missing. Reporting "no backup" for a truncated file would send the
    owner looking for the wrong problem."""
    backup_root.mkdir(parents=True)
    (backup_root / "LAST_BACKUP.json").write_text("{ truncated", encoding="utf-8")

    health = backup_health(backup_root, now=NOW)

    assert health["status"] == "fail"
    assert health["reason"] == REASON_UNREADABLE


def test_a_corrupt_drill_report_does_not_hide_the_one_before_it(backup_root) -> None:
    """One bad file must not make the whole drill history unreadable."""
    _record(backup_root, finished=NOW - timedelta(hours=2))
    _drill(backup_root, finished=NOW - timedelta(days=5), total=180)
    (backup_root / "drills" / "20260913T050000Z-drill.json").write_text("{{{", encoding="utf-8")

    assert backup_health(backup_root, now=NOW)["rto_seconds"] == 180


def test_the_record_shape_matches_what_the_backup_script_writes() -> None:
    """The two halves: bash writes this file and Python reads it. This test reads the SCRIPT
    for the keys rather than trusting the fixture above to have stayed honest."""
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[4] / "scripts" / "cloud" / "backup-cloud-core.sh"
    ).read_text(encoding="utf-8")

    for key in ("snapshot", "finished_at", "offhost", "kind"):
        assert f'"{key}":' in script, f"backup-cloud-core.sh no longer writes {key}"


def test_the_drill_report_shape_matches_what_the_restore_script_writes() -> None:
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[4] / "scripts" / "cloud" / "restore-cloud-core.sh"
    ).read_text(encoding="utf-8")

    for key in ("finished_at", "verdict", "seconds"):
        assert f'"{key}":' in script, f"restore-cloud-core.sh no longer writes {key}"
    assert '"total":' in script, "the drill no longer records a total, which is the RTO"


# ------------------------------------------------------- a failure gets noticed


def _failure_marker(root, unit: str, *, at: datetime) -> None:
    failures = root / "failures"
    failures.mkdir(parents=True, exist_ok=True)
    (failures / f"{unit}.json").write_text(
        json.dumps(
            {"unit": unit, "failed_at": _iso(at), "result": "exit-code", "exit_status": "95"}
        ),
        encoding="utf-8",
    )


def test_a_failed_scheduled_unit_shows_up_even_when_the_last_backup_looks_fine(
    backup_root,
) -> None:
    """req 647. The units had no `OnFailure=` at all: a failure was a journal line nobody
    reads and a file that quietly stopped being updated. Tonight's drill can fail while
    yesterday's backup record still looks perfectly healthy - which is exactly the state
    that used to be invisible."""
    _record(backup_root, finished=NOW - timedelta(hours=5))
    _failure_marker(backup_root, "pagentos-restore-drill.service", at=NOW - timedelta(hours=1))

    health = backup_health(backup_root, now=NOW)

    assert health["status"] == "fail"
    assert REASON_UNIT_FAILED in health["reasons"]
    assert health["failed_units"] == ["pagentos-restore-drill.service"]


def test_a_unit_that_recovered_stops_being_reported(backup_root) -> None:
    """The successful run removes its own marker. A check that complains for ever about one
    bad night is a check nobody reads - which is the defect this requirement is about."""
    _record(backup_root, finished=NOW - timedelta(hours=5))
    _failure_marker(backup_root, "pagentos-backup.service", at=NOW - timedelta(days=2))
    (backup_root / "failures" / "pagentos-backup.service.json").unlink()

    health = backup_health(backup_root, now=NOW)

    assert health["status"] == "ok"
    assert health["failed_units"] == []


def test_an_unreadable_marker_still_counts_as_a_failure(backup_root) -> None:
    """The file being there is the signal; its contents are the detail. Failing to parse the
    detail must not turn a reported failure into silence."""
    _record(backup_root, finished=NOW - timedelta(hours=5))
    (backup_root / "failures").mkdir(parents=True)
    (backup_root / "failures" / "pagentos-backup.service.json").write_text("{{", encoding="utf-8")

    health = backup_health(backup_root, now=NOW)

    assert REASON_UNIT_FAILED in health["reasons"]
    assert health["failed_units"] == ["pagentos-backup.service"]


def test_every_scheduled_unit_reports_its_failures() -> None:
    """The wiring, read from the units themselves. A unit added later with a timer and no
    `OnFailure=` is the defect coming back, and this is what notices."""
    from pathlib import Path

    systemd = Path(__file__).resolve().parents[4] / "infra" / "systemd"
    timers = sorted(systemd.glob("*.timer"))
    assert timers, "no timer units were found - this guard would pass vacuously"

    for timer in timers:
        service = timer.with_suffix(".service")
        assert service.exists(), f"{timer.name} has no service beside it"
        text = service.read_text(encoding="utf-8")
        assert "OnFailure=pagentos-failure-marker@%n.service" in text, (
            f"{service.name} runs on a timer and says nothing when it fails"
        )


def test_the_marker_unit_needs_no_credential_and_no_network() -> None:
    """Deliberate: the moment a backup fails is exactly the moment the host may be unwell,
    and the thing being reported might be the secret store itself."""
    from pathlib import Path

    unit = (
        Path(__file__).resolve().parents[4]
        / "infra"
        / "systemd"
        / "pagentos-failure-marker@.service"
    ).read_text(encoding="utf-8")

    assert "curl" not in unit and "http" not in unit.lower().replace("https://", "")
    assert "Environment=PAGENTOS_BACKUP_ROOT=" in unit


def test_a_successful_run_clears_its_own_marker_in_the_script() -> None:
    """The clearing half, read from the scripts. Without it the first failure would be
    permanent on the health surface."""
    from pathlib import Path

    cloud = Path(__file__).resolve().parents[4] / "scripts" / "cloud"
    backup = (cloud / "backup-cloud-core.sh").read_text(encoding="utf-8")
    restore = (cloud / "restore-cloud-core.sh").read_text(encoding="utf-8")

    assert "failures/pagentos-backup.service.json" in backup
    assert "failures/pagentos-restore-drill.service.json" in restore


# --------------------------------------------- B09: the copy that survives the host


def test_no_offhost_copy_is_reported_without_calling_the_backup_broken(backup_root) -> None:
    """B09 req 644. The repository sits on the disk it protects and nothing said so - the
    backup did everything asked of it, and the gap is a decision nobody has made. Advisory,
    not a failure: blaming last night's backup for a missing second target would send the
    owner to look at the wrong thing."""
    _record(backup_root, finished=NOW - timedelta(hours=5), offhost="not configured")

    health = backup_health(backup_root, now=NOW)

    assert health["status"] == "ok"
    assert REASON_OFFHOST_ABSENT in health["advisories"]


def test_a_configured_but_incomplete_offhost_is_also_flagged(backup_root) -> None:
    """The backup script's own wording for "there is a config file and it names nothing"."""
    _record(
        backup_root,
        finished=NOW - timedelta(hours=5),
        offhost="configured without PAGENTOS_BACKUP_OFFHOST_REPOSITORY",
    )

    assert REASON_OFFHOST_ABSENT in backup_health(backup_root, now=NOW)["advisories"]


def test_a_working_offhost_copy_raises_no_advisory(backup_root) -> None:
    _record(backup_root, finished=NOW - timedelta(hours=5), offhost="ok")

    assert backup_health(backup_root, now=NOW)["advisories"] == []


def test_the_restore_script_can_read_the_offhost_repository() -> None:
    """The gap B09 exists for, read from the script. The copy was WRITE-ONLY: every snapshot
    went to a second repository and this script only ever opened the local one, which on a
    lost host is lost too."""
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[4] / "scripts" / "cloud" / "restore-cloud-core.sh"
    ).read_text(encoding="utf-8")

    assert "--from-offhost" in script
    assert 'export RESTIC_REPOSITORY="$PAGENTOS_BACKUP_OFFHOST_REPOSITORY"' in script


def test_the_offhost_restore_refuses_early_when_the_password_is_not_back_yet() -> None:
    """The repository password is deliberately not in the backup, and on a lost host it is
    not on disk either. Saying so up front is the difference between a procedure and a
    discovery at the worst possible moment."""
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[4] / "scripts" / "cloud" / "restore-cloud-core.sh"
    ).read_text(encoding="utf-8")

    assert "escrow" in script.lower()
    assert "exit 92" in script


def test_the_drill_refuses_a_snapshot_that_cannot_rebuild_a_host() -> None:
    """req 642/643: config and release metadata are not optional extras in a snapshot that
    is supposed to bring a host back."""
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[4] / "scripts" / "cloud" / "restore-cloud-core.sh"
    ).read_text(encoding="utf-8")

    for required in ("config/opt-pagentos/.env", "config/opt-pagentos/RELEASE"):
        assert required in script
