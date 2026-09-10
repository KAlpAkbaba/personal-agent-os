"""Choose ONE video from the device's own search results (ADR-0112).

A pure function of a result list, so what gets played is decided by code with a
test rather than by whatever the search engine happened to rank first. Nothing
here fetches anything.

Two rules, and they are the whole module:

**A watch URL or nothing.** Only ``youtube.com/watch?v=<id>`` and ``youtu.be/<id>``
count. A channel page, a playlist, a Short, a search-results link and a
lookalike host (``youtube.com.evil.tld``) are all refused, because
``browser.media_play`` proves a ``<video>`` element advanced -- pointing it at a
channel page would produce an honest failure the owner would hear as "I could
not verify it", which is a worse answer than "I could not find it".

**Rank order, never a similarity score.** The engine already ranked them. Any
re-ranking here would be this module inventing a judgement it cannot defend to
the owner, and the owner can always say "hayır, diğeri".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit

#: Hosts whose ``/watch?v=`` is a real YouTube video page. Compared against the
#: parsed hostname, never with ``in`` -- ``"youtube.com" in host`` is true for
#: ``youtube.com.evil.tld``, which is the substring-host bug the M26 news review
#: found in this repository once already.
_WATCH_HOSTS: Final[frozenset[str]] = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
)
#: ``youtu.be/<id>`` puts the id in the path instead of a query parameter.
_SHORT_HOSTS: Final[frozenset[str]] = frozenset({"youtu.be", "www.youtu.be"})

#: YouTube video ids are 11 characters of URL-safe base64.
_VIDEO_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{11}$")

MAX_TITLE_CHARS: Final[int] = 200
#: More than this many results is the caller asking for something odd; the
#: engine's own cap is 20 (``browser_agent.search_engines.MAX_RESULTS_CAP``).
MAX_RESULTS_CONSIDERED: Final[int] = 20


@dataclass(frozen=True, slots=True)
class Candidate:
    """One video this module is willing to hand to ``browser.media_play``."""

    video_id: str
    #: Canonical watch URL built from the id, never the engine's own string: a
    #: SERP link can carry tracking parameters and redirect wrappers, and the
    #: device validates what it is given against its destination policy.
    url: str
    title: str
    rank: int


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def video_id_from_url(raw: Any) -> str | None:
    """The video id this URL names, or ``None``.

    ``None`` for anything that is not a watch page -- including
    ``/shorts/<id>``, ``/playlist``, ``/@channel`` and ``/results?search_query=``.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if host in _WATCH_HOSTS:
        if parts.path.rstrip("/") != "/watch":
            return None
        values = parse_qs(parts.query).get("v") or []
        candidate = values[0] if values else ""
    elif host in _SHORT_HOSTS:
        candidate = parts.path.lstrip("/").split("/")[0]
    else:
        return None
    return candidate if _VIDEO_ID_RE.match(candidate) else None


def pick_video(results: Any) -> Candidate | None:
    """The first result that is a real watch URL, in the engine's own order."""
    if not isinstance(results, list):
        return None
    for index, entry in enumerate(results[:MAX_RESULTS_CONSIDERED]):
        if not isinstance(entry, dict):
            continue
        video_id = video_id_from_url(entry.get("url"))
        if video_id is None:
            continue
        raw_title = entry.get("title")
        title = raw_title.strip()[:MAX_TITLE_CHARS] if isinstance(raw_title, str) else ""
        rank = entry.get("rank")
        return Candidate(
            video_id=video_id,
            url=canonical_url(video_id),
            title=title,
            rank=int(rank) if isinstance(rank, int) and not isinstance(rank, bool) else index + 1,
        )
    return None


def search_query(spoken: str) -> str:
    """What to hand ``browser.search``.

    The owner's words plus ``youtube``, and nothing clever: an engine query is
    not the place to guess at what they meant. ``site:`` is deliberately NOT
    used -- it makes Google return a results page the SERP parser reads as
    empty often enough to matter, and the ranking already puts YouTube first for
    a query that names a song.
    """
    words = " ".join(str(spoken or "").split())[:200]
    lowered = words.lower()
    if "youtube" in lowered:
        return words
    return f"{words} youtube".strip()


__all__ = [
    "MAX_RESULTS_CONSIDERED",
    "MAX_TITLE_CHARS",
    "Candidate",
    "canonical_url",
    "pick_video",
    "search_query",
    "video_id_from_url",
]
