"""E2E scenario matrix against the fixture site (ACCEPTANCE_TESTS M2).

Covers the deterministic scenarios not already exercised by
test_semantic_e2e.py / test_mutating_dom.py: history back/forward, tabs,
select/checkbox/radio, form submission, SPA navigation, iframe interaction,
popup windows, upload, ambiguous locators, and deterministic browser-side
error/timeout typing.
"""

import asyncio
import hashlib
import socket

import pytest

from browser_agent import BrowserError, BrowserSession, ErrorClass, TargetSpec

from ..conftest import SAMPLE_FILE

pytestmark = pytest.mark.browser


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------------- #
# history
# --------------------------------------------------------------------------- #


async def test_back_and_forward_across_real_navigations(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/index.html")
    await session.click(TargetSpec(role="link", name="Second page"))
    assert session.url.endswith("/second.html")

    back_url = await session.back()
    assert back_url.endswith("/index.html")
    heading = await session.read_text(TargetSpec(role="heading", name="Personal Agent OS Fixture"))
    assert heading == "Personal Agent OS Fixture"

    forward_url = await session.forward()
    assert forward_url.endswith("/second.html")
    assert await session.read_text(TargetSpec(role="heading", name="Second Page")) == "Second Page"


# --------------------------------------------------------------------------- #
# tabs
# --------------------------------------------------------------------------- #


async def test_new_select_and_close_tab(session: BrowserSession, site_url: str) -> None:
    await session.navigate(f"{site_url}/index.html")
    index = await session.new_tab(f"{site_url}/second.html")
    assert session.url.endswith("/second.html")  # new tab is selected

    tabs = await session.list_tabs()
    assert len(tabs) == 2
    assert tabs[index].is_current
    assert tabs[index].url.endswith("/second.html")
    assert tabs[index].title == "Second Page"

    await session.select_tab(0)
    assert session.url.endswith("/index.html")
    tabs = await session.list_tabs()
    assert tabs[0].is_current and not tabs[1].is_current

    await session.close_tab(1)
    tabs = await session.list_tabs()
    assert len(tabs) == 1
    assert session.url.endswith("/index.html")


async def test_closing_last_tab_is_refused_typed(session: BrowserSession, site_url: str) -> None:
    await session.navigate(f"{site_url}/index.html")
    with pytest.raises(BrowserError) as excinfo:
        await session.close_tab(0)
    assert excinfo.value.error_class is ErrorClass.VALIDATION_ERROR


# --------------------------------------------------------------------------- #
# form controls
# --------------------------------------------------------------------------- #


async def test_select_dropdown_by_value_and_label(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/form.html")
    selected = await session.select_option(TargetSpec(label="Favorite color"), value="green")
    assert selected == ["green"]
    assert await session.read_text(TargetSpec(test_id="color-echo")) == "green"

    selected = await session.select_option(TargetSpec(label="Favorite color"), label="Blue")
    assert selected == ["blue"]
    assert await session.read_text(TargetSpec(test_id="color-echo")) == "blue"


async def test_checkbox_set_checked_and_unchecked(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/form.html")
    await session.set_checked(TargetSpec(label="Subscribe to updates"), True)
    assert await session.read_text(TargetSpec(test_id="subscribe-echo")) == "on"
    await session.set_checked(TargetSpec(label="Subscribe to updates"), False)
    assert await session.read_text(TargetSpec(test_id="subscribe-echo")) == "off"


async def test_radio_selection_via_set_checked(session: BrowserSession, site_url: str) -> None:
    await session.navigate(f"{site_url}/form.html")
    await session.set_checked(TargetSpec(label="Pro plan"), True)
    assert await session.read_text(TargetSpec(test_id="plan-echo")) == "pro"
    await session.set_checked(TargetSpec(label="Basic plan"), True)
    assert await session.read_text(TargetSpec(test_id="plan-echo")) == "basic"


async def test_form_submission_result_page_echoes_submitted_values(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/form.html")
    await session.fill(TargetSpec(label="Full name"), "Alp Akbaba")
    await session.select_option(TargetSpec(label="Favorite color"), value="green")
    await session.set_checked(TargetSpec(label="Subscribe to updates"), True)
    await session.set_checked(TargetSpec(label="Pro plan"), True)
    await session.click(TargetSpec(role="button", name="Submit form"))

    assert "result.html" in session.url
    assert await session.read_text(TargetSpec(test_id="echo-fname")) == "Alp Akbaba"
    assert await session.read_text(TargetSpec(test_id="echo-color")) == "green"
    assert await session.read_text(TargetSpec(test_id="echo-subscribe")) == "yes"
    assert await session.read_text(TargetSpec(test_id="echo-plan")) == "pro"


# --------------------------------------------------------------------------- #
# SPA navigation
# --------------------------------------------------------------------------- #


async def test_spa_pushstate_navigation_without_full_load(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/spa.html")
    load_token = await session.read_text(TargetSpec(test_id="load-token"))
    assert await session.read_text(TargetSpec(test_id="spa-view")) == "home"

    await session.click(TargetSpec(role="button", name="Show section A"))
    assert await session.read_text(TargetSpec(test_id="spa-view")) == "Section A content"
    assert session.url.endswith("#/a")

    await session.click(TargetSpec(role="button", name="Show section B"))
    assert await session.read_text(TargetSpec(test_id="spa-view")) == "Section B content"

    # pushState history integrates with back/forward
    await session.back()
    assert await session.read_text(TargetSpec(test_id="spa-view")) == "Section A content"
    await session.forward()
    assert await session.read_text(TargetSpec(test_id="spa-view")) == "Section B content"

    # content swapped without a full page load: the per-load token is unchanged
    assert await session.read_text(TargetSpec(test_id="load-token")) == load_token


# --------------------------------------------------------------------------- #
# iframe
# --------------------------------------------------------------------------- #


async def test_iframe_click_and_read_inside_named_frame(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/iframe.html")
    await session.click(TargetSpec(role="button", name="Frame Greet"), frame="child")
    greeting = await session.read_text(TargetSpec(test_id="frame-greeting"), frame="child")
    assert greeting == "Hello from frame"
    # the same target is NOT on the host page
    with pytest.raises(BrowserError) as excinfo:
        await session.find(TargetSpec(role="button", name="Frame Greet"), timeout_ms=600)
    assert excinfo.value.error_class is ErrorClass.UI_TARGET_NOT_FOUND


# --------------------------------------------------------------------------- #
# popup / new window
# --------------------------------------------------------------------------- #


async def test_popup_window_appears_as_tab_and_is_interactable(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/popup.html")
    await session.click(TargetSpec(role="button", name="Open popup window"))

    # the popup joins the context's page list; wait deterministically (bounded)
    for _ in range(50):
        tabs = await session.list_tabs()
        if len(tabs) == 2:
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("popup window never appeared as a second tab")

    popup_index = next(t.index for t in tabs if t.url.endswith("popup-window.html"))
    await session.select_tab(popup_index)
    await session.click(TargetSpec(role="button", name="Popup Greet"))
    assert await session.read_text(TargetSpec(test_id="popup-greeting")) == "Hello from popup"

    await session.close_tab(popup_index)
    assert session.url.endswith("popup.html")


# --------------------------------------------------------------------------- #
# upload
# --------------------------------------------------------------------------- #


async def test_upload_fixture_file_server_echoes_matching_sha256(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/upload.html")
    await session.upload(TargetSpec(label="Choose file"), SAMPLE_FILE)
    await session.click(TargetSpec(role="button", name="Upload file"))
    echoed = await session.read_text(TargetSpec(test_id="upload-hash"))
    assert echoed == hashlib.sha256(SAMPLE_FILE.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# ambiguous locator
# --------------------------------------------------------------------------- #


async def test_ambiguous_locator_reports_match_count_and_clicks_first(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/index.html")
    info = await session.find(TargetSpec(role="button", name="Duplicate"))
    assert info.match_count == 2
    await session.click(TargetSpec(role="button", name="Duplicate"))
    assert await session.read_text(TargetSpec(test_id="dup-echo")) == "first-clicked"


# --------------------------------------------------------------------------- #
# typed browser-side errors
# --------------------------------------------------------------------------- #


async def test_navigate_to_dead_port_is_typed_dependency_unavailable(
    session: BrowserSession,
) -> None:
    # Chromium surfaces net::ERR_CONNECTION_REFUSED; the connection-marker rule
    # maps any net::ERR_* to dependency_unavailable (retryable). Documented in
    # README (scenario matrix) and the errors module mapping table.
    dead_url = f"http://127.0.0.1:{_free_port()}/index.html"
    with pytest.raises(BrowserError) as excinfo:
        await session.navigate(dead_url, timeout_ms=5000)
    err = excinfo.value
    assert err.error_class is ErrorClass.DEPENDENCY_UNAVAILABLE
    assert err.retryable is True
    assert err.evidence["url"] == dead_url


async def test_slow_navigation_is_typed_timeout_retryable(
    session: BrowserSession, site_url: str
) -> None:
    with pytest.raises(BrowserError) as excinfo:
        await session.navigate(f"{site_url}/slow", timeout_ms=800)
    err = excinfo.value
    assert err.error_class is ErrorClass.TIMEOUT
    assert err.retryable is True
    assert err.evidence["phase"] == "navigate"


async def test_upload_outside_file_io_root_is_rejected(
    session: BrowserSession, site_url: str, tmp_path
) -> None:
    # Regression for M2 security finding #4: upload sources must live under
    # the session's configured file_io_root.
    outside = tmp_path / "outside.txt"
    outside.write_text("nope")
    await session.navigate(f"{site_url}/upload.html")
    with pytest.raises(BrowserError) as exc_info:
        await session.upload(TargetSpec(label="Choose file"), outside)
    assert str(exc_info.value.error_class) == "validation_error"


async def test_file_and_javascript_urls_are_rejected(session: BrowserSession) -> None:
    # Regression for M2 security finding #2.
    for bad in ("file:///C:/Windows/win.ini", "javascript:alert(1)"):
        with pytest.raises(BrowserError) as exc_info:
            await session.navigate(bad)
        assert str(exc_info.value.error_class) == "validation_error"
        with pytest.raises(BrowserError):
            await session.new_tab(bad)
