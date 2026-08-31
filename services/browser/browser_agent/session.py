"""Provider-neutral semantic browser session on top of Playwright.

Two ways to obtain a session:

- :meth:`BrowserSession.launch_dedicated` — a Playwright-managed Chromium
  (headless configurable). This is the default, fully isolated route.
- :meth:`BrowserSession.connect_existing_cdp` — attach to an already-running
  Chromium-family browser (Chrome/Edge) that was started with
  ``--remote-debugging-port=<port>``, via CDP. This is the documented
  existing-session route for the owner's real browser profile.

All interaction goes through semantic :class:`~browser_agent.targets.TargetSpec`
locators (role+name, text, label, placeholder, test id). Raw coordinates are
not part of the semantic API; :meth:`escape_hatch_click_xy` exists only as an
explicitly-named last resort for the lowest rung of the control-surface
hierarchy and must not be used when a semantic target is expressible.

Every operation is two-phase — *resolve* (wait for the semantic locator to
attach) then *act* — so failures map deterministically onto the typed
taxonomy in :mod:`browser_agent.errors`. Operations raise only
:class:`~browser_agent.errors.BrowserError`.
"""

from __future__ import annotations

import hashlib
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    async_playwright,
)

from .errors import BrowserError, ErrorClass, Phase, map_playwright_error
from .obs_logging import get_logger
from .targets import TargetSpec, coerce_target

logger = get_logger(__name__)

DEFAULT_TIMEOUT_MS = 5_000
DEFAULT_NAV_TIMEOUT_MS = 15_000


@dataclass(frozen=True, slots=True)
class ElementInfo:
    """Provider-neutral result of resolving a semantic target."""

    match_count: int
    text: str


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """Saved download artifact."""

    path: Path
    sha256: str
    suggested_filename: str


class BrowserSession:
    """One page-oriented browser automation session. Not thread-safe."""

    def __init__(
        self,
        playwright: Playwright,
        browser: Browser,
        context: BrowserContext,
        page: Page,
        *,
        mode: str,
    ) -> None:
        self._playwright = playwright
        self._browser = browser
        self._context = context
        self._page = page
        self._mode = mode
        self._closed = False

    # ------------------------------------------------------------------ #
    # constructors
    # ------------------------------------------------------------------ #

    @classmethod
    async def launch_dedicated(
        cls,
        *,
        headless: bool = True,
        browser_args: list[str] | None = None,
    ) -> BrowserSession:
        """Launch a dedicated Playwright-managed Chromium and open one page."""
        start = time.perf_counter()
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch(
                headless=headless, args=browser_args or []
            )
            context = await browser.new_context()
            page = await context.new_page()
        except Exception as exc:
            await playwright.stop()
            raise map_playwright_error(
                exc, phase=Phase.CONNECT, op="launch_dedicated"
            ) from exc
        logger.info(
            "browser.session_started",
            mode="dedicated",
            headless=headless,
            duration_ms=_ms(start),
        )
        return cls(playwright, browser, context, page, mode="dedicated")

    @classmethod
    async def connect_existing_cdp(
        cls,
        endpoint_url: str,
        *,
        timeout_ms: float = 10_000,
    ) -> BrowserSession:
        """Attach to an already-running Chromium via its CDP endpoint.

        ``endpoint_url`` is the HTTP endpoint of a browser started with
        ``--remote-debugging-port``, e.g. ``http://127.0.0.1:9222``. Closing
        the session disconnects; it does not terminate the external browser.
        """
        start = time.perf_counter()
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.connect_over_cdp(
                endpoint_url, timeout=timeout_ms
            )
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
        except Exception as exc:
            await playwright.stop()
            raise map_playwright_error(
                exc,
                phase=Phase.CONNECT,
                op="connect_existing_cdp",
                evidence={"endpoint_url": endpoint_url},
            ) from exc
        logger.info(
            "browser.session_started",
            mode="cdp",
            endpoint_url=endpoint_url,
            duration_ms=_ms(start),
        )
        return cls(playwright, browser, context, page, mode="cdp")

    # ------------------------------------------------------------------ #
    # semantic operations
    # ------------------------------------------------------------------ #

    @property
    def url(self) -> str:
        """Current page URL."""
        return self._page.url

    async def navigate(
        self, url: str, *, timeout_ms: float = DEFAULT_NAV_TIMEOUT_MS
    ) -> None:
        """Navigate the page and wait for the load event."""
        async with self._oplog("navigate", url=url):
            try:
                await self._page.goto(url, timeout=timeout_ms, wait_until="load")
            except Exception as exc:
                raise map_playwright_error(
                    exc, phase=Phase.NAVIGATE, op="navigate", evidence={"url": url}
                ) from exc

    async def find(
        self,
        target: TargetSpec | dict[str, Any],
        *,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
    ) -> ElementInfo:
        """Resolve a semantic target; raise ui_target_not_found if absent."""
        spec = coerce_target(target)
        async with self._oplog("find", target=spec.as_dict()):
            locator = await self._resolve(spec, timeout_ms=timeout_ms, op="find")
            try:
                count = await spec.to_locator(self._page).count()
                text = await locator.inner_text(timeout=timeout_ms)
            except Exception as exc:
                raise self._act_error(exc, "find", spec) from exc
            return ElementInfo(match_count=count, text=text)

    async def click(
        self,
        target: TargetSpec | dict[str, Any],
        *,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
    ) -> None:
        """Click a semantic target."""
        spec = coerce_target(target)
        async with self._oplog("click", target=spec.as_dict()):
            locator = await self._resolve(spec, timeout_ms=timeout_ms, op="click")
            try:
                await locator.click(timeout=timeout_ms)
            except Exception as exc:
                raise self._act_error(exc, "click", spec) from exc

    async def fill(
        self,
        target: TargetSpec | dict[str, Any],
        value: str,
        *,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
    ) -> None:
        """Fill an input/textarea resolved from a semantic target."""
        if not isinstance(value, str):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"fill: value must be a string, got {type(value).__name__}",
                retryable=False,
            )
        spec = coerce_target(target)
        async with self._oplog("fill", target=spec.as_dict()):
            locator = await self._resolve(spec, timeout_ms=timeout_ms, op="fill")
            try:
                await locator.fill(value, timeout=timeout_ms)
            except Exception as exc:
                raise self._act_error(exc, "fill", spec) from exc

    async def read_text(
        self,
        target: TargetSpec | dict[str, Any],
        *,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
    ) -> str:
        """Read the rendered inner text of a semantic target."""
        spec = coerce_target(target)
        async with self._oplog("read_text", target=spec.as_dict()):
            locator = await self._resolve(spec, timeout_ms=timeout_ms, op="read_text")
            try:
                return await locator.inner_text(timeout=timeout_ms)
            except Exception as exc:
                raise self._act_error(exc, "read_text", spec) from exc

    async def accessibility_snapshot(
        self, *, timeout_ms: float = DEFAULT_TIMEOUT_MS
    ) -> str:
        """Structured accessibility (ARIA) snapshot of the page as YAML text."""
        async with self._oplog("accessibility_snapshot"):
            try:
                return await self._page.locator("body").aria_snapshot(timeout=timeout_ms)
            except Exception as exc:
                raise map_playwright_error(
                    exc,
                    phase=Phase.ACT,
                    op="accessibility_snapshot",
                    evidence={"url": self._page.url},
                ) from exc

    async def download(
        self,
        target: TargetSpec | dict[str, Any],
        *,
        save_dir: Path | str | None = None,
        timeout_ms: float = DEFAULT_NAV_TIMEOUT_MS,
    ) -> DownloadResult:
        """Click a semantic target that starts a download; save it and hash it."""
        spec = coerce_target(target)
        directory = Path(save_dir) if save_dir else Path(tempfile.mkdtemp(prefix="pagentos-dl-"))
        directory.mkdir(parents=True, exist_ok=True)
        async with self._oplog("download", target=spec.as_dict()):
            locator = await self._resolve(spec, timeout_ms=timeout_ms, op="download")
            try:
                async with self._page.expect_download(timeout=timeout_ms) as download_info:
                    await locator.click(timeout=timeout_ms)
                download = await download_info.value
                filename = Path(download.suggested_filename).name or "download.bin"
                path = directory / filename
                await download.save_as(path)
            except Exception as exc:
                raise self._act_error(exc, "download", spec) from exc
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            return DownloadResult(path=path, sha256=digest, suggested_filename=filename)

    async def screenshot(self, *, path: Path | str | None = None) -> bytes:
        """Diagnostic-only full-page screenshot (never used for control)."""
        async with self._oplog("screenshot"):
            try:
                return await self._page.screenshot(
                    path=str(path) if path else None, full_page=True
                )
            except Exception as exc:
                raise map_playwright_error(
                    exc, phase=Phase.OTHER, op="screenshot"
                ) from exc

    async def escape_hatch_click_xy(self, x: float, y: float) -> None:
        """LAST RESORT: raw coordinate click (lowest control-surface rung).

        Only for cases where no semantic surface (DOM role/name, text, label,
        placeholder, test id, accessibility tree) can express the target.
        Every use is logged loudly so it shows up in telemetry.
        """
        async with self._oplog("escape_hatch_click_xy", x=x, y=y):
            logger.warning(
                "browser.escape_hatch_used",
                op="escape_hatch_click_xy",
                x=x,
                y=y,
                url=self._page.url,
            )
            try:
                await self._page.mouse.click(x, y)
            except Exception as exc:
                raise map_playwright_error(
                    exc, phase=Phase.ACT, op="escape_hatch_click_xy"
                ) from exc

    async def close(self) -> None:
        """Close the session. Dedicated: closes the browser. CDP: disconnects."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._browser.close()
        except Exception as exc:  # closing must never mask the real failure
            logger.warning("browser.close_error", error=str(exc)[:200])
        finally:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.warning("browser.playwright_stop_error", error=str(exc)[:200])
        logger.info("browser.session_closed", mode=self._mode)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    async def _resolve(
        self, spec: TargetSpec, *, timeout_ms: float, op: str
    ) -> Locator:
        """Resolve phase: wait for the semantic locator to attach.

        A timeout here means the target resolves to nothing on the current
        page -> ui_target_not_found (see errors module mapping table).
        """
        locator = spec.to_locator(self._page).first
        try:
            await locator.wait_for(state="attached", timeout=timeout_ms)
        except Exception as exc:
            raise map_playwright_error(
                exc,
                phase=Phase.RESOLVE,
                op=op,
                evidence={"target": spec.as_dict(), "url": self._page.url},
            ) from exc
        return locator

    def _act_error(self, exc: Exception, op: str, spec: TargetSpec) -> BrowserError:
        return map_playwright_error(
            exc,
            phase=Phase.ACT,
            op=op,
            evidence={"target": spec.as_dict(), "url": self._page.url},
        )

    @asynccontextmanager
    async def _oplog(self, op: str, **fields: Any):
        start = time.perf_counter()
        try:
            yield
        except BrowserError as err:
            logger.error(
                "browser.op_failed",
                op=op,
                duration_ms=_ms(start),
                error_class=str(err.error_class),
                retryable=err.retryable,
                **fields,
            )
            raise
        else:
            logger.info("browser.op", op=op, duration_ms=_ms(start), **fields)


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
