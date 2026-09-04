"""UI-state publication: the ``UiStatePublisher`` protocol plus the default,
in-process implementation.

Kept deliberately dumb: a publisher records "subject X is now in state Y" and
can answer "what is X's state right now" / "what has X gone through". It does
not interpret states, does not retry, and never raises on a normal publish —
the presentation layer must never be the reason a goal loop fails.
"""

from __future__ import annotations

import dataclasses
import threading
from datetime import UTC, datetime
from typing import Any, Protocol

from app.uistate.state import UiState

#: how many past events a subject keeps in the in-memory publisher, per
#: (subject_kind, subject_id). Bounded so a long-lived goal cannot leak memory.
DEFAULT_HISTORY_LIMIT = 100


@dataclasses.dataclass(frozen=True, slots=True)
class UiStateEvent:
    subject_kind: str
    subject_id: str
    state: UiState
    detail: dict[str, Any]
    at: datetime

    def as_dict(self) -> dict[str, Any]:
        at = self.at if self.at.tzinfo is not None else self.at.replace(tzinfo=UTC)
        return {
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "state": self.state.value,
            "detail": dict(self.detail),
            "at": at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }


class UiStatePublisher(Protocol):
    def publish(
        self,
        subject_kind: str,
        subject_id: str,
        state: UiState,
        *,
        detail: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> UiStateEvent: ...

    def current(self, subject_kind: str, subject_id: str) -> UiStateEvent | None: ...

    def history(self, subject_kind: str, subject_id: str) -> list[UiStateEvent]: ...


class InMemoryUiStatePublisher:
    """Process-local publisher. Thread-safe (the API runs sync service calls
    via ``asyncio.to_thread``, so two publishes for the same subject can race
    across threads); a real transport (Redis pub/sub, a websocket fan-out) can
    implement the same protocol later without any caller change."""

    def __init__(self, *, history_limit: int = DEFAULT_HISTORY_LIMIT) -> None:
        self._history_limit = history_limit
        self._lock = threading.Lock()
        self._history: dict[tuple[str, str], list[UiStateEvent]] = {}

    def publish(
        self,
        subject_kind: str,
        subject_id: str,
        state: UiState,
        *,
        detail: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> UiStateEvent:
        event = UiStateEvent(
            subject_kind=subject_kind,
            subject_id=subject_id,
            state=state,
            detail=dict(detail or {}),
            at=at or datetime.now(UTC),
        )
        key = (subject_kind, subject_id)
        with self._lock:
            bucket = self._history.setdefault(key, [])
            bucket.append(event)
            if len(bucket) > self._history_limit:
                del bucket[: len(bucket) - self._history_limit]
        return event

    def current(self, subject_kind: str, subject_id: str) -> UiStateEvent | None:
        with self._lock:
            bucket = self._history.get((subject_kind, subject_id))
            return bucket[-1] if bucket else None

    def history(self, subject_kind: str, subject_id: str) -> list[UiStateEvent]:
        with self._lock:
            return list(self._history.get((subject_kind, subject_id), []))


_default_publisher: UiStatePublisher = InMemoryUiStatePublisher()


def get_publisher() -> UiStatePublisher:
    return _default_publisher


def set_publisher(publisher: UiStatePublisher) -> None:
    """Dependency-injection seam: production wiring (outside this package,
    e.g. app startup) may install a Redis-backed publisher here once one
    exists. Tests use it to install a fresh ``InMemoryUiStatePublisher`` so
    assertions never see another test's history."""
    global _default_publisher
    _default_publisher = publisher


def reset_publisher() -> InMemoryUiStatePublisher:
    """Test helper: install and return a fresh in-memory publisher."""
    publisher = InMemoryUiStatePublisher()
    set_publisher(publisher)
    return publisher


__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "InMemoryUiStatePublisher",
    "UiStateEvent",
    "UiStatePublisher",
    "get_publisher",
    "reset_publisher",
    "set_publisher",
]
