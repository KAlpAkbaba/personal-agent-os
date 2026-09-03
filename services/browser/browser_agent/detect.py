"""M13 worker hello: detect browser availability/version WITHOUT running the browser.

The worker's ``hello`` line (contract §7) must report ``browser.available``
and ``browser.version`` up front. Detection must never start a browser
process, visible or not.

Root cause of the 2026-09-03 owner-machine incident ("browser processes
keep spawning even after the command exited"): the previous implementation
ran ``<chrome.exe> --version`` as a subprocess to read the version. On
Windows, Chrome does not implement ``--version`` as a print-and-exit flag:
it starts the full browser with the default profile (a visible window that
outlives the caller; with Google Chrome already running it hands off to
that instance and opens a new window there). Every worker start, every
installer/verify self-check and every test fixture therefore opened one
more window. Proven by experiment under a desktop window monitor
(docs/QUALIFICATION.md 9.13).

Strategy now, in order:

1. Resolve the channel's executable path (Playwright's own bundled Chromium
   via ``BrowserType.executable_path``; well-known per-OS install locations
   for the ``"chrome"`` channel, overridable by
   ``PAGENTOS_BROWSER_CHROME_PATH``).
2. Read the version from the executable's own metadata: the PE VERSIONINFO
   resource on Windows (``version.dll``, no process created); on other
   platforms ``<executable> --version`` genuinely prints and exits.
3. If the version cannot be read the browser is still ``available``
   (the executable exists); the version is ``None``. There is no launch
   fallback of any kind.
4. If no executable is found at all, ``available=False`` (worker
   ``--self-check`` exits non-zero in this case).
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import async_playwright

_VERSION_SUBPROCESS_TIMEOUT_S = 5.0
IS_WINDOWS = sys.platform == "win32"

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


def file_version_windows(executable: str) -> str | None:
    """``a.b.c.d`` from the PE VERSIONINFO resource (``version.dll``); no process.

    Returns ``None`` when the file carries no version resource or the API
    fails. Never raises.
    """
    if not IS_WINDOWS:  # pragma: no cover - Windows only
        return None
    try:
        version_dll = ctypes.WinDLL("version.dll")  # type: ignore[attr-defined]
        size = version_dll.GetFileVersionInfoSizeW(ctypes.c_wchar_p(executable), None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(ctypes.c_wchar_p(executable), 0, size, buffer):
            return None
        fixed_ptr = ctypes.c_void_p()
        fixed_len = ctypes.c_uint()
        if not version_dll.VerQueryValueW(
            buffer, ctypes.c_wchar_p("\\"), ctypes.byref(fixed_ptr), ctypes.byref(fixed_len)
        ):
            return None
        if not fixed_ptr.value or fixed_len.value < 20:
            return None
        # VS_FIXEDFILEINFO: dwSignature, dwStrucVersion, dwFileVersionMS, dwFileVersionLS, ...
        raw = ctypes.string_at(fixed_ptr.value, 16)
        ms = int.from_bytes(raw[8:12], "little")
        ls = int.from_bytes(raw[12:16], "little")
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:  # noqa: BLE001 - detection must never fail the worker
        return None


async def _version_via_subprocess_posix(executable: str) -> str | None:
    """``<executable> --version`` -- POSIX only, where Chrome/Chromium print
    the version and exit without starting the browser. NEVER used on
    Windows (see module docstring)."""
    if IS_WINDOWS:
        raise RuntimeError("browser executables must never be run for detection on Windows")
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
            await asyncio.sleep(0)
    except (OSError, TimeoutError):
        return None
    match = _VERSION_RE.search(stdout.decode("utf-8", errors="replace"))
    return match.group(1) if match else None


async def read_executable_version(executable: str) -> str | None:
    """Version of a browser executable without starting it."""
    if IS_WINDOWS:
        return file_version_windows(executable)
    return await _version_via_subprocess_posix(executable)


async def detect_browser(
    channel: str | None, *, allow_launch_fallback: bool = False
) -> BrowserInfo:
    """Detect availability/version for ``channel`` ("chrome"/"chromium"/None).

    ``None``/``"chromium"`` resolve Playwright's own bundled build (no system
    install needed — the CI-friendly path); ``"chrome"`` resolves the
    installed Google Chrome. ``allow_launch_fallback`` is accepted for
    call-site compatibility and ignored: detection never launches a browser.
    """
    del allow_launch_fallback
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

    version = await read_executable_version(executable)
    return BrowserInfo(
        effective_channel, available=True, version=version, executable_path=executable
    )


def which_chromedriver_hint() -> str | None:  # pragma: no cover - diagnostic helper
    """Best-effort ``shutil.which`` fallback, used only for diagnostics/logs."""
    return shutil.which("chrome") or shutil.which("google-chrome")


__all__ = ["BrowserInfo", "detect_browser", "file_version_windows", "read_executable_version"]
