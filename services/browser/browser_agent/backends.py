"""Browser backend adapters: transport is separated from semantics (ADR-0019).

A :class:`BrowserBackend` owns *how* a browser is obtained and kept alive
(lifecycle: ``connect``/``close``/``is_alive``/``reconnect``), *which* pages
exist (tab surface: ``list_tabs``/``new_tab``/``close_tab``/``select_tab``/
``current_page``), and *what* it can do (``capabilities()``). The semantic
layer (:class:`~browser_agent.session.BrowserSession`) is written purely
against this interface, so managed Playwright, existing-session CDP attach and
the future visual-fallback adapter are interchangeable transports.

Implemented backends:

- :class:`ManagedBackend` — Playwright-managed Chromium. Two modes:
  *isolated* (fresh non-persistent context; the deterministic/CI default) and
  *persistent dedicated profile* (``profile_dir`` -> persistent context in a
  caller-supplied directory). The profile dir must be a dedicated agent
  profile — the constructor rejects paths that point into a real Chrome/Edge
  ``User Data`` tree.
- :class:`ExistingSessionBackend` — attaches over loopback CDP to an
  already-running browser, authorized by a
  :class:`~browser_agent.enrollment.BrowserEnrollment` record. It never
  launches a browser and never adds ``--remote-debugging-port`` itself; a
  non-loopback endpoint or a reserved transport is a typed
  ``validation_error`` before any I/O happens.
- ``VisualFallbackBackend`` — future explicit adapter (``visual_fallback``
  capability); deliberately not implemented in M2.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from .capabilities import BrowserCapabilities
from .enrollment import BrowserEnrollment, Transport, is_loopback_endpoint
from .errors import (
    BrowserError,
    ErrorClass,
    Phase,
    map_playwright_error,
    redact_url,
    require_navigable_url,
)
from .obs_logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

logger = get_logger(__name__)

DEFAULT_TAB_NAV_TIMEOUT_MS = 15_000


@dataclass(frozen=True, slots=True)
class TabInfo:
    """Provider-neutral description of one open tab."""

    index: int
    url: str
    title: str
    is_current: bool


class BrowserBackend(ABC):
    """Transport adapter contract. All methods raise only ``BrowserError``."""

    # -- lifecycle ------------------------------------------------------ #

    @abstractmethod
    async def connect(self) -> None:
        """Acquire a live browser (launch or attach). Idempotent per instance."""

    @abstractmethod
    async def close(self) -> None:
        """Release the browser (managed: terminate; attach: disconnect)."""

    @abstractmethod
    async def is_alive(self) -> bool:
        """True iff the underlying browser connection is currently usable."""

    @abstractmethod
    async def reconnect(self) -> None:
        """Restore a working browser after a crash/disconnect."""

    # -- capabilities --------------------------------------------------- #

    @abstractmethod
    def capabilities(self) -> BrowserCapabilities:
        """Static declaration; callable before ``connect``."""

    # -- page/tab surface ------------------------------------------------ #

    @property
    @abstractmethod
    def current_page(self) -> Page:
        """The page semantic operations act on."""

    @abstractmethod
    async def list_tabs(self) -> list[TabInfo]: ...

    @abstractmethod
    async def new_tab(self, url: str | None = None) -> int:
        """Open a tab (optionally navigating it), select it, return its index."""

    @abstractmethod
    async def close_tab(self, index: int) -> None: ...

    @abstractmethod
    async def select_tab(self, index: int) -> None: ...


class _PlaywrightBackendBase(BrowserBackend):
    """Shared Playwright plumbing for the two concrete backends."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._context_closed = False
        self._closed = False

    # -- helpers -------------------------------------------------------- #

    def _require_connected(self) -> BrowserContext:
        if self._context is None or self._closed:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"{type(self).__name__} is not connected; call connect() first",
                retryable=False,
            )
        return self._context

    def _open_pages(self) -> list[Page]:
        context = self._require_connected()
        return [page for page in context.pages if not page.is_closed()]

    def _mark_context(self, context: BrowserContext) -> None:
        self._context_closed = False
        context.on("close", lambda _ctx: setattr(self, "_context_closed", True))

    async def _stop_playwright(self) -> None:
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:  # cleanup must not mask real failures
                logger.warning("browser.playwright_stop_error", error=str(exc)[:200])
            self._playwright = None

    async def _discard_dead_browser(self) -> None:
        """Best-effort teardown of stale handles before a reconnect."""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:
                logger.debug("browser.discard_error", error=str(exc)[:200])
        elif self._context is not None:
            try:
                await self._context.close()
            except Exception as exc:
                logger.debug("browser.discard_error", error=str(exc)[:200])
        self._browser = None
        self._context = None
        self._page = None

    # -- page/tab surface ------------------------------------------------ #

    @property
    def current_page(self) -> Page:
        self._require_connected()
        if self._page is None or self._page.is_closed():
            raise BrowserError(
                ErrorClass.DEPENDENCY_UNAVAILABLE,
                f"{type(self).__name__}: current page is closed/unavailable",
                retryable=True,
            )
        return self._page

    async def list_tabs(self) -> list[TabInfo]:
        pages = self._open_pages()
        tabs: list[TabInfo] = []
        for index, page in enumerate(pages):
            try:
                title = await page.title()
            except Exception:  # page may be mid-navigation or freshly crashed
                title = ""
            tabs.append(
                TabInfo(index=index, url=page.url, title=title, is_current=page is self._page)
            )
        return tabs

    async def new_tab(self, url: str | None = None) -> int:
        if url is not None:
            require_navigable_url(url, op="new_tab")
        context = self._require_connected()
        try:
            page = await context.new_page()
        except Exception as exc:
            raise map_playwright_error(exc, phase=Phase.OTHER, op="new_tab") from exc
        self._page = page
        if url is not None:
            try:
                await page.goto(url, timeout=DEFAULT_TAB_NAV_TIMEOUT_MS, wait_until="load")
            except Exception as exc:
                raise map_playwright_error(
                    exc,
                    phase=Phase.NAVIGATE,
                    op="new_tab",
                    evidence={"url": redact_url(url)},
                ) from exc
        return self._open_pages().index(page)

    async def close_tab(self, index: int) -> None:
        pages = self._open_pages()
        page = self._tab_at(pages, index, op="close_tab")
        if len(pages) == 1:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "close_tab: refusing to close the last tab; close the session instead",
                retryable=False,
                evidence={"index": index},
            )
        was_current = page is self._page
        try:
            await page.close()
        except Exception as exc:
            raise map_playwright_error(exc, phase=Phase.OTHER, op="close_tab") from exc
        if was_current:
            remaining = self._open_pages()
            self._page = remaining[-1] if remaining else None

    async def select_tab(self, index: int) -> None:
        pages = self._open_pages()
        self._page = self._tab_at(pages, index, op="select_tab")

    @staticmethod
    def _tab_at(pages: Sequence[Page], index: int, *, op: str) -> Page:
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(pages):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"{op}: tab index {index!r} out of range (open tabs: {len(pages)})",
                retryable=False,
                evidence={"index": index, "tab_count": len(pages)},
            )
        return pages[index]


# --------------------------------------------------------------------------- #
# ManagedBackend
# --------------------------------------------------------------------------- #

# Directory names that indicate a real user browser profile tree. Managed
# persistent mode must only ever use a dedicated agent profile (ADR-0019 /
# task rule: never touch the owner's real Chrome/Edge state).
_REAL_PROFILE_MARKERS = (
    # Windows
    ("google", "chrome", "user data"),
    ("microsoft", "edge", "user data"),
    ("chromium", "user data"),
    ("bravesoftware", "brave-browser", "user data"),
    # macOS
    ("application support", "google", "chrome"),
    ("application support", "chromium"),
    ("application support", "microsoft edge"),
    ("application support", "bravesoftware", "brave-browser"),
    # Linux -- the browser agent runs in a Linux container in the cloud, where the
    # owner's real profiles live under ~/.config. Covering only the Windows shapes
    # made the guard a no-op on exactly the host it runs on in production.
    (".config", "google-chrome"),
    (".config", "chromium"),
    (".config", "microsoft-edge"),
    (".config", "bravesoftware", "brave-browser"),
)


def _candidate_part_tuples(profile_dir: Path) -> tuple[tuple[str, ...], ...]:
    """The path's segments, read both natively and as a Windows path.

    A Windows-shaped string is a SINGLE segment on POSIX -- ``Path`` does not treat
    a backslash as a separator there -- so scanning only ``Path.parts`` lets
    a real Windows-shaped Chrome profile path straight through
    unrejected on Linux. Reading it a second time as a ``PureWindowsPath`` makes the
    guard answer the same way regardless of which OS is asked.
    """
    candidates: list[tuple[str, ...]] = []
    try:
        candidates.append(tuple(part.lower() for part in profile_dir.resolve().parts))
    except OSError:  # unresolvable path: judge what we were given
        candidates.append(tuple(part.lower() for part in profile_dir.parts))
    candidates.append(
        tuple(part.lower().rstrip("\\") for part in PureWindowsPath(str(profile_dir)).parts)
    )
    return tuple(candidates)


def _reject_real_profile_dir(profile_dir: Path) -> None:
    for joined in _candidate_part_tuples(profile_dir):
        for marker in _REAL_PROFILE_MARKERS:
            for start in range(len(joined) - len(marker) + 1):
                if joined[start : start + len(marker)] != marker:
                    continue
                raise BrowserError(
                    ErrorClass.VALIDATION_ERROR,
                    "profile_dir points into a real browser profile tree "
                    f"({profile_dir}); ManagedBackend only accepts dedicated "
                    "agent profiles. Use ExistingSessionBackend + enrollment "
                    "for the owner's real browser.",
                    retryable=False,
                    evidence={"profile_dir": str(profile_dir)},
                )


#: Channels ``ManagedBackend`` accepts (M13). ``None``/omitted keeps M2's
#: behavior: Playwright's own bundled Chromium build, launched with no
#: ``channel=`` argument at all. ``"chrome"`` is the qualification target
#: (installed Google Chrome); ``"chromium"`` explicitly asks Playwright's
#: channel-aliased bundled build (CI-friendly, no system install needed).
ALLOWED_CHANNELS: frozenset[str] = frozenset({"chrome", "chromium"})


class ManagedBackend(_PlaywrightBackendBase):
    """Playwright-managed Chromium (isolated or persistent dedicated profile)."""

    def __init__(
        self,
        *,
        headless: bool = True,
        browser_args: list[str] | None = None,
        profile_dir: Path | str | None = None,
        channel: str | None = None,
    ) -> None:
        super().__init__()
        self._headless = headless
        self._browser_args = list(browser_args or [])
        self._profile_dir = Path(profile_dir) if profile_dir is not None else None
        if self._profile_dir is not None:
            _reject_real_profile_dir(self._profile_dir)
        if channel is not None and channel not in ALLOWED_CHANNELS:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"channel must be one of {sorted(ALLOWED_CHANNELS)} or None, got {channel!r}",
                retryable=False,
            )
        self._channel = channel

    @property
    def channel(self) -> str | None:
        return self._channel

    @property
    def persistent(self) -> bool:
        return self._profile_dir is not None

    def capabilities(self) -> BrowserCapabilities:
        return BrowserCapabilities(
            authenticated_session=self.persistent,
            downloads=True,
            uploads=True,
            extensions=False,
            existing_tabs=False,
            multiple_windows=True,
            visual_fallback=False,
        )

    async def connect(self) -> None:
        if self._context is not None and not self._closed:
            return
        start = time.perf_counter()
        self._closed = False
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        try:
            await self._launch()
        except BrowserError:
            raise
        except Exception as exc:
            await self._stop_playwright()
            raise map_playwright_error(exc, phase=Phase.CONNECT, op="managed_connect") from exc
        logger.info(
            "browser.backend_connected",
            backend="managed",
            persistent=self.persistent,
            headless=self._headless,
            duration_ms=_ms(start),
        )

    async def _launch(self) -> None:
        assert self._playwright is not None
        channel_kwargs: dict[str, str] = {"channel": self._channel} if self._channel else {}
        if self._profile_dir is not None:
            self._profile_dir.mkdir(parents=True, exist_ok=True)
            context = await self._playwright.chromium.launch_persistent_context(
                str(self._profile_dir),
                headless=self._headless,
                args=self._browser_args,
                **channel_kwargs,
            )
            self._browser = context.browser  # None on some persistent launches
            self._context = context
            self._page = context.pages[0] if context.pages else await context.new_page()
        else:
            browser = await self._playwright.chromium.launch(
                headless=self._headless, args=self._browser_args, **channel_kwargs
            )
            self._browser = browser
            self._context = await browser.new_context()
            self._page = await self._context.new_page()
        self._mark_context(self._context)

    async def is_alive(self) -> bool:
        if self._closed or self._context is None or self._context_closed:
            return False
        if self._browser is not None:
            return self._browser.is_connected()
        return self._page is not None and not self._page.is_closed()

    async def reconnect(self) -> None:
        """Relaunch a fresh managed browser (isolated) / reopen the profile."""
        start = time.perf_counter()
        await self._discard_dead_browser()
        self._closed = False
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        try:
            await self._launch()
        except Exception as exc:
            raise map_playwright_error(exc, phase=Phase.CONNECT, op="managed_reconnect") from exc
        logger.info("browser.backend_reconnected", backend="managed", duration_ms=_ms(start))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._browser is not None:
                await self._browser.close()
            elif self._context is not None:
                await self._context.close()
        except Exception as exc:
            logger.warning("browser.close_error", error=str(exc)[:200])
        finally:
            self._browser = None
            self._context = None
            self._page = None
            await self._stop_playwright()
        logger.info("browser.backend_closed", backend="managed")

    # Diagnostics/testing only (e.g. crash injection); never used for control.
    @property
    def native_browser(self) -> Browser | None:
        return self._browser


# --------------------------------------------------------------------------- #
# ExistingSessionBackend
# --------------------------------------------------------------------------- #


class ExistingSessionBackend(_PlaywrightBackendBase):
    """Attach to an already-running browser authorized by an enrollment.

    The backend is handed a loopback CDP endpoint through the enrollment
    record; it never launches a browser and never configures debug ports.
    ``close()`` disconnects without terminating the external browser.
    """

    def __init__(
        self, enrollment: BrowserEnrollment, *, connect_timeout_ms: float = 10_000
    ) -> None:
        super().__init__()
        if enrollment.transport is not Transport.CDP_LOOPBACK:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"enrollment transport {enrollment.transport!s} is not implemented "
                "in M2 (extension_bridge is reserved); use cdp_loopback",
                retryable=False,
                evidence={"enrollment_id": enrollment.id},
            )
        if not is_loopback_endpoint(enrollment.endpoint):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "enrollment endpoint must be a loopback http(s)/ws(s) URL; "
                f"got {enrollment.endpoint!r}. Non-loopback CDP exposure is "
                "prohibited (ADR-0019).",
                retryable=False,
                evidence={"enrollment_id": enrollment.id, "endpoint": enrollment.endpoint},
            )
        self._enrollment = enrollment
        self._connect_timeout_ms = connect_timeout_ms

    @property
    def enrollment(self) -> BrowserEnrollment:
        return self._enrollment

    def capabilities(self) -> BrowserCapabilities:
        base = BrowserCapabilities(
            authenticated_session=True,
            downloads=True,  # best-effort over CDP attach
            uploads=True,  # best-effort over CDP attach
            extensions=True,
            existing_tabs=True,
            multiple_windows=True,
            visual_fallback=False,
        )
        return base.with_overrides(self._enrollment.capability_overrides)

    async def connect(self) -> None:
        if self._context is not None and not self._closed:
            return
        start = time.perf_counter()
        self._closed = False
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        try:
            await self._attach()
        except BrowserError:
            raise
        except Exception as exc:
            await self._stop_playwright()
            raise map_playwright_error(
                exc,
                phase=Phase.CONNECT,
                op="existing_session_connect",
                evidence={
                    "endpoint_url": self._enrollment.endpoint,
                    "enrollment_id": self._enrollment.id,
                },
            ) from exc
        logger.info(
            "browser.backend_connected",
            backend="existing_session",
            enrollment_id=self._enrollment.id,
            endpoint=self._enrollment.endpoint,
            duration_ms=_ms(start),
        )

    async def _attach(self) -> None:
        assert self._playwright is not None
        browser = await self._playwright.chromium.connect_over_cdp(
            self._enrollment.endpoint, timeout=self._connect_timeout_ms
        )
        self._browser = browser
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        self._context = context
        self._page = context.pages[0] if context.pages else await context.new_page()
        self._mark_context(context)

    async def is_alive(self) -> bool:
        if self._closed or self._browser is None:
            return False
        return self._browser.is_connected()

    async def reconnect(self) -> None:
        """Re-attach to the enrollment endpoint after a disconnect/crash."""
        start = time.perf_counter()
        await self._discard_dead_browser()
        self._closed = False
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        try:
            await self._attach()
        except Exception as exc:
            raise map_playwright_error(
                exc,
                phase=Phase.CONNECT,
                op="existing_session_reconnect",
                evidence={
                    "endpoint_url": self._enrollment.endpoint,
                    "enrollment_id": self._enrollment.id,
                },
            ) from exc
        logger.info(
            "browser.backend_reconnected",
            backend="existing_session",
            enrollment_id=self._enrollment.id,
            duration_ms=_ms(start),
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._browser is not None:
                await self._browser.close()  # disconnects; external browser survives
        except Exception as exc:
            logger.warning("browser.close_error", error=str(exc)[:200])
        finally:
            self._browser = None
            self._context = None
            self._page = None
            await self._stop_playwright()
        logger.info(
            "browser.backend_closed",
            backend="existing_session",
            enrollment_id=self._enrollment.id,
        )


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
