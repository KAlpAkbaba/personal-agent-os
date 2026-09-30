"""Container healthcheck: healthy while the broker session is alive.

The bridge touches a marker file on the welcome and on every heartbeat; the check passes
when the marker is younger than ``MAX_AGE_S`` (several missed heartbeats, not one).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from . import config

MARKER = "connected.marker"
MAX_AGE_S = 120.0


def marker_path(cfg: config.CloudConfig) -> Path:
    return cfg.data_dir / MARKER


def touch(cfg: config.CloudConfig) -> None:
    path = marker_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()), encoding="utf-8")


def is_healthy(cfg: config.CloudConfig, *, now: float | None = None) -> bool:
    try:
        age = (time.time() if now is None else now) - marker_path(cfg).stat().st_mtime
    except OSError:
        return False
    return age <= MAX_AGE_S


def main() -> int:
    data_dir = Path(os.environ.get("PAGENTOS_CLOUD_DATA_DIR", config.DEFAULT_DATA_DIR))
    cfg = config.CloudConfig(
        broker_http_url="http://unused", state_dir=data_dir, data_dir=data_dir,
        token_file=data_dir / "unused",
    )
    return 0 if is_healthy(cfg) else 1


if __name__ == "__main__":
    sys.exit(main())
