"""M13 ``browser.search``: SERP parsing + ``auto`` engine fallover (contract §3).

Parsing is DOM-shaped, not vision: each engine's rendered result HTML (read
via the real browser, then handed here as a string — see
:func:`run_search`'s ``fetch`` seam) is parsed with a semantic reading of its
structure (result container -> title link -> snippet), the same information
a ``role=link`` accessibility read would surface, filtering out ads/sponsored
entries and the engine's own domains. The parsers are pure functions of an
HTML string so they are unit-testable from saved fixture files with no
browser process (see ``tests/unit/test_search_engines.py``); the live worker
path feeds them ``await page.content()`` after a real Chrome navigation.

``run_search`` implements the ``auto`` fallover: try each engine in order,
skip to the next on ``captcha``/``blocked``/``empty``, and raise
``provider_rate_limited`` (retryable) when every attempted engine ended
``captcha`` or ``blocked`` (never solving a CAPTCHA itself).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from bs4 import BeautifulSoup
from bs4.element import Tag

from .errors import BrowserError, ErrorClass

ENGINES: tuple[str, ...] = ("duckduckgo", "bing", "brave")
AUTO_ORDER: tuple[str, ...] = ("duckduckgo", "bing", "brave")
MAX_RESULTS_CAP = 20

_ENGINE_OWN_DOMAINS: dict[str, tuple[str, ...]] = {
    "duckduckgo": ("duckduckgo.com",),
    "bing": ("bing.com", "microsoft.com", "msn.com"),
    "brave": ("brave.com", "search.brave.com"),
}

_PUBLISHED_HINT_RE = re.compile(
    r"\b\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago\b", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    published_hint: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "published_hint": self.published_hint,
        }


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    engine: str
    query: str
    results: tuple[SearchResult, ...]
    page_kind: str

    def as_dict(self) -> dict[str, object]:
        return {
            "engine": self.engine,
            "query": self.query,
            "results": [r.as_dict() for r in self.results],
            "page_kind": self.page_kind,
        }


def _extract_published_hint(text: str) -> str | None:
    match = _PUBLISHED_HINT_RE.search(text)
    return match.group(0) if match else None


def _is_own_domain(url: str, engine: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in _ENGINE_OWN_DOMAINS[engine])


def _recency_param(engine: str, recency_days: int | None) -> dict[str, str]:
    """Best-effort mapping of ``recency_days`` to each engine's own recency
    query parameter (contract §3: "recency_days mapped to each engine's own
    recency parameter where it exists"). ``None`` -> no parameter (engine
    default, unfiltered)."""
    if recency_days is None:
        return {}
    if recency_days <= 1:
        bucket = "day"
    elif recency_days <= 7:
        bucket = "week"
    elif recency_days <= 31:
        bucket = "month"
    else:
        bucket = "year"
    if engine == "duckduckgo":
        return {"df": {"day": "d", "week": "w", "month": "m", "year": "y"}[bucket]}
    if engine == "bing":
        return {
            "freshness": {"day": "Day", "week": "Week", "month": "Month", "year": "Year"}[bucket]
        }
    if engine == "brave":
        return {"tf": {"day": "pd", "week": "pw", "month": "pm", "year": "py"}[bucket]}
    return {}


def build_search_url(engine: str, query: str, *, recency_days: int | None = None) -> str:
    """The engine's search-results URL for ``query`` (contract §3 base URLs)."""
    params = {"q": query, **_recency_param(engine, recency_days)}
    if engine == "duckduckgo":
        return f"https://html.duckduckgo.com/html/?{urlencode(params)}"
    if engine == "bing":
        return f"https://www.bing.com/search?{urlencode(params)}"
    if engine == "brave":
        return f"https://search.brave.com/search?{urlencode(params)}"
    raise BrowserError(
        ErrorClass.VALIDATION_ERROR,
        f"unknown search engine {engine!r}; known: {', '.join(ENGINES)}",
        retryable=False,
    )


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def parse_duckduckgo_html(html: str, *, max_results: int = 10) -> list[SearchResult]:
    """Parse DuckDuckGo's server-rendered HTML results page (html.duckduckgo.com)."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[SearchResult] = []
    for block in soup.select("div.result"):
        classes = block.get("class") or []
        if any("result--ad" in c or c == "result--ads" for c in classes):
            continue
        title_link = block.select_one("a.result__a")
        if title_link is None or not title_link.get("href"):
            continue
        url = str(title_link["href"])
        if _is_own_domain(url, "duckduckgo"):
            continue
        snippet_el = block.select_one("a.result__snippet") or block.select_one(
            "div.result__snippet"
        )
        snippet = _clean(snippet_el.get_text() if snippet_el else "")
        results.append(
            SearchResult(
                url=url,
                title=_clean(title_link.get_text()),
                snippet=snippet,
                published_hint=_extract_published_hint(snippet),
            )
        )
        if len(results) >= max_results:
            break
    return results


def parse_bing_html(html: str, *, max_results: int = 10) -> list[SearchResult]:
    """Parse Bing's results page (``#b_results li.b_algo`` organic results)."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[SearchResult] = []
    container = soup.select_one("#b_results") or soup
    for item in container.select("li"):
        classes = item.get("class") or []
        if "b_ad" in classes or "b_algo" not in classes:
            continue
        title_link = item.select_one("h2 a[href]")
        if title_link is None:
            continue
        url = str(title_link["href"])
        if _is_own_domain(url, "bing"):
            continue
        caption = item.select_one(".b_caption") or item
        snippet = _clean(caption.get_text())
        results.append(
            SearchResult(
                url=url,
                title=_clean(title_link.get_text()),
                snippet=snippet,
                published_hint=_extract_published_hint(snippet),
            )
        )
        if len(results) >= max_results:
            break
    return results


def parse_brave_html(html: str, *, max_results: int = 10) -> list[SearchResult]:
    """Parse Brave Search's results page (``.snippet[data-type=web]`` organic results)."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[SearchResult] = []
    for block in soup.select(".snippet"):
        if block.get("data-type") not in (None, "web"):
            continue  # drops data-type="ad" and other non-organic snippet kinds
        link: Tag | None = block.find("a", href=True)
        if link is None:
            continue
        url = str(link["href"])
        if _is_own_domain(url, "brave"):
            continue
        title_el = block.select_one(".title") or link
        desc_el = block.select_one(".snippet-description") or block.select_one(".description")
        snippet = _clean(desc_el.get_text() if desc_el else "")
        results.append(
            SearchResult(
                url=url,
                title=_clean(title_el.get_text()),
                snippet=snippet,
                published_hint=_extract_published_hint(snippet),
            )
        )
        if len(results) >= max_results:
            break
    return results


_PARSERS: dict[str, Callable[..., list[SearchResult]]] = {
    "duckduckgo": parse_duckduckgo_html,
    "bing": parse_bing_html,
    "brave": parse_brave_html,
}


def parse_engine_html(engine: str, html: str, *, max_results: int = 10) -> list[SearchResult]:
    parser = _PARSERS.get(engine)
    if parser is None:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"unknown search engine {engine!r}; known: {', '.join(ENGINES)}",
            retryable=False,
        )
    return parser(html, max_results=max_results)


#: One fetch attempt's outcome: rendered HTML, the page's classified kind,
#: and the HTTP status (worker supplies this from a real navigation).
FetchFn = Callable[[str, str], Awaitable[tuple[str, str, int | None]]]

_TERMINAL_FALLOVER_KINDS = frozenset({"captcha", "blocked"})
_SKIP_KINDS = frozenset({"captcha", "blocked", "empty"})


async def run_search(
    query: str,
    engine: str,
    *,
    fetch: FetchFn,
    max_results: int = 10,
    recency_days: int | None = None,
) -> SearchOutcome:
    """Run a search, trying engines in order for ``engine="auto"``.

    ``fetch(engine, url)`` performs the real navigation + page_kind read and
    returns ``(html, page_kind, http_status)`` — injected so this function
    (and its fallover/terminal-error logic) is unit-testable without a
    browser (see ``tests/unit/test_search_engines.py``); the live worker path
    supplies a closure that drives the real session.
    """
    if max_results < 1 or max_results > MAX_RESULTS_CAP:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"max_results must be between 1 and {MAX_RESULTS_CAP}, got {max_results}",
            retryable=False,
        )
    order = AUTO_ORDER if engine == "auto" else (engine,)
    if engine != "auto" and engine not in ENGINES:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"unknown search engine {engine!r}; known: auto, {', '.join(ENGINES)}",
            retryable=False,
        )

    attempted_kinds: list[str] = []
    last_kind = "empty"
    auto_mode = engine == "auto"
    for eng in order:
        url = build_search_url(eng, query, recency_days=recency_days)
        try:
            html, page_kind, _http_status = await fetch(eng, url)
        except BrowserError:
            # A genuine browser/transport failure reaching THIS engine (e.g.
            # a network-level abort from anti-bot filtering, observed live
            # against Brave Search: net::ERR_ABORTED) is at least as good a
            # reason to try the next engine as a captcha/blocked page_kind
            # is — "auto" exists precisely for this resilience. An
            # explicitly-requested single engine gets no such fallover: its
            # transport failure is accurate and actionable as-is, so it
            # propagates unchanged.
            if not auto_mode:
                raise
            attempted_kinds.append("blocked")
            last_kind = "blocked"
            continue
        if page_kind in _SKIP_KINDS:
            attempted_kinds.append(page_kind)
            last_kind = page_kind
            continue
        results = parse_engine_html(eng, html, max_results=max_results)
        if not results:
            attempted_kinds.append("empty")
            last_kind = "empty"
            continue
        return SearchOutcome(engine=eng, query=query, results=tuple(results), page_kind="ok")

    if attempted_kinds and all(k in _TERMINAL_FALLOVER_KINDS for k in attempted_kinds):
        raise BrowserError(
            ErrorClass.PROVIDER_RATE_LIMITED,
            f"browser.search: every engine ({', '.join(order)}) ended in "
            "captcha/blocked for this query",
            retryable=True,
            evidence={"engines": list(order), "kinds": attempted_kinds, "query": query},
        )
    return SearchOutcome(engine=order[-1], query=query, results=(), page_kind=last_kind)


__all__ = [
    "AUTO_ORDER",
    "ENGINES",
    "MAX_RESULTS_CAP",
    "FetchFn",
    "SearchOutcome",
    "SearchResult",
    "build_search_url",
    "parse_bing_html",
    "parse_brave_html",
    "parse_duckduckgo_html",
    "parse_engine_html",
    "run_search",
]
