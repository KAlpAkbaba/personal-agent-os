"""Channel identity resolution (docs/M26_LATEST_NEWS_MODE_SPEC.md §1) — THE single most
important rule in this module: never guess a channel identity from a display name.
"Show Ana Haber" and a similarly-named channel are not interchangeable, and a wrong
guess would look right for months (task brief). A channel id is accepted ONLY from an
authoritative signal:

1. the owner gave the exact channel id already ("UC" + 22 chars);
2. the owner gave a ``/channel/UC...`` URL (the id is IN the URL, not guessed);
3. the owner gave a ``/@handle``, ``/c/name`` or ``/user/name`` URL, and a live provider
   lookup (the channel page's own ``<link rel="canonical">``, which YouTube itself
   emits) resolves it to a canonical channel id.

A bare display name (no URL at all) NEVER resolves here — that is exactly the case the
task brief means to leave as an owner action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

from app.news.provider import CHANNEL_ID_RE, looks_like_channel_id

RESOLVED_BY_DIRECT_ID = "direct_id"
RESOLVED_BY_CHANNEL_URL = "channel_url"
RESOLVED_BY_PROVIDER_LOOKUP = "provider_lookup"

_CANONICAL_LINK_RE = re.compile(
    r'<link\s+rel="canonical"\s+href="https://www\.youtube\.com/channel/(UC[0-9A-Za-z_-]{22})"'
)
_CHANNEL_ID_META_RE = re.compile(r'"channelId":"(UC[0-9A-Za-z_-]{22})"')


@dataclass(frozen=True, slots=True)
class ChannelIdentity:
    channel_id: str
    resolved_by: str


def _extract_channel_id_from_path(path: str) -> str | None:
    match = re.search(r"/channel/(UC[0-9A-Za-z_-]{22})", path)
    return match.group(1) if match else None


#: The only hosts a channel URL may name. Checked as an EXACT host or a dotted suffix,
#: never as a substring: the security review proved `"youtube.com" in netloc` accepts
#: `youtube.com.evil.example`, `notyoutube.com` and `evil-youtube.com.attacker.net`. That
#: is not a cosmetic bug. The whole point of this module is that a channel identity is
#: resolved from an authoritative source rather than guessed, and whoever fills the
#: documented `fetch_page` seam with a real fetcher would otherwise be handed an
#: attacker-chosen page - which both decides the persisted `channel_id` and makes the
#: fetch itself an SSRF primitive.
_YOUTUBE_HOSTS: Final[tuple[str, ...]] = ("youtube.com", "youtu.be", "youtube-nocookie.com")


def _is_youtube_host(netloc: str) -> bool:
    """Exact host, or a subdomain of one, case-insensitively and without its port."""
    host = netloc.split("@")[-1].split(":")[0].strip().rstrip(".").lower()
    if not host:
        return False
    return any(host == known or host.endswith(f".{known}") for known in _YOUTUBE_HOSTS)


def resolve_channel_identity(
    source_input: str, *, fetch_page: object | None = None
) -> ChannelIdentity | None:
    """Resolve ``source_input`` (whatever the owner gave — an id, a URL, or a bare
    name) to a :class:`ChannelIdentity`, or ``None`` when it cannot be established
    from an authoritative signal.

    ``fetch_page`` is an optional ``Callable[[str], str]`` (a page-fetcher, injected so
    tests never hit the network) used ONLY for the handle/name-URL case; when omitted,
    that one case is left unresolved (``None``) rather than silently skipped — a
    caller that wants live handle resolution must pass a real fetcher.
    """
    text = source_input.strip()
    if not text:
        return None

    # 1. Already a bare channel id.
    if looks_like_channel_id(text):
        return ChannelIdentity(text, RESOLVED_BY_DIRECT_ID)

    # A URL (with or without scheme) — never a bare display name, which has no path.
    parsed = urlparse(text if "://" in text else f"https://{text}")
    if not _is_youtube_host(parsed.netloc):
        return None  # not a YouTube URL and not a bare id: nothing authoritative here

    # 2. /channel/UC... — the id is literally in the URL.
    direct = _extract_channel_id_from_path(parsed.path)
    if direct is not None:
        return ChannelIdentity(direct, RESOLVED_BY_CHANNEL_URL)

    # 3. /@handle, /c/name, /user/name — needs a live provider lookup (the channel
    # page's own canonical link/metadata); never guessed from the handle text itself.
    if fetch_page is None:
        return None
    try:
        html = fetch_page(text if "://" in text else f"https://{text}")  # type: ignore[operator]
    except Exception:
        return None
    if not isinstance(html, str):
        return None
    match = _CANONICAL_LINK_RE.search(html) or _CHANNEL_ID_META_RE.search(html)
    if match is None:
        return None
    return ChannelIdentity(match.group(1), RESOLVED_BY_PROVIDER_LOOKUP)


__all__ = [
    "RESOLVED_BY_CHANNEL_URL",
    "RESOLVED_BY_DIRECT_ID",
    "RESOLVED_BY_PROVIDER_LOOKUP",
    "CHANNEL_ID_RE",
    "ChannelIdentity",
    "resolve_channel_identity",
]
