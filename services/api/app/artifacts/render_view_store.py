"""Bounded-read tokens for opening ONE report render in the owner's own browser (ADR-0210).

``app.artifacts.render_fetch_store`` mints the single-use token a device's ``file.fetch``
redeems: the fetcher is a program that GETs the URL exactly once. A browser tab is not that.
It may load the document again on a reload, on a session restore after Chrome restarts, on
back/forward (the response is ``no-store``, so nothing is served from cache), and the worker's
own navigation is one GET but not a promise about what Chrome does around it. Against a
single-use token every one of those is a bare 404 in the owner's tab, and a report that
vanishes when the page is reloaded is a defect, not a security property.

So a browser open gets its own token, from its own store, on its own route, and the two can
never be exchanged: a fetch token is unknown to this store and a view token is unknown to that
one.

What changes against the ``file.fetch`` token, all of it deliberate:

* it is redeemable up to ``MAX_READS`` times instead of once;
* it lives ``DEFAULT_TTL_S`` (fifteen minutes) instead of ten - long enough to read a report
  and reload it, short enough that the URL Chrome keeps in its history is worthless soon;
* it can be minted only for an ``html`` render (:data:`ALLOWED_FORMAT`). The route serves the
  bytes as a document a browser will interpret, so a format a browser would download or hand to
  another program is refused at mint time, not at serve time.

What does not change: 256 bits of ``secrets.token_urlsafe``, stored only as its SHA-256 (a
memory dump or a ``repr()`` of this store never hands out a redeemable token), one
(artifact, format, content_hash) per token with the hash pinned so a render that changed under
it is refused, in-process and bounded (there is nothing here worth making durable: a token
never redeemed just expires and the owner says "aç" again), unknown / expired / exhausted
tokens indistinguishable, and nothing that can list anything - there is no index, no
enumeration and no way to ask which tokens exist.
"""

from __future__ import annotations

import secrets
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from app.identity.tokens import hash_token

#: The only render format a view token can name.
ALLOWED_FORMAT: Final = "html"
#: Fifteen minutes: see the module docstring.
DEFAULT_TTL_S: Final = 900
#: How many times one token can be redeemed inside its lifetime: the worker's own load, a
#: reload or two, a session restore, a back/forward. Not a budget for sharing the link.
MAX_READS: Final = 6
#: Same bound as the fetch store: one owner's worth of concurrent opens.
MAX_ENTRIES: Final = 32
#: 32 bytes -> 256 bits, as every other token of this kind.
TOKEN_BYTES: Final = 32
#: The route the token is redeemed at, and the name of its one query parameter. The secret is
#: in the QUERY on purpose: the browser worker and the companion's audit both strip the query
#: string from anything they record (``browser_agent.errors.redact_url``), and they keep the
#: path.
VIEW_PATH: Final = "/v1/artifacts/renders/view"
TOKEN_PARAM: Final = "t"


class FormatNotViewableError(ValueError):
    """A view token was asked for a render a browser would not display."""


@dataclass(frozen=True, slots=True)
class RenderViewHandle:
    """What the open needs to build the URL the browser is sent to."""

    token: str
    expires_at: datetime

    def path(self) -> str:
        return f"{VIEW_PATH}?{TOKEN_PARAM}={self.token}"


@dataclass(frozen=True, slots=True)
class RenderViewTarget:
    """What a token resolves to, read back by the view route."""

    artifact_id: uuid.UUID
    fmt: str
    content_hash: str


@dataclass(slots=True)
class _Entry:
    target: RenderViewTarget
    expires_at: datetime
    reads_left: int


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RenderViewStore:
    """token hash -> (artifact_id, format, content_hash, reads left); TTL-bound, thread-safe."""

    def __init__(
        self,
        *,
        ttl_s: int = DEFAULT_TTL_S,
        max_reads: int = MAX_READS,
        max_entries: int = MAX_ENTRIES,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if max_reads < 1:
            raise ValueError("max_reads must be at least 1")
        self._ttl_s = ttl_s
        self._max_reads = max_reads
        self._max_entries = max_entries
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def put(self, *, artifact_id: uuid.UUID, fmt: str, content_hash: str) -> RenderViewHandle:
        if fmt != ALLOWED_FORMAT:
            # Refused before anything is stored: no token exists for a non-HTML render.
            raise FormatNotViewableError(f"only {ALLOWED_FORMAT!r} renders can be viewed")
        moment = self._clock()
        token = secrets.token_urlsafe(TOKEN_BYTES)
        digest = hash_token(token)
        expires_at = moment + timedelta(seconds=self._ttl_s)
        target = RenderViewTarget(artifact_id=artifact_id, fmt=fmt, content_hash=content_hash)
        with self._lock:
            self._purge_expired(moment)
            if len(self._entries) >= self._max_entries:
                oldest = min(self._entries, key=lambda k: self._entries[k].expires_at)
                self._entries.pop(oldest, None)
            self._entries[digest] = _Entry(
                target=target, expires_at=expires_at, reads_left=self._max_reads
            )
        return RenderViewHandle(token=token, expires_at=expires_at)

    def read(self, token: str) -> RenderViewTarget | None:
        """One redemption. ``None`` for an unknown, expired or exhausted token - deliberately
        indistinguishable, so a probe learns nothing from the difference."""
        moment = self._clock()
        digest = hash_token(token)
        with self._lock:
            self._purge_expired(moment)
            entry = self._entries.get(digest)
            if entry is None or entry.expires_at <= moment:
                self._entries.pop(digest, None)
                return None
            entry.reads_left -= 1
            if entry.reads_left <= 0:
                self._entries.pop(digest, None)
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


#: Process-wide store, the same registry shape as ``render_fetch_store``.
_store = RenderViewStore()


def get_render_view_store() -> RenderViewStore:
    return _store


def set_render_view_store(store: RenderViewStore) -> None:
    """Tests (and alternative runtimes) swap the process-wide store."""
    global _store
    _store = store


__all__ = [
    "ALLOWED_FORMAT",
    "DEFAULT_TTL_S",
    "MAX_READS",
    "TOKEN_BYTES",
    "TOKEN_PARAM",
    "VIEW_PATH",
    "FormatNotViewableError",
    "RenderViewHandle",
    "RenderViewStore",
    "RenderViewTarget",
    "get_render_view_store",
    "set_render_view_store",
]
