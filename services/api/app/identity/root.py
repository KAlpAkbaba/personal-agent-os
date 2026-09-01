"""The owner identity root: where the owner credential's hash lives.

Deliberately **not** a database table. Constitution §6 requires a minimal
recovery root that stays independently executable when the main application is
broken, and lists "owner identity root" first. Two consequences follow:

- a lost credential is recovered by running a script on the host
  (`python -m app.identity.recover`), not by calling the API that the
  credential protects — the owner controls the machine, and that is the
  authorization;
- a restored/rebuilt database does not silently resurrect or destroy the
  owner's ability to authenticate.

The file contains a SHA-256 hash of a 256-bit random credential and nothing
else. It is created with owner-only permissions (0o600, plus an inheritance-
stripped Windows ACL where available) and lives outside the repository tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.logging import get_logger

logger = get_logger("app.identity.root")

ROOT_SCHEMA_VERSION = 1
ROOT_FILENAME = "owner_credential.json"


@dataclass(frozen=True)
class RootRecord:
    """What the identity root stores. Never the credential itself."""

    credential_hash: str
    created_at: datetime
    rotated_at: datetime | None = None
    rotations: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "version": ROOT_SCHEMA_VERSION,
            "credential_hash": self.credential_hash,
            "created_at": self.created_at.isoformat(),
            "rotated_at": self.rotated_at.isoformat() if self.rotated_at else None,
            "rotations": self.rotations,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> RootRecord:
        rotated_raw = payload.get("rotated_at")
        return cls(
            credential_hash=str(payload["credential_hash"]),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            rotated_at=datetime.fromisoformat(str(rotated_raw)) if rotated_raw else None,
            rotations=int(payload.get("rotations", 0)),
        )


class CredentialRoot(Protocol):
    """Storage seam for the owner credential hash."""

    def load(self) -> RootRecord | None: ...

    def store(self, record: RootRecord) -> None: ...

    def clear(self) -> None: ...

    def describe(self) -> dict[str, Any]: ...

    def exists(self) -> bool: ...


class InMemoryCredentialRoot:
    """Process-local root used by tests. Never a production configuration."""

    kind = "memory"

    def __init__(self, record: RootRecord | None = None) -> None:
        self._record = record

    def load(self) -> RootRecord | None:
        return self._record

    def store(self, record: RootRecord) -> None:
        self._record = record

    def clear(self) -> None:
        self._record = None

    def exists(self) -> bool:
        return self._record is not None

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "bootstrapped": self.exists()}


class FileCredentialRoot:
    """Owner-only JSON file under `PAGENTOS_IDENTITY_ROOT_DIR`."""

    kind = "file"

    def __init__(self, directory: str | os.PathLike[str], *, harden: bool = True) -> None:
        self.directory = Path(directory)
        self.path = self.directory / ROOT_FILENAME
        self._harden = harden

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> RootRecord | None:
        if not self.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A corrupt root must fail closed, not fall back to "no auth".
            logger.error("identity_root_unreadable", error=f"{type(exc).__name__}: {exc}")
            raise
        if int(payload.get("version", 0)) != ROOT_SCHEMA_VERSION:
            raise ValueError(f"unsupported identity root version: {payload.get('version')!r}")
        return RootRecord.from_json(payload)

    def store(self, record: RootRecord) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record.to_json(), indent=2, sort_keys=True) + "\n"
        # Write-then-replace so a crash never leaves a truncated root behind.
        fd, tmp_name = tempfile.mkstemp(dir=str(self.directory), prefix=".root-", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._restrict(tmp_path)
            os.replace(tmp_path, self.path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        self._restrict(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": str(self.path), "bootstrapped": self.exists()}

    # ------------------------------------------------------------- permissions

    def _restrict(self, path: Path) -> None:
        """Best-effort owner-only permissions; never fatal, always reported.

        POSIX modes are advisory on Windows, so the Windows path additionally
        strips ACL inheritance and grants the current user alone — the M2
        review's finding about a world-readable local credential registry
        applies with more force to the identity root.
        """
        if not self._harden:
            return
        try:
            os.chmod(path, 0o600)
        except OSError as exc:  # pragma: no cover - platform dependent
            logger.warning("identity_root_chmod_failed", error=str(exc))
        if sys.platform != "win32":
            return
        user = os.environ.get("USERNAME") or os.environ.get("USER")
        if not user:  # pragma: no cover - defensive
            return
        try:
            subprocess.run(  # noqa: S603 - fixed argv, no shell, local path only
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                check=False,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            logger.warning("identity_root_acl_failed", error=str(exc))


def utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ROOT_FILENAME",
    "ROOT_SCHEMA_VERSION",
    "CredentialRoot",
    "FileCredentialRoot",
    "InMemoryCredentialRoot",
    "RootRecord",
    "utc_now",
]
