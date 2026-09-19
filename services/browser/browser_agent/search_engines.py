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
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from bs4.element import Tag

from .errors import BrowserError, ErrorClass

ENGINES: tuple[str, ...] = ("google", "duckduckgo", "bing", "brave")
# Provider abstraction (owner decision 2026-09-03): Google is the primary provider, DuckDuckGo
# the fallback; Bing and Brave stay selectable by name only.
AUTO_ORDER: tuple[str, ...] = ("google", "duckduckgo")
PRIMARY_PROVIDER = AUTO_ORDER[0]
# Response schema of browser.search (2 = provider evidence fields present).
SEARCH_SCHEMA_VERSION = 3
MAX_RESULTS_CAP = 20

_ENGINE_OWN_DOMAINS: dict[str, tuple[str, ...]] = {
    "google": (
        "google.com",
        "google.com.tr",
        "googleadservices.com",
        "googleusercontent.com",
        "gstatic.com",
        "google.co.uk",
        "google.de",
        "google.fr",
    ),
    "duckduckgo": ("duckduckgo.com",),
    "bing": ("bing.com", "microsoft.com", "msn.com"),
    "brave": ("brave.com", "search.brave.com"),
}

# Google's "unusual traffic" interstitial and its consent page: recognised, never solved.
_GOOGLE_SORRY_MARKERS = ("unusual traffic from your computer network", "/sorry/index")
_GOOGLE_CONSENT_MARKERS = ("consent.google.com",)

_PUBLISHED_HINT_RE = re.compile(
    r"\b\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago\b"
    r"|\b\d+\s+(?:saniye|dakika|saat|gün|hafta|ay|yıl)\s+önce\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    published_hint: str | None
    rank: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "published_hint": self.published_hint,
        }


@dataclass(frozen=True, slots=True)
class SearchAttempt:
    """One provider tried for a query and how it ended (evidence, contract §3)."""

    provider: str
    outcome: str  # ok | captcha | consent | blocked | empty | malformed | transport_error
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "outcome": self.outcome, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """Results plus provider evidence: which provider was asked for, which one answered,
    whether a fallback happened and why. ``engine`` is kept as an alias of ``provider``.

    Contract v1.1 (§3a) additions: ``path`` names how the results were reached
    (``google_ui`` — typed into Google's real search box; ``google_url`` —
    the loaded results page was re-fetched by URL, e.g. applying
    ``recency_days``'s ``tbs`` filter; ``fallback`` — a non-Google provider
    answered; ``handoff_pending`` — an owner-handoff interstitial is waiting;
    ``handoff_cleared`` — a previously-pending search resumed against the
    already-cleared results page). ``state`` is ``ok`` or
    ``waiting_for_owner_verification``. ``provider`` is ``None`` only for a
    ``handoff_pending`` outcome (nothing answered yet).
    """

    requested_provider: str
    provider: str | None
    query: str
    results: tuple[SearchResult, ...]
    page_kind: str
    attempts: tuple[SearchAttempt, ...] = ()
    locale: str | None = None
    path: str = "fallback"
    state: str = "ok"
    verification_url: str | None = None
    #: Schema 3: the single home of owner-verification evidence (contract §3a).
    #: ``outcome``: None (no interstitial handed to the owner), "pending",
    #: "cleared", "timeout" (fallback after the owner did not complete the page),
    #: "repeat" (a further interstitial after one clearance; fallback applied).
    #: ``interstitial``: the page kind that was shown (captcha/consent/blocked).
    verification: dict[str, object] | None = None

    @property
    def engine(self) -> str | None:
        return self.provider

    @property
    def fallback(self) -> bool:
        # A pending handoff hasn't "fallen back" to anything — it is still
        # waiting on the requested provider itself.
        if self.provider is None:
            return False
        return self.provider != self.requested_provider

    @property
    def fallback_reason(self) -> str | None:
        if not self.fallback:
            return None
        failed = [a for a in self.attempts if a.provider != self.provider]
        return "; ".join(f"{a.provider}:{a.outcome}" for a in failed) or "unknown"

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": SEARCH_SCHEMA_VERSION,
            "engine": self.provider,
            "requested_provider": self.requested_provider,
            "provider": self.provider,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "query": self.query,
            "result_count": len(self.results),
            "locale": self.locale,
            "attempts": [a.as_dict() for a in self.attempts],
            "results": [r.as_dict() for r in self.results],
            "page_kind": self.page_kind,
            "path": self.path,
            "state": self.state,
            "verification": self.verification,
        }
        if self.verification_url is not None:
            result["verification_url"] = self.verification_url
        return result


def _extract_published_hint(text: str) -> str | None:
    match = _PUBLISHED_HINT_RE.search(text)
    return match.group(0) if match else None


def _is_own_domain(url: str, engine: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in _ENGINE_OWN_DOMAINS[engine])


def resolve_result_url(href: str, engine: str) -> str | None:
    """Turn a SERP result link into the destination URL, or ``None``.

    Real engines wrap organic links in a click-tracking redirect on their own
    domain (seen on 2026-09-03 with real Chrome): DuckDuckGo HTML uses
    ``//duckduckgo.com/l/?uddg=<url-encoded destination>`` and Bing uses
    ``https://www.bing.com/ck/a?…&u=a1<base64url destination>``. Without
    unwrapping, every organic result looks like the engine's own domain and is
    dropped. Only ``http(s)`` destinations are accepted; anything else is
    ``None`` (dropped by the caller), never a guess.
    """
    import base64
    from urllib.parse import parse_qs, unquote

    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    host = parts.netloc.lower()
    if engine == "duckduckgo" and host.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        target = parse_qs(parts.query).get("uddg", [""])[0]
        href = unquote(target)
    elif engine == "bing" and host.endswith("bing.com") and parts.path.startswith("/ck/"):
        packed = parse_qs(parts.query).get("u", [""])[0]
        raw = packed[2:] if packed.startswith("a1") else packed
        try:
            href = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    if not href.startswith(("http://", "https://")):
        return None
    return href


def recency_param(engine: str, recency_days: int | None) -> dict[str, str]:
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
    if engine == "google":
        return {"tbs": {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}[bucket]}
    if engine == "duckduckgo":
        return {"df": {"day": "d", "week": "w", "month": "m", "year": "y"}[bucket]}
    if engine == "bing":
        return {
            "freshness": {"day": "Day", "week": "Week", "month": "Month", "year": "Year"}[bucket]
        }
    if engine == "brave":
        return {"tf": {"day": "pd", "week": "pw", "month": "pm", "year": "py"}[bucket]}
    return {}


def locale_params(locale: str | None) -> dict[str, str]:
    """Google's ``hl`` (interface language) and ``gl`` (region) from a BCP-47 locale such as
    ``tr-TR``; nothing is assumed when no locale is known."""
    if not locale:
        return {}
    parts = locale.replace("_", "-").split("-")
    params: dict[str, str] = {}
    if parts and parts[0]:
        params["hl"] = parts[0].lower()
    if len(parts) > 1 and len(parts[-1]) == 2 and parts[-1].isalpha():
        params["gl"] = parts[-1].lower()
    return params


def region_params(engine: str, region: str | None) -> dict[str, str]:
    """The engine's own way of saying "answer this from THIS country" for a region code
    such as ``tr-tr`` (ADR-0178 D2: the API sends one for a Turkish-worded query).

    DuckDuckGo names it ``kl`` and takes the whole code; Google names the country ``gl``
    and, when no interface language was asked for separately, takes ``hl`` from the same
    code. Bing and Brave are left alone: neither has a parameter this product has verified,
    and guessing one would change a search nobody measured.
    """
    if not region:
        return {}
    parts = region.replace("_", "-").split("-")
    if len(parts) != 2 or not all(len(p) == 2 and p.isalpha() for p in parts):
        return {}
    language, country = parts[0].lower(), parts[1].lower()
    if engine == "duckduckgo":
        return {"kl": f"{language}-{country}"}
    if engine == "google":
        return {"hl": language, "gl": country}
    return {}


def build_search_url(
    engine: str,
    query: str,
    *,
    recency_days: int | None = None,
    locale: str | None = None,
    region: str | None = None,
) -> str:
    """The engine's search-results URL for ``query`` (contract §3 base URLs)."""
    params = {"q": query, **recency_param(engine, recency_days)}
    if engine == "google":
        # The region names the COUNTRY, the locale the INTERFACE LANGUAGE: an explicit
        # locale wins on ``hl`` (it was asked for by name), the region keeps ``gl``.
        google_region = region_params(engine, region)
        params.update(google_region)
        params.update(locale_params(locale))
        if "gl" in google_region:
            params["gl"] = google_region["gl"]
        params["num"] = "10"
        return f"https://www.google.com/search?{urlencode(params)}"
    params.update(region_params(engine, region))
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
        url = resolve_result_url(str(title_link["href"]), "duckduckgo")
        if url is None or _is_own_domain(url, "duckduckgo"):
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
        url = resolve_result_url(str(title_link["href"]), "bing")
        if url is None or _is_own_domain(url, "bing"):
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


_GOOGLE_EXCLUDED_ANCESTOR_IDS = {"tads", "tadsb", "taw", "rhs", "bottomads", "topads"}
_GOOGLE_EXCLUDED_ANCESTOR_CLASSES = (
    "related-question-pair",
    "kp-wholepage",
    "uEierd",
    "commercial-unit",
)
_GOOGLE_EXCLUDED_ANCESTOR_TAGS = {"g-scrolling-carousel", "g-section-with-header"}


def _google_block_excluded(block: Tag) -> bool:
    for ancestor in (block, *block.parents):
        if not isinstance(ancestor, Tag):
            continue
        if ancestor.name in _GOOGLE_EXCLUDED_ANCESTOR_TAGS:
            return True
        if ancestor.get("id") in _GOOGLE_EXCLUDED_ANCESTOR_IDS:
            return True
        if ancestor.has_attr("data-text-ad"):
            return True
        classes = ancestor.get("class") or []
        if any(c in _GOOGLE_EXCLUDED_ANCESTOR_CLASSES for c in classes):
            return True
    return False


def _google_result_url(href: str) -> str | None:
    """Direct URLs pass; Google's ``/url?q=<destination>`` redirect is unwrapped; anything
    else (javascript:, relative Google paths, data:) is dropped."""
    if href.startswith("/url?"):
        target = parse_qs(urlsplit(href).query).get("q", [""])[0]
        href = unquote(target)
    if href.startswith("//"):
        href = "https:" + href
    if not href.startswith(("http://", "https://")):
        return None
    return href


def append_recency_param(url: str, recency_days: int) -> str:
    """Add Google's own recency filter (``tbs=qdr:x``) to an already-loaded
    results URL, preserving every other query parameter (contract §3a:
    ``recency_days`` is applied to the loaded results page as Google's own
    time filter, not by rebuilding the search from scratch)."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update(recency_param("google", recency_days))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def is_verification_cleared(url: str, html: str) -> bool:
    """``True`` once a Google interstitial (the unusual-traffic ``/sorry/``
    page or the ``consent.google.*`` page) is no longer showing (contract
    §3a's ``browser.wait for=verification_cleared`` decision). Pure and
    testable with fake page states — no browser required."""
    return detect_google_interstitial(html, url) is None


class GoogleHandoffPending(Exception):
    """Raised by a caller-supplied ``fetch`` to signal that the Google
    attempt hit an interstitial while running in owner-handoff mode
    (contract §3a ``interstitial: "handoff"``): :func:`run_search` stops
    immediately — no further providers are tried — and turns this into a
    SUCCESSFUL outcome with ``state="waiting_for_owner_verification"``,
    ``path="handoff_pending"``, ``provider=None``, ``results=[]``.
    """

    def __init__(self, *, page_kind: str, verification_url: str, detail: str = "") -> None:
        self.page_kind = page_kind
        self.verification_url = verification_url
        self.detail = detail
        super().__init__(f"google handoff pending: {page_kind}")


def detect_google_interstitial(html: str, final_url: str | None = None) -> str | None:
    """``"captcha"`` for the unusual-traffic interstitial, ``"consent"`` for the consent
    page, ``None`` for a normal page. Detection only — neither is ever answered."""
    lowered_url = (final_url or "").lower()
    if "google." in lowered_url and "/sorry/" in lowered_url:
        return "captcha"
    if lowered_url.startswith(("https://consent.google.", "http://consent.google.")):
        return "consent"
    lowered = html.lower()
    if any(marker in lowered for marker in _GOOGLE_SORRY_MARKERS):
        return "captcha"
    if any(marker in lowered for marker in _GOOGLE_CONSENT_MARKERS) and "<form" in lowered:
        return "consent"
    return None


def parse_google_html(html: str, *, max_results: int = 10) -> list[SearchResult]:
    """Google organic results only: blocks with an ``h3`` title inside the results region,
    excluding ads, "People also ask", the knowledge panel / right-hand column, carousels,
    and Google's own domains."""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#rso") or soup.select_one("#search") or soup
    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    seen_titles: set[int] = set()
    for block in container.select("div.g, div[data-hveid]"):
        if _google_block_excluded(block):
            continue
        h3 = block.find("h3")
        if h3 is None or id(h3) in seen_titles:
            continue
        link = h3.find_parent("a", href=True)
        if link is None:
            continue
        seen_titles.add(id(h3))
        url = _google_result_url(str(link["href"]))
        if url is None or _is_own_domain(url, "google") or url in seen_urls:
            continue
        seen_urls.add(url)
        snippet_el = block.select_one("[data-sncf], .VwiC3b, div[data-content-feature]")
        snippet = _clean(snippet_el.get_text(" ") if snippet_el else "")
        results.append(
            SearchResult(
                url=url,
                title=_clean(h3.get_text()),
                snippet=snippet,
                published_hint=_extract_published_hint(snippet),
            )
        )
        if len(results) >= max_results:
            break
    return results


_PARSERS: dict[str, Callable[..., list[SearchResult]]] = {
    "google": parse_google_html,
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
FetchFn = Callable[[str, str], Awaitable[tuple]]

_TERMINAL_FALLOVER_KINDS = frozenset({"captcha", "blocked", "consent", "transport_error"})
_SKIP_KINDS = frozenset({"captcha", "blocked", "empty"})


async def run_search(
    query: str,
    engine: str,
    *,
    fetch: FetchFn,
    max_results: int = 10,
    recency_days: int | None = None,
    locale: str | None = None,
    region: str | None = None,
) -> SearchOutcome:
    """Run a search through the provider abstraction.

    ``engine="auto"`` (the default) tries ``AUTO_ORDER`` — Google first, DuckDuckGo as the
    fallback — and records every attempt with its outcome; a named engine is used alone.
    ``fetch(engine, url)`` performs the real navigation and returns
    ``(html, page_kind, http_status)``, ``(html, page_kind, http_status, final_url)`` or
    ``(html, page_kind, http_status, final_url, path_hint)`` — injected so the
    fallover/evidence logic is unit-testable without a browser; the live worker path
    supplies a closure that drives the real session. ``path_hint`` (5th element, optional)
    overrides the outcome's ``path`` field for a successful provider (contract §3a:
    ``google_ui``/``google_url``/``handoff_cleared``); when absent, ``path`` defaults to
    ``google_ui`` for provider ``google`` and ``fallback`` for any other provider.
    ``fetch`` may also raise :class:`GoogleHandoffPending` (owner-handoff mode) instead of
    returning, which stops the loop immediately — see that class's docstring.
    """
    if max_results < 1 or max_results > MAX_RESULTS_CAP:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"max_results must be between 1 and {MAX_RESULTS_CAP}, got {max_results}",
            retryable=False,
        )
    auto_mode = engine == "auto"
    if not auto_mode and engine not in ENGINES:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"unknown search engine {engine!r}; known: auto, {', '.join(ENGINES)}",
            retryable=False,
        )
    order = AUTO_ORDER if auto_mode else (engine,)
    requested = order[0]

    attempts: list[SearchAttempt] = []
    last_kind = "empty"
    # A handoff-related path hint from a FAILED google attempt (the owner did not
    # complete the page in time, or Google asked again after one clearance) names
    # the route the eventual fallback outcome took (contract §3a).
    fallback_path_hint: str | None = None
    for provider in order:
        url = build_search_url(
            provider, query, recency_days=recency_days, locale=locale, region=region
        )
        try:
            fetched = await fetch(provider, url)
        except GoogleHandoffPending as handoff:
            # Owner-handoff mode (contract §3a): stop immediately, no further
            # provider is ever tried — this is a SUCCESSFUL, waiting outcome,
            # not a failure to record and fall over from.
            attempts.append(
                SearchAttempt(
                    provider,
                    "verification_pending",
                    handoff.detail
                    or f"handoff: {handoff.page_kind} shown; owner verification required",
                )
            )
            return SearchOutcome(
                requested_provider=requested,
                provider=None,
                query=query,
                results=(),
                page_kind=handoff.page_kind,
                attempts=tuple(attempts),
                locale=locale,
                path="handoff_pending",
                state="waiting_for_owner_verification",
                verification_url=handoff.verification_url,
            )
        except BrowserError as exc:
            # A transport failure reaching THIS provider (a network-level abort from anti-bot
            # filtering, seen live against Brave) is as good a reason to try the next one as
            # a captcha page. An explicitly requested single engine gets no fallover.
            if not auto_mode:
                raise
            attempts.append(SearchAttempt(provider, "transport_error", str(exc.error_class)))
            last_kind = "blocked"
            continue
        html, page_kind, _http_status = fetched[0], fetched[1], fetched[2]
        final_url = fetched[3] if len(fetched) > 3 else None
        path_hint = fetched[4] if len(fetched) > 4 else None
        interstitial = detect_google_interstitial(html, final_url) if provider == "google" else None
        if interstitial is not None:
            # The interstitial KIND is evidence exactly once (the ``verification`` block the
            # worker attaches); an attempt records the verification outcome code on the
            # handoff paths and the kind only on the plain unattended fallback.
            outcome_code = interstitial
            detail = f"{interstitial} interstitial detected; not answered"
            if path_hint == "handoff_repeat_fallback":
                outcome_code = "interstitial_after_verification"
                detail = (
                    f"{interstitial} again after the owner's verification; "
                    "not handed off a second time (retry once, never loop)"
                )
            elif path_hint == "handoff_timeout_fallback":
                outcome_code = "verification_timeout"
                detail = f"{interstitial} still pending after the owner handoff; not retried"
            if path_hint and path_hint.startswith("handoff_"):
                fallback_path_hint = path_hint
            attempts.append(SearchAttempt(provider, outcome_code, detail))
            last_kind = "captcha" if interstitial == "captcha" else "blocked"
            continue
        if page_kind in _SKIP_KINDS:
            attempts.append(SearchAttempt(provider, page_kind))
            last_kind = page_kind
            continue
        try:
            results = parse_engine_html(provider, html, max_results=max_results)
        except Exception as exc:  # noqa: BLE001 - malformed markup is a provider outcome, not a crash
            attempts.append(SearchAttempt(provider, "malformed", type(exc).__name__))
            last_kind = "empty"
            continue
        if not results:
            attempts.append(SearchAttempt(provider, "empty"))
            last_kind = "empty"
            continue
        attempts.append(SearchAttempt(provider, "ok", f"{len(results)} results"))
        ranked = tuple(replace(r, rank=i + 1) for i, r in enumerate(results))
        default_path = "google_ui" if provider == "google" else (fallback_path_hint or "fallback")
        return SearchOutcome(
            requested_provider=requested,
            provider=provider,
            query=query,
            results=ranked,
            page_kind="ok",
            attempts=tuple(attempts),
            locale=locale,
            path=path_hint or default_path,
            state="ok",
        )

    kinds = [a.outcome for a in attempts]
    if kinds and all(k in _TERMINAL_FALLOVER_KINDS for k in kinds):
        raise BrowserError(
            ErrorClass.PROVIDER_RATE_LIMITED,
            f"browser.search: every provider ({', '.join(order)}) ended in "
            f"{'/'.join(sorted(set(kinds)))} for this query",
            retryable=True,
            evidence={
                "requested_provider": requested,
                "attempts": [a.as_dict() for a in attempts],
                "query": query,
            },
        )
    return SearchOutcome(
        requested_provider=requested,
        provider=order[-1],
        query=query,
        results=(),
        page_kind=last_kind,
        attempts=tuple(attempts),
        locale=locale,
        path=fallback_path_hint or "fallback",
        state="ok",
    )


__all__ = [
    "AUTO_ORDER",
    "PRIMARY_PROVIDER",
    "SEARCH_SCHEMA_VERSION",
    "GoogleHandoffPending",
    "SearchAttempt",
    "append_recency_param",
    "detect_google_interstitial",
    "is_verification_cleared",
    "locale_params",
    "recency_param",
    "parse_google_html",
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
