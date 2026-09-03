"""Typed browser error taxonomy and the Playwright -> taxonomy mapping.

Every public operation of :class:`browser_agent.session.BrowserSession` raises
:class:`BrowserError` and nothing else. The mapping is *principled*: it keys on
(a) the Playwright exception **type**, (b) the **phase** the operation was in
when it failed, and (c) — only as a secondary discriminator — stable marker
strings that Playwright's own actionability/connection subsystems embed in
their messages. Phases are explicit in the session code (`resolve` = waiting
for the locator to attach, `act` = performing the action on a resolved target,
`connect` = launching/attaching a browser, `navigate` = page navigation), so
classification never depends on guessing what the code was doing.

Mapping table (also reproduced in services/browser/README.md):

+----------------------------------------------------------+-------------------------+-----------+
| Playwright failure (type + phase [+ marker])             | error_class             | retryable |
+----------------------------------------------------------+-------------------------+-----------+
| TimeoutError in phase `resolve` (locator never attached, | ui_target_not_found     | False     |
| i.e. the semantic target resolves to nothing)            |                         |           |
| TimeoutError in phase `act` with an actionability marker | ui_state_changed        | True      |
| ("intercepts pointer events", "element is not attached", |                         |           |
| "element was detached", "not visible", "not stable",     |                         |           |
| "waiting for element to be")                             |                         |           |
| Error (non-timeout) in phase `act`/`navigate` with a     | ui_state_changed        | True      |
| detach/navigation-race marker ("Element is not attached  |                         |           |
| to the DOM", "Execution context was destroyed",          |                         |           |
| "Frame was detached", "Navigation interrupted",          |                         |           |
| "Page is navigating")                                    |                         |           |
| TimeoutError in any other phase (navigation, action wait | timeout                 | True      |
| without an actionability marker, connect handshake)      |                         |           |
| Error with a connection/liveness marker ("connect        | dependency_unavailable  | True      |
| ECONNREFUSED", "net::ERR_", "Target closed", "Browser    |                         |           |
| has been closed", "Target page, context or browser has   |                         |           |
| been closed", "WebSocket error", "browser has            |                         |           |
| disconnected") or any Error in phase `connect`           |                         |           |
| Invalid TargetSpec / bad arguments (raised by our own    | validation_error        | False     |
| validation, never reaches Playwright)                    |                         |           |
| Anything else                                            | internal_bug            | False     |
+----------------------------------------------------------+-------------------------+-----------+

The class names are the project-wide taxonomy from docs/API_AND_PROTOCOLS.md
section 8; this module uses the browser-relevant subset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


class ErrorClass(StrEnum):
    """Browser-relevant subset of the project error taxonomy."""

    UI_TARGET_NOT_FOUND = "ui_target_not_found"
    UI_STATE_CHANGED = "ui_state_changed"
    TIMEOUT = "timeout"
    VALIDATION_ERROR = "validation_error"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    CAPABILITY_MISSING = "capability_missing"
    CANCELLED = "cancelled"
    INTERNAL_BUG = "internal_bug"
    # Project-wide taxonomy (docs/API_AND_PROTOCOLS.md §8); added for M13 —
    # the owner's real-profile browser session is used only where the
    # EnrollmentRegistry records explicit research authorization (ADR-0019,
    # ADR-0035). This is a scope/authorization refusal, not a UI or transport
    # failure, so it gets its own class rather than overloading
    # capability_missing/validation_error.
    SECURITY_SCOPE_ERROR = "security_scope_error"
    # M13 (contract §5): every configured search engine ended in a CAPTCHA or
    # a bot-block page for this query — retryable because it is a transient
    # provider condition, not a permanent refusal.
    PROVIDER_RATE_LIMITED = "provider_rate_limited"


class Phase(StrEnum):
    """What the session was doing when Playwright failed."""

    CONNECT = "connect"
    NAVIGATE = "navigate"
    RESOLVE = "resolve"  # waiting for the semantic locator to attach
    ACT = "act"  # acting on an already-resolved target
    OTHER = "other"


@dataclass(slots=True)
class BrowserError(Exception):
    """Typed browser automation failure.

    Attributes:
        error_class: taxonomy class driving retry/evolution behavior.
        message: human-readable summary.
        retryable: whether a bounded retry may succeed without a plan change.
        evidence: structured context (op, target spec, phase, playwright message).
    """

    error_class: ErrorClass
    message: str
    retryable: bool
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def __str__(self) -> str:  # pragma: no cover - repr convenience
        return f"[{self.error_class}] {self.message} (retryable={self.retryable})"


# Marker strings emitted by Playwright's actionability engine when a resolved
# element stops being actionable (detached, covered, hidden, moving). These are
# stable, documented behaviors of Playwright's auto-waiting subsystem.
# URL schemes the agent may navigate to. file:// (local file read) and
# javascript: (script execution in the current origin — a session-hijack
# primitive on an attached authenticated browser) are deliberately excluded
# (M2 security review finding #2).
ALLOWED_NAV_SCHEMES = frozenset({"http", "https"})


def redact_url(url: str) -> str:
    """URL stripped of query string and fragment — safe for logs/evidence.

    Query strings routinely carry tokens (OAuth callbacks, magic links);
    telemetry must never persist them (M2 security review finding #3).
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def require_navigable_url(url: str, *, op: str) -> None:
    """Raise ``validation_error`` unless ``url`` uses an allowed scheme."""
    if url == "about:blank":
        return
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        scheme = ""
    if scheme not in ALLOWED_NAV_SCHEMES:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"{op}: URL scheme {scheme or '<none>'!r} is not allowed (http/https only)",
            retryable=False,
            evidence={"url": redact_url(url)},
        )


_ACTIONABILITY_MARKERS = (
    "intercepts pointer events",
    "element is not attached",
    "element was detached",
    "not visible",
    "not stable",
    "waiting for element to be",
)

# Markers for mid-action DOM/navigation races raised as plain Error.
_STATE_CHANGE_MARKERS = (
    "element is not attached to the dom",
    "execution context was destroyed",
    "frame was detached",
    "navigation interrupted",
    "page is navigating",
)

# Markers for "the browser/endpoint itself is gone or unreachable".
_CONNECTION_MARKERS = (
    "connect econnrefused",
    "net::err_",
    "target closed",
    "browser has been closed",
    "target page, context or browser has been closed",
    "websocket error",
    "browser has disconnected",
    "connection refused",
)


def _contains(message: str, markers: tuple[str, ...]) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in markers)


def map_playwright_error(
    exc: BaseException,
    *,
    phase: Phase,
    op: str,
    evidence: dict[str, Any] | None = None,
) -> BrowserError:
    """Map a Playwright exception to the typed taxonomy.

    See the module docstring for the full mapping table. `phase` is supplied by
    the session code and is the primary discriminator; marker strings are only
    consulted to split otherwise-identical exception types.
    """
    ev: dict[str, Any] = {"op": op, "phase": str(phase)}
    if evidence:
        ev.update(evidence)
    message = getattr(exc, "message", None) or str(exc)
    ev["playwright_error"] = type(exc).__name__
    ev["playwright_message"] = message[:500]

    if isinstance(exc, PlaywrightTimeoutError):
        if phase is Phase.RESOLVE:
            return BrowserError(
                ErrorClass.UI_TARGET_NOT_FOUND,
                f"{op}: target did not resolve to any element",
                retryable=False,
                evidence=ev,
            )
        if phase is Phase.ACT and _contains(message, _ACTIONABILITY_MARKERS):
            return BrowserError(
                ErrorClass.UI_STATE_CHANGED,
                f"{op}: target resolved but its UI state changed before the "
                "action completed (detached/covered/unstable)",
                retryable=True,
                evidence=ev,
            )
        return BrowserError(
            ErrorClass.TIMEOUT,
            f"{op}: operation timed out",
            retryable=True,
            evidence=ev,
        )

    if isinstance(exc, PlaywrightError):
        if phase is Phase.CONNECT or _contains(message, _CONNECTION_MARKERS):
            return BrowserError(
                ErrorClass.DEPENDENCY_UNAVAILABLE,
                f"{op}: browser or CDP endpoint is not reachable/alive",
                retryable=True,
                evidence=ev,
            )
        if phase in (Phase.ACT, Phase.NAVIGATE) and _contains(message, _STATE_CHANGE_MARKERS):
            return BrowserError(
                ErrorClass.UI_STATE_CHANGED,
                f"{op}: page/element state changed while acting (DOM mutation or navigation race)",
                retryable=True,
                evidence=ev,
            )
        return BrowserError(
            ErrorClass.INTERNAL_BUG,
            f"{op}: unexpected Playwright error",
            retryable=False,
            evidence=ev,
        )

    return BrowserError(
        ErrorClass.INTERNAL_BUG,
        f"{op}: unexpected non-Playwright error",
        retryable=False,
        evidence=ev,
    )
