"""The bounds a self-development run cannot talk its way past.

Attempts (patch, then each fix), wall time, and model tokens. The meter is checked before
every model call and every attempt; the first bound crossed ends the run in QUARANTINE with
that bound named - never a silent retry, never "one more try".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.selfdev.model import ModelUsage


@dataclass(frozen=True, slots=True)
class Budget:
    max_attempts: int = 3
    max_seconds: float = 1800.0
    max_tokens: int = 300_000


@dataclass(slots=True)
class BudgetMeter:
    budget: Budget
    started: float = field(default_factory=time.monotonic)
    attempts: int = 0

    def exhausted(self, usage: ModelUsage) -> str | None:
        if self.attempts > self.budget.max_attempts:
            return f"attempts: {self.attempts - 1} of {self.budget.max_attempts} used"
        elapsed = time.monotonic() - self.started
        if elapsed > self.budget.max_seconds:
            return f"time: {elapsed:.0f}s over {self.budget.max_seconds:.0f}s"
        if usage.total > self.budget.max_tokens:
            return f"tokens: {usage.total} over {self.budget.max_tokens}"
        return None

    def elapsed(self) -> float:
        return time.monotonic() - self.started
