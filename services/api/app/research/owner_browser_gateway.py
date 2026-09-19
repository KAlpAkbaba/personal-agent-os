"""Owner-Chrome fetch gateway (ADR-0177).

Owner decision 2026-09-19: *"Her şeyi kendi Chrome'umda, gözümün önünde yapsın"* — asked
where research should open the pages it reads, the owner chose their own, already-open
Chrome window over the background browser worker (:mod:`app.research.browser_gateway`'s
``DeviceBrowserGateway``) and over a Chrome-extension bridge. They were told the cost:
fetching becomes serial (one owner window, one tab at a time), 2-4 minutes of their own
screen/keyboard per research run, and OCR text quality on a long page.

This is a FETCH gateway only — it reads one page a caller already knows the URL for.
Discovery (finding which URLs answer a query) is unchanged: it still runs through the
device/worker browser exactly as before (see the ADR: "only page FETCHING moves").

Same wire result contract as :class:`app.research.browser_gateway.DeviceBrowserGateway`
(``fetch_url`` -> :class:`~app.research.evidence.EvidenceRecord`), dispatched over the
SAME :class:`~app.devices.commands.DeviceCommandClientProtocol`, so
``app.research.browser_activities.fetch_activity`` can select either gateway for a given
run/URL without the rest of the pipeline caring which one answered. Unlike
``DeviceBrowserGateway`` there is no session concept here: the only device-side state this
gateway creates is the one browser tab it opens for this fetch, and it always closes that
tab again (a ``finally``-guarded ``Ctrl+W``), win or lose.

How a page is read: the target window is activated, a new tab is opened (Ctrl+T), the URL
is typed into the (already-focused, on a fresh tab) address bar and Enter is pressed, the
window is polled (``window.current``) until its title stops being a blank tab's, and then
the page is read by the device's own OCR (``screen.ocr``) — PageDown, OCR, PageDown, OCR —
up to a fixed number of screens or until the excerpt budget is met or a screen adds nothing
new. The window's own tab strip / address bar band is never treated as page content.

Deliberately NOT a dependency of ``app.operator``: the ideas (finding the owner's Chrome
window, driving it by keyboard, recognising a blank-tab title) are the same ones
``app.operator.plans``/``app.operator.mission`` already proved out for the voice operator,
but this module reads its own inputs directly over ``DeviceCommandClientProtocol`` rather
than importing the operator's ``Observation``/``OperatorStep`` machinery, which is built
around a different execution model (a step-by-step mission loop with its own retry/backoff
policy) that a Temporal activity has no use for. Small, rarely-changing vocabulary
(blank-tab titles, the Chrome title suffix) is duplicated rather than imported, the same
choice ``app.research.evidence`` documents for the dedup/rank formula it duplicates from
``browser_agent`` — two subsystems agreeing on a wire/text shape is cheaper to keep in sync
by hand than to couple the research pipeline to the operator subsystem's internals. The one
exception is ``app.operator.ocr_locate.fold`` — a small, PURE text-normalisation helper
(Turkish-aware casefold) with no operator-specific behaviour at all, imported rather than
duplicated a third time (:mod:`app.research.eligibility` already has its own copy for a
different alphabet-folding need; a third hand-copy of the same table was not worth avoiding
one import of a pure function).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.devices.commands import (
    CommandExpired,
    CommandFailed,
    CommandOutcome,
    CommandSucceeded,
    DeviceCommandClientProtocol,
)
from app.logging import get_logger
from app.operator.ocr_locate import fold
from app.research.browser_gateway import (
    DEFAULT_EXCERPT_CHARS,
    BrowserDispatchError,
    fetch_idempotency_key,
)
from app.research.dates import parse_recency_window
from app.research.destination import DestinationPolicyError, validate_fetch_target
from app.research.eligibility import classify_page_validity
from app.research.evidence import (
    PAGE_KIND_AUTH_WALL,
    PAGE_KIND_BLOCKED,
    PAGE_KIND_CAPTCHA,
    PAGE_KIND_EMPTY,
    PAGE_KIND_ERROR_PAGE,
    PAGE_KIND_OK,
    EvidenceRecord,
)
from app.research.forbidden_keys import find_forbidden_keys

logger = get_logger("app.research.owner_browser_gateway")

#: The extraction_method this gateway stamps every record with (spec: distinguishes an
#: owner-Chrome/OCR read from the worker's own ``dom_text`` extraction).
EXTRACTION_METHOD = "owner_browser_ocr"

#: The Windows executable image the owner's browser runs under. Same identity
#: ``app.operator.mission.CHROME_IMAGE`` uses, duplicated (see module docstring).
CHROME_IMAGE = "chrome.exe"

#: A tab that has not gone anywhere yet, in the languages the owner's Chrome may speak
#: (``app.operator.plans._BLANK_TAB_TITLES``, duplicated — see module docstring).
_BLANK_TAB_TITLES = frozenset({"yeni sekme", "new tab", ""})
#: What Chrome appends to every window title (``app.operator.plans._CHROME_TITLE_SUFFIXES``).
_CHROME_TITLE_SUFFIXES = (" - Google Chrome", " – Google Chrome")

#: Folded substring that marks a Chrome window as the Agent's OWN web page: a window this
#: gateway prefers not to drive when another owner Chrome window is open, so a research
#: fetch never hijacks the tab the owner is using to look at their own Agent.
AGENT_OWN_TITLE_MARKER = "personalagentos"

#: BrowserDispatchError classes that mean "the owner-Chrome path cannot do this fetch right
#: now" (no owner Chrome window is open, or the device does not offer/answer screen.ocr) —
#: the caller (``app.research.browser_activities.fetch_activity``) falls back to the
#: device/worker gateway for THIS url rather than failing the whole fetch.
OWNER_BROWSER_FALLBACK_ERROR_CLASSES = frozenset(
    {"dependency_unavailable", "capability_missing", "no_window"}
)

#: How many PageDown+OCR screens one fetch reads at most.
DEFAULT_MAX_SCREENS = 4

#: The window's own tab strip / address bar band, measured from the FIRST screen's height
#: and never treated as page content on any screen (the chrome does not scroll away).
TOP_CHROME_BAND_FRACTION = 0.12

#: How many times ``window.current`` is polled, at most, waiting for a page to leave the
#: blank-tab title, and how long between polls (bounded overall by the caller's own
#: ``timeout_s``, never exceeding it).
_TITLE_POLL_INTERVAL_S = 1.0

_PAGE_VALIDITY_TO_PAGE_KIND: dict[str, str] = {
    "normal_content": PAGE_KIND_OK,
    "consent": PAGE_KIND_BLOCKED,
    "captcha": PAGE_KIND_CAPTCHA,
    "interstitial": PAGE_KIND_BLOCKED,
    "login_required": PAGE_KIND_AUTH_WALL,
    "access_denied": PAGE_KIND_AUTH_WALL,
    "empty": PAGE_KIND_EMPTY,
    "malformed": PAGE_KIND_ERROR_PAGE,
}


def _bare_image(image: str) -> str:
    return image.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def _page_title(title: str) -> str:
    """The page's own title, without Chrome's name after it (``app.operator.plans.page_title``,
    duplicated — see module docstring)."""
    for suffix in _CHROME_TITLE_SUFFIXES:
        if title.endswith(suffix):
            return title[: -len(suffix)]
    return title


def _outcome_or_raise(outcome: CommandOutcome) -> dict[str, Any]:
    if isinstance(outcome, CommandSucceeded):
        return outcome.result
    if isinstance(outcome, CommandFailed):
        raise BrowserDispatchError(outcome.error_class, outcome.message, outcome.retryable)
    if isinstance(outcome, CommandExpired):
        raise BrowserDispatchError("timeout", "command expired before a terminal ack", True)
    raise BrowserDispatchError(  # pragma: no cover - defensive, outcome is a closed union
        "internal_bug", f"unknown command outcome {outcome!r}", False
    )


def _reject_forbidden_keys(result: dict[str, Any]) -> None:
    """Cloud-Core-side forbidden-key scan (mirrors ``browser_gateway._reject_forbidden_keys``,
    finding HIGH-3): a device result that becomes evidence is scanned here too."""
    forbidden = find_forbidden_keys(result)
    if forbidden:
        raise BrowserDispatchError(
            "security_scope_error",
            f"device result contained forbidden key(s): {sorted(set(forbidden))}",
            False,
        )


def _published_at_from_text(text: str, *, now: datetime) -> datetime | None:
    """The smallest HONEST date support available on this path (no HTML metadata exists for
    an OCR'd page — there is no ``<meta property="article:published_time">`` to read).

    Reuses :func:`app.research.dates.parse_recency_window` — the pipeline's existing Turkish
    relative-date parser — against the page's own visible text, but ONLY for the two phrases
    it already resolves to one exact calendar day: "bugün" (today) and "dün" (yesterday).
    "son N gün/hafta/ay" ("in the last N days/weeks/months") is a WINDOW, not a publication
    date, and is deliberately never turned into one here; a phrasing the parser does not
    already recognise at all (e.g. "3 gün önce" / "3 days ago") is left unset rather than
    guessed at. The result is written into :attr:`EvidenceRecord.published_at` — the same
    field the worker path fills from HTML metadata's ``published_at`` — because that is the
    only field the rest of the pipeline reads a publication date from; there is no separate
    "visible-text date" field to invent.
    """
    window = parse_recency_window(text, now=now)
    if window is None or window.unit != "day" or window.amount != 1:
        return None
    if window.label not in ("bugün", "dün"):
        return None
    return window.start


class OwnerBrowserGateway:
    """Fetches ONE url by driving the owner's own, already-open Chrome window.

    ``fetch_url`` has the same signature/result contract as
    :meth:`app.research.browser_gateway.DeviceBrowserGateway.fetch_url` (``tab`` is accepted
    for interface parity but ignored — this gateway always opens and closes its own tab, it
    has no "same tab" mode). Construct a fresh instance per fetch (an activity call), exactly
    as ``DeviceBrowserGateway`` is constructed — there is no cross-call state to share.
    """

    name = "owner_browser"

    def __init__(
        self,
        command_client: DeviceCommandClientProtocol,
        *,
        device_id: uuid.UUID,
        task_id: str,
        trace_id: str = "",
        timeout_s: float = 15.0,
        excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
        max_screens: int = DEFAULT_MAX_SCREENS,
        sleep_fn: Callable[[float], None] = time.sleep,
        poll_interval_s: float = _TITLE_POLL_INTERVAL_S,
    ) -> None:
        self._client = command_client
        self._device_id = device_id
        self._task_id = task_id
        self._trace_id = trace_id or task_id
        self._timeout_s = timeout_s
        self._excerpt_chars = excerpt_chars
        self._max_screens = max(1, max_screens)
        self._sleep = sleep_fn
        self._poll_interval_s = max(0.0, poll_interval_s)

    # ------------------------------------------------------------- dispatch

    def _run(
        self, capability: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        outcome = self._client.run(
            device_id=self._device_id,
            capability=capability,
            payload=payload,
            idempotency_key=idempotency_key,
            timeout_s=self._timeout_s,
            trace_id=self._trace_id,
        )
        result = _outcome_or_raise(outcome)
        _reject_forbidden_keys(result)
        return result

    # --------------------------------------------------------------- window

    def _find_owner_window(self, key: Callable[[str], str]) -> dict[str, Any]:
        """The owner's Chrome window: the foreground one when it is Chrome and not showing
        the Agent's own page, else any other open Chrome window not showing it, else
        whichever Chrome window there is. No Chrome window open at all is the typed error
        the caller falls back on."""
        result = self._run("window.list", {}, key("window_list"))
        windows = [w for w in (result.get("windows") or []) if isinstance(w, dict)]
        chrome_windows = [
            w for w in windows if _bare_image(str(w.get("image") or "")) == CHROME_IMAGE
        ]
        if not chrome_windows:
            raise BrowserDispatchError(
                "dependency_unavailable", "no owner Chrome window is open", False
            )

        def _is_agent_page(w: dict[str, Any]) -> bool:
            return AGENT_OWN_TITLE_MARKER in fold(str(w.get("title") or ""))

        preferred = [w for w in chrome_windows if not _is_agent_page(w)] or chrome_windows
        foreground = next((w for w in preferred if w.get("foreground")), None)
        window = foreground or preferred[0]
        if not window.get("window_id"):
            raise BrowserDispatchError(
                "dependency_unavailable", "owner Chrome window has no window_id", False
            )
        return window

    def _wait_for_title(self, window_id: str, key: Callable[[str], str]) -> str:
        """Poll ``window.current`` until the title stops being a blank tab's, bounded by
        this gateway's own ``timeout_s`` (never an unbounded wait)."""
        polls = max(1, int(self._timeout_s // max(self._poll_interval_s, 0.1)) or 1)
        last_title = ""
        for i in range(polls):
            result = self._run("window.current", {}, key(f"wait_title:{i}"))
            window = result.get("window") if isinstance(result.get("window"), dict) else {}
            last_title = str(window.get("title") or "")
            if _page_title(last_title).strip().lower() not in _BLANK_TAB_TITLES:
                return _page_title(last_title)
            if i < polls - 1:
                self._sleep(self._poll_interval_s)
        return _page_title(last_title)

    # ----------------------------------------------------------------- read

    def _read_page(self, window_id: str, key: Callable[[str], str]) -> str:
        """PageDown + screen.ocr, merged: a line already seen (normalised text equality) is
        dropped, the window's own top chrome band is never content, and reading stops the
        moment a screen adds nothing new, the excerpt budget is met, or ``max_screens`` is
        reached — whichever comes first."""
        seen: set[str] = set()
        merged: list[str] = []
        band_px: int | None = None
        total_chars = 0
        for screen_index in range(self._max_screens):
            result = self._run("screen.ocr", {"window_id": window_id}, key(f"ocr:{screen_index}"))
            lines = [line for line in (result.get("lines") or []) if isinstance(line, dict)]
            if band_px is None:
                try:
                    band_px = int(float(result.get("height") or 0) * TOP_CHROME_BAND_FRACTION)
                except (TypeError, ValueError):
                    band_px = 0
            added = 0
            for line in lines:
                text = str(line.get("text") or "").strip()
                if not text:
                    continue
                try:
                    y = int(line.get("y"))
                except (TypeError, ValueError):
                    y = None
                if band_px and y is not None and y < band_px:
                    continue  # tab strip / address bar, never content
                normalized = fold(text)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                merged.append(text)
                added += 1
                total_chars += len(text) + 1
            if total_chars >= self._excerpt_chars:
                break
            if added == 0:
                break  # this screen added nothing new: scrolling further would not help
            if screen_index < self._max_screens - 1:
                self._run(
                    "keyboard.key",
                    {"window_id": window_id, "key": "pagedown"},
                    key(f"pagedown:{screen_index}"),
                )
        return "\n".join(merged)

    # --------------------------------------------------------------- fetch

    def fetch_url(
        self,
        url: str,
        *,
        query: str = "",
        source_class: str = "unknown",
        attempt: int = 1,
        tab: str = "new",
    ) -> EvidenceRecord:
        del tab  # interface parity with DeviceBrowserGateway.fetch_url; always "new" here

        try:
            validate_fetch_target(url)
        except DestinationPolicyError as exc:
            raise BrowserDispatchError("security_scope_error", str(exc), False) from exc

        def key(suffix: str) -> str:
            return f"{fetch_idempotency_key(self._task_id, url, attempt=attempt)}:{suffix}"

        window = self._find_owner_window(key)
        window_id = str(window["window_id"])

        self._run("window.activate", {"window_id": window_id}, key("activate"))
        self._run(
            "keyboard.shortcut", {"window_id": window_id, "keys": ["ctrl", "t"]}, key("tab_new")
        )
        try:
            self._run(
                "keyboard.type",
                {"window_id": window_id, "text": url, "secret": False},
                key("type_url"),
            )
            self._run("keyboard.key", {"window_id": window_id, "key": "enter"}, key("enter"))
            title = self._wait_for_title(window_id, key)
            excerpt = self._read_page(window_id, key)
        finally:
            # Best-effort, exactly like DeviceBrowserGateway.close_session: a failure here
            # must never mask whatever the read itself produced (or raised), but the tab
            # this fetch opened must never be left behind either.
            try:
                self._run(
                    "keyboard.shortcut",
                    {"window_id": window_id, "keys": ["ctrl", "w"]},
                    key("tab_close"),
                )
            except BrowserDispatchError as exc:
                logger.warning("owner_browser_tab_close_failed", url=url, error=str(exc))

        fetched_at = datetime.now(UTC)
        page_validity = classify_page_validity(
            title=title, excerpt=excerpt, http_status=None, url=url
        )
        page_kind = _PAGE_VALIDITY_TO_PAGE_KIND.get(page_validity, PAGE_KIND_OK)

        return EvidenceRecord(
            url=url,
            title=title,
            excerpt=excerpt,
            fetched_at=fetched_at,
            extraction_method=EXTRACTION_METHOD,
            source_class=source_class,
            query=query,
            final_url=url,
            published_at=_published_at_from_text(excerpt, now=fetched_at),
            retrieved_at=fetched_at,
            page_kind=page_kind,
            http_status=None,
            injection_suspected=False,
            device_id=str(self._device_id),
            command_id=None,
        )


__all__ = [
    "AGENT_OWN_TITLE_MARKER",
    "CHROME_IMAGE",
    "DEFAULT_MAX_SCREENS",
    "EXTRACTION_METHOD",
    "OWNER_BROWSER_FALLBACK_ERROR_CLASSES",
    "TOP_CHROME_BAND_FRACTION",
    "OwnerBrowserGateway",
]
