"""Environment/file configuration. No enrollment material, no start."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

#: The contract's device identity for this module (the api has no ``device_kind`` field:
#: ``platform`` is a free string, 1..64 characters). Pinned by tests.
DEVICE_NAME = "bulut"
DEVICE_PLATFORM = "cloud"

DEFAULT_STATE_DIR = "/var/lib/pagentos-cloud/state"
DEFAULT_DATA_DIR = "/var/lib/pagentos-cloud/data"
CONNECT_PATH = "/v1/devices/connect"


class ConfigError(Exception):
    """The worker must not start: the reason names what is missing."""


@dataclass(frozen=True, slots=True)
class CloudConfig:
    broker_http_url: str
    state_dir: Path
    data_dir: Path
    token_file: Path
    max_concurrent: int = 2

    @property
    def ws_url(self) -> str:
        parsed = urlparse(self.broker_http_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}{CONNECT_PATH}"

    @property
    def identity_path(self) -> Path:
        return self.state_dir / "identity.json"

    @property
    def key_path(self) -> Path:
        return self.state_dir / "device.key"

    def has_identity(self) -> bool:
        return self.identity_path.is_file() and self.key_path.is_file()

    def read_device_id(self) -> str:
        return str(json.loads(self.identity_path.read_text(encoding="utf-8"))["device_id"])

    def read_token(self) -> str:
        return self.token_file.read_text(encoding="utf-8").strip()


def load_config(env: Mapping[str, str]) -> CloudConfig:
    url = env.get("PAGENTOS_CLOUD_BROKER_URL", "").strip()
    if not url:
        raise ConfigError("PAGENTOS_CLOUD_BROKER_URL is not set")
    if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
        raise ConfigError(f"PAGENTOS_CLOUD_BROKER_URL must be an http(s) URL, got {url!r}")
    state_dir = Path(env.get("PAGENTOS_CLOUD_STATE_DIR", DEFAULT_STATE_DIR))
    token_file = Path(
        env.get("PAGENTOS_CLOUD_ENROLLMENT_TOKEN_FILE", str(state_dir / "enroll.token"))
    )
    cfg = CloudConfig(
        broker_http_url=url.rstrip("/"),
        state_dir=state_dir,
        data_dir=Path(env.get("PAGENTOS_CLOUD_DATA_DIR", DEFAULT_DATA_DIR)),
        token_file=token_file,
    )
    if not cfg.has_identity():
        has_token = token_file.is_file() and bool(cfg.read_token())
        if not has_token:
            raise ConfigError(
                "no enrollment material: neither a persisted identity under "
                f"{state_dir} nor a non-empty enrollment token file at {token_file}"
            )
    return cfg
