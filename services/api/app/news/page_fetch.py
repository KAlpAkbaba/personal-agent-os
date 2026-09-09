"""The live page fetcher `resolve_channel_identity` needs, and nothing more than that.

`app.news.identity` has always taken `fetch_page` as an argument and never had a real one:
the seam existed so a channel could be resolved from an authoritative signal one day, and
until an owner actually configured a source there was nothing to resolve. This is that
implementation, written for the day it arrived.

It is deliberately small and deliberately suspicious:

* it is called ONLY when the owner is configuring a news source, never on a read path;
* the host is checked by `identity._is_youtube_host` BEFORE the call, and checked again
  HERE after redirects - a redirect off the allowlist is the SSRF the M26 news review named
  when the host check was still a substring test;
* it sends no cookies and no credentials, and it does not pretend to be a browser. The feed
  and the channel page both answer an honest client. If YouTube ever refuses one, that is a
  refusal to REPORT, not one to dress around: this repository does not evade anti-bot
  controls, and a User-Agent chosen to look like Chrome would be exactly that.
"""

from __future__ import annotations

import re
from typing import Final

from app.logging import get_logger

logger = get_logger("app.news.page_fetch")

#: A channel page is large (the real one measured 2.1 MB on 2026-09-09) but not unbounded.
MAX_PAGE_BYTES: Final = 8 * 1024 * 1024
DEFAULT_TIMEOUT_S: Final = 20.0
MAX_REDIRECTS: Final = 5

#: An honest identifier. Not a browser string: see the module docstring.
USER_AGENT: Final = "PersonalAgentOS/1.0 (owner-configured news source identity check)"


class PageFetchError(RuntimeError):
    """The page could not be fetched, for a reason worth telling the owner."""


class ConsentWallError(PageFetchError):
    """YouTube served a consent interstitial instead of the page.

    Measured 2026-09-09 from the Cloud Core in Hetzner NBG1: the channel page came back as
    34 KB titled "Bevor Sie zu YouTube weitergehen" - the EU consent form - where the
    owner's own machine received 2.1 MB of channel. This is its own error class because it
    has its own ANSWER: the owner can paste a `/channel/UC...` URL, which needs no fetch at
    all. It is never bypassed - not with a CONSENT cookie, not with a browser User-Agent -
    which is both this project's rule and the M26 spec's own words about consent walls.
    """


#: Markers of the interstitial, in the languages the datacentre's region can serve it in.
#: Matched against the TITLE only, so a channel whose description happens to discuss
#: cookies is not mistaken for a consent wall.
_CONSENT_TITLE_MARKERS: Final[tuple[str, ...]] = (
    "before you continue",
    "bevor sie zu youtube weitergehen",
    "avant de continuer",
    "antes de continuar",
    "prima di continuare",
    "youtube'a devam etmeden",
)


def _looks_like_consent_wall(html: str) -> bool:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if match is None:
        return False
    title = " ".join(match.group(1).split()).casefold()
    return any(marker in title for marker in _CONSENT_TITLE_MARKERS)


def fetch_channel_page(url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> str:
    """GET a YouTube channel page and return its HTML, bounded in every direction.

    Raises :class:`PageFetchError` rather than returning something empty, so a caller
    cannot mistake "the fetch failed" for "the page said nothing".
    """
    import httpx

    from app.news.identity import _is_youtube_host

    try:
        with httpx.Client(
            timeout=timeout_s,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        ) as client:
            with client.stream("GET", url) as response:
                # The host is checked before the call; this is the check AFTER, because a
                # redirect can land anywhere and the first check said nothing about where.
                final_host = response.url.host or ""
                if not _is_youtube_host(final_host):
                    raise PageFetchError(f"redirected off YouTube to {final_host!r}")
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_PAGE_BYTES:
                        raise PageFetchError(f"channel page exceeded {MAX_PAGE_BYTES} bytes")
                    chunks.append(chunk)
                body = b"".join(chunks)
    except PageFetchError:
        raise
    except httpx.HTTPError as exc:
        raise PageFetchError(f"channel page request failed: {exc}") from exc

    html = body.decode("utf-8", errors="replace")
    if _looks_like_consent_wall(html):
        logger.warning("news_channel_page_consent_wall", url=url[:120], bytes=len(body))
        raise ConsentWallError(
            "YouTube served a consent page to this server instead of the channel; it was "
            "NOT bypassed. Give a https://www.youtube.com/channel/UC... URL instead - the "
            "id is in the URL and needs no page fetch."
        )
    return html


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "ConsentWallError",
    "MAX_PAGE_BYTES",
    "MAX_REDIRECTS",
    "USER_AGENT",
    "PageFetchError",
    "fetch_channel_page",
]
