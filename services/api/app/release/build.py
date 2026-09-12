"""Which BUILD is running - the cloud half of the device's ``AgentInfo.BuildId``.

``app_version`` answers "which product release is this" and is deliberately still across
builds: production has served ``0.1.0`` for every release it has ever had. ``version`` answers
"which commit", but only when the release script exported one - a container started by hand,
a local run, or an image rebuilt from a dirty tree all report ``unknown``, and two different
images then look identical to anything that compares them.

The device already solved this (ADR-0118, migration 0040): its ``build_id`` is DERIVED from
its own files, so it cannot drift, cannot be forgotten, and changes by construction when the
code changes. This is the same rule on this side of the wire, computed the same way - every
``*.py`` under ``app/``, sorted by path, each file's name and SHA-256 folded into one digest,
first 16 hex characters - so the two halves of one system answer "which build is this" in one
shape.

Never raises. A tree that cannot be read answers ``"unknown"``, and a comparison against
``"unknown"`` means "no build identity available", never a match.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

#: What :func:`build_identity` answers when the tree cannot be read.
UNKNOWN_BUILD_ID = "unknown"

#: Hex characters kept from the fold - the same width the device uses, so a person reading
#: one identity next to the other is reading the same kind of thing.
BUILD_ID_HEX_CHARS = 16

#: The tree the identity is taken from: the application package itself, nothing else. A
#: dependency upgrade is not a new build of THIS code (and the lock file already names it).
_APP_ROOT = Path(__file__).resolve().parents[1]


def _iter_sources(root: Path) -> list[Path]:
    """Every source file that makes up this build, in a stable order.

    ``__pycache__`` is excluded: a byte-compiled copy of a file already folded in would make
    the identity depend on whether the container had been warmed up.
    """
    return sorted(
        (p for p in root.rglob("*.py") if "__pycache__" not in p.parts),
        key=lambda p: p.relative_to(root).as_posix(),
    )


def compute_build_identity(root: Path) -> str:
    """Fold ``root``'s sources into one identity, or ``"unknown"`` if it cannot be read."""
    try:
        files = _iter_sources(root)
        if not files:
            return UNKNOWN_BUILD_ID
        fold = hashlib.sha256()
        for path in files:
            # The path is folded in as well as the bytes, so adding, removing or renaming a
            # module changes the identity even when the remaining bytes are untouched.
            fold.update(path.relative_to(root).as_posix().encode("utf-8"))
            fold.update(hashlib.sha256(path.read_bytes()).digest())
        return fold.hexdigest()[:BUILD_ID_HEX_CHARS]
    except OSError:
        # An identity that raises would take /v1/system/health down with it. A build that
        # cannot say which build it is says so.
        return UNKNOWN_BUILD_ID


@lru_cache(maxsize=1)
def build_identity() -> str:
    """This process's build identity. Computed once - the tree does not change under us."""
    return compute_build_identity(_APP_ROOT)


__all__ = [
    "BUILD_ID_HEX_CHARS",
    "UNKNOWN_BUILD_ID",
    "build_identity",
    "compute_build_identity",
]
