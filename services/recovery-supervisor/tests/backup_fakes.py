"""Fakes for the host backup scripts (ADR-0122): docker, restic, flock, systemctl, apt-get.

Each is a small bash program that records how it was called and emulates just enough of the
real tool for backup-cloud-core.sh, restore-cloud-core.sh and install-backup.sh to run
end to end on a developer machine or a CI runner - with REAL files moving through REAL
restic-shaped snapshots, and the REAL manifest/fingerprint helper judging them.

What the fake docker emulates, per container name:
  pg (production postgres)      databases are files FAKE_STATE/prod-db/<db>.sql holding the
                                COPY text a `pg_restore --data-only` would print; a "dump" is
                                that text, so the fingerprint of a dump is computable
  pagentos-drill-pg-*           the same, under FAKE_STATE/db-<name>/
  minio / pagentos-drill-minio-* objects under FAKE_STATE/prod-objects/<bucket>/...; the
                                in-container filesystem under FAKE_STATE/fs/<name>/
Knobs: FAKE_PGDUMP_FAIL=<db>, FAKE_RESTORE_DROP_ROW=<db>, FAKE_MIRROR_EXIT, FAKE_READBACK_DROP=1,
FAKE_RESTIC_BACKUP_EXIT, FAKE_RESTIC_CHECK_EXIT, FAKE_RESTIC_COPY_EXIT, FAKE_TAMPER=1,
FAKE_FLOCK_EXIT.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLOUD = REPO_ROOT / "scripts" / "cloud"
BACKUP = CLOUD / "backup-cloud-core.sh"
RESTORE = CLOUD / "restore-cloud-core.sh"
INSTALL = CLOUD / "install-backup.sh"
HELPER = CLOUD / "backup_manifest.py"

FAKES = Path(__file__).resolve().parent / "fakes"


def copy_row(*cells: str) -> str:
    return "\t".join(cells)


def dump_text(tables: dict[str, tuple[str, list[tuple[str, ...]]]], sequences=()) -> str:
    """The text a `pg_restore --data-only -f -` prints for these tables."""
    lines = ["--", "-- PostgreSQL database dump", "--", "SET statement_timeout = 0;", ""]
    for name, (columns, rows) in tables.items():
        lines.append(f"COPY {name} {columns} FROM stdin;")
        lines.extend(copy_row(*row) for row in rows)
        lines.append("\\.")
        lines.append("")
    lines.extend(sequences)
    return "\n".join(lines) + "\n"


def _fake(name: str) -> str:
    return (FAKES / name).read_text("utf-8")


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class Host:
    """A production host in a temp directory, with fake tools and real scripts."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = root / "state"
        self.bin = self.state / "bin"
        self.base = root / "opt-pagentos"
        self.data = root / "pagentos-data"
        self.backup_root = root / "backup"
        self.systemd = root / "systemd"
        for d in (self.bin, self.base, self.data / "identity", self.data / "edge", self.systemd):
            d.mkdir(parents=True, exist_ok=True)
        self.docker = _executable(self.bin / "docker", _fake("docker.sh"))
        self.restic = _executable(self.bin / "restic", _fake("restic.sh"))
        self.flock = _executable(self.bin / "flock", _fake("logger.sh"))
        self.systemctl = _executable(self.bin / "systemctl", _fake("logger.sh"))
        self.apt = _executable(self.bin / "apt-get", _fake("logger.sh"))
        (self.base / ".env").write_text("PAGENTOS_DB_PASSWORD=not-a-real-one\n", "utf-8")
        (self.base / "RELEASE").write_text("c" * 40 + "\n", "utf-8")
        (self.base / "LAST_KNOWN_GOOD").write_text("d" * 40 + "\n", "utf-8")
        (self.base / "backup.password").write_text("fake-repository-password", "utf-8")
        (self.data / "identity" / "owner.credential").write_text("hash-only\n", "utf-8")
        (self.data / "edge" / "active.txt").write_text("green\n", "utf-8")
        (self.systemd / "pagentos-bluegreen-reconcile.service").write_text("[Unit]\n", "utf-8")
        prod_db = self.state / "prod-db"
        prod_db.mkdir(parents=True)
        (prod_db / "pagentos_prod.sql").write_text(
            dump_text(
                {
                    "public.devices": ("(id, name)", [("1", "ev-pc"), ("2", "laptop")]),
                    "public.tasks": ("(id, title)", [("7", "Notlar"), ("8", "Alarm")]),
                },
                sequences=["SELECT pg_catalog.setval('public.tasks_id_seq', 8, true);"],
            ),
            "utf-8",
        )
        (prod_db / "temporal.sql").write_text(
            dump_text({"public.executions": ("(id)", [("a1",), ("a2",), ("a3",)])}), "utf-8"
        )
        (prod_db / "postgres.sql").write_text(dump_text({}), "utf-8")
        objects = self.state / "prod-objects" / "pagentos-artifacts"
        (objects / "tasks" / "7").mkdir(parents=True)
        (objects / "tasks" / "7" / "report.html").write_text("<h1>Rapor</h1>\n", "utf-8")
        (objects / "notlarim.exe").write_bytes(b"MZ" + bytes(range(256)))

    @staticmethod
    def posix(path: Path) -> str:
        return path.as_posix()

    def env(self, **extra: str) -> dict[str, str]:
        p = self.posix
        return {
            **os.environ,
            "PAGENTOS_ALLOW_NONROOT": "1",
            "FAKE_STATE": p(self.state),
            "PAGENTOS_BASE": p(self.base),
            "PAGENTOS_DATA": p(self.data),
            "PAGENTOS_BACKUP_ROOT": p(self.backup_root),
            "PAGENTOS_RECOVERY_ROOT": p(self.root / "no-recovery"),
            "PAGENTOS_SYSTEMD_DIR": p(self.systemd),
            "PAGENTOS_PG_CONTAINER": "pg",
            "PAGENTOS_MINIO_CONTAINER": "minio",
            "PAGENTOS_TEMPORAL_CONTAINER": "temporal",
            "PAGENTOS_COLOUR_CONTAINERS": "blue green",
            "PAGENTOS_DOCKER": p(self.docker),
            "PAGENTOS_RESTIC": p(self.restic),
            "PAGENTOS_FLOCK": p(self.flock),
            "PAGENTOS_SYSTEMCTL": p(self.systemctl),
            "PAGENTOS_APT": p(self.apt),
            "PAGENTOS_PYTHON": Path(sys.executable).as_posix(),
            "PAGENTOS_BACKUP_HELPER": p(HELPER),
            "PAGENTOS_BACKUP_SCRIPT": p(BACKUP),
            "PAGENTOS_DRILL_READY_STEP_S": "0",
            "RESTIC_PASSWORD_FILE": p(self.base / "backup.password"),
            **extra,
        }

    def log(self, name: str) -> list[str]:
        path = self.state / f"{name}.log"
        return path.read_text("utf-8").splitlines() if path.exists() else []

    def snapshots(self) -> list[Path]:
        root = self.backup_root / "restic" / "snapshots"
        return sorted(root.iterdir()) if root.exists() else []

    def wipe_prod_db(self) -> None:
        shutil.rmtree(self.state / "prod-db")
        (self.state / "prod-db").mkdir()
