"""E2E: backend seam — persistent profiles, crash/reconnect, enrollment attach.

Crash injection: the browser process is terminated via its PID (obtained over
a diagnostic CDP session), which deterministically simulates a browser crash.
All browsers involved are throwaway; the owner's real browser is never
touched.
"""

import asyncio
import os
import signal
import socket
from pathlib import Path

import pytest
from playwright.async_api import Browser, async_playwright

from browser_agent import (
    BrowserEnrollment,
    BrowserError,
    BrowserSession,
    EnrollmentRegistry,
    ErrorClass,
    ExistingSessionBackend,
    ManagedBackend,
    TargetSpec,
)

pytestmark = pytest.mark.browser


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _browser_pid(browser: Browser) -> int:
    cdp = await browser.new_browser_cdp_session()
    info = await cdp.send("SystemInfo.getProcessInfo")
    for process in info["processInfo"]:
        if process["type"] == "browser":
            return process["id"]
    raise AssertionError("browser process not found in SystemInfo.getProcessInfo")


async def _wait_until_dead(backend, *, timeout_s: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if not await backend.is_alive():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("backend never observed the browser as dead")


# --------------------------------------------------------------------------- #
# persistent dedicated profile
# --------------------------------------------------------------------------- #


async def test_persistent_profile_preserves_state_across_relaunch(
    site_url: str, tmp_path: Path
) -> None:
    profile_dir = tmp_path / "agent-profile"

    first = await BrowserSession.launch_dedicated(headless=True, profile_dir=profile_dir)
    try:
        assert first.backend.capabilities().authenticated_session is True
        await first.navigate(f"{site_url}/storage.html")
        assert await first.read_text(TargetSpec(test_id="stored-value")) == "empty"
        await first.click(TargetSpec(role="button", name="Save value"))
        assert await first.read_text(TargetSpec(test_id="stored-value")) == "persisted-42"
    finally:
        await first.close()

    second = await BrowserSession.launch_dedicated(headless=True, profile_dir=profile_dir)
    try:
        await second.navigate(f"{site_url}/storage.html")
        assert await second.read_text(TargetSpec(test_id="stored-value")) == "persisted-42"
    finally:
        await second.close()


async def test_isolated_mode_shares_no_state_between_sessions(site_url: str) -> None:
    first = await BrowserSession.launch_dedicated(headless=True)
    try:
        assert first.backend.capabilities().authenticated_session is False
        await first.navigate(f"{site_url}/storage.html")
        await first.click(TargetSpec(role="button", name="Save value"))
    finally:
        await first.close()

    second = await BrowserSession.launch_dedicated(headless=True)
    try:
        await second.navigate(f"{site_url}/storage.html")
        assert await second.read_text(TargetSpec(test_id="stored-value")) == "empty"
    finally:
        await second.close()


# --------------------------------------------------------------------------- #
# crash / reconnect: managed relaunch
# --------------------------------------------------------------------------- #


async def test_managed_browser_crash_is_typed_and_reconnect_restores(
    site_url: str,
) -> None:
    backend = ManagedBackend(headless=True)
    await backend.connect()
    session = BrowserSession(backend)
    try:
        await session.navigate(f"{site_url}/index.html")
        assert backend.native_browser is not None
        pid = await _browser_pid(backend.native_browser)

        os.kill(pid, signal.SIGTERM)  # hard-terminate the browser process
        await _wait_until_dead(backend)

        with pytest.raises(BrowserError) as excinfo:
            await session.navigate(f"{site_url}/index.html")
        err = excinfo.value
        assert err.error_class is ErrorClass.DEPENDENCY_UNAVAILABLE
        assert err.retryable is True

        await backend.reconnect()
        assert await backend.is_alive()
        await session.navigate(f"{site_url}/index.html")
        heading = await session.read_text(
            TargetSpec(role="heading", name="Personal Agent OS Fixture")
        )
        assert heading == "Personal Agent OS Fixture"
    finally:
        await session.close()


# --------------------------------------------------------------------------- #
# crash / reconnect: CDP reattach to a freshly started throwaway browser
# --------------------------------------------------------------------------- #


async def test_cdp_disconnect_is_typed_and_reattach_to_fresh_browser(
    site_url: str,
) -> None:
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}"
    playwright = await async_playwright().start()
    replacement: Browser | None = None
    external = await playwright.chromium.launch(
        headless=True, args=[f"--remote-debugging-port={port}"]
    )
    backend = ExistingSessionBackend(
        BrowserEnrollment.cdp_loopback(endpoint, name="throwaway")
    )
    await backend.connect()
    session = BrowserSession(backend)
    try:
        await session.navigate(f"{site_url}/index.html")

        await external.close()  # the external browser goes away mid-session
        await _wait_until_dead(backend)

        with pytest.raises(BrowserError) as excinfo:
            await session.navigate(f"{site_url}/index.html")
        assert excinfo.value.error_class is ErrorClass.DEPENDENCY_UNAVAILABLE
        assert excinfo.value.retryable is True

        # A fresh throwaway browser appears on the SAME enrolled endpoint...
        replacement = await playwright.chromium.launch(
            headless=True, args=[f"--remote-debugging-port={port}"]
        )
        # ...and reconnect() reattaches through the unchanged enrollment.
        await backend.reconnect()
        assert await backend.is_alive()
        await session.navigate(f"{site_url}/index.html")
        await session.click(TargetSpec(role="button", name="Greet"))
        assert await session.read_text(TargetSpec(test_id="greeting")) == "Hello, Agent!"
    finally:
        await session.close()  # disconnect only
        if replacement is not None:
            await replacement.close()
        await playwright.stop()


# --------------------------------------------------------------------------- #
# enrollment-authorized attach + capability grants
# --------------------------------------------------------------------------- #


async def test_enrollment_registry_attach_and_capability_grant_enforced(
    site_url: str, tmp_path: Path
) -> None:
    port = _free_port()
    playwright = await async_playwright().start()
    external = await playwright.chromium.launch(
        headless=True, args=[f"--remote-debugging-port={port}"]
    )
    try:
        registry = EnrollmentRegistry(tmp_path / "enrollments.json")
        registry.register(
            BrowserEnrollment.cdp_loopback(
                f"http://127.0.0.1:{port}",
                name="throwaway-owner-browser",
                enrollment_id="enr-1",
                capability_overrides={"downloads": False},
            )
        )

        backend = ExistingSessionBackend(registry.get("enr-1"))
        await backend.connect()
        session = BrowserSession(backend)
        try:
            await session.navigate(f"{site_url}/index.html")
            await session.click(TargetSpec(role="button", name="Greet"))
            assert await session.read_text(TargetSpec(test_id="greeting")) == "Hello, Agent!"

            # The enrollment did not grant downloads: the guard fires before
            # any browser interaction happens.
            with pytest.raises(BrowserError) as excinfo:
                await session.download(TargetSpec(role="link", name="Download sample"))
            assert excinfo.value.error_class is ErrorClass.CAPABILITY_MISSING
        finally:
            await session.close()
    finally:
        await external.close()
        await playwright.stop()
