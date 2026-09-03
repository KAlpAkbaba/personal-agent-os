"""Windows process-level guards for the one-owned-research-browser invariant.

Incident (owner machine, 2026-09-03): ``launch_persistent_context`` on the
dedicated PagentOS research profile while another Chrome process already
holds that profile fails almost instantly (a ``TargetClosedError``-shaped
error, because Chrome's own single-instance ("ProcessSingleton") mechanism
hands the request off to the existing process and the freshly spawned one
exits) **and** silently opens a new window in that *existing* Chrome. With an
orphaned worker-launched Chrome (a worker killed by the installer/companion
cannot close its own browser) plus any retry source, this cascades into dozens
of windows.

Required runtime invariant (browser_agent.worker, contract-adjacent but
worker-internal): one research job = one browser worker + one Chrome
process/profile. This module supplies the two mechanical primitives that
invariant is built on:

- :func:`find_profile_chrome_pids` / :func:`reap_orphan_chrome` — find and
  terminate every **main** ``chrome.exe`` process (no ``--type=`` in its
  command line — that flag marks a renderer/gpu/utility *child* process,
  never the browser process itself) whose command line names a specific
  ``--user-data-dir``. Never touches a process that does not carry that exact
  profile directory.
- :func:`is_locked_profile_error` — recognizes the marker strings a
  Playwright ``launch_persistent_context`` call raises when the profile is
  still locked by another live Chrome process, so the caller can map that
  failure onto ``ErrorClass.BROWSER_LIFECYCLE_VIOLATION`` (non-retryable)
  instead of retrying (which is exactly the cascade above).

Every OS-facing function accepts its process-listing/kill/sleep primitives as
injectable keyword arguments (``scan=``, ``kill=``, ``sleep=``, ``now=``) so
the pure parsing/arithmetic can be unit-tested with fakes, never a real
PowerShell/taskkill subprocess (``tests/unit/test_lifecycle.py``); the
Windows-real path is exercised by the browser e2e suite
(``tests/browser/test_lifecycle_e2e.py``) with real Chrome processes.
"""

from __future__ import annotations

import ctypes
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .obs_logging import get_logger

logger = get_logger(__name__)

# Absolute paths per the task brief -- PATH is unreliable in spawned shells on
# the owner's machine, so every OS tool is invoked by its well-known absolute
# location rather than relying on process PATH resolution.
POWERSHELL_EXE = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
TASKKILL_EXE = r"C:\Windows\System32\taskkill.exe"

_PS_SCAN_TIMEOUT_S = 15.0
_TASKKILL_TIMEOUT_S = 10.0
DEFAULT_WAIT_TIMEOUT_S = 10.0
_WAIT_POLL_S = 0.2

# ctypes/Win32 constants for a lightweight liveness check (no subprocess per
# poll iteration): PROCESS_QUERY_LIMITED_INFORMATION is the minimal access
# right needed to read a process's exit code.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259

# Markers Playwright's launch_persistent_context raises when the target
# profile is still held by another live Chrome process -- the same
# "connection/liveness" vocabulary as errors.py's _CONNECTION_MARKERS,
# recognized here as a *lifecycle* condition (profile lock), not a generic
# dependency-unavailable one, only at this one call site.
_LOCKED_PROFILE_MARKERS: tuple[str, ...] = (
    "target closed",
    "browser has been closed",
    "target page, context or browser has been closed",
)

_USER_DATA_DIR_RE = re.compile(r'--user-data-dir=("([^"]*)"|(\S+))', re.IGNORECASE)


# --------------------------------------------------------------------------- #
# pure helpers (unit-testable with fakes)
# --------------------------------------------------------------------------- #


def _normalize_path_token(value: str) -> str:
    """Lowercase, unquoted, no trailing separator -- profile-dir comparison key."""
    return value.strip().strip('"').strip("'").rstrip("\\/").lower()


def parse_chrome_pid_scan(raw: str, *, profile_dir: Path | str) -> list[int]:
    """Pure parse: a PowerShell ``ConvertTo-Json`` process-list -> main
    ``chrome.exe`` pids whose command line names ``profile_dir`` as
    ``--user-data-dir``.

    ``raw`` is the stdout of ``Get-CimInstance Win32_Process -Filter
    "Name='chrome.exe'" | Select-Object ProcessId, CommandLine | ConvertTo-Json``
    (a JSON object for exactly one match, a JSON array otherwise, or empty
    text for none -- PowerShell's own ``ConvertTo-Json`` behavior). Malformed
    input never raises; it is treated as "no processes found" and logged.

    A *main* process is one with no ``--type=`` flag (every renderer/gpu/
    utility/zygote child process carries one; only the top-level browser
    process omits it). Matching is on the normalized ``--user-data-dir``
    *value*, never a raw substring of the whole command line, so a profile
    whose path happens to be a textual substring of a sibling profile's path
    is never confused with it.
    """
    text = (raw or "").strip()
    if not text:
        return []
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("browser.pid_scan_parse_error", raw_preview=text[:200])
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    target = _normalize_path_token(str(profile_dir))
    pids: list[int] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        cmdline = item.get("CommandLine")
        pid = item.get("ProcessId")
        if not isinstance(cmdline, str) or not isinstance(pid, int):
            continue
        if "--type=" in cmdline:
            continue  # a renderer/gpu/utility child process, never the owner
        match = _USER_DATA_DIR_RE.search(cmdline)
        if not match:
            continue
        value = match.group(2) if match.group(2) is not None else match.group(3)
        if _normalize_path_token(value or "") != target:
            continue
        pids.append(pid)
    return pids


def is_locked_profile_error(exc: BaseException) -> bool:
    """Whether ``exc`` (from ``launch_persistent_context``) is shaped like
    Chrome's single-instance hand-off: the freshly spawned process exits
    almost immediately because another live process already owns the
    profile. Pure string classification -- see module docstring."""
    message = getattr(exc, "message", None) or str(exc)
    lowered = message.lower()
    return any(marker in lowered for marker in _LOCKED_PROFILE_MARKERS)


def check_tab_budget(tab_count: int, max_tabs: int, *, op: str) -> None:
    """Raise ``BrowserError(BROWSER_LIFECYCLE_VIOLATION)`` when ``tab_count``
    is already at or over ``max_tabs`` -- the pre-flight half of the tab
    budget guard (``tab_new``/``fetch_evidence`` refuse *before* opening
    anything; the count is therefore unchanged by a refused call). A separate
    post-hoc check in the worker dispatch loop catches anything that slipped
    past this (an unclosed popup racing a close) using the same class."""
    # Imported lazily to avoid a module-load cycle: errors.py has no
    # dependency on this module, but keeping the import local here makes the
    # dependency direction explicit (lifecycle -> errors, never the reverse).
    from .errors import BrowserError, ErrorClass

    if tab_count + 1 > max_tabs:
        raise BrowserError(
            ErrorClass.BROWSER_LIFECYCLE_VIOLATION,
            f"{op}: opening a new tab would raise the open-tab count to "
            f"{tab_count + 1}, above max_tabs={max_tabs}",
            retryable=False,
            evidence={"op": op, "tab_count": tab_count, "max_tabs": max_tabs},
        )


def check_tab_count_within_budget(tab_count: int, max_tabs: int, *, op: str) -> None:
    """Backstop check: any op discovering the *current* tab count already
    above budget (e.g. a popup that evaded the auto-close guard) refuses."""
    from .errors import BrowserError, ErrorClass

    if tab_count > max_tabs:
        raise BrowserError(
            ErrorClass.BROWSER_LIFECYCLE_VIOLATION,
            f"{op}: open tab count {tab_count} exceeds max_tabs={max_tabs}",
            retryable=False,
            evidence={"op": op, "tab_count": tab_count, "max_tabs": max_tabs},
        )


# --------------------------------------------------------------------------- #
# OS-facing primitives (real implementations; every caller can inject fakes)
# --------------------------------------------------------------------------- #


def _run_powershell_scan() -> str:
    """Query every ``chrome.exe`` process's pid + full command line (impure)."""
    if sys.platform != "win32":  # pragma: no cover - exercised only on Windows
        return ""
    completed = subprocess.run(
        [
            POWERSHELL_EXE,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress",
        ],
        capture_output=True,
        text=True,
        timeout=_PS_SCAN_TIMEOUT_S,
        check=False,
    )
    return completed.stdout


def find_profile_chrome_pids(
    profile_dir: Path | str,
    *,
    scan: Callable[[], str] = _run_powershell_scan,
) -> list[int]:
    """Main ``chrome.exe`` pids currently holding ``profile_dir`` (any owner)."""
    return parse_chrome_pid_scan(scan(), profile_dir=profile_dir)


def terminate_pid(pid: int) -> None:
    """``taskkill /PID <pid> /T /F`` via the absolute path (best effort)."""
    if sys.platform != "win32":  # pragma: no cover - exercised only on Windows
        return
    subprocess.run(
        [TASKKILL_EXE, "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        timeout=_TASKKILL_TIMEOUT_S,
        check=False,
    )


def _pid_alive_ctypes(pid: int) -> bool:
    if sys.platform != "win32":  # pragma: no cover - exercised only on Windows
        return False
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def wait_for_pids_exit(
    pids: list[int],
    *,
    timeout_s: float = DEFAULT_WAIT_TIMEOUT_S,
    poll_s: float = _WAIT_POLL_S,
    is_alive: Callable[[int], bool] = _pid_alive_ctypes,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> list[int]:
    """Wait (bounded) for every pid in ``pids`` to exit.

    Returns the pids still alive when ``timeout_s`` elapsed (empty when all
    exited in time). Checks before sleeping so a call with everything already
    dead returns immediately without ever sleeping.
    """
    remaining = set(pids)
    deadline = now() + timeout_s
    while True:
        remaining = {pid for pid in remaining if is_alive(pid)}
        if not remaining or now() >= deadline:
            return sorted(remaining)
        sleep(poll_s)


def wait_for_profile_clear(
    profile_dir: Path | str,
    *,
    timeout_s: float = DEFAULT_WAIT_TIMEOUT_S,
    poll_s: float = _WAIT_POLL_S,
    scan: Callable[[], str] = _run_powershell_scan,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> bool:
    """Wait (bounded) until no ``chrome.exe`` main process holds ``profile_dir``.

    Used by ``session_close``/idle-reap/shutdown to answer
    ``browser_pid_exited`` truthfully rather than assuming a Playwright-level
    ``close()`` actually tore down the OS process.
    """
    deadline = now() + timeout_s
    while True:
        pids = parse_chrome_pid_scan(scan(), profile_dir=profile_dir)
        if not pids or now() >= deadline:
            return not pids
        sleep(poll_s)


def reap_orphan_chrome(
    profile_dir: Path | str,
    *,
    owned_pid: int | None = None,
    scan: Callable[[], str] = _run_powershell_scan,
    kill: Callable[[int], None] = terminate_pid,
    wait: Callable[..., list[int]] = wait_for_pids_exit,
    timeout_s: float = DEFAULT_WAIT_TIMEOUT_S,
) -> list[int]:
    """Find and terminate every main ``chrome.exe`` process on ``profile_dir``
    other than ``owned_pid``, waiting (bounded) for exit.

    Returns the pids that were successfully reaped (exited within
    ``timeout_s``) -- the caller logs this as ``browser.orphan_reaped``. Never
    touches a process whose command line does not carry
    ``--user-data-dir=<profile_dir>`` exactly (see
    :func:`parse_chrome_pid_scan`).
    """
    pids = [pid for pid in find_profile_chrome_pids(profile_dir, scan=scan) if pid != owned_pid]
    if not pids:
        return []
    for pid in pids:
        kill(pid)
    still_alive = set(wait(pids, timeout_s=timeout_s))
    return [pid for pid in pids if pid not in still_alive]


__all__ = [
    "DEFAULT_WAIT_TIMEOUT_S",
    "POWERSHELL_EXE",
    "TASKKILL_EXE",
    "check_tab_budget",
    "check_tab_count_within_budget",
    "find_profile_chrome_pids",
    "is_locked_profile_error",
    "parse_chrome_pid_scan",
    "reap_orphan_chrome",
    "terminate_pid",
    "wait_for_pids_exit",
    "wait_for_profile_clear",
]
