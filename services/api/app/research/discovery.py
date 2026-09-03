"""Query -> candidate-URL discovery for the "official"/"technical"/"academic"
source classes (M13 spec §2): HN Algolia (technical), arXiv Atom API
(academic), and generic RSS/Atom parsing with the stdlib ``xml`` module
(official, via ``app.research.sources``). "news"/"community" discovery
happens on the device (``browser.search`` — see ``app.research.browser_gateway``).

Every function here is split into a pure half (build the request / parse the
response — unit-tested with fixture text, never touches the network) and an
I/O half (``fetch_*`` — httpx, bounded by a timeout, lazily imported so
importing this module never requires a socket). Unit tests exercise only the
pure half; a live opt-in test (``@pytest.mark.live``) exercises ``fetch_*``
against the real endpoints and is never run by default CI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

DEFAULT_TIMEOUT_S = 10.0
HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"
ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

#: A discovery HTTP response is bounded at 2 MiB (finding MEDIUM-8): a
#: publisher feed or third-party API is untrusted, and an unbounded response
#: body could otherwise be read entirely into memory before anything checks
#: its size. The body is streamed and cut off as soon as it would exceed the
#: cap; oversize is a DiscoveryError (a recorded discovery failure the caller
#: already treats as "this one source failed", per every fetch_* docstring
#: below), never an unbounded read.
MAX_DISCOVERY_BODY_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DiscoveredCandidate:
    """A candidate URL from discovery — ``ResearchSource``/``research_candidates``
    shape (M13 spec §1/§5), before it has been fetched through the device."""

    url: str
    title: str
    publisher: str
    discovered_by: str  # "hn" | "arxiv" | "rss" | "browser_search"
    query_id: str
    published_hint: str | None = None


class DiscoveryError(RuntimeError):
    """A discovery HTTP call failed or returned an unparseable body."""


def _bounded_get_text(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = MAX_DISCOVERY_BODY_BYTES,
    transport: Any = None,
) -> str:
    """GET (streaming) ``url``, stopping and raising :class:`DiscoveryError`
    the moment the body would exceed ``max_bytes`` — the body is never read
    unbounded into memory before its size is known. ``transport`` is
    injectable (an ``httpx.MockTransport``) so this bound, and the three
    ``fetch_*`` functions built on it, are unit-tested with a fake transport
    rather than a real network call."""
    import httpx

    client_kwargs: dict[str, Any] = {"timeout": timeout_s}
    if transport is not None:
        client_kwargs["transport"] = transport
    try:
        with httpx.Client(**client_kwargs) as client:
            with client.stream(method, url, params=params) as resp:
                resp.raise_for_status()
                total = 0
                chunks: list[bytes] = []
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise DiscoveryError(
                            f"response body for {url} exceeded {max_bytes} bytes"
                        )
                    chunks.append(chunk)
                encoding = resp.encoding or "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace")
    except httpx.HTTPError as exc:
        raise DiscoveryError(f"request failed for {url}: {type(exc).__name__}") from exc


# --------------------------------------------------------------- HN Algolia


def build_hn_request(
    query: str, *, window_start: datetime, max_results: int = 10
) -> tuple[str, dict[str, Any]]:
    params = {
        "query": query,
        "tags": "story",
        "numericFilters": f"created_at_i>={int(window_start.timestamp())}",
        "hitsPerPage": max_results,
    }
    return HN_ALGOLIA_URL, params


def parse_hn_response(payload: dict[str, Any], *, query_id: str) -> list[DiscoveredCandidate]:
    out: list[DiscoveredCandidate] = []
    for hit in payload.get("hits", []) or []:
        url = hit.get("url") or (
            f"https://news.ycombinator.com/item?id={hit['objectID']}"
            if hit.get("objectID")
            else None
        )
        if not url:
            continue
        out.append(
            DiscoveredCandidate(
                url=str(url),
                title=str(hit.get("title") or ""),
                publisher="Hacker News",
                discovered_by="hn",
                query_id=query_id,
                published_hint=hit.get("created_at"),
            )
        )
    return out


def fetch_hn(
    query: str,
    *,
    window_start: datetime,
    max_results: int = 10,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    transport: Any = None,
) -> list[DiscoveredCandidate]:
    url, params = build_hn_request(query, window_start=window_start, max_results=max_results)
    text = _bounded_get_text("GET", url, params=params, timeout_s=timeout_s, transport=transport)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise DiscoveryError("hn algolia response was not valid JSON") from exc
    return parse_hn_response(payload, query_id=query)


# -------------------------------------------------------------------- arXiv


def build_arxiv_request(query: str, *, max_results: int = 10) -> tuple[str, dict[str, Any]]:
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    return ARXIV_API_URL, params


def parse_arxiv_response(xml_text: str, *, query_id: str) -> list[DiscoveredCandidate]:
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 - trusted API response, stdlib parser
    except ET.ParseError as exc:
        raise DiscoveryError(f"arxiv response was not valid XML: {exc}") from None
    out: list[DiscoveredCandidate] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        title_el = entry.find("atom:title", ATOM_NS)
        id_el = entry.find("atom:id", ATOM_NS)
        published_el = entry.find("atom:published", ATOM_NS)
        url = (id_el.text or "").strip() if id_el is not None and id_el.text else ""
        if not url:
            continue
        title = (title_el.text or "").strip() if title_el is not None and title_el.text else ""
        out.append(
            DiscoveredCandidate(
                url=url,
                title=" ".join(title.split()),
                publisher="arXiv",
                discovered_by="arxiv",
                query_id=query_id,
                published_hint=published_el.text if published_el is not None else None,
            )
        )
    return out


def fetch_arxiv(
    query: str,
    *,
    max_results: int = 10,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    transport: Any = None,
) -> list[DiscoveredCandidate]:
    url, params = build_arxiv_request(query, max_results=max_results)
    text = _bounded_get_text("GET", url, params=params, timeout_s=timeout_s, transport=transport)
    return parse_arxiv_response(text, query_id=query)


# --------------------------------------------------------------- RSS / Atom


def parse_rss_or_atom(
    xml_text: str, *, publisher: str, query_id: str, max_items: int = 20
) -> list[DiscoveredCandidate]:
    """Parses either RSS 2.0 (``<item>``) or Atom (``<entry>``) with the
    stdlib ``xml.etree`` parser — no third-party feed library."""
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 - trusted publisher feed, stdlib parser
    except ET.ParseError as exc:
        raise DiscoveryError(f"feed was not valid XML: {exc}") from None

    items = root.findall(".//item")
    if items:
        out: list[DiscoveredCandidate] = []
        for item in items[:max_items]:
            link = (item.findtext("link") or "").strip()
            if not link:
                continue
            out.append(
                DiscoveredCandidate(
                    url=link,
                    title=(item.findtext("title") or "").strip(),
                    publisher=publisher,
                    discovered_by="rss",
                    query_id=query_id,
                    published_hint=item.findtext("pubDate"),
                )
            )
        return out

    out = []
    for entry in root.findall("atom:entry", ATOM_NS)[:max_items]:
        link_el = entry.find("atom:link", ATOM_NS)
        href = link_el.get("href") if link_el is not None else None
        if not href:
            continue
        title = entry.findtext("atom:title", namespaces=ATOM_NS) or ""
        updated = entry.findtext("atom:updated", namespaces=ATOM_NS)
        out.append(
            DiscoveredCandidate(
                url=href,
                title=title.strip(),
                publisher=publisher,
                discovered_by="rss",
                query_id=query_id,
                published_hint=updated,
            )
        )
    return out


def fetch_rss(
    feed_url: str,
    *,
    publisher: str,
    query_id: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    transport: Any = None,
) -> list[DiscoveredCandidate]:
    text = _bounded_get_text("GET", feed_url, timeout_s=timeout_s, transport=transport)
    return parse_rss_or_atom(text, publisher=publisher, query_id=query_id)


__all__ = [
    "ARXIV_API_URL",
    "DEFAULT_TIMEOUT_S",
    "HN_ALGOLIA_URL",
    "MAX_DISCOVERY_BODY_BYTES",
    "DiscoveredCandidate",
    "DiscoveryError",
    "build_arxiv_request",
    "build_hn_request",
    "fetch_arxiv",
    "fetch_hn",
    "fetch_rss",
    "parse_arxiv_response",
    "parse_hn_response",
    "parse_rss_or_atom",
]
