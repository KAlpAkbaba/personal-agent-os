"""Push credentials and mobile-surface bounds, read from the environment.

Why this is not in `app/config.py`: push credentials are provisioned by an
*owner action* (a Firebase service account, an Apple APNs auth key, a VAPID key
pair) and are read only by the real adapters in `providers.py`. Keeping them in
one small env-backed dataclass beside the adapters means the rest of the API
carries no field that only exists to be empty, and the "is this provider
activated?" question has exactly one answer site.

Every value is empty/defaulted out of the box, so the real adapters are inert
and the deterministic fake is what the suite exercises. NEVER commit real keys.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Owner-provisioned credential env vars, all optional.
ENV_FCM_PROJECT_ID = "PAGENTOS_PUSH_FCM_PROJECT_ID"
ENV_FCM_ACCESS_TOKEN = "PAGENTOS_PUSH_FCM_ACCESS_TOKEN"  # noqa: S105 - env var name
ENV_APNS_TEAM_ID = "PAGENTOS_PUSH_APNS_TEAM_ID"
ENV_APNS_KEY_ID = "PAGENTOS_PUSH_APNS_KEY_ID"
ENV_APNS_BUNDLE_ID = "PAGENTOS_PUSH_APNS_BUNDLE_ID"
ENV_APNS_JWT = "PAGENTOS_PUSH_APNS_JWT"
ENV_APNS_SANDBOX = "PAGENTOS_PUSH_APNS_SANDBOX"
ENV_VAPID_PUBLIC_KEY = "PAGENTOS_PUSH_VAPID_PUBLIC_KEY"
ENV_VAPID_PRIVATE_KEY = "PAGENTOS_PUSH_VAPID_PRIVATE_KEY"  # noqa: S105 - env var name
ENV_VAPID_SUBJECT = "PAGENTOS_PUSH_VAPID_SUBJECT"

#: Bound on a share/export download, overridable for a genuinely large report.
ENV_SHARE_MAX_BYTES = "PAGENTOS_MOBILE_SHARE_MAX_BYTES"
DEFAULT_SHARE_MAX_BYTES = 25 * 1024 * 1024  # 25 MiB

#: How many delivery records the in-process fake keeps. This is a debugging and
#: reference-client surface, not a durable notification log: the frozen M9
#: schema has no notifications table and inventing one is not this task's call.
ENV_DELIVERY_LOG_SIZE = "PAGENTOS_MOBILE_DELIVERY_LOG_SIZE"
DEFAULT_DELIVERY_LOG_SIZE = 200


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class FcmCredentials:
    project_id: str = ""
    access_token: str = ""

    @classmethod
    def from_env(cls) -> FcmCredentials:
        return cls(
            project_id=_env(ENV_FCM_PROJECT_ID),
            access_token=_env(ENV_FCM_ACCESS_TOKEN),
        )

    @property
    def configured(self) -> bool:
        return bool(self.project_id and self.access_token)


@dataclass(frozen=True, slots=True)
class ApnsCredentials:
    team_id: str = ""
    key_id: str = ""
    bundle_id: str = ""
    jwt: str = ""
    sandbox: bool = False

    @classmethod
    def from_env(cls) -> ApnsCredentials:
        return cls(
            team_id=_env(ENV_APNS_TEAM_ID),
            key_id=_env(ENV_APNS_KEY_ID),
            bundle_id=_env(ENV_APNS_BUNDLE_ID),
            jwt=_env(ENV_APNS_JWT),
            sandbox=_env_bool(ENV_APNS_SANDBOX),
        )

    @property
    def configured(self) -> bool:
        return bool(self.bundle_id and self.jwt)


@dataclass(frozen=True, slots=True)
class WebPushCredentials:
    public_key: str = ""
    private_key: str = ""
    subject: str = ""

    @classmethod
    def from_env(cls) -> WebPushCredentials:
        return cls(
            public_key=_env(ENV_VAPID_PUBLIC_KEY),
            private_key=_env(ENV_VAPID_PRIVATE_KEY),
            subject=_env(ENV_VAPID_SUBJECT),
        )

    @property
    def configured(self) -> bool:
        return bool(self.public_key and self.private_key)


def share_max_bytes() -> int:
    return _env_int(ENV_SHARE_MAX_BYTES, DEFAULT_SHARE_MAX_BYTES)


def delivery_log_size() -> int:
    return _env_int(ENV_DELIVERY_LOG_SIZE, DEFAULT_DELIVERY_LOG_SIZE)


__all__ = [
    "DEFAULT_DELIVERY_LOG_SIZE",
    "DEFAULT_SHARE_MAX_BYTES",
    "ApnsCredentials",
    "FcmCredentials",
    "WebPushCredentials",
    "delivery_log_size",
    "share_max_bytes",
]
