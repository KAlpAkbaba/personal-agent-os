"""Release identity of the running worker: WHICH copy of this package is executing.

Deployment truthfulness defect, 2026-09-04 (docs/DECISIONS.md ADR-0050 item 16):
the installer's self-check and the companion's runtime imported two different
copies of ``browser_agent`` from the same venv. The self-check ran with the
browser tree as its working directory, so ``python -m browser_agent.worker``
imported the freshly copied SOURCE tree (``sys.path[0]`` is the cwd) and
reported the new version; the companion runs the worker with its data
directory as cwd, so the same command imported the venv's site-packages copy,
which uv had rebuilt from a stale cached wheel (its cache key is the
``pyproject.toml`` mtime, unchanged across code-only releases). The installer
said INSTALL VERIFIED; the live worker stayed 0.1.0.

Every hello and every ``browser.worker_status`` therefore now carries a
``module`` block that a verifier can check against the release it staged:

- ``file``: the absolute path of the executing ``worker.py``;
- ``sha256``: its content hash;
- ``package_sha256``: a digest over every ``*.py`` directly inside the
  executing ``browser_agent`` package (sorted by name, ``"<name>:<sha256>\\n"``
  per file, UTF-8, then SHA-256) -- the same algorithm as
  ``scripts/lib/BrowserRelease.ps1`` ``Get-BrowserPackageDigest``;
- ``cwd``: the working directory the import happened from.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_digest(package_dir: Path | str) -> str:
    """Digest of every ``*.py`` directly inside ``package_dir`` (no recursion).

    Sorted by file name (ordinal), one ``name:sha256`` line per file, joined
    with ``\\n`` and terminated, hashed as UTF-8 with SHA-256. Mirrors
    ``Get-BrowserPackageDigest`` in ``scripts/lib/BrowserRelease.ps1``; the
    two are pinned to each other by a shared fixture test.
    """
    root = Path(package_dir)
    lines = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.is_file() and entry.suffix == ".py":
            lines.append(f"{entry.name}:{sha256_file(entry)}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def module_info() -> dict[str, Any]:
    """The ``module`` block for hello / worker_status. Never raises."""
    try:
        from . import worker as _worker  # noqa: PLC0415 - avoid import cycle at module load

        worker_file = Path(_worker.__file__).resolve()
        package_dir = worker_file.parent
        return {
            "file": str(worker_file),
            "sha256": sha256_file(worker_file),
            "package_sha256": package_digest(package_dir),
            "package_dir": str(package_dir),
            "cwd": os.getcwd(),
        }
    except Exception as exc:  # noqa: BLE001 - identity must never break the hello
        return {"file": None, "sha256": None, "package_sha256": None, "error": str(exc)[:200]}


__all__ = ["module_info", "package_digest", "sha256_file"]
