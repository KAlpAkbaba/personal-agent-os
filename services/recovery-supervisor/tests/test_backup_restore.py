"""The Cloud Core backup, its restore drill and its guarded restore (ADR-0122).

Real scripts, real restic-shaped snapshots, the real manifest/fingerprint helper; fake
docker/restic/flock/systemctl/apt-get (tests/backup_fakes.py). What a fake cannot prove -
that the real pg_dump/pg_restore/mc/restic agree with each other on the real host - is the
measured drill on production, recorded as evidence, not here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "cloud"))
import backup_manifest  # noqa: E402
from backup_fakes import BACKUP, INSTALL, RESTORE, Host, dump_text  # noqa: E402


def _bash() -> str:
    if os.name == "nt":
        git_bash = Path(os.environ["ProgramFiles"]) / "Git" / "bin" / "bash.exe"
        if git_bash.is_file():
            return str(git_bash)
    found = shutil.which("bash")
    if not found:
        raise RuntimeError("bash is required")
    return found


def run(script: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), script.as_posix(), *args], env=env, text=True, capture_output=True, check=False
    )


@pytest.fixture()
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


def _backup(host: Host, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return run(BACKUP, *args, env=host.env(**env))


# ------------------------------------------------------------------ the fingerprint itself


def test_a_fingerprint_does_not_care_what_order_the_rows_are_stored_in() -> None:
    one = dump_text({"public.t": ("(id)", [("1",), ("2",), ("3",)])})
    other = dump_text({"public.t": ("(id)", [("3",), ("1",), ("2",)])})
    a = backup_manifest.fingerprint(one.splitlines(True))
    b = backup_manifest.fingerprint(other.splitlines(True))
    assert a == b
    assert a["tables"]["public.t"]["rows"] == 3


def test_a_fingerprint_sees_one_changed_row_and_a_moved_sequence() -> None:
    base = backup_manifest.fingerprint(
        dump_text(
            {"public.t": ("(id, v)", [("1", "a")])}, ["SELECT pg_catalog.setval('s', 1, true);"]
        ).splitlines(True)
    )
    changed = backup_manifest.fingerprint(
        dump_text(
            {"public.t": ("(id, v)", [("1", "b")])}, ["SELECT pg_catalog.setval('s', 1, true);"]
        ).splitlines(True)
    )
    moved = backup_manifest.fingerprint(
        dump_text(
            {"public.t": ("(id, v)", [("1", "a")])}, ["SELECT pg_catalog.setval('s', 2, true);"]
        ).splitlines(True)
    )
    assert backup_manifest.compare(base, changed) == ["table differs: public.t (rows 1 -> 1)"]
    assert backup_manifest.compare(base, moved) == ["sequence positions differ"]


def test_a_truncated_dump_is_refused_rather_than_fingerprinted_short() -> None:
    text = "COPY public.t (id) FROM stdin;\n1\n2\n"
    with pytest.raises(ValueError, match="never ended"):
        backup_manifest.fingerprint(text.splitlines(True))


# ------------------------------------------------------------------ backup


def test_a_backup_holds_every_database_every_object_and_the_hosts_configuration(host: Host) -> None:
    completed = _backup(host)

    assert completed.returncode == 0, completed.stderr
    assert "BACKUP OK" in completed.stdout
    [snapshot] = host.snapshots()
    tree = snapshot / "tree"
    for db in ("pagentos_prod", "postgres", "temporal"):
        assert (tree / "postgres" / f"{db}.dump").is_file()
        assert (tree / "postgres" / f"{db}.fingerprint.json").is_file()
    assert (tree / "postgres" / "globals.sql").read_text("utf-8").startswith("CREATE ROLE")
    assert (tree / "minio" / "pagentos-artifacts" / "notlarim.exe").read_bytes().startswith(b"MZ")
    assert (tree / "minio" / "pagentos-artifacts" / "tasks" / "7" / "report.html").is_file()
    for name in (".env", "RELEASE", "LAST_KNOWN_GOOD"):
        assert (tree / "config" / "opt-pagentos" / name).is_file(), name
    assert (tree / "config" / "pagentos-data" / "identity" / "owner.credential").is_file()
    assert (tree / "config" / "systemd" / "pagentos-bluegreen-reconcile.service").is_file()
    # The password is not locked inside what it opens.
    assert not any(p.name == "backup.password" for p in tree.rglob("*"))

    manifest = json.loads((tree / "MANIFEST.json").read_text("utf-8"))
    assert manifest["metadata"]["kind"] == "scheduled"
    assert manifest["metadata"]["release"] == "c" * 40
    assert manifest["metadata"]["alembic"] == "0040_fake"
    assert manifest["databases"]["pagentos_prod"]["tables"]["public.tasks"]["rows"] == 2
    assert manifest["totals"]["objects"] == 2
    assert backup_manifest.verify(tree) == []

    restic = host.log("restic")
    assert any(" init" in line for line in restic)
    backup_call = next(line for line in restic if " backup " in line)
    assert "--tag pagentos --tag scheduled" in backup_call
    forget = next(line for line in restic if " forget " in line)
    assert "--tag scheduled --keep-daily 14 --keep-weekly 8 --keep-monthly 6 --prune" in forget
    assert any(" check --read-data-subset=100%" in line for line in restic)

    record = json.loads((host.backup_root / "LAST_BACKUP.json").read_text("utf-8"))
    assert record["snapshot"] == snapshot.name
    assert record["offhost"] == "not configured"
    # Nothing is left behind: not the staging copy, not the mirror inside the container.
    assert not (host.backup_root / "staging" / "snapshot").exists()
    assert not any((host.state / "fs" / "minio" / "tmp").glob("pagentos-backup-*"))


def test_a_pre_migration_backup_keeps_its_own_last_ten(host: Host) -> None:
    completed = _backup(host, "--kind", "pre-migration", "--label", "release-cc0212bf2377")

    assert completed.returncode == 0, completed.stderr
    backup_call = next(line for line in host.log("restic") if " backup " in line)
    assert "--tag pre-migration --tag release-cc0212bf2377" in backup_call
    forget = next(line for line in host.log("restic") if " forget " in line)
    assert "--tag pre-migration --keep-last 10 --prune" in forget


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (("--kind", "hourly"), "unknown --kind"),
        (("--label", "a b"), "may hold only"),
        (("--x",), "usage"),
    ],
)
def test_bad_arguments_are_refused_before_anything_runs(host: Host, args, needle) -> None:
    completed = _backup(host, *args)
    assert completed.returncode == 2
    assert needle in completed.stderr
    assert host.log("docker") == []


def test_no_password_no_backup(host: Host) -> None:
    (host.base / "backup.password").unlink()
    completed = _backup(host)
    assert completed.returncode == 91
    assert host.log("docker") == []


def test_a_second_backup_while_one_runs_is_not_started(host: Host) -> None:
    completed = _backup(host, FAKE_FLOCK_EXIT="1")
    assert completed.returncode == 90
    assert host.log("docker") == []


def test_a_failed_dump_takes_no_snapshot_and_leaves_no_staging(host: Host) -> None:
    completed = _backup(host, FAKE_PGDUMP_FAIL="temporal")
    assert completed.returncode == 92
    assert "pg_dump temporal failed" in completed.stderr
    assert host.snapshots() == []
    assert not (host.backup_root / "staging" / "snapshot").exists()
    assert not (host.backup_root / "LAST_BACKUP.json").exists()


def test_a_failed_object_mirror_takes_no_snapshot(host: Host) -> None:
    completed = _backup(host, FAKE_MIRROR_EXIT="1")
    assert completed.returncode == 93
    assert host.snapshots() == []


def test_a_repository_that_fails_its_read_back_fails_the_backup(host: Host) -> None:
    completed = _backup(host, FAKE_RESTIC_CHECK_EXIT="1")
    assert completed.returncode == 94
    assert "damaged" in completed.stderr
    assert not (host.backup_root / "LAST_BACKUP.json").exists()


def test_the_off_host_copy_follows_the_local_snapshot(host: Host) -> None:
    (host.base / "backup-offhost.env").write_text(
        f"PAGENTOS_BACKUP_OFFHOST_REPOSITORY={(host.root / 'offhost').as_posix()}\n", "utf-8"
    )
    completed = _backup(host)
    assert completed.returncode == 0, completed.stderr
    copies = [line for line in host.log("restic") if " copy " in line]
    assert len(copies) == 1 and "offhost" in copies[0]
    assert json.loads((host.backup_root / "LAST_BACKUP.json").read_text("utf-8"))["offhost"] == "ok"


def test_an_off_host_failure_is_loud_and_the_local_snapshot_still_stands(host: Host) -> None:
    (host.base / "backup-offhost.env").write_text(
        f"PAGENTOS_BACKUP_OFFHOST_REPOSITORY={(host.root / 'offhost').as_posix()}\n", "utf-8"
    )
    completed = _backup(host, FAKE_RESTIC_COPY_EXIT="1")
    assert completed.returncode == 95
    assert "off-host copy FAILED" in completed.stderr
    assert len(host.snapshots()) == 1
    assert (
        json.loads((host.backup_root / "LAST_BACKUP.json").read_text("utf-8"))["offhost"]
        == "failed"
    )


# ------------------------------------------------------------------ the drill


def _drill(host: Host, **env: str) -> subprocess.CompletedProcess[str]:
    return run(RESTORE, "--drill", env=host.env(**env))


def test_the_drill_restores_the_newest_snapshot_beside_production_and_proves_it(host: Host) -> None:
    assert _backup(host).returncode == 0
    before = sorted(p.name for p in (host.state / "prod-db").iterdir())

    completed = _drill(host)

    assert completed.returncode == 0, completed.stderr
    assert "DRILL OK" in completed.stdout
    [report_path] = list((host.backup_root / "drills").glob("*-drill.json"))
    report = json.loads(report_path.read_text("utf-8"))
    assert report["verdict"] == "passed"
    assert report["databases"] == {"pagentos_prod": 4, "postgres": 0, "temporal": 3}
    assert report["objects"] == 2
    assert set(report["seconds"]) == {"restore", "verify_files", "databases", "objects", "total"}
    docker = host.log("docker")
    # Scratch containers on no network, and gone afterwards; production's untouched.
    runs = [line for line in docker if line.startswith("docker run ")]
    assert len(runs) == 2 and all("--network none" in line for line in runs)
    assert any(line.startswith("docker rm -f pagentos-drill-pg-") for line in docker)
    assert not any(
        line.startswith(("docker stop", "docker exec pg createdb", "docker exec pg dropdb"))
        for line in docker
    )
    assert sorted(p.name for p in (host.state / "prod-db").iterdir()) == before
    assert not list((host.backup_root / "drill").glob("*"))


def test_a_file_that_changed_inside_the_snapshot_fails_the_drill(host: Host) -> None:
    assert _backup(host).returncode == 0
    completed = _drill(host, FAKE_TAMPER="1")
    assert completed.returncode == 97
    assert "differ from the snapshot's manifest" in completed.stderr


def test_a_database_that_comes_back_one_row_short_fails_the_drill(host: Host) -> None:
    assert _backup(host).returncode == 0
    completed = _drill(host, FAKE_RESTORE_DROP_ROW="pagentos_prod")
    assert completed.returncode == 98
    assert "pagentos_prod did not restore to the same rows" in completed.stderr


def test_objects_that_do_not_read_back_through_minio_fail_the_drill(host: Host) -> None:
    assert _backup(host).returncode == 0
    completed = _drill(host, FAKE_READBACK_DROP="1")
    assert completed.returncode == 99


def test_a_repository_with_nothing_in_it_has_nothing_to_drill(host: Host) -> None:
    completed = _drill(host)
    assert completed.returncode == 96


# ------------------------------------------------------------------ --apply


def test_apply_refuses_without_the_confirmation_naming_the_snapshot(host: Host) -> None:
    assert _backup(host).returncode == 0
    snapshot = host.snapshots()[0].name
    for confirm in ("", "yes", f"RESTORE {'0' * 64} OVER PRODUCTION"):
        completed = run(
            RESTORE, "--apply", "--snapshot", snapshot, "--confirm", confirm, env=host.env()
        )
        assert completed.returncode == 2, confirm
    assert not any(line.startswith("docker stop") for line in host.log("docker"))


def test_apply_backs_up_first_holds_both_locks_and_replaces_the_data(host: Host) -> None:
    assert _backup(host).returncode == 0
    snapshot = host.snapshots()[0].name
    # Production moves on after the snapshot: a new row and a new object.
    prod = host.state / "prod-db" / "pagentos_prod.sql"
    prod.write_text(prod.read_text("utf-8").replace("2\tlaptop\n", "2\tlaptop\n3\tnew\n"), "utf-8")
    (host.state / "prod-objects" / "pagentos-artifacts" / "later.txt").write_text(
        "later\n", "utf-8"
    )
    reconcile = host.bin / "reconcile.sh"
    reconcile.write_text(
        '#!/usr/bin/env bash\necho "reconcile $*" >> "$FAKE_STATE/reconcile.log"\n',
        "utf-8",
        newline="\n",
    )
    reconcile.chmod(0o755)

    completed = run(
        RESTORE,
        "--apply",
        "--snapshot",
        snapshot,
        "--confirm",
        f"RESTORE {snapshot} OVER PRODUCTION",
        env=host.env(PAGENTOS_RECONCILE_SCRIPT=reconcile.as_posix()),
    )

    assert completed.returncode == 0, completed.stderr
    assert "RESTORE OK" in completed.stdout
    # The safety point: a second snapshot, labelled pre-restore, taken before anything else.
    assert len(host.snapshots()) == 2
    assert any("--tag manual --tag pre-restore" in line for line in host.log("restic"))
    # Both locks, the backup's and the blue/green one.
    flocks = host.log("flock")
    assert any(line.endswith(" 8") for line in flocks) and any(
        line.endswith(" 9") for line in flocks
    )
    docker = host.log("docker")
    stop = docker.index("docker stop blue")
    assert docker.index("docker stop temporal") > stop
    drop = next(i for i, line in enumerate(docker) if "dropdb" in line and "pagentos_prod" in line)
    assert drop > stop
    assert docker.index("docker start temporal") > drop
    assert (host.state / "reconcile.log").read_text("utf-8").strip() == "reconcile --reconcile"
    # Production holds exactly the snapshot again.
    assert "3\tnew" not in prod.read_text("utf-8")
    assert not (host.state / "prod-objects" / "pagentos-artifacts" / "later.txt").exists()


def test_apply_touches_nothing_when_the_safety_backup_fails(host: Host) -> None:
    assert _backup(host).returncode == 0
    snapshot = host.snapshots()[0].name
    completed = run(
        RESTORE,
        "--apply",
        "--snapshot",
        snapshot,
        "--confirm",
        f"RESTORE {snapshot} OVER PRODUCTION",
        env=host.env(FAKE_PGDUMP_FAIL="temporal"),
    )
    assert completed.returncode == 100
    assert not any(line.startswith("docker stop") for line in host.log("docker"))


# ------------------------------------------------------------------ install


def _install_env(host: Host, **extra: str) -> dict[str, str]:
    bin_root = host.root / "opt-pagentos-backup"
    return host.env(PAGENTOS_BACKUP_BIN=bin_root.as_posix(), **extra)


def test_install_pins_the_scripts_proves_a_restore_and_only_then_schedules(host: Host) -> None:
    (host.base / "backup.password").unlink()
    completed = run(INSTALL, env=_install_env(host))

    assert completed.returncode == 0, completed.stderr
    bin_root = host.root / "opt-pagentos-backup"
    for name in (
        "backup-cloud-core.sh",
        "restore-cloud-core.sh",
        "backup_manifest.py",
        "SHA256SUMS",
    ):
        assert (bin_root / name).is_file(), name
    password = (host.base / "backup.password").read_text("utf-8")
    assert len(password) >= 60
    assert password not in completed.stdout and password not in completed.stderr
    for unit in (
        "pagentos-backup.service",
        "pagentos-backup.timer",
        "pagentos-restore-drill.service",
        "pagentos-restore-drill.timer",
    ):
        assert (host.systemd / unit).is_file(), unit
    systemctl = host.log("systemctl")
    assert (
        systemctl[-2] == "systemctl enable --now pagentos-backup.timer pagentos-restore-drill.timer"
    )
    assert host.log("apt-get") == []  # restic was already there
    assert len(host.snapshots()) == 1
    assert list((host.backup_root / "drills").glob("*-drill.json"))


def test_install_keeps_an_existing_password(host: Host) -> None:
    before = (host.base / "backup.password").read_text("utf-8")
    assert run(INSTALL, env=_install_env(host)).returncode == 0
    assert (host.base / "backup.password").read_text("utf-8") == before


def test_install_gets_restic_from_the_archive_when_it_is_missing(host: Host) -> None:
    provided = host.root / "usr-bin-restic"
    completed = run(
        INSTALL,
        env=_install_env(
            host, PAGENTOS_RESTIC=provided.as_posix(), FAKE_APT_PROVIDES=provided.as_posix()
        ),
    )
    assert completed.returncode == 0, completed.stderr
    assert any(line.startswith("apt-get install -y -qq restic") for line in host.log("apt-get"))


def test_install_never_schedules_a_backup_that_cannot_be_restored(host: Host) -> None:
    completed = run(INSTALL, env=_install_env(host, FAKE_RESTORE_DROP_ROW="temporal"))
    assert completed.returncode == 5
    assert not any("enable --now" in line for line in host.log("systemctl"))
