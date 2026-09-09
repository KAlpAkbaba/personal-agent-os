"""Where resolver candidates come from (docs/M26_LATEST_NEWS_MODE_SPEC.md §2, §3, §4).

Discovery preference order (spec §2, highest semantic surface first — the same
"prefer the most authoritative signal" discipline ``app.research.sources`` already
uses for official publishers):

1. the official channel feed/API (:class:`YouTubeFeedProvider` — YouTube's own public
   Atom feed per channel, real ``published`` timestamps, Cloud Core ``httpx``, no key,
   no browser);
2. the channel's Videos listing in newest order — NOT implemented (honest gap, see the
   module docstring below);
3. structured DOM extraction of the exact channel — NOT implemented (same gap).

Both unimplemented tiers would need the governed browser worker (the same ``news``
profile playback uses) to drive a real page, which this offline development
environment cannot qualify end-to-end; :class:`NewsUploadProvider` is the seam a later
track can fill in without touching the resolver or anything upstream of it.
:class:`FixtureNewsProvider` is deterministic (spec §3) and is what makes the resolver
testable every day without any of this.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.news.classification import VideoCandidate
from app.news.models import ANSWERED_BY_CHANNEL_FEED, ANSWERED_BY_FIXTURE

#: YouTube's own per-channel Atom feed (no API key, real upload timestamps). Documented
#: as the "official channel feed" tier (spec §2's own preference order).
YOUTUBE_FEED_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

_ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


class ProviderUnavailableError(RuntimeError):
    """The provider could not answer at all (network error, malformed feed, ...) — this
    is DIFFERENT from "the channel has zero uploads": a caller must never read this as
    "no candidates", only as "no answer" (task brief: "if provider data cannot
    establish upload order, say so and do not claim 'latest'")."""


class NewsUploadProvider(Protocol):
    def list_recent_uploads(self, channel_id: str) -> tuple[list[VideoCandidate], str]:
        """Returns ``(candidates, answered_by)``. Raises
        :class:`ProviderUnavailableError` when it cannot answer at all."""
        ...


@dataclass
class FixtureNewsProvider:
    """A deterministic, in-memory provider (spec §3): known publish times, for the
    resolver's own test fixtures. Never touches the network."""

    channels: dict[str, list[VideoCandidate]] = field(default_factory=dict)

    def list_recent_uploads(self, channel_id: str) -> tuple[list[VideoCandidate], str]:
        return list(self.channels.get(channel_id, [])), ANSWERED_BY_FIXTURE


def _parse_feed(channel_id: str, xml_bytes: bytes) -> list[VideoCandidate]:
    root = ET.fromstring(xml_bytes)  # noqa: S314 - our own known-schema fetch, not owner input
    out: list[VideoCandidate] = []
    for entry in root.findall("a:entry", _ATOM_NS):
        video_id_el = entry.find("yt:videoId", _ATOM_NS)
        title_el = entry.find("a:title", _ATOM_NS)
        published_el = entry.find("a:published", _ATOM_NS)
        if video_id_el is None or video_id_el.text is None:
            continue
        if published_el is None or published_el.text is None:
            continue  # spec §2: never guess a publish date — skip, don't invent "now"
        published_at = datetime.fromisoformat(published_el.text.replace("Z", "+00:00"))
        description = ""
        group = entry.find("media:group", _ATOM_NS)
        if group is not None:
            desc_el = group.find("media:description", _ATOM_NS)
            if desc_el is not None and desc_el.text:
                description = desc_el.text
        video_id = video_id_el.text
        out.append(
            VideoCandidate(
                video_id=video_id,
                title=(title_el.text or "") if title_el is not None else "",
                published_at=published_at,
                channel_id=channel_id,
                url=f"https://www.youtube.com/watch?v={video_id}",
                description=description,
            )
        )
    return out


@dataclass
class YouTubeFeedProvider:
    """The real, live path (spec §4's own "official channel feed/API" preference): one
    ``httpx`` GET of the channel's public Atom feed. No API key, no browser, no
    scraping of anything but a feed YouTube itself publishes for exactly this purpose.
    Duration is never present in this feed, so Shorts classification for its
    candidates falls back to the text/URL markers in ``app.news.classification``
    (documented there) rather than a duration threshold.
    """

    timeout_s: float = 10.0

    def list_recent_uploads(self, channel_id: str) -> tuple[list[VideoCandidate], str]:
        import httpx

        url = YOUTUBE_FEED_URL_TEMPLATE.format(channel_id=channel_id)
        try:
            response = httpx.get(url, timeout=self.timeout_s, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"channel feed request failed: {exc}") from exc
        try:
            candidates = _parse_feed(channel_id, response.content)
        except ET.ParseError as exc:
            raise ProviderUnavailableError(f"channel feed did not parse: {exc}") from exc
        return candidates, ANSWERED_BY_CHANNEL_FEED


#: A syntactically valid YouTube channel id: "UC" + 22 URL-safe chars (24 total).
CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")


def looks_like_channel_id(value: str) -> bool:
    return bool(CHANNEL_ID_RE.match(value))


__all__ = [
    "CHANNEL_ID_RE",
    "YOUTUBE_FEED_URL_TEMPLATE",
    "FixtureNewsProvider",
    "NewsUploadProvider",
    "ProviderUnavailableError",
    "YouTubeFeedProvider",
    "looks_like_channel_id",
]
