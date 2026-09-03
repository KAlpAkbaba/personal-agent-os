"""DeviceCommandClient: create + await a device command over the broker path.

Used by Temporal research activities (synchronous, run in a thread-pool
executor — see app/worker.py) and by anything else that needs to dispatch a
device command and block for its terminal outcome with idempotency and a
heartbeat callback (e.g. Temporal's ``activity.heartbeat()``).

Delivery: the command row is always created durably through
``broker.service.create_command`` first (the idempotency key is the caller's
— DEVICE_PROTOCOL.md's at-least-once/effectively-once discipline). When the
API process's own ``BrokerRuntime`` (registered via
:func:`register_broker_runtime` at startup — the "module-level registry" the
spec asks for) has a live connection for the device, delivery is attempted
immediately, exactly like ``POST /v1/devices/{id}/commands`` does. Otherwise
the row sits ``pending`` and ``BrokerRuntime._sweep_loop`` delivers it within
one sweep interval (app/broker/runtime.py). Either way this client only
POLLS the row for its terminal status — it never assumes it were the one that
delivered it.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session, sessionmaker

from app.broker import service
from app.broker.models import (
    COMMAND_STATUS_CANCELLED,
    COMMAND_STATUS_EXPIRED,
    COMMAND_STATUS_FAILED,
    COMMAND_STATUS_SUCCEEDED,
)
from app.broker.runtime import BrokerRuntime
from app.logging import get_logger

logger = get_logger("app.devices.commands")

DEFAULT_POLL_INTERVAL_S = 0.5

# Device-taxonomy classes considered retryable by a caller that gets one back
# as a CommandFailed (DEVICE_PROTOCOL.md §8 / BROWSER_CAPABILITIES.md §5).
_RETRYABLE_ERROR_CLASSES = frozenset(
    {
        "dependency_unavailable",
        "timeout",
        "ui_state_changed",
        "provider_rate_limited",
    }
)


# ------------------------------------------------------------- module registry


_broker_runtime: BrokerRuntime | None = None


def register_broker_runtime(runtime: BrokerRuntime | None) -> None:
    """The API sets this at startup so DeviceCommandClient can deliver
    in-process without waiting for the sweep (spec §5)."""
    global _broker_runtime
    _broker_runtime = runtime


def get_broker_runtime() -> BrokerRuntime | None:
    return _broker_runtime


# ------------------------------------------------------------------- outcomes


@dataclass(frozen=True, slots=True)
class CommandSucceeded:
    result: dict[str, Any] = field(default_factory=dict)
    # The command row's own id (spec §3/§5: SourceItem.command_id provenance).
    # Optional so existing scripted-outcome tests that build CommandSucceeded
    # without it keep working; the real DeviceCommandClient always sets it.
    command_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class CommandFailed:
    error_class: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class CommandExpired:
    pass


CommandOutcome = CommandSucceeded | CommandFailed | CommandExpired


def _is_retryable(error_class: str | None) -> bool:
    return (error_class or "") in _RETRYABLE_ERROR_CLASSES


@runtime_checkable
class DeviceCommandClientProtocol(Protocol):
    def run(
        self,
        *,
        device_id: uuid.UUID,
        capability: str,
        payload: dict[str, Any],
        idempotency_key: str,
        timeout_s: float,
        trace_id: str,
        heartbeat: Callable[[], None] | None = None,
    ) -> CommandOutcome: ...


class DeviceCommandClient:
    """Real client: creates the command row, delivers if possible, polls."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        broker_runtime: BrokerRuntime | None = None,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self._session_factory = session_factory
        self._broker_runtime = broker_runtime
        self._poll_interval_s = poll_interval_s

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    def _runtime(self) -> BrokerRuntime | None:
        return self._broker_runtime or get_broker_runtime()

    def run(
        self,
        *,
        device_id: uuid.UUID,
        capability: str,
        payload: dict[str, Any],
        idempotency_key: str,
        timeout_s: float,
        trace_id: str,
        heartbeat: Callable[[], None] | None = None,
    ) -> CommandOutcome:
        with self._session() as db:
            command, created = service.create_command(
                db,
                device_id=device_id,
                capability=capability,
                payload=payload,
                idempotency_key=idempotency_key,
                timeout_s=timeout_s,
                trace_id=trace_id,
            )
            command_id = command.id
            expires_at = command.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)

        if created:
            self._try_immediate_delivery(device_id, command_id)

        return self._poll(device_id, command_id, expires_at, heartbeat)

    def _try_immediate_delivery(self, device_id: uuid.UUID, command_id: uuid.UUID) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        connection = runtime.get_connection(device_id)
        if connection is None:
            return
        # ws.deliver_command is async (it sends over the live WebSocket and
        # records delivery); imported lazily to avoid a broker.ws <-> devices
        # import cycle at module load time.
        from app.broker.ws import deliver_command

        with self._session() as db:
            command = service.get_command(db, device_id, command_id)
        if command is None:
            return
        try:
            asyncio.run(deliver_command(runtime, connection, command))
        except RuntimeError as exc:  # pragma: no cover - nested loop misuse guard
            logger.warning(
                "device_command_immediate_delivery_skipped",
                command_id=str(command_id),
                error=f"{type(exc).__name__}: {exc}",
            )

    def _poll(
        self,
        device_id: uuid.UUID,
        command_id: uuid.UUID,
        expires_at: datetime,
        heartbeat: Callable[[], None] | None,
    ) -> CommandOutcome:
        while True:
            with self._session() as db:
                row = service.get_command(db, device_id, command_id)
            if row is None:  # pragma: no cover - defensive; row was just created
                return CommandFailed("internal_bug", "command vanished", False)
            if row.status == COMMAND_STATUS_SUCCEEDED:
                return CommandSucceeded(row.result_json or {}, command_id=command_id)
            if row.status == COMMAND_STATUS_FAILED:
                return CommandFailed(
                    row.error_class or "internal_bug",
                    row.error_message or "",
                    _is_retryable(row.error_class),
                )
            if row.status == COMMAND_STATUS_EXPIRED:
                return CommandExpired()
            if row.status == COMMAND_STATUS_CANCELLED:
                return CommandFailed("cancelled", row.error_message or "cancelled", False)

            if datetime.now(UTC) >= expires_at:
                return CommandExpired()
            if heartbeat is not None:
                heartbeat()
            time.sleep(self._poll_interval_s)


__all__ = [
    "CommandExpired",
    "CommandFailed",
    "CommandOutcome",
    "CommandSucceeded",
    "DeviceCommandClient",
    "DeviceCommandClientProtocol",
    "get_broker_runtime",
    "register_broker_runtime",
]
