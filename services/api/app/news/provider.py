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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Protocol

# `defusedxml`, not the stdlib parser. This repository added it as a hard dependency
# after an XML-bomb finding on the OOXML path (ADR-0085 addendum 6), and this is the one
# new network-facing parser this track adds - fed a body from the public internet,
# through a redirect chain we do not control. `ET.fromstring` here refuses entity
# expansion, external entities and DTDs outright; the size cap below bounds the rest.
from defusedxml import ElementTree as ET

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


#: Whoever can upload to (or briefly influence) a configured channel writes these
#: strings, and they reach the owner's ears verbatim: every news tool's registration tells
#: the model "Donen 'speech' metnini aynen oku". A title carrying newlines, control
#: characters or a few kilobytes of crafted text would be spoken as if it were the
#: assistant's own words, and handed back into the model's tool-call context as literal
#: content later turns can see. `app.mail.providers._sanitize_header` established the
#: pattern for exactly this class of input; the news feed had no equivalent.
MAX_TITLE_LEN: Final = 300
MAX_DESCRIPTION_LEN: Final = 2000
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")


def _sanitize_text(value: str | None, limit: int) -> str:
    """Fold every control character (newlines included) to a single space and bound the
    length - at the READ, the one place every candidate this module hands back passes
    through, rather than hoping each later speech/receipt/ledger site remembers."""
    if not value:
        return ""
    return _CONTROL_CHARS.sub(" ", value).strip()[:limit]


def _parse_feed(channel_id: str, xml_bytes: bytes) -> list[VideoCandidate]:
    root = ET.fromstring(xml_bytes)
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
                description = _sanitize_text(desc_el.text, MAX_DESCRIPTION_LEN)
        video_id = video_id_el.text
        out.append(
            VideoCandidate(
                video_id=video_id,
                title=_sanitize_text(title_el.text if title_el is not None else "", MAX_TITLE_LEN),
                published_at=published_at,
                channel_id=channel_id,
                url=f"https://www.youtube.com/watch?v={video_id}",
                description=description,
            )
        )
    return out


def fetch_feed_bytes(url: str, *, timeout_s: float, max_bytes: int) -> bytes:
    """The ONE place this module talks to the network, named so a test can replace it.

    Streamed and capped rather than `response.content`, which buffers the whole body
    before anyone can object to its size - the same discipline the M20 OOXML-bomb
    remediation established for the other parser this repository feeds from outside.

    It is a module-level function, not an inline `httpx` call, for a reason the suite
    paid for: three offline tests patched `httpx.get`, the call changed shape, the
    patches stopped matching, and those tests quietly began fetching the real YouTube
    feed. A named seam cannot be missed silently - remove it and the patch fails loudly.
    """
    import httpx

    try:
        with httpx.stream("GET", url, timeout=timeout_s, follow_redirects=True) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ProviderUnavailableError(f"channel feed exceeded {max_bytes} bytes")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError(f"channel feed request failed: {exc}") from exc


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
    #: A channel's upload feed is a few tens of kilobytes. Anything past this is not a
    #: feed, and `response.content` buffers the WHOLE body before the parser ever sees
    #: it - so the cap has to be applied while streaming, not after. The same discipline
    #: the M20 OOXML-bomb remediation established for the other parser this repo feeds
    #: from the outside world.
    max_bytes: int = 4 * 1024 * 1024

    def list_recent_uploads(self, channel_id: str) -> tuple[list[VideoCandidate], str]:
        url = YOUTUBE_FEED_URL_TEMPLATE.format(channel_id=channel_id)
        body = fetch_feed_bytes(url, timeout_s=self.timeout_s, max_bytes=self.max_bytes)
        try:
            candidates = _parse_feed(channel_id, body)
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
