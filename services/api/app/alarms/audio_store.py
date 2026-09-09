"""One-time tokens for greeting audio (M18.3 spec §3.7).

The companion has to FETCH the greeting WAV over HTTP, and it holds no owner session — it
is a Windows service talking to the broker origin, not a browser with a bearer token. So
the token IS the authority: 256 bits from ``secrets.token_urlsafe``, valid for one GET,
expiring five minutes after it was minted, and naming exactly one blob of bytes whose
sha256 also travels inside the signed device command payload. A fetch with the right token
gets the audio once; a second fetch with the same token gets nothing, so a token captured
from a log after the fact is already spent.

In-process and bounded on purpose. This is ephemeral delivery state, not a source of truth
(PROJECT_CONSTITUTION.md's Redis rule, applied one level down: if the process restarts
mid-alarm the greeting is re-synthesised, which costs a TTS call and loses nothing). The
store caps how many entries it will hold so a device that never fetches cannot grow it
without bound.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

#: Spec §3.7: five minutes, single use.
DEFAULT_TTL_S: Final = 300
#: Spec §5.4 caps the companion's own fetch at 2 MiB; refusing here too means an oversized
#: blob is rejected where it is created rather than after it has crossed the network.
MAX_AUDIO_BYTES: Final = 2 * 1024 * 1024
#: A greeting is one sentence and there is one owner; anything beyond this is a leak.
MAX_ENTRIES: Final = 32

#: Token length in BYTES before base64 (``token_urlsafe`` takes bytes): 32 -> 256 bits.
TOKEN_BYTES: Final = 32


class AudioTooLarge(ValueError):
    """The blob exceeds :data:`MAX_AUDIO_BYTES` (spec §5.4's own bound)."""


@dataclass(frozen=True, slots=True)
class AudioHandle:
    """What a caller needs to put in a ``desktop.play_audio`` payload."""

    token: str
    sha256: str
    size_bytes: int
    expires_at: datetime

    def path(self) -> str:
        return f"/v1/alarms/audio/{self.token}"


@dataclass(slots=True)
class _Entry:
    audio: bytes
    sha256: str
    expires_at: datetime
    content_type: str


class AudioStore:
    """Token -> bytes, five minute TTL, single use, thread-safe.

    Thread-safe because the routine clock's tick runs in a worker thread while the HTTP
    route that redeems the token runs on the event loop's thread pool: two threads, one
    dict, and a lock is cheaper than reasoning about whether CPython's GIL happens to make
    the check-then-delete atomic (it does not — that is two bytecodes).
    """

    def __init__(self, *, ttl_s: int = DEFAULT_TTL_S, max_entries: int = MAX_ENTRIES) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def put(
        self, audio: bytes, *, content_type: str = "audio/wav", now: datetime | None = None
    ) -> AudioHandle:
        if len(audio) > MAX_AUDIO_BYTES:
            raise AudioTooLarge(
                f"greeting audio is {len(audio)} bytes; the bound is {MAX_AUDIO_BYTES}"
            )
        moment = now or datetime.now(UTC)
        digest = hashlib.sha256(audio).hexdigest()
        token = secrets.token_urlsafe(TOKEN_BYTES)
        expires_at = moment + timedelta(seconds=self._ttl_s)
        with self._lock:
            self._purge_expired(moment)
            if len(self._entries) >= self._max_entries:
                # Drop the oldest rather than refuse: a stale, unfetched greeting has no
                # claim on the store ahead of the alarm that is ringing right now.
                oldest = min(self._entries, key=lambda k: self._entries[k].expires_at)
                self._entries.pop(oldest, None)
            self._entries[token] = _Entry(
                audio=audio, sha256=digest, expires_at=expires_at, content_type=content_type
            )
        return AudioHandle(token=token, sha256=digest, size_bytes=len(audio), expires_at=expires_at)

    def take(self, token: str, *, now: datetime | None = None) -> tuple[bytes, str] | None:
        """Redeem a token: the bytes and their content type, ONCE. ``None`` for an unknown,
        expired or already-redeemed token — the three are deliberately indistinguishable to
        the caller, so a probe learns nothing from the difference."""
        moment = now or datetime.now(UTC)
        with self._lock:
            self._purge_expired(moment)
            entry = self._entries.pop(token, None)
        if entry is None or entry.expires_at <= moment:
            return None
        return entry.audio, entry.content_type

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


#: Process-wide store, the same registry shape as
#: ``app.devices.commands.register_broker_runtime`` / ``get_broker_runtime``.
_store = AudioStore()


def get_audio_store() -> AudioStore:
    return _store


def set_audio_store(store: AudioStore) -> None:
    """Tests and alternative runtimes swap the process-wide store."""
    global _store
    _store = store


__all__ = [
    "DEFAULT_TTL_S",
    "MAX_AUDIO_BYTES",
    "MAX_ENTRIES",
    "TOKEN_BYTES",
    "AudioHandle",
    "AudioStore",
    "AudioTooLarge",
    "get_audio_store",
    "set_audio_store",
]
