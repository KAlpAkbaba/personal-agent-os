"""M13 multi-source gather orchestration: partial failure never aborts the batch."""

import pytest

from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.research import FetchTarget, gather_evidence


class _SometimesFailingDriver:
    """Fails navigate() for URLs containing 'bad', succeeds otherwise."""

    def __init__(self) -> None:
        self.navigated_to: list[str] = []

    async def navigate(self, url: str, *, timeout_ms: float = 15_000) -> None:
        self.navigated_to.append(url)
        if "bad" in url:
            raise BrowserError(ErrorClass.TIMEOUT, f"navigate timed out: {url}", retryable=True)

    async def title(self) -> str:
        return "Title"

    async def page_text(self, *, timeout_ms: float = 5_000) -> str:
        return "some page content about the topic"

    async def accessibility_snapshot(self, *, timeout_ms: float = 5_000) -> str:
        return "- text: fallback"


@pytest.mark.asyncio
async def test_gather_evidence_collects_partial_success() -> None:
    driver = _SometimesFailingDriver()
    targets = [
        FetchTarget(url="https://example.com/good1", source_class="news", query="q"),
        FetchTarget(url="https://example.com/bad1", source_class="news", query="q"),
        FetchTarget(url="https://example.com/good2", source_class="official", query="q2"),
    ]
    result = await gather_evidence(driver, targets)
    assert result.success_count == 2
    assert result.failure_count == 1
    assert {e.url for e in result.evidence} == {
        "https://example.com/good1",
        "https://example.com/good2",
    }
    failure = result.failures[0]
    assert failure.url == "https://example.com/bad1"
    assert failure.error_class == "timeout"


@pytest.mark.asyncio
async def test_gather_evidence_empty_targets_is_empty_result() -> None:
    result = await gather_evidence(_SometimesFailingDriver(), [])
    assert result.success_count == 0
    assert result.failure_count == 0


@pytest.mark.asyncio
async def test_gather_evidence_preserves_target_order_in_navigation() -> None:
    driver = _SometimesFailingDriver()
    targets = [
        FetchTarget(url="https://example.com/1"),
        FetchTarget(url="https://example.com/2"),
        FetchTarget(url="https://example.com/3"),
    ]
    await gather_evidence(driver, targets)
    assert driver.navigated_to == [
        "https://example.com/1",
        "https://example.com/2",
        "https://example.com/3",
    ]


@pytest.mark.asyncio
async def test_gather_evidence_all_failures_returns_no_evidence() -> None:
    driver = _SometimesFailingDriver()
    targets = [FetchTarget(url="https://example.com/badonly")]
    result = await gather_evidence(driver, targets)
    assert result.success_count == 0
    assert result.failure_count == 1
