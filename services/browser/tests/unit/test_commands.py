"""Browser-command layer semantics: idempotency + cancellation (no browser)."""

import asyncio

import pytest

from browser_agent import (
    BrowserCommandExecutor,
    BrowserError,
    CancelToken,
    ErrorClass,
)


class _CountingOp:
    """Async op that counts executions and can be made slow or failing."""

    def __init__(self, *, result: object = "ok", delay: float = 0.0,
                 error: BrowserError | None = None,
                 exception: Exception | None = None) -> None:
        self.calls = 0
        self.completed = 0
        self._result = result
        self._delay = delay
        self._error = error
        self._exception = exception

    async def __call__(self) -> object:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        if self._exception is not None:
            raise self._exception
        self.completed += 1
        return self._result


async def test_execute_returns_op_value() -> None:
    executor = BrowserCommandExecutor()
    op = _CountingOp(result=42)
    assert await executor.execute("cmd-1", "key-1", op) == 42
    assert op.calls == 1


async def test_duplicate_key_replays_recorded_result_without_reexecution() -> None:
    executor = BrowserCommandExecutor()
    op = _CountingOp(result="value")
    first = await executor.execute("cmd-1", "key-1", op)
    second = await executor.execute("cmd-2", "key-1", op)  # duplicate delivery
    assert first == second == "value"
    assert op.calls == 1


async def test_duplicate_key_replays_recorded_typed_error() -> None:
    executor = BrowserCommandExecutor()
    typed = BrowserError(ErrorClass.UI_TARGET_NOT_FOUND, "nope", retryable=False)
    op = _CountingOp(error=typed)
    with pytest.raises(BrowserError):
        await executor.execute("cmd-1", "key-1", op)
    with pytest.raises(BrowserError) as excinfo:
        await executor.execute("cmd-2", "key-1", op)
    assert excinfo.value.error_class is ErrorClass.UI_TARGET_NOT_FOUND
    assert op.calls == 1


async def test_concurrent_duplicate_joins_inflight_execution() -> None:
    executor = BrowserCommandExecutor()
    op = _CountingOp(result="shared", delay=0.15)
    results = await asyncio.gather(
        executor.execute("cmd-1", "key-1", op),
        executor.execute("cmd-2", "key-1", op),
        executor.execute("cmd-3", "key-1", op),
    )
    assert results == ["shared", "shared", "shared"]
    assert op.calls == 1  # exactly one execution


async def test_cancel_in_flight_raises_typed_cancelled() -> None:
    executor = BrowserCommandExecutor()
    op = _CountingOp(result="never", delay=5.0)
    token = CancelToken()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.05)
        token.cancel()

    canceller = asyncio.ensure_future(cancel_soon())
    with pytest.raises(BrowserError) as excinfo:
        await executor.execute("cmd-1", "key-1", op, cancel_token=token)
    await canceller
    err = excinfo.value
    assert err.error_class is ErrorClass.CANCELLED
    assert err.retryable is False
    assert op.calls == 1
    assert op.completed == 0  # the underlying task never finished


async def test_pre_cancelled_token_never_starts_the_op() -> None:
    executor = BrowserCommandExecutor()
    op = _CountingOp()
    token = CancelToken()
    token.cancel()
    with pytest.raises(BrowserError) as excinfo:
        await executor.execute("cmd-1", "key-1", op, cancel_token=token)
    assert excinfo.value.error_class is ErrorClass.CANCELLED
    assert excinfo.value.evidence["pre_execution"] is True
    assert op.calls == 0


async def test_cancelled_is_terminal_and_replayed_for_duplicates() -> None:
    executor = BrowserCommandExecutor()
    token = CancelToken()
    token.cancel()
    op = _CountingOp()
    with pytest.raises(BrowserError):
        await executor.execute("cmd-1", "key-1", op, cancel_token=token)
    with pytest.raises(BrowserError) as excinfo:
        await executor.execute("cmd-2", "key-1", op)  # duplicate, no token
    assert excinfo.value.error_class is ErrorClass.CANCELLED
    assert op.calls == 0


async def test_lru_bound_evicts_oldest_record() -> None:
    executor = BrowserCommandExecutor(max_records=2)
    op = _CountingOp(result="v")
    await executor.execute("c1", "key-1", op)
    await executor.execute("c2", "key-2", op)
    await executor.execute("c3", "key-3", op)  # evicts key-1
    assert op.calls == 3
    await executor.execute("c4", "key-2", op)  # still recorded -> replay
    assert op.calls == 3
    await executor.execute("c5", "key-1", op)  # evicted -> re-executes
    assert op.calls == 4


async def test_unexpected_exception_is_wrapped_internal_bug() -> None:
    executor = BrowserCommandExecutor()
    op = _CountingOp(exception=RuntimeError("boom"))
    with pytest.raises(BrowserError) as excinfo:
        await executor.execute("cmd-1", "key-1", op)
    assert excinfo.value.error_class is ErrorClass.INTERNAL_BUG
    assert excinfo.value.retryable is False


async def test_empty_idempotency_key_is_validation_error() -> None:
    executor = BrowserCommandExecutor()
    with pytest.raises(BrowserError) as excinfo:
        await executor.execute("cmd-1", "  ", _CountingOp())
    assert excinfo.value.error_class is ErrorClass.VALIDATION_ERROR


async def test_duplicate_with_different_op_fingerprint_fails_loudly():
    from browser_agent.commands import BrowserCommandExecutor
    from browser_agent.errors import BrowserError

    executor = BrowserCommandExecutor()

    async def op():
        return "v1"

    assert (
        await executor.execute("c1", "key-fp", op, op_fingerprint="fp-aaa") == "v1"
    )
    # same key + same fingerprint replays fine
    assert (
        await executor.execute("c2", "key-fp", op, op_fingerprint="fp-aaa") == "v1"
    )
    # same key + DIFFERENT fingerprint must not replay another command's result
    with pytest.raises(BrowserError) as exc_info:
        await executor.execute("c3", "key-fp", op, op_fingerprint="fp-bbb")
    assert str(exc_info.value.error_class) == "validation_error"
