"""Provider-neutral semantic browser session on top of a BrowserBackend.

The session owns *semantics* (what to do, expressed via
:class:`~browser_agent.targets.TargetSpec`); the injected
:class:`~browser_agent.backends.BrowserBackend` owns *transport* (which
browser, how it is reached, tabs/lifecycle). All semantic operations are
therefore transport-agnostic (ADR-0019).

Ways to obtain a session:

- ``BrowserSession(backend)`` with a connected backend — the canonical route.
- :meth:`BrowserSession.launch_dedicated` — convenience: isolated
  :class:`~browser_agent.backends.ManagedBackend` (v1-compatible).
- :meth:`BrowserSession.connect_existing_cdp` — convenience: attaches via an
  ephemeral loopback :class:`~browser_agent.enrollment.BrowserEnrollment`
  (dev/test only; production attach goes through the EnrollmentRegistry).

All interaction goes through semantic :class:`~browser_agent.targets.TargetSpec`
locators (role+name, text, label, placeholder, test id). Raw coordinates are
not part of the semantic API; :meth:`escape_hatch_click_xy` exists only as an
explicitly-named last resort for the lowest rung of the control-surface
hierarchy and must not be used when a semantic target is expressible.

Every operation is two-phase — *resolve* (wait for the semantic locator to
attach) then *act* — so failures map deterministically onto the typed
taxonomy in :mod:`browser_agent.errors`. Operations raise only
:class:`~browser_agent.errors.BrowserError`.

Iframe interaction: operations accept ``frame=<name>`` naming the target
``<iframe name="...">``; the semantic target is then resolved inside that
frame. The frame is addressed by its semantic ``name`` attribute, never by
CSS/XPath supplied by callers.
"""

from __future__ import annotations

import hashlib
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import FrameLocator, Locator, Page, Response

from .backends import (
    BrowserBackend,
    ExistingSessionBackend,
    ManagedBackend,
    TabInfo,
)
from .capabilities import require_capability
from .enrollment import BrowserEnrollment
from .errors import (
    BrowserError,
    ErrorClass,
    Phase,
    map_playwright_error,
    redact_url,
    require_navigable_url,
)
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
    """One semantic browser automation session over a backend. Not thread-safe."""

    def __init__(self, backend: BrowserBackend, *, file_io_root: Path | str | None = None) -> None:
        """``file_io_root``: the only directory tree this session may read
        upload sources from or write screenshot files to (defense in depth
        against page-content-influenced path arguments; M2 security review
        finding #4). When unset, ``upload`` and ``screenshot(path=...)`` are
        refused; downloads still land in a fresh temp dir by default."""
        self._backend = backend
        self._file_io_root = Path(file_io_root).resolve() if file_io_root else None
        self._closed = False

    def _require_within_file_io_root(self, path: Path, *, op: str) -> Path:
        resolved = path.resolve()
        if self._file_io_root is None or not resolved.is_relative_to(self._file_io_root):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"{op}: path is outside the session's configured file_io_root",
                retryable=False,
                evidence={"op": op, "file_io_root": str(self._file_io_root)},
            )
        return resolved

    # ------------------------------------------------------------------ #
    # constructors (v1-compatible convenience routes)
    # ------------------------------------------------------------------ #

    @classmethod
    async def launch_dedicated(
        cls,
        *,
        headless: bool = True,
        browser_args: list[str] | None = None,
        profile_dir: Path | str | None = None,
        file_io_root: Path | str | None = None,
    ) -> BrowserSession:
        """Launch a dedicated Playwright-managed Chromium session.

        ``profile_dir`` switches the managed backend into persistent
        dedicated-profile mode (never a real user profile).
        """
        backend = ManagedBackend(
            headless=headless, browser_args=browser_args, profile_dir=profile_dir
        )
        await backend.connect()
        logger.info(
            "browser.session_started",
            mode="dedicated",
            persistent=backend.persistent,
            headless=headless,
        )
        return cls(backend, file_io_root=file_io_root)

    @classmethod
    async def connect_existing_cdp(
        cls,
        endpoint_url: str,
        *,
        timeout_ms: float = 10_000,
        file_io_root: Path | str | None = None,
    ) -> BrowserSession:
        """Attach to an already-running Chromium via a loopback CDP endpoint.

        Dev/test convenience: wraps the endpoint in an ephemeral
        ``BrowserEnrollment``. Production attach uses a registered enrollment
        from the ``EnrollmentRegistry`` and ``ExistingSessionBackend``
        directly. Non-loopback endpoints are rejected (validation_error).
        Closing the session disconnects; it does not terminate the external
        browser.
        """
        enrollment = BrowserEnrollment.cdp_loopback(endpoint_url, name="ephemeral-dev")
        backend = ExistingSessionBackend(enrollment, connect_timeout_ms=timeout_ms)
        await backend.connect()
        logger.info("browser.session_started", mode="cdp", endpoint_url=endpoint_url)
        return cls(backend, file_io_root=file_io_root)

    # ------------------------------------------------------------------ #
    # backend surface
    # ------------------------------------------------------------------ #

    @property
    def backend(self) -> BrowserBackend:
        """The transport backend (capabilities, reconnect, diagnostics)."""
        return self._backend

    @property
    def _page(self) -> Page:
        return self._backend.current_page

    @property
    def url(self) -> str:
        """Current page URL."""
        return self._page.url

    async def is_alive(self) -> bool:
        """Whether the underlying browser connection is currently usable."""
        return await self._backend.is_alive()

    # ------------------------------------------------------------------ #
    # navigation and history
    # ------------------------------------------------------------------ #

    async def navigate(
        self, url: str, *, timeout_ms: float = DEFAULT_NAV_TIMEOUT_MS
    ) -> Response | None:
        """Navigate the page and wait for the load event (http/https only).

        Returns the navigation's :class:`~playwright.async_api.Response`
        (``None`` for same-document navigations Playwright reports without
        one) so callers needing HTTP status (M13 ``page_kind``/site-error
        classification, contract §3) can read ``response.status`` without a
        second round trip. Existing callers that only awaited this for its
        side effect are unaffected — the return value was ``None`` before and
        is simply not ``None`` now when a response exists.
        """
        require_navigable_url(url, op="navigate")
        async with self._oplog("navigate", url=redact_url(url)):
            try:
                return await self._page.goto(url, timeout=timeout_ms, wait_until="load")
            except BrowserError:
                raise
            except Exception as exc:
                raise map_playwright_error(
                    exc,
                    phase=Phase.NAVIGATE,
                    op="navigate",
                    evidence={"url": redact_url(url)},
                ) from exc

    async def back(self, *, timeout_ms: float = DEFAULT_NAV_TIMEOUT_MS) -> str:
        """Go back in history (full navigations and SPA pushState alike)."""
        async with self._oplog("back"):
            try:
                await self._page.go_back(timeout=timeout_ms, wait_until="load")
            except BrowserError:
                raise
            except Exception as exc:
                raise map_playwright_error(exc, phase=Phase.NAVIGATE, op="back") from exc
            return self._page.url

    async def forward(self, *, timeout_ms: float = DEFAULT_NAV_TIMEOUT_MS) -> str:
        """Go forward in history."""
        async with self._oplog("forward"):
            try:
                await self._page.go_forward(timeout=timeout_ms, wait_until="load")
            except BrowserError:
                raise
            except Exception as exc:
                raise map_playwright_error(exc, phase=Phase.NAVIGATE, op="forward") from exc
            return self._page.url

    # ------------------------------------------------------------------ #
    # tab surface (delegated to the backend)
    # ------------------------------------------------------------------ #

    async def list_tabs(self) -> list[TabInfo]:
        """Open tabs of the current browser context."""
        async with self._oplog("list_tabs"):
            return await self._backend.list_tabs()

    async def new_tab(self, url: str | None = None) -> int:
        """Open (and select) a new tab; optionally navigate it. Returns index."""
        if url is not None:
            require_navigable_url(url, op="new_tab")
        async with self._oplog("new_tab", url=redact_url(url) if url else None):
            return await self._backend.new_tab(url)

    async def select_tab(self, index: int) -> None:
        """Make the tab at ``index`` the current page for semantic operations."""
        async with self._oplog("select_tab", index=index):
            await self._backend.select_tab(index)

    async def close_tab(self, index: int) -> None:
        """Close the tab at ``index`` (never the last remaining tab)."""
        async with self._oplog("close_tab", index=index):
            await self._backend.close_tab(index)

    # ------------------------------------------------------------------ #
    # semantic operations
    # ------------------------------------------------------------------ #

    async def find(
        self,
        target: TargetSpec | dict[str, Any],
        *,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
        frame: str | None = None,
    ) -> ElementInfo:
        """Resolve a semantic target; raise ui_target_not_found if absent."""
        spec = coerce_target(target)
        async with self._oplog("find", target=spec.as_dict(), frame=frame):
            locator = await self._resolve(spec, timeout_ms=timeout_ms, op="find", frame=frame)
            try:
                count = await spec.to_locator(self._root(frame)).count()
                text = await locator.inner_text(timeout=timeout_ms)
            except Exception as exc:
                raise self._act_error(exc, "find", spec) from exc
            return ElementInfo(match_count=count, text=text)

    async def click(
        self,
        target: TargetSpec | dict[str, Any],
        *,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
        frame: str | None = None,
    ) -> None:
        """Click a semantic target (``.first`` on ambiguous matches)."""
        spec = coerce_target(target)
        async with self._oplog("click", target=spec.as_dict(), frame=frame):
            locator = await self._resolve(spec, timeout_ms=timeout_ms, op="click", frame=frame)
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
        frame: str | None = None,
    ) -> None:
        """Fill an input/textarea resolved from a semantic target."""
        if not isinstance(value, str):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"fill: value must be a string, got {type(value).__name__}",
                retryable=False,
            )
        spec = coerce_target(target)
        async with self._oplog("fill", target=spec.as_dict(), frame=frame):
            locator = await self._resolve(spec, timeout_ms=timeout_ms, op="fill", frame=frame)
            try:
                await locator.fill(value, timeout=timeout_ms)
            except Exception as exc:
                raise self._act_error(exc, "fill", spec) from exc

    async def read_text(
        self,
        target: TargetSpec | dict[str, Any],
        *,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
        frame: str | None = None,
    ) -> str:
        """Read the rendered inner text of a semantic target."""
        spec = coerce_target(target)
        async with self._oplog("read_text", target=spec.as_dict(), frame=frame):
            locator = await self._resolve(spec, timeout_ms=timeout_ms, op="read_text", frame=frame)
            try:
                return await locator.inner_text(timeout=timeout_ms)
            except Exception as exc:
                raise self._act_error(exc, "read_text", spec) from exc

    async def select_option(
        self,
        target: TargetSpec | dict[str, Any],
        *,
        value: str | None = None,
        label: str | None = None,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
        frame: str | None = None,
    ) -> list[str]:
        """Select a ``<select>`` option by option value or visible label."""
        if (value is None) == (label is None):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "select_option: set exactly one of value= or label=",
                retryable=False,
            )
        spec = coerce_target(target)
        async with self._oplog("select_option", target=spec.as_dict(), frame=frame):
            locator = await self._resolve(
                spec, timeout_ms=timeout_ms, op="select_option", frame=frame
            )
            try:
                return await locator.select_option(value=value, label=label, timeout=timeout_ms)
            except Exception as exc:
                raise self._act_error(exc, "select_option", spec) from exc

    async def set_checked(
        self,
        target: TargetSpec | dict[str, Any],
        checked: bool,
        *,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
        frame: str | None = None,
    ) -> None:
        """Check/uncheck a checkbox or select a radio button."""
        if not isinstance(checked, bool):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"set_checked: checked must be a bool, got {type(checked).__name__}",
                retryable=False,
            )
        spec = coerce_target(target)
        async with self._oplog("set_checked", target=spec.as_dict(), checked=checked, frame=frame):
            locator = await self._resolve(
                spec, timeout_ms=timeout_ms, op="set_checked", frame=frame
            )
            try:
                await locator.set_checked(checked, timeout=timeout_ms)
            except Exception as exc:
                raise self._act_error(exc, "set_checked", spec) from exc

    async def upload(
        self,
        target: TargetSpec | dict[str, Any],
        file_path: Path | str,
        *,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
        frame: str | None = None,
    ) -> None:
        """Populate a file input from a local file (capability: uploads).

        The file must live under the session's ``file_io_root``."""
        require_capability(self._backend, "uploads")
        path = self._require_within_file_io_root(Path(file_path), op="upload")
        if not path.is_file():
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"upload: file does not exist: {path}",
                retryable=False,
                evidence={"file_path": str(path)},
            )
        spec = coerce_target(target)
        async with self._oplog("upload", target=spec.as_dict(), file=str(path), frame=frame):
            locator = await self._resolve(spec, timeout_ms=timeout_ms, op="upload", frame=frame)
            try:
                await locator.set_input_files(path, timeout=timeout_ms)
            except Exception as exc:
                raise self._act_error(exc, "upload", spec) from exc

    async def title(self) -> str:
        """The current page's document title (DOM-level, no coordinates).

        Used by M13 evidence extraction for source provenance — the highest
        control surface available for "what is this page called" (CLAUDE.md
        browser rule: DOM/Playwright before accessibility tree, before
        vision/coordinates).
        """
        async with self._oplog("title"):
            try:
                return await self._page.title()
            except BrowserError:
                raise
            except Exception as exc:
                raise map_playwright_error(
                    exc,
                    phase=Phase.ACT,
                    op="title",
                    evidence={"url": redact_url(self._page.url)},
                ) from exc

    async def page_text(self, *, timeout_ms: float = DEFAULT_TIMEOUT_MS) -> str:
        """Rendered inner text of the whole page body (DOM-level extraction).

        M13 evidence extraction's primary text source: ``body.inner_text()``
        is a DOM read, one rung above the accessibility snapshot on the
        control-surface hierarchy and far above vision/coordinates. Callers
        needing structural (role-labelled) text instead should use
        :meth:`accessibility_snapshot`.
        """
        async with self._oplog("page_text"):
            try:
                return await self._page.locator("body").inner_text(timeout=timeout_ms)
            except BrowserError:
                raise
            except Exception as exc:
                raise map_playwright_error(
                    exc,
                    phase=Phase.ACT,
                    op="page_text",
                    evidence={"url": redact_url(self._page.url)},
                ) from exc

    async def accessibility_snapshot(self, *, timeout_ms: float = DEFAULT_TIMEOUT_MS) -> str:
        """Structured accessibility (ARIA) snapshot of the page as YAML text."""
        async with self._oplog("accessibility_snapshot"):
            try:
                return await self._page.locator("body").aria_snapshot(timeout=timeout_ms)
            except BrowserError:
                raise
            except Exception as exc:
                raise map_playwright_error(
                    exc,
                    phase=Phase.ACT,
                    op="accessibility_snapshot",
                    evidence={"url": redact_url(self._page.url)},
                ) from exc

    async def download(
        self,
        target: TargetSpec | dict[str, Any],
        *,
        save_dir: Path | str | None = None,
        timeout_ms: float = DEFAULT_NAV_TIMEOUT_MS,
    ) -> DownloadResult:
        """Click a semantic target that starts a download; save it and hash it."""
        require_capability(self._backend, "downloads")
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
        """Diagnostic-only full-page screenshot (never used for control).

        A file ``path`` must live under the session's ``file_io_root``."""
        if path is not None:
            path = self._require_within_file_io_root(Path(path), op="screenshot")
        async with self._oplog("screenshot"):
            try:
                return await self._page.screenshot(path=str(path) if path else None, full_page=True)
            except BrowserError:
                raise
            except Exception as exc:
                raise map_playwright_error(exc, phase=Phase.OTHER, op="screenshot") from exc

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
                url=redact_url(self._page.url),
            )
            try:
                await self._page.mouse.click(x, y)
            except BrowserError:
                raise
            except Exception as exc:
                raise map_playwright_error(
                    exc, phase=Phase.ACT, op="escape_hatch_click_xy"
                ) from exc

    async def close(self) -> None:
        """Close the session by closing/disconnecting the backend."""
        if self._closed:
            return
        self._closed = True
        await self._backend.close()
        logger.info("browser.session_closed", backend=type(self._backend).__name__)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _root(self, frame: str | None) -> Page | FrameLocator:
        """The locator root: the page, or a named iframe within it."""
        if frame is None:
            return self._page
        if not isinstance(frame, str) or not frame.strip():
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "frame must be a non-empty iframe name",
                retryable=False,
            )
        if '"' in frame or "\\" in frame:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"frame name contains illegal characters: {frame!r}",
                retryable=False,
            )
        return self._page.frame_locator(f'iframe[name="{frame}"]')

    async def _resolve(
        self,
        spec: TargetSpec,
        *,
        timeout_ms: float,
        op: str,
        frame: str | None = None,
    ) -> Locator:
        """Resolve phase: wait for the semantic locator to attach.

        A timeout here means the target resolves to nothing on the current
        page -> ui_target_not_found (see errors module mapping table).
        """
        locator = spec.to_locator(self._root(frame)).first
        try:
            await locator.wait_for(state="attached", timeout=timeout_ms)
        except Exception as exc:
            evidence: dict[str, Any] = {
                "target": spec.as_dict(),
                "url": redact_url(self._page.url),
            }
            if frame is not None:
                evidence["frame"] = frame
            raise map_playwright_error(exc, phase=Phase.RESOLVE, op=op, evidence=evidence) from exc
        return locator

    def _act_error(self, exc: Exception, op: str, spec: TargetSpec) -> BrowserError:
        if isinstance(exc, BrowserError):
            return exc  # already typed (e.g. dead page surfaced by the backend)
        return map_playwright_error(
            exc,
            phase=Phase.ACT,
            op=op,
            evidence={"target": spec.as_dict(), "url": redact_url(self._page.url)},
        )

    @asynccontextmanager
    async def _oplog(self, op: str, **fields: Any):
        start = time.perf_counter()
        fields = {k: v for k, v in fields.items() if v is not None}
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
