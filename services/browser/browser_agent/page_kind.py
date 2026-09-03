"""Website-error vs browser-error split: ``page_kind`` classification (contract §3).

A website problem is a *successful* command whose result carries
``page_kind != "ok"`` plus a ``site_error`` describing it; a browser/transport
problem is a typed :class:`~browser_agent.errors.BrowserError` instead (that
split already exists — see ``errors.py``). This module only decides which of
the six ``page_kind`` values a landed page is, from DOM/HTTP signals — never
from vision or coordinates.

Detection rules (contract §3 + task spec, verbatim where the contract gives
one):

- ``auth_wall`` — a password field is present on the landing page, OR the
  HTTP status was 401/403, OR the title/main heading contains a login marker
  (English: "login", "sign in"; Turkish: "oturum aç", "giriş yap").
- ``captcha`` — known CAPTCHA markers: "recaptcha", "hcaptcha", "turnstile",
  "verify you are human".
- ``blocked`` — bot-block wording ("access denied", "you have been blocked",
  "unusual traffic", "automated queries") or HTTP 429. Checked after
  auth_wall/captcha so a login page that also happens to mention "unusual
  activity" is not misclassified.
- ``error_page`` — any other HTTP status >= 400.
- ``empty`` — the page loaded (2xx/no-status) but rendered essentially no
  text (used by ``browser.search``'s per-engine fallover on a blank results
  page as much as by ``fetch_evidence``).
- ``ok`` — none of the above.

The worker never attempts to solve a CAPTCHA; this module only names one.
"""

from __future__ import annotations

from dataclasses import dataclass

_CAPTCHA_MARKERS: tuple[str, ...] = (
    "recaptcha",
    "hcaptcha",
    "turnstile",
    "verify you are human",
    # Seen for real on 2026-09-03 (contract §3 list extended): DuckDuckGo's anomaly
    # page, Cloudflare's interstitial and generic robot checks.
    "bots use duckduckgo",
    "unfortunately, bots",
    "checking your browser",
    "just a moment...",
    "are you a robot",
    "prove you are human",
)

_LOGIN_MARKERS: tuple[str, ...] = (
    "login",
    "log in",
    "sign in",
    "oturum aç",
    "oturum ac",
    "giriş yap",
    "giris yap",
)

_BLOCKED_MARKERS: tuple[str, ...] = (
    "access denied",
    "you have been blocked",
    "unusual traffic",
    "automated queries",
    "your ip has been blocked",
)

_EMPTY_TEXT_THRESHOLD = 20

AUTH_WALL_HTTP_STATUSES = frozenset({401, 403})
BLOCKED_HTTP_STATUS = 429


@dataclass(frozen=True, slots=True)
class SiteError:
    kind: str
    http_status: int | None
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "http_status": self.http_status, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class PageKindResult:
    page_kind: str
    site_error: SiteError | None

    def as_dict(self) -> dict[str, object]:
        return {
            "page_kind": self.page_kind,
            "site_error": self.site_error.as_dict() if self.site_error else None,
        }


def _contains_any(haystack: str, markers: tuple[str, ...]) -> bool:
    lowered = haystack.lower()
    return any(marker in lowered for marker in markers)


def classify_page(
    *,
    title: str,
    heading_text: str,
    body_text: str,
    has_password_field: bool,
    http_status: int | None,
) -> PageKindResult:
    """Classify a landed page from DOM/HTTP signals already read by the caller.

    Callers gather ``title``/``heading_text`` (e.g. the first ``h1``, best
    effort — empty string if none)/``body_text``/``has_password_field`` via
    normal DOM reads (never coordinates), and ``http_status`` from the
    navigation response (``None`` when unknown, e.g. a same-document
    navigation).
    """
    title_and_heading = f"{title} {heading_text}"

    if _contains_any(title_and_heading, _CAPTCHA_MARKERS) or _contains_any(
        body_text, _CAPTCHA_MARKERS
    ):
        return PageKindResult(
            "captcha",
            SiteError("captcha", http_status, "CAPTCHA challenge markers detected"),
        )

    login_markers = has_password_field or _contains_any(title_and_heading, _LOGIN_MARKERS)
    if login_markers or http_status == 401:
        detail = (
            f"HTTP {http_status} on landing page"
            if http_status in AUTH_WALL_HTTP_STATUSES
            else "login markers detected"
        )
        return PageKindResult("auth_wall", SiteError("auth_wall", http_status, detail))

    # A bare 403 with no login form or sign-in wording is bot filtering, not a login
    # wall (seen for real: a public newsroom answering headless Chrome with 403). It
    # is reported as `blocked` so research treats it as a source that refused automation.
    if (
        _contains_any(body_text, _BLOCKED_MARKERS)
        or http_status == BLOCKED_HTTP_STATUS
        or http_status == 403
    ):
        return PageKindResult(
            "blocked", SiteError("blocked", http_status, "bot-block markers or HTTP 403")
        )

    if http_status is not None and http_status >= 400:
        return PageKindResult(
            "error_page",
            SiteError("http_error", http_status, f"HTTP {http_status}"),
        )

    if len(body_text.strip()) < _EMPTY_TEXT_THRESHOLD:
        return PageKindResult("empty", None)

    return PageKindResult("ok", None)


__all__ = [
    "AUTH_WALL_HTTP_STATUSES",
    "BLOCKED_HTTP_STATUS",
    "PageKindResult",
    "SiteError",
    "classify_page",
]
