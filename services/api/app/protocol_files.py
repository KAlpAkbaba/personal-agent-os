"""The shared contract files the Cloud Core reads while it RUNS, shipped inside the app.

``packages/protocol/`` is where each two-halves decision is written once. The production
image is built from ``services/api`` and never contained that directory, so every module that
reached it through a repository-relative path crashed at import in the image - found on
2026-09-16, when the first release since B13 could not start (``alarm-timing.json``). Tests
and CI never saw it: they run from a checkout, where the path resolves.

So the run-time copies live here, in ``app/protocol_bundle/``, byte-identical to the shared
files (``tests/unit/test_protocol_bundle.py`` fails the moment one drifts, and
``scripts/sync-protocol-bundle.py`` refreshes them). The app reads ONLY the bundle - there is
no "repository if present, else bundle" branch, so a checkout and an image read the same bytes
and a missing file is an error, never a quiet default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

BUNDLE_DIR: Final[Path] = Path(__file__).resolve().parent / "protocol_bundle"

#: The shared files the app reads at run time. Adding a reader of another one means adding
#: it here, copying it with scripts/sync-protocol-bundle.py, and nothing else.
BUNDLED: Final[tuple[str, ...]] = (
    "alarm-timing.json",
    "desktop-notify.json",
    "file-search-roots.json",
    "operator-allowlists.json",
)


def protocol_file(name: str) -> Path:
    """The run-time copy of ``packages/protocol/<name>``."""
    if name not in BUNDLED:
        raise KeyError(f"{name} is not a bundled protocol file; add it to BUNDLED first")
    return BUNDLE_DIR / name


__all__ = ["BUNDLED", "BUNDLE_DIR", "protocol_file"]
