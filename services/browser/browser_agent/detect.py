"""M13 worker hello: detect browser availability/version without opening a window.

The worker's ``hello`` line (contract §7) must report ``browser.available``
and ``browser.version`` up front. Launching a full Chrome window just to
answer that would be wasteful and, per the task contract, is explicitly the
*last* resort: "resolve the channel executable and read its version; if you
must launch, launch headless and close."

Strategy, in order:

1. Resolve the channel's executable path (Playwright's own bundled Chromium
   via ``BrowserType.executable_path``, well-known per-OS install locations
   for the ``"chrome"`` channel, overridable by
   ``PAGENTOS_BROWSER_CHROME_PATH`` for non-standard installs).
2. If found, run ``<executable> --version`` as a plain subprocess (prints
   version text and exits immediately — this never opens a browser UI
   window) and parse the version string out of it.
3. If the executable exists but ``--version`` could not be parsed, fall back
   to actually launching headless and reading ``Browser.version``, then
   closing immediately — the documented last resort.
4. If no executable is found at all, ``available=False`` (worker
   ``--self-check`` exits non-zero in this case).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import async_playwright

_VERSION_SUBPROCESS_TIMEOUT_S = 5.0

# Well-known Google Chrome install locations per OS. Checked in order; the
# first that exists wins. `PAGENTOS_BROWSER_CHROME_PATH` overrides all of
# these when set (dev-machine/non-standard install override).
_CHROME_CANDIDATES_WINDOWS: tuple[str, ...] = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
_CHROME_CANDIDATES_MACOS: tuple[str, ...] = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
_CHROME_CANDIDATES_LINUX: tuple[str, ...] = (
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/opt/google/chrome/chrome",
)

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+\.\d+)")


@dataclass(frozen=True, slots=True)
class BrowserInfo:
    channel: str
    available: bool
    version: str | None
    executable_path: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "available": self.available,
            "version": self.version,
        }


def _windows_local_appdata_chrome() -> str | None:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return None
    candidate = str(Path(local_appdata) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return candidate


def _chrome_candidates() -> list[str]:
    override = os.environ.get("PAGENTOS_BROWSER_CHROME_PATH")
    if override:
        return [override]
    if sys.platform.startswith("win"):
        candidates = list(_CHROME_CANDIDATES_WINDOWS)
        appdata = _windows_local_appdata_chrome()
        if appdata:
            candidates.append(appdata)
        return candidates
    if sys.platform == "darwin":
        return list(_CHROME_CANDIDATES_MACOS)
    return list(_CHROME_CANDIDATES_LINUX)


def _resolve_chrome_executable() -> str | None:
    for candidate in _chrome_candidates():
        if Path(candidate).is_file():
            return candidate
    return None


async def _version_via_subprocess(executable: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            executable,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_VERSION_SUBPROCESS_TIMEOUT_S
            )
        finally:
            # Windows ProactorEventLoop schedules pipe-transport cleanup via
            # call_soon; without yielding once more here, asyncio.run() can
            # tear the loop down before that callback runs, and the
            # transport's __del__ then fires during interpreter shutdown as a
            # noisy (but harmless) ResourceWarning traceback on stderr. One
            # extra loop iteration lets the scheduled close happen cleanly.
            await asyncio.sleep(0)
    except (OSError, TimeoutError):
        return None
    match = _VERSION_RE.search(stdout.decode("utf-8", errors="replace"))
    return match.group(1) if match else None


async def _version_via_headless_launch(channel: str | None, executable: str | None) -> str | None:
    """Last resort per the task contract: launch headless, read version, close."""
    try:
        async with async_playwright() as p:
            kwargs: dict[str, str] = {}
            if channel:
                kwargs["channel"] = channel
            elif executable:
                kwargs["executable_path"] = executable
            browser = await p.chromium.launch(headless=True, **kwargs)
            try:
                return browser.version
            finally:
                await browser.close()
    except Exception:
        return None


async def detect_browser(channel: str | None, *, allow_launch_fallback: bool = True) -> BrowserInfo:
    """Detect availability/version for ``channel`` ("chrome"/"chromium"/None).

    ``None``/``"chromium"`` resolve Playwright's own bundled build (no system
    install needed — the CI-friendly path); ``"chrome"`` resolves the
    installed Google Chrome.
    """
    effective_channel = channel or "chromium"
    executable: str | None = None

    if effective_channel == "chrome":
        executable = _resolve_chrome_executable()
    else:
        try:
            async with async_playwright() as p:
                executable = p.chromium.executable_path
        except Exception:
            executable = None
        if executable and not Path(executable).is_file():
            executable = None

    if executable is None:
        return BrowserInfo(effective_channel, available=False, version=None, executable_path=None)

    version = await _version_via_subprocess(executable)
    if version is None and allow_launch_fallback:
        version = await _version_via_headless_launch(
            channel if channel else None, executable if not channel else None
        )
    return BrowserInfo(
        effective_channel, available=True, version=version, executable_path=executable
    )


def which_chromedriver_hint() -> str | None:  # pragma: no cover - diagnostic helper
    """Best-effort ``shutil.which`` fallback, used only for diagnostics/logs."""
    return shutil.which("chrome") or shutil.which("google-chrome")


__all__ = ["BrowserInfo", "detect_browser"]
