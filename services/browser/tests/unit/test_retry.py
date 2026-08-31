"""Unit tests for the bounded retry helper (no browser needed)."""

import pytest

from browser_agent import BrowserError, ErrorClass, with_retry


def _retryable() -> BrowserError:
    return BrowserError(ErrorClass.UI_STATE_CHANGED, "state changed", retryable=True)


def _terminal() -> BrowserError:
    return BrowserError(ErrorClass.UI_TARGET_NOT_FOUND, "missing", retryable=False)


async def test_success_first_try_calls_op_once() -> None:
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert await with_retry(op, attempts=3, base_delay=0.01) == "ok"
    assert calls == 1


async def test_retryable_error_is_retried_until_success() -> None:
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _retryable()
        return "ok"

    assert await with_retry(op, attempts=3, base_delay=0.01) == "ok"
    assert calls == 3


async def test_terminal_error_is_not_retried() -> None:
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise _terminal()

    with pytest.raises(BrowserError) as excinfo:
        await with_retry(op, attempts=3, base_delay=0.01)
    assert calls == 1
    assert excinfo.value.error_class is ErrorClass.UI_TARGET_NOT_FOUND


async def test_attempt_cap_reraises_last_typed_error_with_retry_evidence() -> None:
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise _retryable()

    with pytest.raises(BrowserError) as excinfo:
        await with_retry(op, attempts=3, base_delay=0.01)
    assert calls == 3
    assert excinfo.value.error_class is ErrorClass.UI_STATE_CHANGED
    assert excinfo.value.evidence["retry"] == {"attempts": 3, "exhausted": True}


async def test_non_browser_error_propagates_without_retry() -> None:
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await with_retry(op, attempts=3, base_delay=0.01)
    assert calls == 1


async def test_attempts_must_be_positive() -> None:
    async def op() -> None:
        pass

    with pytest.raises(ValueError):
        await with_retry(op, attempts=0)
