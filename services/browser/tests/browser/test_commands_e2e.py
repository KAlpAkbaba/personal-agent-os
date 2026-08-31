"""E2E: browser-command layer against a real browser session.

The fixture page's Increment counter makes exactly-once execution observable:
if a duplicate command re-executed, the counter would advance past 1.
"""

import asyncio

import pytest

from browser_agent import (
    BrowserCommandExecutor,
    BrowserError,
    BrowserSession,
    CancelToken,
    ErrorClass,
    TargetSpec,
)

pytestmark = pytest.mark.browser


async def test_cancel_in_flight_browser_command_is_typed_cancelled(
    session: BrowserSession, site_url: str
) -> None:
    executor = BrowserCommandExecutor()
    token = CancelToken()

    async def slow_navigation() -> None:
        await session.navigate(f"{site_url}/slow", timeout_ms=20_000)

    async def cancel_soon() -> None:
        await asyncio.sleep(0.3)
        token.cancel()

    canceller = asyncio.ensure_future(cancel_soon())
    with pytest.raises(BrowserError) as excinfo:
        await executor.execute("cmd-nav", "key-nav", slow_navigation, cancel_token=token)
    await canceller
    err = excinfo.value
    assert err.error_class is ErrorClass.CANCELLED
    assert err.retryable is False


async def test_duplicate_browser_command_executes_exactly_once(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/index.html")
    executor = BrowserCommandExecutor()

    async def increment() -> str:
        await session.click(TargetSpec(role="button", name="Increment"))
        return await session.read_text(TargetSpec(test_id="count-echo"))

    first = await executor.execute("cmd-1", "increment-once", increment)
    second = await executor.execute("cmd-2", "increment-once", increment)  # duplicate
    assert first == second == "1"
    assert await session.read_text(TargetSpec(test_id="count-echo")) == "1"


async def test_concurrent_duplicate_browser_command_executes_exactly_once(
    session: BrowserSession, site_url: str
) -> None:
    await session.navigate(f"{site_url}/index.html")
    executor = BrowserCommandExecutor()

    async def increment() -> str:
        await session.click(TargetSpec(role="button", name="Increment"))
        return await session.read_text(TargetSpec(test_id="count-echo"))

    results = await asyncio.gather(
        executor.execute("cmd-1", "increment-race", increment),
        executor.execute("cmd-2", "increment-race", increment),
    )
    assert results == ["1", "1"]
    assert await session.read_text(TargetSpec(test_id="count-echo")) == "1"
