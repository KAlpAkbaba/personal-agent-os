"""E2E: deterministic typed failures on the mutating fixture page.

The fixture (tests/fixtures/site/mutating.html) provides two deterministic
scenarios:

- "Vanishing Button" is removed from the DOM 300ms after load. Resolving it
  after that window deterministically yields ui_target_not_found.
- "Blocked Button" is covered by a pointer-intercepting overlay for the first
  1200ms after load. A click attempted inside that window resolves (the
  element is attached) but the action times out on Playwright's actionability
  check ("intercepts pointer events") -> deterministically ui_state_changed
  with retryable=True. Once the overlay's timer removes it, the same semantic
  target stabilizes, so with_retry recovers.
"""

import asyncio

import pytest

from browser_agent import BrowserError, BrowserSession, ErrorClass, TargetSpec, with_retry

pytestmark = pytest.mark.browser


async def test_vanished_target_is_ui_target_not_found(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/mutating.html")
    await asyncio.sleep(0.6)  # deterministically past the 300ms removal timer
    with pytest.raises(BrowserError) as excinfo:
        await session.click(TargetSpec(role="button", name="Vanishing Button"), timeout_ms=700)
    assert excinfo.value.error_class is ErrorClass.UI_TARGET_NOT_FOUND
    assert excinfo.value.retryable is False


async def test_blocked_target_is_ui_state_changed_and_retryable(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/mutating.html")
    # Inside the 1200ms overlay window: resolves, then the action is
    # intercepted -> ui_state_changed (see browser_agent.errors mapping table).
    with pytest.raises(BrowserError) as excinfo:
        await session.click(TargetSpec(role="button", name="Blocked Button"), timeout_ms=500)
    assert excinfo.value.error_class is ErrorClass.UI_STATE_CHANGED
    assert excinfo.value.retryable is True
    assert excinfo.value.evidence["phase"] == "act"


async def test_with_retry_succeeds_once_target_stabilizes(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/mutating.html")

    async def click_blocked() -> None:
        await session.click(TargetSpec(role="button", name="Blocked Button"), timeout_ms=600)

    # Attempt 1 fails (overlay intercepts, retryable), backoff waits, and a
    # later attempt lands after the overlay removes itself at 1200ms.
    await with_retry(click_blocked, attempts=4, base_delay=0.4)
    assert await session.read_text(TargetSpec(test_id="blocked-result")) == "clicked"
