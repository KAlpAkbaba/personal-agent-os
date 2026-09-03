"""Regression: browser detection must never start a browser process.

Owner-machine incident 2026-09-03: ``chrome.exe --version`` on Windows
starts the full browser (a visible window that outlives the caller), so the
old detection opened one window per worker start / self-check / test
fixture. These tests pin the fix: on Windows the version comes from the PE
version resource and no subprocess of any kind is created; a launch
fallback no longer exists.
"""

from __future__ import annotations

import asyncio
import pathlib
import subprocess
import sys

import pytest

from browser_agent import detect


class _NoSubprocess(Exception):
    pass


_BROWSER_BINARIES = ("chrome.exe", "chrome-headless-shell.exe", "chrome", "chromium", "msedge.exe")


def _names_a_browser(args) -> bool:
    flat = []
    for arg in args:
        if isinstance(arg, (list, tuple)):
            flat.extend(str(a) for a in arg)
        else:
            flat.append(str(arg))
    return any(a.lower().replace("\\", "/").rsplit("/", 1)[-1] in _BROWSER_BINARIES for a in flat)


@pytest.fixture()
def forbid_process_creation(monkeypatch):
    """Playwright's own driver (node) may start; any BROWSER executable may not."""
    original_exec = asyncio.create_subprocess_exec
    original_popen = subprocess.Popen

    async def guarded_exec(*args, **kwargs):
        if _names_a_browser(args):
            raise _NoSubprocess(f"detection tried to run a browser: {args!r}")
        return await original_exec(*args, **kwargs)

    def guarded_popen(*args, **kwargs):
        if _names_a_browser(args):
            raise _NoSubprocess(f"detection tried to run a browser: {args!r}")
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", guarded_exec)
    monkeypatch.setattr(detect.asyncio, "create_subprocess_exec", guarded_exec)
    monkeypatch.setattr(subprocess, "Popen", guarded_popen)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PE version resource")
async def test_detect_chromium_reads_version_without_any_process(forbid_process_creation) -> None:
    info = await detect.detect_browser("chromium")
    assert info.available is True
    assert info.executable_path and info.executable_path.lower().endswith("chrome.exe")
    assert info.version is not None and detect._VERSION_RE.fullmatch(info.version)
    assert info.version == detect.file_version_windows(info.executable_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PE version resource")
async def test_detect_chrome_channel_never_runs_the_executable(
    forbid_process_creation, monkeypatch
) -> None:
    executable = detect._resolve_chrome_executable()
    if executable is None:
        pytest.skip("Google Chrome not installed on this machine")
    info = await detect.detect_browser("chrome")
    assert info.available is True
    assert info.executable_path == executable
    assert info.version == detect.file_version_windows(executable)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows guard")
async def test_posix_subprocess_path_is_refused_on_windows() -> None:
    with pytest.raises(RuntimeError):
        await detect._version_via_subprocess_posix(r"C:\nowhere\chrome.exe")


async def test_missing_executable_reports_unavailable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PAGENTOS_BROWSER_CHROME_PATH", str(tmp_path / "missing.exe"))
    info = await detect.detect_browser("chrome")
    assert info.available is False
    assert info.version is None


def test_file_version_of_a_non_pe_file_is_none(tmp_path) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows")
    target = tmp_path / "plain.txt"
    target.write_text("not a PE file", encoding="utf-8")
    assert detect.file_version_windows(str(target)) is None
    assert detect.file_version_windows(str(tmp_path / "absent.exe")) is None


def test_no_launch_path_exists_in_detection_source() -> None:
    # the keyword survives for call-site compatibility only; there is no launch path left
    source = pathlib.Path(detect.__file__).read_text(encoding="utf-8")
    assert ".launch(" not in source
    assert "launch_persistent_context" not in source
