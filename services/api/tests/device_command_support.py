"""Shared test double for app.devices.commands.DeviceCommandClientProtocol.

Scripts a sequence of outcomes per (capability) or per call, so tests can
assert idempotency-key handling (duplicate terminal replay), retry-after-
expired-then-success, and failed/retryable behaviour without a real broker,
WebSocket or Temporal worker.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.devices.commands import CommandOutcome, CommandSucceeded


@dataclass
class RecordedCall:
    device_id: uuid.UUID
    capability: str
    payload: dict[str, Any]
    idempotency_key: str
    trace_id: str


@dataclass
class FakeDeviceCommandClient:
    """Scripted outcomes: a list of outcomes (consumed in order per distinct
    idempotency_key), or a single default outcome/factory for every call.

    - ``outcomes_by_key``: idempotency_key -> list[CommandOutcome], popped in
      order; a duplicate call with an ALREADY-CONSUMED key replays the last
      outcome (mirrors the real terminal-ack replay guarantee).
    - ``default_outcome``: used when no scripted list exists for a key.
    - ``factory``: overrides both — called with the call kwargs, for tests
      that need payload-dependent behaviour.
    """

    outcomes_by_key: dict[str, list[CommandOutcome]] = field(default_factory=dict)
    default_outcome: CommandOutcome = field(default_factory=lambda: CommandSucceeded({}))
    factory: Callable[..., CommandOutcome] | None = None
    calls: list[RecordedCall] = field(default_factory=list)
    _last_by_key: dict[str, CommandOutcome] = field(default_factory=dict)

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
        self.calls.append(
            RecordedCall(device_id, capability, payload, idempotency_key, trace_id)
        )
        if heartbeat is not None:
            heartbeat()
        if self.factory is not None:
            outcome = self.factory(
                device_id=device_id, capability=capability, payload=payload,
                idempotency_key=idempotency_key,
            )
            self._last_by_key[idempotency_key] = outcome
            return outcome

        queue = self.outcomes_by_key.get(idempotency_key)
        if queue:
            outcome = queue.pop(0)
            self._last_by_key[idempotency_key] = outcome
            return outcome
        if idempotency_key in self._last_by_key:
            return self._last_by_key[idempotency_key]  # terminal-ack replay
        self._last_by_key[idempotency_key] = self.default_outcome
        return self.default_outcome


__all__ = ["FakeDeviceCommandClient", "RecordedCall"]
