"""E2E: semantic interaction against the local fixture site (real Chromium)."""

import hashlib

import pytest

from browser_agent import BrowserError, BrowserSession, ErrorClass, TargetSpec

from ..conftest import SAMPLE_FILE

pytestmark = pytest.mark.browser


async def test_navigation(session: BrowserSession, site_url: str) -> None:
    await session.navigate(f"{site_url}/index.html")
    assert session.url == f"{site_url}/index.html"
    heading = await session.read_text(TargetSpec(role="heading", name="Personal Agent OS Fixture"))
    assert heading == "Personal Agent OS Fixture"


async def test_click_by_role_and_name(session: BrowserSession, site_url: str) -> None:
    await session.navigate(f"{site_url}/index.html")
    await session.click(TargetSpec(role="button", name="Greet"))
    greeting = await session.read_text(TargetSpec(test_id="greeting"))
    assert greeting == "Hello, Agent!"


async def test_fill_by_label_and_placeholder(session: BrowserSession, site_url: str) -> None:
    await session.navigate(f"{site_url}/index.html")
    await session.fill(TargetSpec(label="Your name"), "Alp")
    assert await session.read_text(TargetSpec(test_id="name-echo")) == "Alp"
    await session.fill(TargetSpec(placeholder="Search query"), "agents")
    assert await session.read_text(TargetSpec(test_id="query-echo")) == "agents"


async def test_navigate_via_link_click(session: BrowserSession, site_url: str) -> None:
    await session.navigate(f"{site_url}/index.html")
    await session.click(TargetSpec(role="link", name="Second page"))
    heading = await session.read_text(TargetSpec(role="heading", name="Second Page"))
    assert heading == "Second Page"
    assert session.url.endswith("/second.html")


async def test_find_returns_element_info(session: BrowserSession, site_url: str) -> None:
    await session.navigate(f"{site_url}/index.html")
    info = await session.find(TargetSpec(text="All systems nominal"))
    assert info.match_count == 1
    assert info.text == "All systems nominal"


async def test_accessibility_snapshot_contains_roles_and_names(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/index.html")
    snapshot = await session.accessibility_snapshot()
    assert 'heading "Personal Agent OS Fixture"' in snapshot
    assert 'button "Greet"' in snapshot
    assert 'link "Second page"' in snapshot
    assert 'link "Download sample"' in snapshot


async def test_download_saves_file_with_matching_sha256(site_url: str, tmp_path) -> None:
    # B31 req 180: a named save_dir must lie inside the session's file_io_root, so this
    # session's root is the folder the download is saved to.
    root_session = await BrowserSession.launch_dedicated(headless=True, file_io_root=tmp_path)
    try:
        await root_session.navigate(f"{site_url}/index.html")
        result = await root_session.download(
            TargetSpec(role="link", name="Download sample"), save_dir=tmp_path / "downloads"
        )
    finally:
        await root_session.close()
    assert result.path.exists()
    assert result.path.resolve().is_relative_to(tmp_path.resolve())
    assert result.suggested_filename == "sample.txt"
    expected = hashlib.sha256(SAMPLE_FILE.read_bytes()).hexdigest()
    assert result.sha256 == expected


async def test_download_refuses_a_save_dir_outside_the_file_io_root(
    session: BrowserSession, site_url: str, tmp_path
) -> None:
    # The fixture session's root is the fixture site; tmp_path is elsewhere on the machine.
    await session.navigate(f"{site_url}/index.html")
    with pytest.raises(BrowserError) as refused:
        await session.download(TargetSpec(role="link", name="Download sample"), save_dir=tmp_path)
    assert refused.value.error_class == ErrorClass.VALIDATION_ERROR
    assert not any(tmp_path.iterdir())


async def test_screenshot_returns_png_bytes(session: BrowserSession, site_url: str) -> None:
    await session.navigate(f"{site_url}/index.html")
    image = await session.screenshot()
    assert image[:8] == b"\x89PNG\r\n\x1a\n"


async def test_missing_target_raises_typed_ui_target_not_found(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/index.html")
    with pytest.raises(BrowserError) as excinfo:
        await session.click(TargetSpec(role="button", name="No Such Button"), timeout_ms=800)
    assert excinfo.value.error_class is ErrorClass.UI_TARGET_NOT_FOUND
    assert excinfo.value.retryable is False
    assert excinfo.value.evidence["target"] == {"role": "button", "name": "No Such Button"}


# One deliberately-marked last-resort fallback check. The escape hatch is NOT
# part of the semantic API and no other test may use it (CLAUDE.md browser
# rule: raw coordinates only as last resort).
async def test_escape_hatch_click_xy_last_resort_fallback(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/index.html")
    await session.escape_hatch_click_xy(5, 5)
    echoed = await session.read_text(TargetSpec(test_id="click-echo"))
    assert echoed == "clicked-somewhere"
