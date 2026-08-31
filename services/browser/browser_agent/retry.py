"""Bounded retry helper for retryable browser errors.

Retries an async operation only when it fails with a
:class:`~browser_agent.errors.BrowserError` whose ``retryable`` flag is True
(ui_state_changed, timeout, dependency_unavailable). Terminal classes
(ui_target_not_found, validation_error, internal_bug) and non-BrowserError
exceptions propagate immediately.

When attempts are exhausted the *last* typed error is re-raised with
``evidence["retry"]`` recording attempt count — the original error_class is
preserved because it is what drives evolution/repair behavior (decision:
we do not collapse to ``retry_exhausted`` at this layer; the orchestrator
can do that when it owns the whole task retry budget).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .errors import BrowserError
from .obs_logging import get_logger

logger = get_logger(__name__)


async def with_retry[T](
    op: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.25,
    max_delay: float = 2.0,
    op_name: str | None = None,
) -> T:
    """Run ``op`` with bounded retries on retryable BrowserErrors.

    Backoff is exponential: base_delay * 2**(attempt-1), capped at max_delay.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    name = op_name or getattr(op, "__name__", "op")
    last_error: BrowserError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await op()
        except BrowserError as err:
            if not err.retryable:
                raise
            last_error = err
            if attempt == attempts:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            logger.warning(
                "browser.retry",
                op=name,
                attempt=attempt,
                attempts=attempts,
                error_class=str(err.error_class),
                delay_s=round(delay, 3),
            )
            await asyncio.sleep(delay)
    assert last_error is not None
    last_error.evidence["retry"] = {"attempts": attempts, "exhausted": True}
    logger.error(
        "browser.retry_exhausted",
        op=name,
        attempts=attempts,
        error_class=str(last_error.error_class),
    )
    raise last_error
