"""Every ``OnFailure=`` a unit names must be a unit an installer actually ships.

2026-09-18, measured on the live host right after the recovery supervisor was installed:
``pagentos-bluegreen-reconcile.service`` (and the backup and restore-drill units) declare
``OnFailure=pagentos-failure-marker@%n.service``, that template lived in the repository, and
NO installer copied it to ``/etc/systemd/system``. systemd answered "No files found for
pagentos-failure-marker@x.service": a failed backup, drill or recovery wrote no marker, so
the product's ``backup`` health check - and req 647's whole notification path - could never
see one. Both halves were green: the unit files named it and the health check read the
directory, and nothing tied the name to an installer.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SYSTEMD = REPO / "infra" / "systemd"
INSTALLERS = [
    REPO / "scripts" / "cloud" / "install-backup.sh",
    REPO / "scripts" / "cloud" / "install-recovery-supervisor.sh",
]


def declared_on_failure_targets() -> dict[str, list[str]]:
    """{template unit file name: [units that name it]} across every shipped unit."""
    targets: dict[str, list[str]] = {}
    for unit in sorted(SYSTEMD.glob("*.service")):
        for value in re.findall(r"^OnFailure=(\S+)$", unit.read_text(encoding="utf-8"), re.M):
            # `foo@%n.service` is an instance of the template file `foo@.service`.
            template = re.sub(r"@\S*\.service$", "@.service", value)
            targets.setdefault(template, []).append(unit.name)
    return targets


def test_the_units_do_name_a_failure_handler() -> None:
    targets = declared_on_failure_targets()
    assert targets, "no unit declares OnFailure - req 647's marker would never be written"
    assert "pagentos-failure-marker@.service" in targets


def test_every_on_failure_target_exists_as_a_unit_file() -> None:
    for template in declared_on_failure_targets():
        assert (SYSTEMD / template).is_file(), f"{template} is named by OnFailure but not shipped"


def test_every_on_failure_target_is_installed_by_an_installer() -> None:
    """The host only has what an installer copies. A unit file in the repository that no
    installer names is a handler that does not exist where it has to run."""
    installer_text = "\n".join(p.read_text(encoding="utf-8") for p in INSTALLERS)
    for template, named_by in declared_on_failure_targets().items():
        assert template in installer_text, (
            f"{template} is named by {', '.join(sorted(named_by))} but no installer ships it"
        )


def test_the_marker_writes_where_the_health_check_reads() -> None:
    """The marker's directory and the backup health check's directory are one place."""
    marker = (SYSTEMD / "pagentos-failure-marker@.service").read_text(encoding="utf-8")
    assert "PAGENTOS_BACKUP_ROOT" in marker
    assert '"${PAGENTOS_BACKUP_ROOT}/failures"' in marker
    health = (REPO / "services" / "api" / "app" / "backup_health.py").read_text(encoding="utf-8")
    assert '"failures"' in health


# --------------------------------------------------------- the check must be able to look

COMPOSE = REPO / "infra" / "docker" / "docker-compose.prod.yml"
BACKUP_HEALTH = REPO / "services" / "api" / "app" / "backup_health.py"


def paths_backup_health_reads() -> set[str]:
    """Every ``root / "<name>"`` the backup health check opens."""
    text = BACKUP_HEALTH.read_text(encoding="utf-8")
    return set(re.findall(r'root\s*/\s*"([^"]+)"', text))


def test_production_mounts_every_path_the_backup_check_reads() -> None:
    """2026-09-18, live: the API container mounted none of it, so the check answered
    `skipped: backup_root_not_visible` for ever - reqs 646-650 and the 647 notice were blind
    in the one environment they exist for, while both halves were green on their own."""
    compose = COMPOSE.read_text(encoding="utf-8")
    assert paths_backup_health_reads(), "the check reads nothing under the backup root"
    for name in paths_backup_health_reads():
        assert f"/var/lib/pagentos-backup/{name}:ro" in compose, f"{name} is not mounted"


def test_the_backup_mounts_are_read_only_and_exclude_the_repository() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    mounts = [
        line
        for line in compose.splitlines()
        if "/var/lib/pagentos-backup" in line and ":ro" in line
    ]
    assert mounts
    assert all(line.rstrip().endswith(":ro") for line in mounts)
    # The encrypted repository and the password are never handed to the application.
    assert "/var/lib/pagentos-backup/restic" not in compose
    assert "backup.password" not in compose


# ------------------------------------------- a benign skip is not a failure, and clears

RECONCILE = REPO / "scripts" / "cloud" / "release-cloud-core-bluegreen.sh"
RECONCILE_UNIT = SYSTEMD / "pagentos-bluegreen-reconcile.service"
LOCK_HELD_EXIT = 82


def test_the_lock_held_skip_is_not_counted_as_a_unit_failure() -> None:
    """2026-09-18, live: a release holds the blue/green operation lock, so the timer's
    reconcile exits 82 ("another operation is running") every minute while it runs. systemd
    counted that as a failure, OnFailure wrote a marker, the backup check read it, the app
    reported degraded - and the release then refused to promote its own candidate and rolled
    back. The skip is a skip."""
    script = RECONCILE.read_text(encoding="utf-8")
    assert f"exit {LOCK_HELD_EXIT}" in script
    assert "another blue/green release or recovery operation is running" in script
    unit = RECONCILE_UNIT.read_text(encoding="utf-8")
    assert re.search(rf"^SuccessExitStatus=(?:[^\n]*\b){LOCK_HELD_EXIT}\b", unit, re.M), (
        f"the unit must accept {LOCK_HELD_EXIT} as success"
    )


def test_a_successful_reconcile_clears_its_own_marker() -> None:
    """Every scheduled unit that writes a marker on failure clears it on its next success -
    the backup and the restore drill already did; the reconcile did not, so one failed run
    left the product degraded for ever."""
    marker = "failures/pagentos-bluegreen-reconcile.service.json"
    script = RECONCILE.read_text(encoding="utf-8")
    assert marker in script
    clearing = [line for line in script.splitlines() if marker in line]
    assert clearing and all(line.strip().startswith("rm -f") for line in clearing)
    for peer, own in (
        ("scripts/cloud/backup-cloud-core.sh", "failures/pagentos-backup.service.json"),
        ("scripts/cloud/restore-cloud-core.sh", "failures/pagentos-restore-drill.service.json"),
    ):
        assert own in (REPO / peer).read_text(encoding="utf-8"), peer
