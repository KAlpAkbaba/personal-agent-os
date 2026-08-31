"""Browser-command layer: idempotency and cancellation (ADR-0019).

Mirrors the device-protocol command semantics (ADR-0016): upstream delivery is
at-least-once, so the browser-command layer provides effectively-once
*execution* through ``idempotency_key`` dedup, plus cooperative cancellation:

- A duplicate ``idempotency_key`` arriving after the command reached a
  terminal state replays the recorded terminal result (value or typed error)
  without re-executing anything. Terminal records live in a bounded LRU.
- A duplicate arriving while the original is still in flight awaits the
  original's terminal result — the operation body runs exactly once.
- Cancelling an in-flight command via its :class:`CancelToken` aborts the
  underlying task and surfaces the taxonomy error ``cancelled``
  (retryable=False). Cancellation is itself a terminal state: later
  duplicates of a cancelled command replay ``cancelled`` and do not
  re-execute.

The layer is transport-agnostic: ``op`` is any async callable, typically a
closure over :class:`~browser_agent.session.BrowserSession` methods.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from .errors import BrowserError, ErrorClass
from .obs_logging import get_logger

logger = get_logger(__name__)


class CancelToken:
    """Cooperative cancellation handle for one submitted command."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()


@dataclass(slots=True)
class _Terminal:
    """Recorded terminal outcome of one idempotency key."""

    command_id: str
    value: Any = None
    error: BrowserError | None = None


class BrowserCommandExecutor:
    """Executes browser commands with idempotency + cancellation semantics.

    Not thread-safe; one executor per event loop / session owner. The terminal
    record store is a bounded LRU (``max_records``): once a key is evicted a
    duplicate would re-execute, which matches the device-agent idempotency
    store semantics (ADR-0017, LRU 1000).
    """

    def __init__(self, *, max_records: int = 512) -> None:
        if max_records < 1:
            raise ValueError("max_records must be >= 1")
        self._max_records = max_records
        self._records: OrderedDict[str, _Terminal] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[_Terminal]] = {}

    async def execute(
        self,
        command_id: str,
        idempotency_key: str,
        op: Callable[[], Awaitable[Any]],
        cancel_token: CancelToken | None = None,
    ) -> Any:
        """Run ``op`` (or replay/join its outcome) under ``idempotency_key``.

        Returns the operation's value; raises the recorded/produced typed
        ``BrowserError`` otherwise. A duplicate call's own ``cancel_token`` is
        ignored once it joins an in-flight or recorded execution (the original
        submission owns the execution).
        """
        _require_nonempty("command_id", command_id)
        _require_nonempty("idempotency_key", idempotency_key)

        recorded = self._records.get(idempotency_key)
        if recorded is not None:
            self._records.move_to_end(idempotency_key)
            logger.info(
                "browser.command_duplicate_replayed",
                command_id=command_id,
                original_command_id=recorded.command_id,
                idempotency_key=idempotency_key,
            )
            return _deliver(recorded)

        inflight = self._inflight.get(idempotency_key)
        if inflight is not None:
            logger.info(
                "browser.command_duplicate_joined_inflight",
                command_id=command_id,
                idempotency_key=idempotency_key,
            )
            terminal = await asyncio.shield(inflight)
            return _deliver(terminal)

        future: asyncio.Future[_Terminal] = asyncio.get_running_loop().create_future()
        self._inflight[idempotency_key] = future
        try:
            terminal = await self._run(command_id, op, cancel_token)
        except asyncio.CancelledError:
            # The *executor caller's* task was cancelled (not a CancelToken).
            # Don't record a terminal; release any joined duplicates.
            self._inflight.pop(idempotency_key, None)
            if not future.done():
                future.cancel()
            raise
        self._record(idempotency_key, terminal)
        self._inflight.pop(idempotency_key, None)
        future.set_result(terminal)
        return _deliver(terminal)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    async def _run(
        self,
        command_id: str,
        op: Callable[[], Awaitable[Any]],
        cancel_token: CancelToken | None,
    ) -> _Terminal:
        try:
            if cancel_token is not None and cancel_token.cancelled:
                raise _cancelled_error(command_id, pre_execution=True)
            task = asyncio.ensure_future(op())
            if cancel_token is None:
                value = await task
            else:
                waiter = asyncio.ensure_future(cancel_token.wait())
                try:
                    done, _pending = await asyncio.wait(
                        {task, waiter}, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    waiter.cancel()
                    with suppress(asyncio.CancelledError):
                        await waiter
                if task not in done:
                    task.cancel()
                    with suppress(BaseException):
                        await task
                    logger.info("browser.command_cancelled", command_id=command_id)
                    raise _cancelled_error(command_id, pre_execution=False)
                value = task.result()
        except BrowserError as err:
            return _Terminal(command_id=command_id, error=err)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _Terminal(
                command_id=command_id,
                error=BrowserError(
                    ErrorClass.INTERNAL_BUG,
                    f"command op raised unexpected {type(exc).__name__}: {exc}",
                    retryable=False,
                    evidence={"command_id": command_id},
                ),
            )
        return _Terminal(command_id=command_id, value=value)

    def _record(self, idempotency_key: str, terminal: _Terminal) -> None:
        self._records[idempotency_key] = terminal
        self._records.move_to_end(idempotency_key)
        while len(self._records) > self._max_records:
            self._records.popitem(last=False)


def _deliver(terminal: _Terminal) -> Any:
    if terminal.error is not None:
        raise terminal.error
    return terminal.value


def _cancelled_error(command_id: str, *, pre_execution: bool) -> BrowserError:
    when = "before execution started" if pre_execution else "while in flight"
    return BrowserError(
        ErrorClass.CANCELLED,
        f"command {command_id} was cancelled {when}",
        retryable=False,
        evidence={"command_id": command_id, "pre_execution": pre_execution},
    )


def _require_nonempty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"{field_name} must be a non-empty string",
            retryable=False,
        )
