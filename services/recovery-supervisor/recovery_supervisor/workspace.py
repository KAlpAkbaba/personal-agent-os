"""Versioned release workspace with pointer files (Windows-friendly, no symlinks).

Layout (RECOVERY_AND_SELF_HEALING.md §3):

    <root>/releases/<version>/...   immutable release directories
    <root>/current.txt              pointer file: the active release version
    <root>/last_known_good.txt      pointer file: the rollback target
    <root>/incidents-outbox/        incident reports (source of truth if API down)
    <root>/history.jsonl            append-only pointer-transition log

Invariants:

- releases are staged then renamed into place; an existing release directory is
  never overwritten in place (stage-preserve-activate);
- pointer writes are atomic (temp file + os.replace);
- ``promote`` only ever marks the *currently active* release as last-known-good
  so a never-verified release can never become the rollback target;
- ``rollback`` repoints current to last-known-good and never deletes anything.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

_EXCLUDED_DIRS = {"__pycache__"}
_EXCLUDED_FILES = {"manifest.json"}
_EXCLUDED_SUFFIXES = {".pyc"}


class WorkspaceError(Exception):
    """Typed workspace failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def manifest_digest(release_dir: Path) -> str:
    """Deterministic digest over a release directory's file contents.

    sha256 over lines ``<relative-posix-path>\\0<sha256(file)>\\n`` for every
    file, sorted by path. ``__pycache__``/``*.pyc``/``manifest.json`` are
    excluded so a written manifest never changes the digest it records.
    The API side (app.selfhealing.service.compute_manifest_digest) implements
    the identical formula; the M6 E2E pins the two together.
    """
    release_dir = Path(release_dir)
    entries: list[tuple[str, Path]] = []
    for path in release_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(release_dir)
        if any(part in _EXCLUDED_DIRS for part in rel.parts):
            continue
        if rel.name in _EXCLUDED_FILES or path.suffix in _EXCLUDED_SUFFIXES:
            continue
        entries.append((rel.as_posix(), path))
    outer = hashlib.sha256()
    for rel_posix, path in sorted(entries):
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        outer.update(f"{rel_posix}\0{file_hash}\n".encode())
    return outer.hexdigest()


class ReleaseWorkspace:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.releases_dir = self.root / "releases"
        self.current_file = self.root / "current.txt"
        self.last_known_good_file = self.root / "last_known_good.txt"
        self.outbox_dir = self.root / "incidents-outbox"
        self.history_file = self.root / "history.jsonl"

    # ------------------------------------------------------------ filesystem

    def ensure(self) -> None:
        self.releases_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)

    def release_dir(self, version: str) -> Path:
        _validate_version(version)
        return self.releases_dir / version

    def has_release(self, version: str) -> bool:
        return self.release_dir(version).is_dir()

    def list_releases(self) -> list[str]:
        if not self.releases_dir.is_dir():
            return []
        return sorted(
            p.name
            for p in self.releases_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )

    # -------------------------------------------------------------- pointers

    @staticmethod
    def _read_pointer(pointer: Path) -> str | None:
        try:
            value = pointer.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        return value or None

    def _write_pointer(self, pointer: Path, version: str) -> None:
        tmp = pointer.with_name(f".{pointer.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(version + "\n", encoding="utf-8")
        os.replace(tmp, pointer)

    @property
    def current(self) -> str | None:
        return self._read_pointer(self.current_file)

    @property
    def last_known_good(self) -> str | None:
        return self._read_pointer(self.last_known_good_file)

    def _history(self, action: str, **detail: object) -> None:
        record = {"ts": _utcnow_iso(), "action": action, **detail}
        with self.history_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------ operations

    def stage_release(self, version: str, source_dir: Path | str) -> Path:
        """Copy a release into the workspace without activating it.

        Stage-preserve-activate: the copy lands in a hidden staging directory
        first and is renamed into ``releases/<version>`` only when complete, so
        a crashed copy never looks like a valid release. Existing releases are
        immutable — staging over one is refused.
        """
        self.ensure()
        source = Path(source_dir)
        if not source.is_dir():
            raise WorkspaceError("source_missing", f"source directory not found: {source}")
        target = self.release_dir(version)
        if target.exists():
            raise WorkspaceError(
                "release_exists", f"release {version} already staged; releases are immutable"
            )
        staging = self.releases_dir / f".staging-{version}-{uuid.uuid4().hex[:8]}"
        shutil.copytree(
            source,
            staging,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        os.replace(staging, target)
        self._history("stage", version=version, source=str(source))
        return target

    def activate(self, version: str, source_dir: Path | str | None = None) -> str | None:
        """Point ``current`` at ``version`` (staging it first when a source is given).

        The previous release directory is preserved untouched; only the pointer
        moves. last_known_good is NOT changed here — only ``promote`` (after
        the health policy passes) may move it.
        """
        self.ensure()
        if source_dir is not None:
            self.stage_release(version, source_dir)
        if not self.has_release(version):
            raise WorkspaceError("release_missing", f"release {version} is not staged")
        previous = self.current
        self._write_pointer(self.current_file, version)
        self._history("activate", version=version, previous=previous)
        return previous

    def promote(self, version: str | None = None) -> str | None:
        """Mark the active (health-verified) release as last-known-good."""
        self.ensure()
        active = self.current
        if active is None:
            raise WorkspaceError("no_active_release", "nothing is active; activate first")
        if version is not None and version != active:
            raise WorkspaceError(
                "promote_requires_active",
                f"cannot promote {version}: active release is {active}; "
                "only the health-verified active release may become last-known-good",
            )
        previous = self.last_known_good
        self._write_pointer(self.last_known_good_file, active)
        self._history("promote", version=active, previous_last_known_good=previous)
        return previous

    def rollback(self) -> tuple[str | None, str]:
        """Repoint current to last-known-good. Returns (previous_current, lkg)."""
        self.ensure()
        lkg = self.last_known_good
        if lkg is None:
            raise WorkspaceError("no_last_known_good", "no last-known-good release recorded")
        if not self.has_release(lkg):
            raise WorkspaceError(
                "last_known_good_missing", f"last-known-good release {lkg} directory is gone"
            )
        previous = self.current
        self._write_pointer(self.current_file, lkg)
        self._history("rollback", from_version=previous, to_version=lkg)
        return previous, lkg

    def status(self) -> dict[str, object]:
        current = self.current
        return {
            "root": str(self.root),
            "current": current,
            "last_known_good": self.last_known_good,
            "releases": self.list_releases(),
            "current_manifest_digest": (
                manifest_digest(self.release_dir(current))
                if current and self.has_release(current)
                else None
            ),
            "outbox_pending": (
                len(list(self.outbox_dir.glob("incident-*.json")))
                if self.outbox_dir.is_dir()
                else 0
            ),
        }


def _validate_version(version: str) -> None:
    if not version or any(sep in version for sep in ("/", "\\", "..", ":")):
        raise WorkspaceError("invalid_version", f"invalid release version: {version!r}")
