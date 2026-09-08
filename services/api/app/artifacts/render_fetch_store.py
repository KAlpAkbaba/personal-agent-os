"""One-time tokens for a device's ``file.fetch`` to reach a render's bytes without an
owner session (ADR-0085 addendum 5 — closing the gap addendum 4 decision 5 recorded).

DEVICE_PROTOCOL.md §6k step 6: the device's GET carries "no owner token, no cookie, no
header of its own" — the M13 bearer-gated download route
(``GET /v1/artifacts/{id}/renders/{fmt}``) can never satisfy that; only a browser with a
session can use it. This module mints, for ONE (artifact, format, content_hash) at a
time, a single-use, short-lived, random token that IS the authority for the device's GET
— the same shape ``app.alarms.audio_store.AudioStore`` already established for the
greeting WAV (``desktop.play_audio``, ADR-0069), with one difference: the token itself is
never the dict key. It is hashed (``app.identity.tokens.hash_token`` — the same SHA-256
primitive session tokens use) before it is stored, so a memory dump, a log line or a
stray ``repr()`` of this store's internal state can never hand out a redeemable token —
an improvement over the audio store's own plaintext dict key, made here because a render
can be considerably larger and longer-lived than a five-minute greeting and is worth the
extra hop; nothing about that choice requires the audio store to change.

In-process and bounded, like the audio store: ephemeral delivery state
(PROJECT_CONSTITUTION.md's Redis rule, applied one level down — there is nothing here
worth making durable, since a token that is never redeemed just expires and the owner
says "Bunu aç" again). Only METADATA is stored (artifact id, render format, the render's
own ``content_hash``) — never the bytes themselves; the device-facing route redeems the
token and then reads the render the normal way (through ``render_store``, from the
canonical object store), so the bytes served are always the CURRENT render for that
(artifact, format) pair, and a render that no longer matches the pinned ``content_hash``
(changed underneath the token, however unlikely inside a ten-minute window) is refused
rather than served stale.
"""

from __future__ import annotations

import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from app.identity.tokens import hash_token

#: ADR-0085 addendum 5: at most ten minutes — long enough for a companion to dial in over
#: a slow link, short enough that a captured URL is worthless soon after.
DEFAULT_TTL_S: Final = 600
#: One personal owner's worth of concurrently in-flight opens; a token that is never
#: redeemed just ages out, so this bounds memory, not correctness (mirrors
#: AudioStore.MAX_ENTRIES).
MAX_ENTRIES: Final = 32
#: Token length in BYTES before base64 (``token_urlsafe`` takes bytes): 32 -> 256 bits —
#: matching ``AudioStore.TOKEN_BYTES`` and comfortably over the >= 32 bytes required.
TOKEN_BYTES: Final = 32


@dataclass(frozen=True, slots=True)
class RenderFetchHandle:
    """What ``open_service`` needs to build the device's ``file.fetch`` URL."""

    token: str
    expires_at: datetime

    def path(self) -> str:
        #: Under ``/v1/artifacts/`` (DEVICE_PROTOCOL.md §6k step 4: "the path must start
        #: with /v1/artifacts/"), on a router with no owner-session dependency.
        return f"/v1/artifacts/renders/fetch/{self.token}"


@dataclass(frozen=True, slots=True)
class RenderFetchTarget:
    """What a token resolves to, read back by the device-facing route."""

    artifact_id: uuid.UUID
    fmt: str
    content_hash: str


@dataclass(slots=True)
class _Entry:
    target: RenderFetchTarget
    expires_at: datetime


class RenderFetchStore:
    """token hash -> (artifact_id, format, content_hash); single-use, TTL-bound,
    thread-safe — the same concurrency reasoning as ``AudioStore`` (a voice tool call and
    the HTTP route that redeems the token run on different threads than each other)."""

    def __init__(self, *, ttl_s: int = DEFAULT_TTL_S, max_entries: int = MAX_ENTRIES) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def put(
        self,
        *,
        artifact_id: uuid.UUID,
        fmt: str,
        content_hash: str,
        now: datetime | None = None,
    ) -> RenderFetchHandle:
        moment = now or datetime.now(UTC)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        digest = hash_token(token)
        expires_at = moment + timedelta(seconds=self._ttl_s)
        target = RenderFetchTarget(artifact_id=artifact_id, fmt=fmt, content_hash=content_hash)
        with self._lock:
            self._purge_expired(moment)
            if len(self._entries) >= self._max_entries:
                # Drop the oldest rather than refuse: a stale, unfetched open has no claim
                # on the store ahead of the one happening right now (mirrors AudioStore).
                oldest = min(self._entries, key=lambda k: self._entries[k].expires_at)
                self._entries.pop(oldest, None)
            self._entries[digest] = _Entry(target=target, expires_at=expires_at)
        return RenderFetchHandle(token=token, expires_at=expires_at)

    def take(self, token: str, *, now: datetime | None = None) -> RenderFetchTarget | None:
        """Redeem a token ONCE. ``None`` for an unknown, expired or already-redeemed
        token — deliberately indistinguishable, so a probe learns nothing from the
        difference (mirrors ``AudioStore.take``)."""
        moment = now or datetime.now(UTC)
        digest = hash_token(token)
        with self._lock:
            self._purge_expired(moment)
            entry = self._entries.pop(digest, None)
        if entry is None or entry.expires_at <= moment:
            return None
        return entry.target

    def _purge_expired(self, moment: datetime) -> None:
        expired = [k for k, v in self._entries.items() if v.expires_at <= moment]
        for key in expired:
            self._entries.pop(key, None)

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


#: Process-wide store, the same registry shape as ``app.alarms.audio_store``.
_store = RenderFetchStore()


def get_render_fetch_store() -> RenderFetchStore:
    return _store


def set_render_fetch_store(store: RenderFetchStore) -> None:
    """Tests (and alternative runtimes) swap the process-wide store."""
    global _store
    _store = store


__all__ = [
    "DEFAULT_TTL_S",
    "MAX_ENTRIES",
    "TOKEN_BYTES",
    "RenderFetchHandle",
    "RenderFetchStore",
    "RenderFetchTarget",
    "get_render_fetch_store",
    "set_render_fetch_store",
]
