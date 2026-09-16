"""Single-use fetch tokens for a mail attachment on its way to the owner's disk (B45 req 348).

The device's ``file.fetch`` GET carries no owner credential of its own (DEVICE_PROTOCOL.md
6k), so - exactly like ``app.artifacts.render_fetch_store`` for a render - the Cloud Core
mints a 256-bit token that names ONE stored attachment (its object key and the hash its
bytes must still have), is redeemable once, and expires in ten minutes. Only the token's
SHA-256 is kept as the key, so a memory dump of this store cannot be replayed against the
fetch route.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

DEFAULT_TTL_S: Final = 600
MAX_ENTRIES: Final = 256
FETCH_PATH_PREFIX: Final = "/v1/mail/attachments/fetch/"


@dataclass(frozen=True, slots=True)
class AttachmentFetchTarget:
    object_key: str
    sha256: str
    filename: str
    content_type: str


@dataclass(slots=True)
class _Entry:
    target: AttachmentFetchTarget
    expires_at: datetime


class AttachmentFetchStore:
    """token hash -> the one attachment it may fetch; single-use, TTL-bound, bounded."""

    def __init__(self, *, ttl_s: int = DEFAULT_TTL_S, max_entries: int = MAX_ENTRIES) -> None:
        self._ttl = timedelta(seconds=ttl_s)
        self._max_entries = max_entries
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def put(
        self,
        *,
        object_key: str,
        sha256: str,
        filename: str,
        content_type: str,
        now: datetime | None = None,
    ) -> str:
        moment = now or datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_expired(moment)
            while len(self._entries) >= self._max_entries:
                oldest = min(self._entries, key=lambda k: self._entries[k].expires_at)
                del self._entries[oldest]
            self._entries[self._key(token)] = _Entry(
                AttachmentFetchTarget(object_key, sha256, filename, content_type),
                moment + self._ttl,
            )
        return token

    def take(self, token: str, *, now: datetime | None = None) -> AttachmentFetchTarget | None:
        """The target once, then never again; None for an unknown or expired token."""
        moment = now or datetime.now(UTC)
        with self._lock:
            entry = self._entries.pop(self._key(token), None)
        if entry is None or entry.expires_at <= moment:
            return None
        return entry.target

    def _purge_expired(self, moment: datetime) -> None:
        for key in [k for k, e in self._entries.items() if e.expires_at <= moment]:
            del self._entries[key]

    def size(self) -> int:
        with self._lock:
            return len(self._entries)


_STORE = AttachmentFetchStore()


def get_attachment_fetch_store() -> AttachmentFetchStore:
    return _STORE


def set_attachment_fetch_store(store: AttachmentFetchStore) -> None:
    global _STORE
    _STORE = store


__all__ = [
    "DEFAULT_TTL_S",
    "FETCH_PATH_PREFIX",
    "AttachmentFetchStore",
    "AttachmentFetchTarget",
    "get_attachment_fetch_store",
    "set_attachment_fetch_store",
]
