"""M13 ``browser.extract``: metadata / links / structured data from a live page.

All reads go through one DOM-level ``page.evaluate`` call (a normal
Playwright DOM read — see CLAUDE.md's browser rule: DOM/Playwright ranks
above accessibility tree, far above vision/coordinates; this module never
uses either of the latter). Nothing here classifies the page (that is
``page_kind.py``) or drives an action; it only reads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Page

MAX_LINKS = 200

# One JS pass over the DOM collecting every field this module needs, so
# extraction never round-trips the page more than once per call.
_EXTRACT_JS = r"""
() => {
  const byMeta = (sel) => {
    const el = document.querySelector(sel);
    return el ? el.getAttribute('content') : null;
  };
  const links = [];
  for (const a of document.querySelectorAll('a[href]')) {
    if (links.length >= 400) break;  // worker-side code applies the real cap
    links.push({ text: (a.textContent || '').trim(), href: a.getAttribute('href') || '' });
  }
  const jsonLd = [];
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    const text = s.textContent || '';
    if (text.trim()) jsonLd.push(text);
  }
  const heading = document.querySelector('h1, h2');
  return {
    title: document.title || '',
    heading_text: heading ? (heading.textContent || '').trim() : '',
    has_password_field: !!document.querySelector('input[type="password"]'),
    html_lang: document.documentElement
      ? (document.documentElement.getAttribute('lang') || null)
      : null,
    canonical_url: (document.querySelector('link[rel="canonical"]') || {}).href || null,
    description: byMeta('meta[name="description"]'),
    og_site_name: byMeta('meta[property="og:site_name"]'),
    article_published_time: byMeta('meta[property="article:published_time"]'),
    article_modified_time: byMeta('meta[property="article:modified_time"]'),
    meta_date: byMeta('meta[name="date"]'),
    meta_author: byMeta('meta[name="author"]'),
    time_datetime: (document.querySelector('time[datetime]') || {}).getAttribute
      ? document.querySelector('time[datetime]').getAttribute('datetime')
      : null,
    links: links,
    json_ld_raw: jsonLd,
  };
}
"""


@dataclass(frozen=True, slots=True)
class RawPageData:
    """Unprocessed DOM read; ``extract_metadata``/``extract_links`` shape it."""

    title: str
    heading_text: str
    has_password_field: bool
    html_lang: str | None
    canonical_url: str | None
    description: str | None
    og_site_name: str | None
    article_published_time: str | None
    article_modified_time: str | None
    meta_date: str | None
    meta_author: str | None
    time_datetime: str | None
    links: list[dict[str, str]]
    json_ld_raw: list[str]


_PRIMARY_TEXT_JS = (
    "() => { const el = document.querySelector('main, article'); "
    "return (el || document.body).innerText; }"
)


async def read_primary_text(page: Page) -> str:
    """Main/article region text, falling back to the whole body.

    ``browser.fetch_evidence``'s contract text: "main/article-first text,
    body fallback" — a plain DOM read (``document.querySelector`` +
    ``innerText``), same tier as ``BrowserSession.page_text()``, just scoped
    to the content region when the page marks one.
    """
    return await page.evaluate(_PRIMARY_TEXT_JS)


async def read_raw_page_data(page: Page, *, timeout_ms: float = 5_000) -> RawPageData:
    """One DOM read producing everything ``extract_metadata``/``extract_links`` need."""
    data = await page.evaluate(_EXTRACT_JS)
    return RawPageData(
        title=data.get("title") or "",
        heading_text=data.get("heading_text") or "",
        has_password_field=bool(data.get("has_password_field")),
        html_lang=data.get("html_lang"),
        canonical_url=data.get("canonical_url"),
        description=data.get("description"),
        og_site_name=data.get("og_site_name"),
        article_published_time=data.get("article_published_time"),
        article_modified_time=data.get("article_modified_time"),
        meta_date=data.get("meta_date"),
        meta_author=data.get("meta_author"),
        time_datetime=data.get("time_datetime"),
        links=list(data.get("links") or []),
        json_ld_raw=list(data.get("json_ld_raw") or []),
    )


def parse_json_ld(raw_blocks: list[str]) -> list[Any]:
    """Best-effort parse of each ``<script type="application/ld+json">`` block.

    A block that fails to parse is skipped (never raises) — malformed JSON-LD
    on a real-world page must not abort extraction of everything else.
    """
    parsed: list[Any] = []
    for block in raw_blocks:
        try:
            value = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        parsed.append(value)
    return parsed


def _json_ld_items(parsed: list[Any]) -> list[dict[str, Any]]:
    """Flatten JSON-LD values (object, or ``@graph``/list of objects) to dicts."""
    items: list[dict[str, Any]] = []
    for value in parsed:
        if isinstance(value, dict):
            if isinstance(value.get("@graph"), list):
                items.extend(v for v in value["@graph"] if isinstance(v, dict))
            else:
                items.append(value)
        elif isinstance(value, list):
            items.extend(v for v in value if isinstance(v, dict))
    return items


def _first_json_ld_value(items: list[dict[str, Any]], *keys: str) -> str | None:
    for item in items:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def _json_ld_publisher(items: list[dict[str, Any]]) -> str | None:
    for item in items:
        publisher = item.get("publisher")
        if isinstance(publisher, str) and publisher.strip():
            return publisher
        if isinstance(publisher, dict):
            name = publisher.get("name")
            if isinstance(name, str) and name.strip():
                return name
    return None


def _json_ld_author(items: list[dict[str, Any]]) -> str | None:
    for item in items:
        author = item.get("author")
        if isinstance(author, str) and author.strip():
            return author
        if isinstance(author, dict):
            name = author.get("name")
            if isinstance(name, str) and name.strip():
                return name
        if isinstance(author, list):
            for entry in author:
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    return entry["name"]
    return None


# A conservative set of absolute-date formats seen in real meta/time tags.
# ISO-8601 (with or without a timezone) is tried first via ``fromisoformat``;
# these are the additional non-ISO shapes worth normalising rather than
# leaving as an opaque string.
_FALLBACK_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%A, %B %d, %Y",
)


def normalize_date_to_iso_utc(raw: str | None) -> str | None:
    """Best-effort normalisation to ISO-8601 UTC (``...Z``).

    Never guesses: an unparseable string is returned unchanged (not dropped,
    not invented) so the caller still has the source value; only a value
    that actually parses as a date/time is rewritten. Absent input stays
    ``None`` (contract: "absent -> null, never guessed").
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    iso_candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in _FALLBACK_DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class PageMetadata:
    canonical_url: str | None
    published_at: str | None
    modified_at: str | None
    publisher: str | None
    author: str | None
    language: str | None
    description: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "canonical_url": self.canonical_url,
            "published_at": self.published_at,
            "modified_at": self.modified_at,
            "publisher": self.publisher,
            "author": self.author,
            "language": self.language,
            "description": self.description,
        }


def build_metadata(raw: RawPageData) -> PageMetadata:
    """Shape a :class:`RawPageData` read into contract §3's metadata object.

    Source priority (contract order): ``article:published_time`` ->
    ``meta[name=date]`` -> ``<time datetime>`` -> JSON-LD ``datePublished``
    for ``published_at``; ``article:modified_time`` -> JSON-LD
    ``dateModified`` for ``modified_at``; ``og:site_name`` -> JSON-LD
    ``publisher`` for publisher; JSON-LD ``author`` -> ``meta[name=author]``
    for author (not an explicit contract source list, but a declared page
    signal, never a guess); ``<html lang>`` for language; ``description``
    from ``meta[name=description]`` only — absent stays ``null``.
    """
    json_ld = _json_ld_items(parse_json_ld(raw.json_ld_raw))

    published_raw = (
        raw.article_published_time
        or raw.meta_date
        or raw.time_datetime
        or _first_json_ld_value(json_ld, "datePublished")
    )
    modified_raw = raw.article_modified_time or _first_json_ld_value(json_ld, "dateModified")

    publisher = raw.og_site_name or _json_ld_publisher(json_ld)
    author = _json_ld_author(json_ld) or raw.meta_author

    return PageMetadata(
        canonical_url=raw.canonical_url or None,
        published_at=normalize_date_to_iso_utc(published_raw),
        modified_at=normalize_date_to_iso_utc(modified_raw),
        publisher=publisher or None,
        author=author or None,
        language=raw.html_lang or None,
        description=raw.description or None,
    )


_ALLOWED_LINK_SCHEMES = frozenset({"http", "https"})


def build_links(
    raw_links: list[dict[str, str]], *, base_url: str, limit: int = MAX_LINKS
) -> list[dict[str, str]]:
    """Resolve, filter (http/https only) and cap the page's outbound links."""
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for entry in raw_links:
        href = (entry.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        absolute = urljoin(base_url, href)
        scheme = urlsplit(absolute).scheme.lower()
        if scheme not in _ALLOWED_LINK_SCHEMES:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        result.append({"text": (entry.get("text") or "").strip(), "href": absolute})
        if len(result) >= limit:
            break
    return result


def truncate_text(text: str, *, max_chars: int) -> tuple[str, bool]:
    """Truncate to ``max_chars`` (whitespace is left as-is). Returns ``(text, truncated)``."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


__all__ = [
    "MAX_LINKS",
    "PageMetadata",
    "RawPageData",
    "build_links",
    "build_metadata",
    "normalize_date_to_iso_utc",
    "parse_json_ld",
    "read_primary_text",
    "read_raw_page_data",
    "truncate_text",
]
